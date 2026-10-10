"""rtl.py — run firmware on the Verilator model of soc_top and lockstep it against the ISS.

  python -m cosim.rtl --sim build/cosim/Vcosim_top [--seed N] [--keep DIR] <elf> ...

For each ELF: write the $readmemh image, run the model (it dumps one RVFI record per
retired instruction), then step a fresh ISS once per record with guided lockstep
(compare.lockstep: counter, ID-CSR, mip and MMIO-load values come from the RTL, and so
does the fact that an interrupt was taken if the SoC has the RVFI side band; everything
else is computed and compared). One summary line per program; the full divergence
report on failure. Exit status is non-zero if any program fails or diverges.

--seed randomizes every uninitialized flop and memory bit in the model
(+verilator+rand+reset+2), so a dependence on X/reset values shows up as a seed-dependent
result instead of being hidden by Verilator's default zero initialization.
"""
import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

from .compare import lockstep
from .devices import SocMMIO
from .iss import ISS, RAM_BASE, load_elf
from .trace import read_rvfi

MEM_BYTES = 64 * 1024        # cosim_top's MEM_WORDS = 16384


def write_hex(elf: str, path: pathlib.Path) -> int:
    """Word image for $readmemh, '@<word index>' per segment. Returns tohost (or 0)."""
    _, segs, syms = load_elf(elf)
    lines = []
    for vaddr, blob in segs:
        off = vaddr - RAM_BASE
        if off < 0 or off + len(blob) > MEM_BYTES or off % 4:
            raise ValueError(f"{elf}: segment {vaddr:08x}+{len(blob)} outside the 64 KB RAM")
        blob = blob + b"\0" * (-len(blob) % 4)
        lines.append(f"@{off // 4:x}")
        lines += [f"{int.from_bytes(blob[i:i + 4], 'little'):08x}"
                  for i in range(0, len(blob), 4)]
    path.write_text("\n".join(lines) + "\n")
    return syms.get("tohost", 0)


def _interrupt_entry(iss, res) -> bool:
    """True if the divergence is the RTL entering its trap handler while the ISS, at the
    same point, has interrupts globally enabled: the RTL took an interrupt, which the
    ISS cannot follow (a SoC without the RVFI interrupt side band)."""
    if res.mismatch is None:
        return False
    _, _, dut, _ = res.mismatch
    return dut.pc == iss.mtvec & ~3 & 0xFFFFFFFF and bool(iss.mstatus & 0x8)


