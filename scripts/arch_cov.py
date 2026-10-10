"""arch_cov.py — architectural coverage, computed post hoc from RVFI dumps.

Every bin below is defined on the retired-instruction stream (the RTL's own trace), so a
hit means the RTL really executed that situation, not that a generator intended to.

Bins
  insn      every RV32I + Zicsr + Zifencei + M-mode instruction the core implements
  raw       read-after-write: producer class x consumer slot x distance 1..3
            (distance = retirements between the producer and the reader; the nearest
            producer of that register counts). Distance 1 from a LOAD is load-use.
  branch    each branch type, taken and not taken
  trap      each exception cause
  adj       what retired immediately before a trapping instruction: the shadow of a
            redirect, of a load, of an MMIO access, of a CSR write, of a trap/mret
  csr       CSR op x {mscratch, other CSR}
  mem       load/store width x byte offset; store->load to the same word, distance 1..3
  mmio      MMIO load, MMIO store, MMIO load-use
  x0        x0 written, x0 read
  stall     a gap of >1 cycle between two retirements, by what retired first
            (needs the cycle column)

Usage: python3 scripts/arch_cov.py [--holes] NAME=dumpglob [NAME=dumpglob ...]
Each NAME is a column; the last column is the union of all sets.
"""
import glob
import sys
from collections import Counter, OrderedDict

MNEMONICS = ("lui auipc jal jalr beq bne blt bge bltu bgeu lb lh lw lbu lhu sb sh sw "
             "addi slti sltiu xori ori andi slli srli srai add sub sll slt sltu xor srl "
             "sra or and fence fence.i ecall ebreak mret wfi csrrw csrrs csrrc csrrwi "
             "csrrsi csrrci").split()
BR = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}
LD = {0: "lb", 1: "lh", 2: "lw", 4: "lbu", 5: "lhu"}
ST = {0: "sb", 1: "sh", 2: "sw"}
OPI = {0: "addi", 2: "slti", 3: "sltiu", 4: "xori", 6: "ori", 7: "andi", 1: "slli"}
OPR = {(0, 0): "add", (0x20, 0): "sub", (0, 1): "sll", (0, 2): "slt", (0, 3): "sltu",
       (0, 4): "xor", (0, 5): "srl", (0x20, 5): "sra", (0, 6): "or", (0, 7): "and"}
CSROP = {1: "csrrw", 2: "csrrs", 3: "csrrc", 5: "csrrwi", 6: "csrrsi", 7: "csrrci"}
PRODUCERS = ["ALU", "LOAD", "LINK", "CSR"]
SLOTS = ["alu.rs1", "alu.rs2", "branch.rs1", "branch.rs2", "load.addr", "store.addr",
         "store.data", "jalr.rs1", "csr.rs1"]


def decode(insn):
    """-> (mnemonic or None, producer class or None, [(slot, reg)], rd field)."""
    op, f3, f7 = insn & 0x7F, (insn >> 12) & 7, insn >> 25
    rd, rs1, rs2 = (insn >> 7) & 31, (insn >> 15) & 31, (insn >> 20) & 31
    if op == 0x37: return "lui", "ALU", [], rd
    if op == 0x17: return "auipc", "ALU", [], rd
    if op == 0x6F: return "jal", "LINK", [], rd
    if op == 0x67: return "jalr", "LINK", [("jalr.rs1", rs1)], rd
    if op == 0x63: return BR.get(f3), None, [("branch.rs1", rs1), ("branch.rs2", rs2)], 0
    if op == 0x03: return LD.get(f3), "LOAD", [("load.addr", rs1)], rd
    if op == 0x23: return ST.get(f3), None, [("store.addr", rs1), ("store.data", rs2)], 0
    if op == 0x13:
        m = "srai" if (f3 == 5 and f7 == 0x20) else "srli" if f3 == 5 else OPI.get(f3)
        return m, "ALU", [("alu.rs1", rs1)], rd
    if op == 0x33:
        return OPR.get((f7, f3)), "ALU", [("alu.rs1", rs1), ("alu.rs2", rs2)], rd
    if op == 0x0F: return ("fence.i" if f3 == 1 else "fence"), None, [], 0
    if op == 0x73:
        if f3 == 0:
            return {0x00000073: "ecall", 0x00100073: "ebreak", 0x30200073: "mret",
                    0x10500073: "wfi"}.get(insn), None, [], 0
        srcs = [("csr.rs1", rs1)] if f3 in (1, 2, 3) else []
        return CSROP.get(f3), "CSR", srcs, rd
    return None, None, [], 0


