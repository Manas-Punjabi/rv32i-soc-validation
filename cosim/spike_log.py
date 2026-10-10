"""spike_log.py — parse Spike's --log-commits output into Retire records.

Spike prints one line per retired instruction, e.g.
  core   0: 3 0x80000000 (0x00000297) x5  0x80000000
  core   0: 3 0x80000010 (0x00b52023) mem 0x80001000 0x00000123
  core   0: 3 0x8000000c (0x00052503) x10 0x00000123 mem 0x80001000
Register writes to x0 are not printed. Spike runs a few instructions of boot ROM at
0x1000 before jumping to the program; those are dropped (pc < RAM_BASE).

After the program writes tohost, Spike keeps executing (usually crt0's spin loop) until
its host process polls tohost, so the log runs on for a few thousand instructions. Pass
the tohost address and the trace stops at that store, which is where the ISS and the RTL
stop too.
"""
import re
from typing import Iterator, Optional, TextIO

from .iss import RAM_BASE
from .trace import Retire

_LINE = re.compile(r"core\s+\d+:\s+\d+\s+0x([0-9a-f]+)\s+\(0x([0-9a-f]+)\)(.*)")
_REG = re.compile(r"\bx(\d+)\s+0x([0-9a-f]+)")
_MEM = re.compile(r"\bmem\s+0x([0-9a-f]+)(?:\s+0x([0-9a-f]+))?")


def read_spike(f: TextIO, tohost: Optional[int] = None) -> Iterator[Retire]:
    for line in f:
        m = _LINE.match(line.strip())
        if not m:
            continue
        pc, insn, rest = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        if pc < RAM_BASE:
            continue
        rd, rd_val = 0, 0
        r = _REG.search(rest)
        if r and int(r.group(1)) != 0:
            rd, rd_val = int(r.group(1)), int(r.group(2), 16) & 0xFFFFFFFF
        mem_addr = store_data = None
        store_bytes = 0
        mm = _MEM.search(rest)
        if mm:
            mem_addr = int(mm.group(1), 16) & 0xFFFFFFFF
            if mm.group(2) is not None:            # value present: it's a store
                digits = len(mm.group(2))
                store_bytes = max(1, digits // 2)
                store_data = int(mm.group(2), 16)
        yield Retire(pc=pc, insn=insn, rd=rd, rd_val=rd_val,
                     mem_addr=mem_addr, store_data=store_data,
                     store_bytes=store_bytes)
        if (tohost is not None and store_bytes and mem_addr == tohost
                and store_data is not None and store_data & 1):
            return
