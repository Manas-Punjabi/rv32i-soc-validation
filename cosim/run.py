"""run.py — command-line entry points.

  python -m cosim.run iss   <elf> [--trace out.txt]      run firmware on the ISS
  python -m cosim.run cmp-spike <elf> <spike.log>        ISS vs Spike, lockstep
  python -m cosim.run cmp-rvfi  <elf> <rvfi.txt>         ISS vs RTL (RVFI dump), lockstep
"""
import argparse
import sys

from .compare import compare, lockstep
from .devices import SocMMIO
from .iss import ISS, Halt
from .spike_log import read_spike
from .trace import read_rvfi


def iss_trace(elf: str, max_insns: int = 2_000_000):
    """Run the ISS to completion; return (records, exit code or None, the ISS)."""
    iss = ISS(mmio=SocMMIO())
    iss.load_program(elf)
    recs, code = [], None
    try:
        for r in iss.run(max_insns):
            recs.append(r)
    except Halt as h:
        code = h.code
    return recs, code, iss


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cosim.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("iss"); p.add_argument("elf"); p.add_argument("--trace")
    p = sub.add_parser("cmp-spike"); p.add_argument("elf"); p.add_argument("log")
    p = sub.add_parser("cmp-rvfi"); p.add_argument("elf"); p.add_argument("rvfi")
    a = ap.parse_args(argv)

    if a.cmd == "iss":
        recs, code, iss = iss_trace(a.elf)
        if a.trace:
            with open(a.trace, "w") as f:
                for r in recs:
                    f.write(r.describe() + "\n")
        uart = iss.mmio.uart.out.decode(errors="replace") if hasattr(iss.mmio, "uart") else ""
        if uart:
            print(f"uart: {uart!r}")
        status = "PASS" if code == 0 else f"FAIL (exit code {code})"
        print(f"ISS: {status}, {len(recs)} instructions retired")
        return 0 if code == 0 else 1

    if a.cmd == "cmp-spike":
        recs, code, iss = iss_trace(a.elf)
        # Spike's commit log omits instructions that trap; RVFI reports them with
        # trap=1. Drop the ISS's trap records for this comparison only. A trap that
        # happens in the wrong place still shows up, as a pc mismatch at the next
        # instruction (the handler's first pc versus the fall-through pc).
        with open(a.log) as f:
            res = compare(read_spike(f, tohost=iss.tohost),
                          [r for r in recs if not r.trap])
        print(res.report("spike", "iss"))
    else:
        # guided lockstep: a fresh ISS, stepped once per RTL retirement
        iss = ISS(mmio=SocMMIO())
        iss.load_program(a.elf)
        with open(a.rvfi) as f:
            res = lockstep(iss, read_rvfi(f))
        print(res.report("iss", "rtl") + f"  ({res.injected} RTL values injected)")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