def trap_cause(insn):
    m = decode(insn)[0]
    if m == "ecall": return "ecall"
    if m == "ebreak": return "ebreak"
    if m in ("lb", "lh", "lw", "lbu", "lhu"): return "load misaligned"
    if m in ("sb", "sh", "sw"): return "store misaligned"
    if m in ("jal", "jalr") or m in BR.values(): return "fetch misaligned"
    return "illegal"


def all_bins():
    b = OrderedDict()
    for m in MNEMONICS: b[("insn", m)] = 0
    for p in PRODUCERS:
        for s in SLOTS:
            for d in (1, 2, 3): b[("raw", f"{p:4s} -> {s:10s} d={d}")] = 0
    for m in BR.values():
        for t in ("taken", "not taken"): b[("branch", f"{m} {t}")] = 0
    for c in ("illegal", "ecall", "ebreak", "load misaligned", "store misaligned",
              "fetch misaligned"):
        b[("trap", c)] = 0
    for a in ("after taken branch/jump", "after load", "after store", "after MMIO access",
              "after CSR write", "after trap", "after mret"):
        b[("adj", f"trap {a}")] = 0
    for o in CSROP.values():
        for c in ("mscratch", "other"): b[("csr", f"{o} {c}")] = 0
    for m, offs in (("lb", 4), ("lbu", 4), ("lh", 2), ("lhu", 2), ("lw", 1),
                    ("sb", 4), ("sh", 2), ("sw", 1)):
        step = 4 // offs
        for o in range(0, 4, step): b[("mem", f"{m} offset {o}")] = 0
    for d in (1, 2, 3): b[("mem", f"store->load same word d={d}")] = 0
    for k in ("load", "store", "load-use d=1"): b[("mmio", k)] = 0
    for k in ("x0 written (rd field 0)", "x0 read"): b[("x0", k)] = 0
    for k in ("load", "store", "MMIO access", "taken branch/jump", "trap", "mret", "CSR"):
        b[("stall", f">1 cycle after {k}")] = 0
    return b


# Bins that cannot be hit by construction, with the reason. Reported, never counted.
EXCLUDED = {
    ("adj", "trap after trap"):
        "the retirement after a trap is always the handler's first instruction; making "
        "it trap needs a handler whose entry faults, which re-enters itself forever",
}


def records(path):
    for line in open(path):
        if line.strip() and line[0] != "#":
            f = line.split()
            yield (int(f[1], 16), int(f[2], 16), f[3] == "1", int(f[4], 16),
                   int(f[6], 16), int(f[7], 16), int(f[8], 16),
                   int(f[10]) if len(f) > 10 else None)


