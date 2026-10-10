"""riscv_tests.py — ISS vs Spike, lockstep, over the riscv-tests rv32ui/rv32mi suites.

Runs every test the rv32i-soc-asic core can run: all of rv32ui and rv32mi except ma_data
(misaligned access), pmpaddr (PMP) and breakpoint (debug triggers) — 41 + 14 = 55,
the same set rv32i-soc-asic runs on the RTL. For each: the
ISS must pass on its own, and its retirement trace must match Spike's exactly.

Usage: python3 scripts/riscv_tests.py <dir with rv32ui-p-* / rv32mi-p-* ELFs>
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.compare import compare       # noqa: E402
from cosim.run import iss_trace         # noqa: E402
from cosim.spike_log import read_spike  # noqa: E402

SPIKE = [os.environ.get("SPIKE", "spike"),"--isa=rv32i_zicsr_zicntr_zifencei", "--priv=M",
         "--pmpregions=0", "--triggers=0", "--log-commits"]
EXCLUDED = {"ma_data": "misaligned data access traps on this core (spec-legal)",
            "pmpaddr": "no PMP on this core",
            # The core traps on tselect (confirmed on the RTL: mcause 2, mtval = the
            # instruction word); Spike commits it even with --triggers=0 and no Sdtrig.
            # rv32i-soc-asic excludes the same three tests (scripts/run_isa_tests.sh).
            "breakpoint": "no debug triggers on this core; Spike still implements tselect"}


def main(d):
    tests = sorted(p for p in pathlib.Path(d).iterdir()
                   if p.name.startswith(("rv32ui-p-", "rv32mi-p-")) and p.suffix == "")
    n_ok = n_ret = 0
    bad = []
    for t in tests:
        name = t.name.split("-p-")[1]
        if name in EXCLUDED:
            print(f"  skip {t.name:28s} {EXCLUDED[name]}")
            continue
        recs, code, iss = iss_trace(str(t))
        log = subprocess.run(SPIKE + [str(t)], capture_output=True, text=True,
                             timeout=30).stderr.splitlines()
        res = compare(read_spike(log, tohost=iss.tohost),
                      [r for r in recs if not r.trap])
        status = "PASS" if code == 0 else f"FAIL({code})"
        traps = sum(r.trap for r in recs)
        if code == 0 and res.ok:
            n_ok += 1
            n_ret += res.matched
            print(f"  ok   {t.name:28s} ISS {status}, {res.matched:5d} match Spike"
                  + (f", {traps} traps" if traps else ""))
        else:
            bad.append(t.name)
            print(f"  BAD  {t.name:28s} ISS {status}\n" + res.report("spike", "iss"))
    total = n_ok + len(bad)
    print(f"{n_ok} / {total} tests: ISS passes and matches Spike, "
          f"{n_ret} retirements identical")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "riscv-tests"))
