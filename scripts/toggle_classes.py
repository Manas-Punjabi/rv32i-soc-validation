"""toggle_classes.py — sort never-toggled bits into explained classes and the rest.

A toggle percentage mixes bits that cannot move in this SoC with bits the tests failed to
move. Two narrow, name-based rules take out the first kind; everything else is printed
one signal at a time, to be explained individually or treated as a test gap.

  ADDR     a pc/address-carrying signal, bit >= 16, or bit 0/1 of a pc. Code and data
           live in 64 KB of RAM at 0x8000_0000 (MEM_WORDS = 16384): above bit 15 an
           address only ever reads 0x8000 (RAM) or 0x4000 (MMIO, data side), and pcs are
           word-aligned (no C extension).
  COUNTER  a free-running counter, bit >= 18: the longest run is under 2^18 cycles.
  other    everything else, listed.

Usage: python3 scripts/toggle_classes.py <merged.dat> [--scope <hier substring>]
"""
import re
import sys
from collections import Counter, defaultdict

FIELD = re.compile(r"\x01(\w+)\x02([^\x01']*)")
SIG = re.compile(r"^(.*?)(?:\[(\d+)\])?$")
PC = re.compile(r"(^|_)(pc|tvec|epc|mtvec|mepc|redirect_pc|jump_target|trap_pc|instr_pc|"
                r"pc_issue|pc_q|pc_next|pc_rdata|pc_wdata|s1_pc|s2_pc|q_pc|imem_addr)")
ADDR = re.compile(r"(addr|r_addr|rw_addr|paddr|mem_addr_q|dmem_addr|rv_wb_addr)")
COUNTERS = re.compile(r"(rvfi_order|mcycle|minstret|mtime|order)")


def classify(sig, bit):
    if bit is None:
        return "other"
    if PC.search(sig) and (bit >= 16 or bit < 2):
        return "ADDR"
    if ADDR.search(sig) and bit >= 16:
        return "ADDR"
    if COUNTERS.search(sig) and bit >= 18:
        return "COUNTER"
    return "other"


def main(argv):
    path = argv[0]
    scope = argv[argv.index("--scope") + 1] if "--scope" in argv else ""
    classes, total = Counter(), 0
    other = defaultdict(list)
    for line in open(path, encoding="latin-1"):
        if not line.startswith("C '"):
            continue
        body, count = line.rsplit("' ", 1)
        f = dict(FIELD.findall(body))
        if not f.get("page", "").startswith("v_toggle") or scope not in f.get("h", ""):
            continue
        total += 1
        if int(count):
            continue
        m = SIG.match(f.get("o", ""))
        sig, bit = m.group(1), (int(m.group(2)) if m.group(2) else None)
        c = classify(sig, bit)
        classes[c] += 1
        if c == "other":
            other[(f["h"].split(".")[-1], sig)].append(bit)
    zeros = sum(classes.values())
    print(f"toggle points in scope '{scope or 'all'}': {total}, never toggled: {zeros}")
    for c in ("ADDR", "COUNTER", "other"):
        print(f"  {c:8s} {classes[c]:6d}")
    explained = classes["ADDR"] + classes["COUNTER"]
    print(f"toggle coverage {100 * (total - zeros) / total:.1f} % raw; "
          f"{100 * (total - zeros + explained) / total:.1f} % with ADDR/COUNTER bits explained")
    print("\nunexplained, by signal (instance, signal: bits never toggled):")
    for (inst, sig), bits in sorted(other.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        b = sorted(x for x in bits if x is not None)
        rng = f"{len(bits)} bits" + (f" [{b[0]}..{b[-1]}]" if b else "")
        print(f"  {inst:12s} {sig:28s} {rng}")


if __name__ == "__main__":
    main(sys.argv[1:])
