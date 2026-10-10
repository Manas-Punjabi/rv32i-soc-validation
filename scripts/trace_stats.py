"""trace_stats.py — count the events a set of RVFI dumps actually exercised.

A clean lockstep run proves something only about events that happened. This counts, from
the RTL's own trace (the cycle column included), the events the stress programs aim at:
  retirements, traps by cause class, backward taken branches (loops), CSR instructions,
  MMIO loads/stores, fence.i, and cycles spent with no retirement after an MMIO access
  (APB stall cycles as seen from WB).

Usage: python3 scripts/trace_stats.py <rvfi dump> ...
"""
import sys
from collections import Counter


def main(paths):
    c = Counter()
    for p in paths:
        prev_cycle = None
        prev_mmio = False
        for line in open(p):
            if not line.strip() or line[0] == "#":
                continue
            f = line.split()
            pc, pc_insn, trap = int(f[1], 16), int(f[2], 16), f[3] == "1"
            addr, rmask, wmask = int(f[6], 16), int(f[7], 16), int(f[8], 16)
            cyc = int(f[10]) if len(f) > 10 else None
            op, f3 = pc_insn & 0x7F, (pc_insn >> 12) & 7
            c["retirements"] += 1
            if trap:
                c["traps"] += 1
                if pc_insn == 0x00000073: c["  trap ecall"] += 1
                elif pc_insn == 0x00100073: c["  trap ebreak"] += 1
                elif op in (0x03, 0x23): c["  trap misaligned ld/st"] += 1
                else: c["  trap illegal"] += 1
            if op == 0x73 and f3 in (1, 2, 3, 5, 6, 7):
                c["CSR instructions"] += 1
            if op == 0x73 and pc_insn == 0x30200073:
                c["mret"] += 1
            if op == 0x0F and f3 == 1:
                c["fence.i"] += 1
            if op == 0x63 and not trap:
                imm_neg = pc_insn >> 31
                c["branches"] += 1
                if imm_neg:
                    c["  backward branches"] += 1
            mmio = (rmask or wmask) and (addr >> 28) == 4
            if mmio:
                c["MMIO loads" if rmask else "MMIO stores"] += 1
            if cyc is not None and prev_cycle is not None and prev_mmio:
                c["cycles after an MMIO access before next retirement"] += cyc - prev_cycle - 1
            prev_cycle, prev_mmio = cyc, mmio
    order = ["retirements", "traps", "  trap illegal", "  trap ecall", "  trap ebreak",
             "  trap misaligned ld/st", "mret", "CSR instructions", "branches",
             "  backward branches", "fence.i", "MMIO loads", "MMIO stores",
             "cycles after an MMIO access before next retirement"]
    for k in order:
        print(f"  {k:52s} {c[k]:8d}")


if __name__ == "__main__":
    main(sys.argv[1:])