def cover(paths):
    hits = Counter()
    for p in paths:
        recs = list(records(p))
        last_writer = {}                       # reg -> (index, producer class)
        stores = []                            # (index, word address)
        prev = None                            # (insn, trap, mmio, taken, cycle, rd...)
        for i, (pc, insn, trap, rd, addr, rmask, wmask, cyc) in enumerate(recs):
            m, prod, srcs, rd_field = decode(insn)
            nxt_pc = recs[i + 1][0] if i + 1 < len(recs) else None
            taken = nxt_pc is not None and nxt_pc != pc + 4 and not trap
            mmio = bool(rmask or wmask) and addr >> 28 == 4
            if m: hits[("insn", m)] += 1
            if trap:
                hits[("trap", trap_cause(insn))] += 1
                if prev is not None:
                    pm, ptrap, pmmio, ptaken, _ = prev
                    if ptaken: hits[("adj", "trap after taken branch/jump")] += 1
                    if pm in LD.values(): hits[("adj", "trap after load")] += 1
                    if pm in ST.values(): hits[("adj", "trap after store")] += 1
                    if pmmio: hits[("adj", "trap after MMIO access")] += 1
                    if pm in CSROP.values(): hits[("adj", "trap after CSR write")] += 1
                    if ptrap: hits[("adj", "trap after trap")] += 1
                    if pm == "mret": hits[("adj", "trap after mret")] += 1
            else:
                for slot, r in srcs:
                    if r == 0:
                        hits[("x0", "x0 read")] += 1
                        continue
                    w = last_writer.get(r)
                    if w and 1 <= i - w[0] <= 3:
                        hits[("raw", f"{w[1]:4s} -> {slot:10s} d={i - w[0]}")] += 1
                        if w[2] and i - w[0] == 1:
                            hits[("mmio", "load-use d=1")] += 1
                if m in BR.values():
                    hits[("branch", f"{m} {'taken' if taken else 'not taken'}")] += 1
                if m in CSROP.values():
                    csr = insn >> 20
                    hits[("csr", f"{m} {'mscratch' if csr == 0x340 else 'other'}")] += 1
                if (rmask or wmask) and m in ("lb", "lbu", "lh", "lhu", "lw", "sb", "sh", "sw"):
                    lane = ((rmask or wmask) & -(rmask or wmask)).bit_length() - 1
                    hits[("mem", f"{m} offset {lane}")] += 1
                if wmask and not mmio:
                    stores.append((i, addr))
                if rmask and not mmio:
                    for si, sa in stores[-3:]:
                        if sa == addr and 1 <= i - si <= 3:
                            hits[("mem", f"store->load same word d={i - si}")] += 1
                if mmio:
                    hits[("mmio", "load" if rmask else "store")] += 1
                if prod and rd_field == 0 and m not in ("fence", "fence.i"):
                    hits[("x0", "x0 written (rd field 0)")] += 1
                if prod and rd:
                    last_writer[rd] = (i, prod, mmio and bool(rmask))
            if prev is not None and cyc is not None and prev_cyc is not None and cyc - prev_cyc > 1:
                pm, ptrap, pmmio, ptaken, _ = prev
                if pmmio: hits[("stall", ">1 cycle after MMIO access")] += 1
                elif pm in LD.values(): hits[("stall", ">1 cycle after load")] += 1
                elif pm in ST.values(): hits[("stall", ">1 cycle after store")] += 1
                if ptaken: hits[("stall", ">1 cycle after taken branch/jump")] += 1
                if ptrap: hits[("stall", ">1 cycle after trap")] += 1
                if pm == "mret": hits[("stall", ">1 cycle after mret")] += 1
                if pm in CSROP.values(): hits[("stall", ">1 cycle after CSR")] += 1
            prev = (m, trap, mmio, taken or (m in ("jal", "jalr") and not trap), cyc)
            prev_cyc = cyc
    return hits


def main(argv):
    holes_only = "--holes" in argv
    sets = [a.split("=", 1) for a in argv if "=" in a]
    cols = [(name, cover(sorted(glob.glob(g)))) for name, g in sets]
    union = Counter()
    for _, h in cols:
        union.update(h)
    cols.append(("ALL", union))
    bins = all_bins()
    width = max(len(k[1]) for k in bins) + 8
    print(f"{'bin':{width}s}" + "".join(f"{n:>10s}" for n, _ in cols))
    group = None
    n_hit = Counter()
    n_total = Counter()
    holes = []
    for key in bins:
        if key in EXCLUDED:
            continue
        n_total[key[0]] += 1
        if union[key]:
            n_hit[key[0]] += 1
        else:
            holes.append(key)
        if holes_only and union[key]:
            continue
        if key[0] != group:
            group = key[0]
            print(f"-- {group}")
        print(f"  {key[1]:{width - 2}s}" + "".join(f"{h[key]:10d}" for _, h in cols))
    print("\nsummary (bins hit by any set / bins defined):")
    for g in n_total:
        print(f"  {g:8s} {n_hit[g]:4d} / {n_total[g]:4d}")
    print(f"  {'total':8s} {sum(n_hit.values()):4d} / {sum(n_total.values()):4d}")
    print(f"holes: {len(holes)}")
    for key, why in EXCLUDED.items():
        print(f"excluded: {key[0]} / {key[1]} ({union[key]} hits): {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
