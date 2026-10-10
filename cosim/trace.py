"""trace.py — the retirement record every trace source is normalized to.

Three sources produce these records:
  * the Python ISS (cosim/iss.py)
  * Spike's --log-commits output (cosim/spike_log.py)
  * the RTL's RVFI port, written by the Verilator harness (read_rvfi below)

A record is what one retired instruction did architecturally. Comparing two traces
record by record is lockstep co-simulation.
"""
from dataclasses import dataclass
from typing import Iterator, Optional, TextIO


@dataclass(frozen=True)
class Retire:
    pc: int
    insn: int
    rd: int = 0                     # 0 = no register write (x0 writes are dropped)
    rd_val: int = 0
    mem_addr: Optional[int] = None  # load or store address
    store_data: Optional[int] = None  # store data, masked to the access width
    store_bytes: int = 0            # 1, 2 or 4 for stores; 0 otherwise
    trap: bool = False
    # next pc (rvfi_pc_wdata), when the source reports it. Not in compare.FIELDS: the
    # ISS and Spike don't produce it per record; lockstep checks it against the ISS pc.
    pc_next: Optional[int] = None
    # interrupt side band (RTL only, when the SoC has it): this is the first instruction
    # of a handler entered by an interrupt with mcause intr_cause, mip lines intr_mip
    intr: bool = False
    intr_cause: Optional[int] = None
    intr_mip: Optional[int] = None

    def describe(self) -> str:
        s = f"pc={self.pc:08x} insn={self.insn:08x}"
        if self.intr:
            s = f"[IRQ mcause={self.intr_cause:08x} mip={self.intr_mip:08x}] " + s
        if self.rd:
            s += f" x{self.rd}={self.rd_val:08x}"
        if self.store_bytes:
            s += f" st{self.store_bytes}[{self.mem_addr:08x}]={self.store_data:0{self.store_bytes*2}x}"
        elif self.mem_addr is not None:
            s += f" ld[{self.mem_addr:08x}]"
        if self.trap:
            s += " TRAP"
        if self.pc_next is not None:
            s += f" -> {self.pc_next:08x}"
        return s


def write_rvfi(recs, f: TextIO) -> None:
    """Inverse of read_rvfi, in the core's convention (word-aligned address, lanes).
    Used by tests to produce RTL-shaped dumps without RTL."""
    f.write("# order pc insn trap rd_addr rd_wdata mem_addr mem_rmask mem_wmask mem_wdata\n")
    for i, r in enumerate(recs):
        rmask = wmask = wdata = 0
        addr = 0
        if r.mem_addr is not None:
            lane = r.mem_addr & 3
            addr = r.mem_addr & ~3
            if r.store_bytes:
                wmask = ((1 << r.store_bytes) - 1) << lane
                wdata = (r.store_data << (8 * lane)) & 0xFFFFFFFF
            else:
                n = {0: 4, 2: 2}.get(lane, 1)
                rmask = 0xF if n == 4 else ((1 << n) - 1) << lane
        f.write(f"{i} {r.pc:08x} {r.insn:08x} {int(r.trap)} {r.rd:02x} {r.rd_val:08x} "
                f"{addr:08x} {rmask:x} {wmask:x} {wdata:08x}\n")


# ---- RVFI text format ------------------------------------------------------
# One line per retirement, written by tb/ from the RTL's rvfi_* signals:
#   order pc insn trap rd_addr rd_wdata mem_addr mem_rmask mem_wmask mem_wdata
#   [cycle pc_wdata [intr mcause mip]]
# The optional cycle column is ignored here; pc_wdata becomes Retire.pc_next; the
# interrupt side band (rvfi_intr, rvfi_ext_mcause, rvfi_ext_mip) becomes Retire.intr*.
# all hex except order (decimal). Lines starting with '#' are comments.

def _store_bytes(wmask: int) -> int:
    return {0x1: 1, 0x2: 1, 0x4: 1, 0x8: 1, 0x3: 2, 0xC: 2, 0xF: 4}.get(wmask, 0)


def read_rvfi(f: TextIO) -> Iterator[Retire]:
    """Parse the harness's RVFI dump into Retire records.

    The rv32i-soc-asic core reports rvfi_mem_addr word-aligned, with the accessed bytes
    given by the lanes set in rmask/wmask, and rvfi_mem_wdata lane-aligned (a byte
    store to addr+1 sits in bits 15:8). Both are normalized to what the ISS and Spike
    report: the exact byte address, and the value the program stored.
    """
    expect = 0
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split()
        (order, pc, insn, trap, rd, rd_wdata,
         mem_addr, rmask, wmask, wdata) = cols[:10]
        pc_next = int(cols[11], 16) if len(cols) > 11 else None
        intr = len(cols) > 14 and cols[12] == "1"
        order = int(order)
        if order != expect:
            raise ValueError(f"rvfi_order gap: expected {expect}, got {order}")
        expect += 1
        rd = int(rd, 16)
        if rd == 0 and int(rd_wdata, 16) != 0:
            # RVFI: "rvfi_rd_wdata must be zero when rd is zero". Ignoring it would hide
            # a core that leaks a value onto the bus for instructions without a result.
            raise ValueError(f"rvfi #{order}: rd_addr 0 with rd_wdata {rd_wdata}")
        wmask, rmask = int(wmask, 16), int(rmask, 16)
        nbytes = _store_bytes(wmask)
        mask = wmask or rmask
        lane = (mask & -mask).bit_length() - 1 if mask else 0
        addr = (int(mem_addr, 16) & ~3) + lane
        data = None
        if nbytes:
            data = (int(wdata, 16) >> (8 * lane)) & ((1 << (8 * nbytes)) - 1)
        yield Retire(
            pc=int(pc, 16), insn=int(insn, 16),
            rd=rd, rd_val=int(rd_wdata, 16) if rd else 0,
            mem_addr=addr if (rmask or wmask) else None,
            store_data=data, store_bytes=nbytes,
            trap=trap != "0", pc_next=pc_next, intr=intr,
            intr_cause=int(cols[13], 16) if intr else None,
            intr_mip=int(cols[14], 16) if intr else None,
        )
