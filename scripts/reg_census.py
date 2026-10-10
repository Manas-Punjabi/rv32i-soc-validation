"""reg_census.py — exactly which peripheral registers and CSRs the runs touched.

Verilator's line coverage attributes case items to shifted and merged lines (seen on
apb_timer.sv: the counter printed at line 76 counts MTIME_HI reads), so a line-coverage
hole on a register decode is confirmed here, from the RTL's own RVFI traces, before it is
acted on.

  MMIO   (slave, offset, read/write) for every retired access to 0x4xxx_xxxx
  CSR    (csr, read/write) for every retired, non-trapping CSR instruction; a write is
         csrrw/csrrwi always, csrrs/csrrc(+i) only with a nonzero rs1/zimm

Usage: python3 scripts/reg_census.py <rvfi dump> ...
"""
import sys
from collections import Counter

SLAVES = {0: "uart", 1: "accel", 2: "timer", 3: "gpio"}
REGS = {
    "uart": {0x0: "TXDATA", 0x4: "RXDATA", 0x8: "STATUS", 0xC: "CTRL", 0x10: "DIV"},
    "accel": {0x0: "CTRL", 0x4: "STATUS", 0x8: "PARAMS"},
    "timer": {0x0: "MTIME_LO", 0x4: "MTIME_HI", 0x8: "MTIMECMP_LO", 0xC: "MTIMECMP_HI",
              0x10: "MSIP"},
    "gpio": {0x0: "OUT", 0x4: "IN", 0x8: "SET", 0xC: "CLR"},
}
CSRS = {0x300: "mstatus", 0x301: "misa", 0x304: "mie", 0x305: "mtvec", 0x310: "mstatush",
        0x320: "mcountinhibit", 0x340: "mscratch", 0x341: "mepc", 0x342: "mcause",
        0x343: "mtval", 0x344: "mip", 0xB00: "mcycle", 0xB02: "minstret",
        0xB80: "mcycleh", 0xB82: "minstreth", 0xC00: "cycle", 0xC02: "instret",
        0xC80: "cycleh", 0xC82: "instreth", 0xF11: "mvendorid", 0xF12: "marchid",
        0xF13: "mimpid", 0xF14: "mhartid"}


def reg_name(addr):
    slave = SLAVES.get((addr >> 12) & 0xFFFF)
    off = addr & 0xFFF
    if slave == "accel" and off >= 0x400:
        return slave, "ABC"[(off - 0x400) // 0x400] + "[...]"
    return slave, REGS.get(slave, {}).get(off, f"unmapped+{off:#x}")


def main(paths):
    mmio, csr = Counter(), Counter()
    for p in paths:
        for line in open(p):
            if not line.strip() or line[0] == "#":
                continue
            f = line.split()
            insn, trap = int(f[2], 16), f[3] == "1"
            addr, rmask, wmask = int(f[6], 16), int(f[7], 16), int(f[8], 16)
            if (rmask or wmask) and addr >> 28 == 4:
                mmio[(*reg_name(addr), "read" if rmask else "write")] += 1
            op, f3, rs1 = insn & 0x7F, (insn >> 12) & 7, (insn >> 15) & 31
            if op == 0x73 and f3 in (1, 2, 3, 5, 6, 7) and not trap:
                name = CSRS.get(insn >> 20, f"{insn >> 20:#x}")
                rd = (insn >> 7) & 31
                writes = f3 in (1, 5) or rs1 != 0
                reads = not (f3 in (1, 5) and rd == 0)
                if reads: csr[(name, "read")] += 1
                if writes: csr[(name, "write")] += 1
    print("MMIO registers (slave register access: count); '-' = never")
    for slave, regs in REGS.items():
        for off, name in regs.items():
            r, w = mmio[(slave, name, "read")], mmio[(slave, name, "write")]
            print(f"  {slave:6s}{name:12s} read {r if r else '-':>7}  write {w if w else '-':>7}")
    extra = [k for k in mmio if k[1] not in REGS.get(k[0], {}).values()]
    for k in sorted(extra, key=str):
        print(f"  {k[0]!s:6s}{k[1]:12s} {k[2]:5s} {mmio[k]:>7}")
    print("CSRs")
    for a, name in CSRS.items():
        r, w = csr[(name, "read")], csr[(name, "write")]
        print(f"  {name:14s} read {r if r else '-':>7}  write {w if w else '-':>7}")


if __name__ == "__main__":
    main(sys.argv[1:])
