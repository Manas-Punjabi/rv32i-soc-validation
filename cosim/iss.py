"""iss.py — a small RV32I instruction-set simulator, written as the co-sim reference.

Why not just use Spike? Spike has no model of this SoC's peripherals, so the first
MMIO read diverges it from the RTL permanently. This ISS routes 0x4000_0000-0x4FFF_FFFF
to a pluggable handler: a Python device model when running firmware stand-alone, or
the RTL's observed value when running in lockstep. Spike is used to prove this ISS
correct on everything that doesn't touch MMIO (see scripts/).

Scope: RV32I + Zicsr + Zicntr + Zifencei, machine mode only, matching the rv32i-soc-asic
core. Exceptions (illegal instruction, ecall, ebreak, misaligned load/store/fetch) take
a trap to mtvec with mepc/mcause/mtval set, exactly as the core does; mret returns.
Interrupts are entered on the RTL's word (take_interrupt), after interrupt_error has
checked that the RTL was allowed to take them; the ISS has no device timing of its own.

Spike equivalent: spike --isa=rv32i_zicsr_zicntr_zifencei --priv=M --pmpregions=0
--triggers=0. A CSR that csr_read/csr_write do not handle is illegal, as on the core.

Implementation-defined values (marchid, mimpid, mvendorid) differ between Spike and the
RTL; pass them in `impl` so the ISS can impersonate either. Counter reads (cycle,
instret) can never agree with the RTL's pipeline, so lockstep injects the RTL's value
(see compare.lockstep).
"""
import struct
from typing import Dict, Iterator, Optional

from .trace import Retire

RAM_BASE = 0x8000_0000
MMIO_LO, MMIO_HI = 0x4000_0000, 0x5000_0000
MASK32 = 0xFFFF_FFFF


