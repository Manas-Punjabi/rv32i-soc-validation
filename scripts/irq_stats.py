"""irq_stats.py — where did the interrupts in a set of RVFI dumps land?

For each record flagged rvfi_intr (the handler's first instruction), classify the
instruction that retired just before it, i.e. what the pipeline was doing when the
interrupt was taken, and count causes.

Usage: python3 scripts/irq_stats.py <rvfi dump> ...
"""
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from arch_cov import decode, LD, ST, CSROP   # noqa: E402


def main(paths):
    cause, before = Counter(), Counter()
    total = 0
    for p in paths:
        prev = None
        for line in open(p):
            if not line.strip() or line[0] == "#":
                continue
            f = line.split()
            if len(f) > 14 and f[12] == "1":
                total += 1
                cause[{0x8000000B: "external", 0x80000003: "software",
                       0x80000007: "timer"}.get(int(f[13], 16), f[13])] += 1
                if prev:
                    m = decode(int(prev[2], 16))[0]
                    mmio = (int(prev[7], 16) or int(prev[8], 16)) and int(prev[6], 16) >> 28 == 4
                    taken = int(prev[11], 16) != int(prev[1], 16) + 4
                    if prev[3] == "1": before["a trapping instruction"] += 1
                    elif mmio: before["an MMIO access"] += 1
                    elif m in LD.values(): before["a load"] += 1
                    elif m in ST.values(): before["a store"] += 1
                    elif m in CSROP.values(): before["a CSR instruction"] += 1
                    elif m == "mret": before["mret"] += 1
                    elif taken: before["a taken branch/jump"] += 1
                    else: before["other (ALU, not-taken branch, ...)"] += 1
            prev = f
    print(f"interrupts: {total}")
    for k, v in cause.most_common():
        print(f"  cause {k:10s} {v}")
    print("instruction retired just before the interrupt was taken:")
    for k, v in before.most_common():
        print(f"  {k:36s} {v}")


if __name__ == "__main__":
    main(sys.argv[1:])
