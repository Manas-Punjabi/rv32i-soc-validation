"""bench_xcheck.py — does an independent simulator reproduce rv32i-soc-asic's benchmark?

rv32i-soc-asic measures its accelerator benchmark (sw/main.c) on Icarus (`make bench`) and
publishes the numbers. This runs the same image three ways:

  1. Icarus, rv32i-soc-asic's own tb_soc, with rv32i-soc-asic's elf2hex image   (its measurement)
  2. Verilator, this repo's cosim_top                                 (the cross-check)
  3. the Verilator run's RVFI trace in guided lockstep against the ISS

and requires the two UART reports to be identical line for line, the two runs to end
with the same exit code, and the lockstep to match.

Usage: python3 scripts/bench_xcheck.py <rv32i-soc-asic dir> <elf> <tb_soc vvp> <Vcosim_top>
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.compare import lockstep      # noqa: E402
from cosim.devices import SocMMIO       # noqa: E402
from cosim.iss import ISS               # noqa: E402
from cosim.rtl import write_hex         # noqa: E402
from cosim.trace import read_rvfi       # noqa: E402


def report_lines(text):
    """The benchmark's UART report, normalized: no CR, no blank lines."""
    return [l.rstrip() for l in text.replace("\r", "").split("\n") if l.strip()]


def main(p2, elf, vvp, vsim):
    p2, elf = pathlib.Path(p2), pathlib.Path(elf)
    out = elf.parent
    tohost = int(subprocess.run(["riscv64-unknown-elf-nm", str(elf)], capture_output=True,
                                text=True).stdout.split("tohost")[0].split()[-2], 16)

    # 1. Icarus, rv32i-soc-asic's testbench and image converter
    hex_p2 = out / "p2_bench.p2.hex"
    subprocess.run(["python3", str(p2 / "scripts/elf2hex.py"), str(elf), str(hex_p2)],
                   check=True)
    ic = subprocess.run(["vvp", "-n", vvp, f"+hex={hex_p2}", f"+tohost={tohost:x}",
                         "+timeout=2000000"], capture_output=True, text=True,
                        errors="replace", timeout=1800)
    ic_lines = [l for l in report_lines(ic.stdout)
                if not l.startswith(("sorry:", "VCD")) and "$finish" not in l]
    ic_status = next((l for l in ic_lines if l.startswith(("PASS", "FAIL"))), "no PASS/FAIL")
    ic_report = [l for l in ic_lines if not l.startswith(("PASS", "FAIL"))]

    # 2. Verilator, this repo's harness (UART decoded by sim_main, '\n' printed literally)
    hex_v = out / "p2_bench.hex"
    write_hex(str(elf), hex_v)
    rvfi = out / "p2_bench.rvfi"
    vl = subprocess.run([vsim, f"+hex={hex_v}", f"+tohost={tohost:x}", f"+rvfi={rvfi}",
                         "+uart_div=16"], capture_output=True, text=True, errors="replace",
                        timeout=1800)
    uart = next((l[6:] for l in vl.stdout.splitlines() if l.startswith("uart: ")), "")
    vl_report = report_lines(uart.replace("\\n", "\n"))
    vl_status = next((l for l in vl.stdout.splitlines() if l.startswith("RTL:")), "no RTL line")

    # 3. lockstep on the Verilator trace
    iss = ISS(mmio=SocMMIO())
    iss.load_program(str(elf))
    with open(rvfi) as f:
        res = lockstep(iss, read_rvfi(f))

    print(f"icarus   (rv32i-soc-asic tb_soc): {ic_status}")
    print(f"verilator (cosim_top):        {vl_status}")
    print(f"lockstep on the Verilator trace: "
          + (f"MATCH {res.matched} retirements, {res.injected} RTL values injected"
             if res.ok else res.report("iss", "rtl")))
    print("UART report, Icarus | Verilator:")
    same = ic_report == vl_report
    for i in range(max(len(ic_report), len(vl_report))):
        a = ic_report[i] if i < len(ic_report) else ""
        b = vl_report[i] if i < len(vl_report) else ""
        print(f"  {'  ' if a == b else '!='} {a:52s} | {b}")
    ok = same and res.ok and ic_status.startswith("PASS") and "PASS" in vl_status
    print("bench-xcheck: " + ("PASS: identical reports, lockstep MATCH" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:5]))