def sext(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


# CSR addresses
MSTATUS, MISA, MIE, MTVEC, MSTATUSH = 0x300, 0x301, 0x304, 0x305, 0x310
MCOUNTINHIBIT = 0x320
MSCRATCH, MEPC, MCAUSE, MTVAL, MIP = 0x340, 0x341, 0x342, 0x343, 0x344
MCYCLE, MINSTRET, MCYCLEH, MINSTRETH = 0xB00, 0xB02, 0xB80, 0xB82
CYCLE, INSTRET, CYCLEH, INSTRETH = 0xC00, 0xC02, 0xC80, 0xC82
MVENDORID, MARCHID, MIMPID, MHARTID, MCONFIGPTR = 0xF11, 0xF12, 0xF13, 0xF14, 0xF15
COUNTER_CSRS = {MCYCLE, MINSTRET, MCYCLEH, MINSTRETH, CYCLE, INSTRET, CYCLEH, INSTRETH}

# exception causes
CAUSE_FETCH_MISALIGNED, CAUSE_ILLEGAL, CAUSE_BREAKPOINT = 0, 2, 3
CAUSE_LOAD_MISALIGNED, CAUSE_STORE_MISALIGNED, CAUSE_ECALL_M = 4, 6, 11

SPIKE_IMPL = {MVENDORID: 0, MARCHID: 5, MIMPID: 0}


class Trap(Exception):
    def __init__(self, cause: int, tval: int = 0):
        super().__init__(f"trap cause={cause} tval={tval:#x}")
        self.cause, self.tval = cause, tval


class Halt(Exception):
    """Raised when firmware writes tohost. code 0 = pass."""
    def __init__(self, code: int):
        super().__init__(f"tohost exit code {code}")
        self.code = code


class MMIO:
    """Default MMIO handler: reads return 0, writes are dropped. Subclass for models."""
    def read(self, addr: int, nbytes: int) -> int:
        return 0

    def write(self, addr: int, nbytes: int, value: int) -> None:
        pass


# ---- minimal ELF32 loader ---------------------------------------------------

def load_elf(path: str):
    """Return (entry, segments[(vaddr, bytes)], symbols{name: addr})."""
    data = open(path, "rb").read()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise ValueError("not a 32-bit ELF")
    (e_entry, e_phoff, e_shoff) = struct.unpack_from("<III", data, 24)
    (e_phentsize, e_phnum, e_shentsize, e_shnum) = struct.unpack_from("<HHHH", data, 42)
    segs = []
    for i in range(e_phnum):
        p_type, p_off, p_vaddr, _, p_filesz, p_memsz = struct.unpack_from(
            "<IIIIII", data, e_phoff + i * e_phentsize)
        if p_type == 1:  # PT_LOAD
            blob = data[p_off:p_off + p_filesz] + b"\0" * (p_memsz - p_filesz)
            segs.append((p_vaddr, blob))
    syms: Dict[str, int] = {}
    shdrs = [struct.unpack_from("<IIIIIIIIII", data, e_shoff + i * e_shentsize)
             for i in range(e_shnum)]
    for sh in shdrs:
        if sh[1] == 2:  # SHT_SYMTAB
            strtab = shdrs[sh[6]]
            for j in range(sh[5] // 16):
                st_name, st_value = struct.unpack_from("<II", data, sh[4] + j * 16)
                end = data.index(b"\0", strtab[4] + st_name)
                name = data[strtab[4] + st_name:end].decode()
                if name:
                    syms[name] = st_value
    return e_entry, segs, syms


# ---- the simulator ----------------------------------------------------------

class ISS:
    def __init__(self, ram_size: int = 64 * 1024, mmio: Optional[MMIO] = None,
                 impl: Optional[Dict[int, int]] = None):
        self.x = [0] * 32
        self.pc = RAM_BASE
        self.ram = bytearray(ram_size)
        self.mmio = mmio or MMIO()
        if hasattr(self.mmio, "bind_clock"):        # device models' time base
            self.mmio.bind_clock(lambda: self.retired)
        self.tohost: Optional[int] = None
        self.retired = 0
        self.impl = dict(SPIKE_IMPL if impl is None else impl)
        # machine-mode CSR state
        self.mstatus = 0x1800            # MPP hardwired to M (no U/S modes)
        self.mtvec = self.mscratch = self.mepc = self.mcause = self.mtval = 0
        self.mie = 0
        self.mcountinhibit = 0
        self._inhibit = 0                # mcountinhibit as it was before this instruction
        self.mcycle = 0
        self.minstret = 0
        # set by lockstep to force the value of the next counter / ID-CSR / mip read or
        # MMIO load (values only the RTL can know)
        self.inject: Optional[int] = None
        self.interrupts_taken = 0

    # memory ------------------------------------------------------------------
    def load_program(self, path: str) -> None:
        entry, segs, syms = load_elf(path)
        for vaddr, blob in segs:
            off = vaddr - RAM_BASE
            if off < 0 or off + len(blob) > len(self.ram):
                raise ValueError(f"segment {vaddr:08x}+{len(blob)} outside RAM")
            self.ram[off:off + len(blob)] = blob
        self.pc = entry
        self.tohost = syms.get("tohost")

    # Data accesses follow soc_top's decode (rtl/soc_top.sv header):
    #   0x8xxx_xxxx  RAM, aliased: address bits above the RAM size are ignored
    #   0x4xxx_xxxx  peripherals (MMIO handler, or the RTL's value in lockstep)
    #   elsewhere    reads return 0, writes are dropped, no trap
    # Instruction fetch is stricter (fetch()): leaving RAM means a broken program, and
    # the ISS stops rather than modelling what the RTL's fetch port would alias to.
    def _ram_off(self, addr: int) -> int:
        return (addr - RAM_BASE) % len(self.ram)

    def fetch(self, pc: int) -> int:
        off = pc - RAM_BASE
        if off < 0 or off + 4 > len(self.ram):
            raise MemoryError(f"fetch {pc:08x} outside RAM")
        return int.from_bytes(self.ram[off:off + 4], "little")

    def read(self, addr: int, n: int) -> int:
        if MMIO_LO <= addr < MMIO_HI:
            if self.inject is not None:
                v, self.inject = self.inject, None
                return v & ((1 << (8 * n)) - 1)
            return self.mmio.read(addr, n) & ((1 << (8 * n)) - 1)
        if addr >> 28 != RAM_BASE >> 28:
            return 0                                    # unmapped
        off = self._ram_off(addr)
        return int.from_bytes(self.ram[off:off + n], "little")

    def write(self, addr: int, n: int, v: int) -> None:
        v &= (1 << (8 * n)) - 1
        if self.tohost is not None and addr == self.tohost:
            if v & 1:
                raise Halt(v >> 1)
        if MMIO_LO <= addr < MMIO_HI:
            self.mmio.write(addr, n, v)
            return
        if addr >> 28 != RAM_BASE >> 28:
            return                                      # unmapped: dropped
        off = self._ram_off(addr)
        self.ram[off:off + n] = v.to_bytes(n, "little")

    # CSRs --------------------------------------------------------------------
    def csr_read(self, a: int) -> int:
        # counters, implementation-defined ID registers and mip (it mirrors device
        # interrupt lines the ISS does not time): lockstep injects the RTL's value
        if (a in COUNTER_CSRS or a in (MVENDORID, MARCHID, MIMPID, MIP)) and self.inject is not None:
            v, self.inject = self.inject, None
            return v & MASK32
        if a == MSTATUS:   return self.mstatus
        if a == MISA:      return 0x4000_0100          # MXL=32, I
        if a == MIE:       return self.mie
        if a == MIP:       return 0                     # stand-alone: no interrupt lines
        if a == MTVEC:     return self.mtvec
        if a == MSTATUSH:  return 0
        if a == MCOUNTINHIBIT: return self.mcountinhibit
        if a == MSCRATCH:  return self.mscratch
        if a == MEPC:      return self.mepc
        if a == MCAUSE:    return self.mcause
        if a == MTVAL:     return self.mtval
        if a in (MCYCLE, CYCLE):       return self.mcycle & MASK32
        if a in (MCYCLEH, CYCLEH):     return self.mcycle >> 32 & MASK32
        if a in (MINSTRET, INSTRET):   return self.minstret & MASK32
        if a in (MINSTRETH, INSTRETH): return self.minstret >> 32 & MASK32
        if a in self.impl:  return self.impl[a]
        if a in (MHARTID, MCONFIGPTR): return 0
        raise Trap(CAUSE_ILLEGAL)

    def csr_write(self, a: int, v: int, insn: int) -> None:
        v &= MASK32
        if a >> 10 == 3:                                # read-only range
            raise Trap(CAUSE_ILLEGAL, insn)
        if a == MSTATUS:   self.mstatus = 0x1800 | (v & 0x88)     # MIE, MPIE
        elif a == MISA:    pass                                    # WARL, fixed
        elif a == MIE:     self.mie = v & 0x888                    # MSIE MTIE MEIE
        elif a == MIP:     pass
        elif a == MTVEC:   self.mtvec = v & ~2 & MASK32            # modes 0/1 only
        elif a == MSTATUSH: pass
        elif a == MCOUNTINHIBIT: self.mcountinhibit = v & 0x5      # CY, IR
        elif a == MSCRATCH: self.mscratch = v
        elif a == MEPC:    self.mepc = v & ~3 & MASK32             # no C extension
        elif a == MCAUSE:  self.mcause = v
        elif a == MTVAL:   self.mtval = v
        elif a == MCYCLE:    self.mcycle = (self.mcycle & ~MASK32) | v
        elif a == MCYCLEH:   self.mcycle = (self.mcycle & MASK32) | v << 32
        elif a == MINSTRET:  self.minstret = (self.minstret & ~MASK32) | v
        elif a == MINSTRETH: self.minstret = (self.minstret & MASK32) | v << 32
        else:
            raise Trap(CAUSE_ILLEGAL, insn)

    def _take_trap(self, pc: int, t: Trap) -> None:
        self.mepc = pc
        self.mcause = t.cause
        self.mtval = t.tval & MASK32
        mie = (self.mstatus >> 3) & 1
        self.mstatus = 0x1800 | (mie << 7)             # MPIE <= MIE, MIE <= 0
        self.pc = self.mtvec & ~3 & MASK32             # exceptions are never vectored

    # interrupts ----------------------------------------------------------------
    # The ISS does not time devices, so it cannot know *when* a line rises. In lockstep
    # the RTL's RVFI side band says an interrupt was taken before a record; the ISS checks
    # it was legal (interrupt_error) and then performs the entry itself, so mepc, mcause
    # and mstatus are computed here, not copied.
    INTR_PRIORITY = (11, 3, 7)        # MEI > MSI > MTI (privileged spec, mip/mie)

    def interrupt_error(self, cause: int, mip: int) -> Optional[str]:
        """None if taking interrupt `cause` with lines `mip` is legal right now."""
        code = cause & 0x7FFF_FFFF
        if not cause >> 31 or code not in self.INTR_PRIORITY:
            return f"mcause {cause:#010x} is not a machine interrupt this core has"
        if not self.mstatus & 0x8:
            return "taken with mstatus.MIE = 0"
        if not self.mie >> code & 1:
            return f"source {code} not enabled in mie ({self.mie:#x})"
        if not mip >> code & 1:
            return f"source {code} not pending in mip ({mip:#x})"
        best = next(c for c in self.INTR_PRIORITY if (mip & self.mie) >> c & 1)
        if best != code:
            return f"priority: took {code}, but {best} was pending and enabled"
        return None

    def take_interrupt(self, cause: int) -> None:
        # the pre-empted instruction (self.pc) is re-executed after mret
        self.mepc = self.pc
        self.mcause = cause & MASK32
        self.mtval = 0
        mie = (self.mstatus >> 3) & 1
        self.mstatus = 0x1800 | (mie << 7)             # MPIE <= MIE, MIE <= 0
        self.pc = self.mtvec & ~3 & MASK32             # the core's mtvec is direct-only
        self.interrupts_taken += 1

    # execution ---------------------------------------------------------------
    def step(self) -> Retire:
        pc = self.pc
        insn = self.fetch(pc)
        counters_before = (self.mcycle, self.minstret)
        # mcountinhibit applies from the next instruction: the one that writes it still
        # counts under the old value (matches Spike; tb/asm/csr_ops.S check 7)
        self._inhibit = self.mcountinhibit
        try:
            r = self._execute(pc, insn)
        except Trap as t:
            self._take_trap(pc, t)
            self.retired += 1
            self._tick(counters_before, retired=False)
            return Retire(pc=pc, insn=insn, trap=True)
        self.retired += 1
        self._tick(counters_before, retired=True)
        return r

    def _tick(self, before, retired: bool) -> None:
        # An instruction that writes a counter CSR suppresses that counter's own
        # increment for itself (the written value is what software sees next).
        inhibit = self._inhibit
        if (self.mcycle == before[0]) and not inhibit & 1:
            self.mcycle = (self.mcycle + 1) & (2**64 - 1)
        if retired and (self.minstret == before[1]) and not inhibit & 4:
            self.minstret = (self.minstret + 1) & (2**64 - 1)

    def _execute(self, pc: int, insn: int) -> Retire:
        op = insn & 0x7F
        rd = (insn >> 7) & 0x1F
        f3 = (insn >> 12) & 0x7
        rs1_i = (insn >> 15) & 0x1F
        rs1 = self.x[rs1_i]
        rs2 = self.x[(insn >> 20) & 0x1F]
        f7 = insn >> 25
        imm_i = sext(insn >> 20, 12)
        imm_s = sext(((insn >> 25) << 5) | ((insn >> 7) & 0x1F), 12)
        imm_b = sext(((insn >> 31) << 12) | (((insn >> 7) & 1) << 11)
                     | (((insn >> 25) & 0x3F) << 5) | (((insn >> 8) & 0xF) << 1), 13)
        imm_u = insn & 0xFFFFF000
        imm_j = sext(((insn >> 31) << 20) | (((insn >> 12) & 0xFF) << 12)
                     | (((insn >> 20) & 1) << 11) | (((insn >> 21) & 0x3FF) << 1), 21)

        next_pc = (pc + 4) & MASK32
        wr: Optional[int] = None
        mem_addr = store_data = None
        store_bytes = 0

        def jump(target):
            if target & 3:
                raise Trap(CAUSE_FETCH_MISALIGNED, target)
            return target

        if op == 0x37:                                   # LUI
            wr = imm_u
        elif op == 0x17:                                 # AUIPC
            wr = (pc + imm_u) & MASK32
        elif op == 0x6F:                                 # JAL
            next_pc = jump((pc + imm_j) & MASK32)
            wr = (pc + 4) & MASK32
        elif op == 0x67 and f3 == 0:                     # JALR
            next_pc = jump((rs1 + imm_i) & MASK32 & ~1)
            wr = (pc + 4) & MASK32
        elif op == 0x63:                                 # branches
            s1, s2 = sext(rs1, 32), sext(rs2, 32)
            cond = {0: rs1 == rs2, 1: rs1 != rs2, 4: s1 < s2,
                    5: s1 >= s2, 6: rs1 < rs2, 7: rs1 >= rs2}.get(f3)
            if cond is None:
                raise Trap(CAUSE_ILLEGAL, insn)
            if cond:
                next_pc = jump((pc + imm_b) & MASK32)
        elif op == 0x03:                                 # loads
            addr = (rs1 + imm_i) & MASK32
            size = {0: 1, 1: 2, 2: 4, 4: 1, 5: 2}.get(f3)
            if size is None:
                raise Trap(CAUSE_ILLEGAL, insn)
            if addr % size:
                raise Trap(CAUSE_LOAD_MISALIGNED, addr)
            v = self.read(addr, size)
            wr = sext(v, 8 * size) & MASK32 if f3 < 4 else v
            mem_addr = addr
        elif op == 0x23:                                 # stores
            addr = (rs1 + imm_s) & MASK32
            size = {0: 1, 1: 2, 2: 4}.get(f3)
            if size is None:
                raise Trap(CAUSE_ILLEGAL, insn)
            if addr % size:
                raise Trap(CAUSE_STORE_MISALIGNED, addr)
            store_data = rs2 & ((1 << (8 * size)) - 1)
            store_bytes, mem_addr = size, addr
            self.write(addr, size, rs2)                  # may raise Halt (tohost)
        elif op == 0x13:                                 # OP-IMM
            sh = (insn >> 20) & 0x1F
            if f3 == 0:   wr = rs1 + imm_i
            elif f3 == 2: wr = int(sext(rs1, 32) < imm_i)
            elif f3 == 3: wr = int(rs1 < (imm_i & MASK32))
            elif f3 == 4: wr = rs1 ^ (imm_i & MASK32)
            elif f3 == 6: wr = rs1 | (imm_i & MASK32)
            elif f3 == 7: wr = rs1 & (imm_i & MASK32)
            elif f3 == 1 and f7 == 0x00: wr = rs1 << sh
            elif f3 == 5 and f7 == 0x00: wr = rs1 >> sh
            elif f3 == 5 and f7 == 0x20: wr = sext(rs1, 32) >> sh
            else: raise Trap(CAUSE_ILLEGAL, insn)
        elif op == 0x33:                                 # OP
            sh = rs2 & 0x1F
            ops = {
                (0x00, 0): lambda: rs1 + rs2,
                (0x20, 0): lambda: rs1 - rs2,
                (0x00, 1): lambda: rs1 << sh,
                (0x00, 2): lambda: int(sext(rs1, 32) < sext(rs2, 32)),
                (0x00, 3): lambda: int(rs1 < rs2),
                (0x00, 4): lambda: rs1 ^ rs2,
                (0x00, 5): lambda: rs1 >> sh,
                (0x20, 5): lambda: sext(rs1, 32) >> sh,
                (0x00, 6): lambda: rs1 | rs2,
                (0x00, 7): lambda: rs1 & rs2,
            }
            if (f7, f3) not in ops:
                raise Trap(CAUSE_ILLEGAL, insn)
            wr = ops[(f7, f3)]()
        elif op == 0x0F and f3 in (0, 1):                # FENCE, FENCE.I: no-ops here
            pass
        elif op == 0x73:                                 # SYSTEM
            if f3 == 0:
                if insn == 0x00000073:
                    raise Trap(CAUSE_ECALL_M, 0)
                if insn == 0x00100073:
                    raise Trap(CAUSE_BREAKPOINT, pc)
                if insn == 0x30200073:                   # mret
                    mpie = (self.mstatus >> 7) & 1
                    self.mstatus = 0x1800 | (mpie << 3) | 0x80   # MIE <= MPIE, MPIE <= 1
                    next_pc = self.mepc
                elif insn == 0x10500073:                 # wfi: no-op
                    pass
                else:
                    raise Trap(CAUSE_ILLEGAL, insn)
            elif f3 in (1, 2, 3, 5, 6, 7):
                csr = insn >> 20
                src = rs1_i if f3 >= 5 else rs1          # zimm or register
                writes = f3 in (1, 5) or rs1_i != 0      # csrrs/c with x0/0: no write
                reads = not (f3 in (1, 5) and rd == 0)   # csrrw with rd=x0: no read
                old = self.csr_read(csr) if reads or writes else 0
                if writes:
                    if f3 in (1, 5):   new = src
                    elif f3 in (2, 6): new = old | src
                    else:              new = old & ~src
                    self.csr_write(csr, new, insn)
                wr = old if reads else None
            else:
                raise Trap(CAUSE_ILLEGAL, insn)
        else:
            raise Trap(CAUSE_ILLEGAL, insn)

        rd_val = 0
        if wr is not None and rd != 0:
            rd_val = wr & MASK32
            self.x[rd] = rd_val
        else:
            rd = 0
        self.pc = next_pc
        return Retire(pc=pc, insn=insn, rd=rd, rd_val=rd_val,
                      mem_addr=mem_addr, store_data=store_data,
                      store_bytes=store_bytes)

    def run(self, max_insns: int = 1_000_000) -> Iterator[Retire]:
        """Yield retirements until tohost or the instruction limit.

        Traps are ordinary control flow (they redirect to mtvec), not a stop.

        The store to tohost itself is yielded before Halt propagates, matching RVFI,
        where that store retires normally.
        """
        for _ in range(max_insns):
            pc_before = self.pc
            try:
                r = self.step()
            except Halt:
                insn = self.fetch(pc_before)
                rs2 = self.x[(insn >> 20) & 0x1F]
                yield Retire(pc=pc_before, insn=insn, mem_addr=self.tohost,
                             store_data=rs2 & MASK32, store_bytes=4)
                raise
            yield r
        raise TimeoutError(f"no tohost after {max_insns} instructions")
