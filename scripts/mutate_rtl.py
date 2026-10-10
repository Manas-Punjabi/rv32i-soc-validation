"""mutate_rtl.py — would lockstep co-simulation have caught rv32i-soc-asic's own RTL bugs?

rv32i-soc-asic checks its core with 28 mutants (scripts/mutate_core.sh): known bugs
injected into copies of the RTL, each of which its self-checking suites
must catch. This runs the same mutants (the same sed expressions, read from that script as
data) under lockstep:

  for each mutant: copy rv32i-soc-asic's rtl/ to a scratch tree, apply the sed expression,
  verilate soc_top with -DRVFI, run every program of the regression in lockstep, and
  record each program's outcome.

A mutant is killed if any program FAILS: the RTL's own exit code is wrong, the lockstep
comparison diverges, the trace is malformed, or the run times out. For each kill the
first divergence is reported as (program, retirement number); a self-checking test only
reports its exit code, at the end.

Program order is cheap-first and fixed, so "first killing program" is reproducible:
riscv-tests, directed, firmware, soc_smoke, random, stress, mmio, ptr. Before any mutant,
every program must pass on the unmutated design (the control).

Usage: python3 scripts/mutate_rtl.py <rv32i-soc-asic dir> <program list file> [M1 F3 ...]
"""
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.rtl import run_one   # noqa: E402


def mutants(script: pathlib.Path):
    """[(name, file, sed_expr)] from the `mut NAME FILE EXPR` lines of mutate_core.sh."""
    out = []
    for line in script.read_text().splitlines():
        if line.startswith("mut ") and not line.startswith("mut()"):
            args = shlex.split(line)[1:4]
            out.append(tuple(args))
    return out


def build(soc: pathlib.Path, bdir: pathlib.Path) -> pathlib.Path:
    rtl = sorted(str(p) for d in ("core", "accel", "bus", "periph", "mem")
                 for p in (soc / "rtl" / d).glob("*.sv")) + [str(soc / "rtl" / "soc_top.sv")]
    # interrupt side band when the SoC has it (rv32i-soc-asic does)
    intr = "rvfi_intr" in (soc / "rtl" / "soc_top.sv").read_text()
    idef = " -DRVFI_INTR" if intr else ""
    cmd = ["verilator", "--cc", "--exe", "--build", "-j", "8", "-Wno-fatal", "-DRVFI",
           "--top-module", "cosim_top", "--x-assign", "unique", "--x-initial", "unique",
           "-CFLAGS", '-O1 -DVTOP=Vcosim_top -DVTOP_HEADER=\\"Vcosim_top.h\\" -DHAS_UART' + idef,
           "-Mdir", str(bdir)] + (["-DRVFI_INTR"] if intr else []) + rtl + [str(ROOT / "tb/cosim_top.sv"),
                                        str(ROOT / "tb/sim_main.cpp")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode and "attempted to destroy locked Thread Pool" in r.stdout + r.stderr:
        # Verilator 5.020 internal error at shutdown, after the model is built; seen once,
        # clean on retry. Retry once so a tool crash is never counted.
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stdout[-2000:] + r.stderr[-2000:])
    return bdir / "Vcosim_top"


def main(argv):
    p2 = pathlib.Path(argv[0]).resolve()
    programs = [l.strip() for l in open(argv[1]) if l.strip()]
    only = set(argv[2:])
    # programs that take interrupts on purpose: on a SoC without the RVFI side band they
    # are compared up to their first interrupt, never counted as a kill for that alone
    irq = {p for p in programs if pathlib.Path(p).name in ("soc_smoke.elf", "d_irq_soc.elf")
           or pathlib.Path(p).name.startswith("irq")}
    muts = [m for m in mutants(p2 / "scripts/mutate_core.sh")
            if not only or m[0].split("_")[0] in only]
    print(f"{len(muts)} mutants x {len(programs)} programs")
    killed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        # control: every program must pass on the unmutated design, or it would "kill"
        # every mutant without saying anything about them
        sim = build(p2, tmp / "obj")
        work = tmp / "run"
        work.mkdir()
        base_fail = [pathlib.Path(p).name for p in programs
                     if run_one(str(sim), p, work, interrupts=p in irq,
                                max_cycles=1_000_000)[0] == "fail"]
        shutil.rmtree(tmp / "obj", ignore_errors=True)
        if base_fail:
            print(f"baseline FAILED on the unmutated design: {', '.join(base_fail)}")
            return 1
        print(f"baseline: all {len(programs)} programs pass on the unmutated design")
        for name, fname, expr in muts:
            t0 = time.time()
            soc = tmp / "soc"
            shutil.rmtree(soc, ignore_errors=True)
            shutil.copytree(p2 / "rtl", soc / "rtl")
            target = soc / "rtl" / "core" / fname
            before = target.read_bytes()
            subprocess.run(["sed", "-i", expr, str(target)], check=True)
            if target.read_bytes() == before:
                print(f"  {name:32s} NOT APPLIED (pattern not found in current RTL)", flush=True)
                continue
            try:
                sim = build(soc, tmp / "obj")
            except RuntimeError as e:
                print(f"  {name:32s} BUILD FAILED\n{e}", flush=True)
                continue
            work = tmp / "run"
            work.mkdir(exist_ok=True)
            kills, first, how = [], None, ""
            for prog in programs:
                # 1M cycles: the longest program (rv32i-soc-asic's main.c) needs ~175k, so a
                # hung mutant is cut off quickly instead of at the 4M default
                status, line, detail = run_one(str(sim), prog, work,
                                               interrupts=prog in irq, max_cycles=1_000_000)
                if status == "fail":
                    kills.append(pathlib.Path(prog).name)
                    if first is None:
                        first = pathlib.Path(prog).name
                        m = re.search(r"DIVERGED after (\d+)", line)
                        if m:
                            how = f"lockstep diverged at retirement #{m.group(1)}"
                        elif "ERROR" in line:
                            how = "malformed trace: " + line.split("ERROR", 1)[1].strip()[:70]
                        elif "TIMEOUT" in line:
                            how = "RTL timed out"
                        else:
                            how = "RTL self-check failed, lockstep matched"
            shutil.rmtree(tmp / "obj", ignore_errors=True)
            dt = time.time() - t0
            if kills:
                killed += 1
                print(f"  killed   {name:32s} by {len(kills):3d}/{len(programs)} programs; "
                      f"first {first}: {how}  ({dt:.0f} s)", flush=True)
            else:
                print(f"  SURVIVED {name:32s} 0/{len(programs)} programs  ({dt:.0f} s)",
                      flush=True)
    print(f"lockstep killed {killed} / {len(muts)} mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