def run_one(sim: str, elf: str, work: pathlib.Path, seed=None, uart_div: int = 16,
            interrupts: bool = False, cov_dir=None, max_cycles=None):
    """Returns (status, summary line, detail text); status is 'ok', 'irq' or 'fail'.

    interrupts=True only for programs that take interrupts on purpose: lockstep is then
    reported valid up to the first interrupt entry ('irq'), never as a full MATCH."""
    name = pathlib.Path(elf).name
    hexf, rvfi = work / f"{name}.hex", work / f"{name}.rvfi"
    tohost = write_hex(elf, hexf)
    if not tohost:
        return False, f"{name:28s} no tohost symbol", ""
    cmd = [sim, f"+hex={hexf}", f"+tohost={tohost:x}", f"+rvfi={rvfi}", f"+uart_div={uart_div}"]
    if max_cycles:                                # sim_main takes it in hex
        cmd.append(f"+max_cycles={max_cycles:x}")
    if cov_dir:                                   # coverage build: one .dat per run
        cmd.append(f"+cov={pathlib.Path(cov_dir) / (name + '.dat')}")
    if seed is not None:
        cmd += ["+verilator+rand+reset+2", f"+verilator+seed+{seed}"]
    # errors="replace": a broken design can put arbitrary bytes on the UART
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=600)
    rtl_line = next((l for l in p.stdout.splitlines() if l.startswith("RTL:")), p.stdout.strip())
    m = re.search(r"(\d+) instructions retired \((\d+) traps\) in (\d+) cycles \(CPI ([\d.]+)\)",
                  rtl_line)

    iss = ISS(mmio=SocMMIO())
    iss.load_program(elf)
    try:
        with open(rvfi) as f:
            res = lockstep(iss, read_rvfi(f))
    except (ValueError, MemoryError) as e:            # malformed trace / ISS out of RAM
        return "fail", f"{name:28s} {rtl_line}  | lockstep ERROR {e}", p.stdout
    irq = interrupts and _interrupt_entry(iss, res)
    ok = p.returncode == 0 and (res.ok or irq)

    # <elf>.expect: UART text the program must print, as rv32i-soc-asic's tb_soc checks it
    # (the harness prints '\n' as a literal backslash-n; trailing newlines don't count)
    expect = pathlib.Path(elf + ".expect")
    uart = next((l[6:] for l in p.stdout.splitlines() if l.startswith("uart: ")), "")
    uart_note = ""
    if expect.exists():
        want = expect.read_text().rstrip("\n").replace("\n", "\\n")
        got = uart
        while got.endswith("\\n"):
            got = got[:-2]
        uart_note = "  uart OK" if got == want else f"  uart MISMATCH {got!r} != {want!r}"
        ok = ok and got == want
    if m:
        rtl = f"RTL {'PASS' if p.returncode == 0 else 'FAIL'} {int(m[3]):7d} cyc CPI {m[4]}"
    else:
        rtl = rtl_line
    if res.ok:
        cmp = f"MATCH {res.matched:6d} ret, {res.injected:3d} injected"
        if iss.interrupts_taken:
            cmp += f", {iss.interrupts_taken} interrupts"
    elif irq:
        cmp = (f"MATCH {res.matched:6d} ret up to the first interrupt "
               f"(no RVFI interrupt side band)")
    else:
        cmp = f"DIVERGED after {res.matched}"
    detail = "" if ok else (p.stdout + res.report("iss", "rtl"))
    status = "fail" if not ok else ("irq" if irq else "ok")
    return status, f"{name:28s} {rtl}  | lockstep {cmp}{uart_note}", detail


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cosim.rtl")
    ap.add_argument("--sim", required=True, help="Verilator model (build/cosim/Vcosim_top)")
    ap.add_argument("--seed", type=int, help="randomize uninitialized state with this seed")
    ap.add_argument("--cov", help="coverage build: write <dir>/<elf>.dat per run")
    ap.add_argument("--keep", help="directory for hex/RVFI files (default: a temp dir)")
    ap.add_argument("--interrupts", action="append", default=[], metavar="ELF",
                    help="program that takes interrupts on purpose (repeatable)")
    ap.add_argument("--xfail", action="append", default=[], metavar="ELF:BUG",
                    help="program expected to fail because of a known, unfixed RTL bug; "
                         "if it passes, that is an error (XPASS): the list is stale")
    ap.add_argument("elfs", nargs="+")
    a = ap.parse_args(argv)

    tmp = None if a.keep else tempfile.TemporaryDirectory()
    work = pathlib.Path(a.keep or tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    irq_elfs = {pathlib.Path(e).resolve() for e in a.interrupts}
    xfail = {pathlib.Path(x.rsplit(":", 1)[0]).resolve(): x.rsplit(":", 1)[1] for x in a.xfail}
    count = {"ok": 0, "irq": 0, "fail": 0, "xfail": 0, "xpass": 0}
    for elf in a.elfs:
        path = pathlib.Path(elf).resolve()
        status, line, detail = run_one(a.sim, elf, work, a.seed, interrupts=path in irq_elfs,
                                       cov_dir=a.cov)
        if path in xfail:
            bug = xfail[path]
            if status == "fail":
                status, line = "xfail", f"{line}  -> XFAIL (known RTL bug: {bug})"
                detail = ""
            else:
                status, line = "xpass", (f"{line}  -> XPASS: {bug} no longer reproduces; "
                                         f"drop the --xfail")
        print(line, flush=True)
        if status == "fail" and detail:
            print(detail)
        count[status] += 1
    seed = f" (seed {a.seed})" if a.seed is not None else ""
    print(f"{count['ok']} / {len(a.elfs)} programs: RTL passes and matches the ISS in lockstep"
          + (f"; {count['irq']} match up to their first interrupt" if count["irq"] else "")
          + (f"; {count['xfail']} XFAIL (known RTL bugs)" if count["xfail"] else "")
          + (f"; {count['xpass']} XPASS" if count["xpass"] else "")
          + (f"; {count['fail']} FAIL" if count["fail"] else "") + seed)
    return 0 if count["fail"] == 0 and count["xpass"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
