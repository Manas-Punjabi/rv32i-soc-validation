"""compare.py — lockstep comparison of two retirement traces.

Reports the first divergence with a window of preceding instructions, which is what
you need to debug a pipeline: the bug is usually a few instructions before the symptom.
"""
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Iterable, List, Optional, Tuple

from .iss import COUNTER_CSRS, MMIO_LO, MMIO_HI, MVENDORID, MARCHID, MIMPID, MIP, Halt, sext
from .trace import Retire

FIELDS = ("pc", "insn", "rd", "rd_val", "mem_addr", "store_bytes", "store_data", "trap")


@dataclass
class Result:
    matched: int
    mismatch: Optional[Tuple[int, Retire, Retire, List[str]]] = None
    history: List[Tuple[Retire, Retire]] = field(default_factory=list)
    length_diff: Optional[str] = None
    injected: int = 0               # RTL values fed to the ISS (guided lockstep only)

    @property
    def ok(self) -> bool:
        return self.mismatch is None and self.length_diff is None

    def report(self, ref_name="ref", dut_name="dut") -> str:
        if self.ok:
            return f"MATCH: {self.matched} retirements identical"
        out = [f"DIVERGED after {self.matched} matching retirements"]
        if self.mismatch:
            idx, ref, dut, diffs = self.mismatch
            out.append(f"  first mismatch at retirement #{idx}: {', '.join(diffs)}")
            out.append(f"    {ref_name:>4}: {ref.describe()}")
            out.append(f"    {dut_name:>4}: {dut.describe()}")
            if self.history:
                out.append("  preceding (both agreed):")
                for r, _ in self.history:
                    out.append(f"          {r.describe()}")
        if self.length_diff:
            out.append(f"  {self.length_diff}")
        return "\n".join(out)


def _diff(a: Retire, b: Retire) -> List[str]:
    return [f for f in FIELDS if getattr(a, f) != getattr(b, f)]


def compare(ref: Iterable[Retire], dut: Iterable[Retire], context: int = 6) -> Result:
    hist: deque = deque(maxlen=context)
    ref_it, dut_it = iter(ref), iter(dut)
    n = 0
    while True:
        r = next(ref_it, None)
        d = next(dut_it, None)
        if r is None and d is None:
            return Result(matched=n)
        if r is None or d is None:
            who = "ref" if r is None else "dut"
            return Result(matched=n, history=list(hist),
                          length_diff=f"{who} trace ended first, at retirement #{n}")
        diffs = _diff(r, d)
        if diffs:
            return Result(matched=n, mismatch=(n, r, d, diffs), history=list(hist))
        hist.append((r, d))
        n += 1


# ---- guided lockstep ---------------------------------------------------------
# Offline comparison works until the first value the ISS cannot know: a counter CSR
# (the RTL's cycle count depends on its pipeline), an implementation-defined ID CSR,
# mip (device interrupt lines), or a load from a peripheral (the RTL's accelerator takes
# N+4 cycles; the model is instant, so STATUS polls read differently). Guided lockstep
# steps the ISS once per RTL retirement and, for exactly those instructions, injects the
# RTL's value before the ISS executes. When the RTL reports an interrupt (RVFI side
# band), the ISS checks it was legal and takes it itself. Everything else is computed
# independently and compared.

def _needs_injection(iss, insn: int) -> bool:
    op, f3 = insn & 0x7F, (insn >> 12) & 7
    if op == 0x73 and f3 in (1, 2, 3, 5, 6, 7):
        # counters, implementation-defined IDs, and mip (it mirrors device state)
        return (insn >> 20) in COUNTER_CSRS | {MVENDORID, MARCHID, MIMPID, MIP}
    if op == 0x03:
        addr = (iss.x[(insn >> 15) & 31] + sext(insn >> 20, 12)) & 0xFFFFFFFF
        return MMIO_LO <= addr < MMIO_HI
    return False


def lockstep(iss, rtl: Iterable[Retire], context: int = 6) -> Result:
    """Step the ISS alongside the RTL trace, injecting RTL-only values; compare each."""
    hist: deque = deque(maxlen=context)
    n = 0
    injected = 0
    for d in rtl:
        if d.intr:                           # RTL entered a handler on an interrupt
            err = iss.interrupt_error(d.intr_cause, d.intr_mip)
            if err:
                here = Retire(pc=iss.pc, insn=iss.fetch(iss.pc))
                return Result(matched=n, mismatch=(n, here, d, [f"intr: {err}"]),
                              history=list(hist), injected=injected)
            iss.take_interrupt(d.intr_cause)
        insn = iss.fetch(iss.pc)
        if _needs_injection(iss, insn) and d.rd:
            iss.inject = d.rd_val
            injected += 1
        pc_before = iss.pc
        halted = False
        try:
            r = iss.step()
        except Halt:
            halted = True
            rs2 = iss.x[(insn >> 20) & 31]
            r = Retire(pc=pc_before, insn=insn, mem_addr=iss.tohost,
                       store_data=rs2 & 0xFFFFFFFF, store_bytes=4)
        iss.inject = None                    # never let an unused injection leak
        diffs = _diff(r, d)
        # rvfi_pc_wdata (the RTL's claimed next pc) against where the ISS actually went:
        # a trace whose next-pc field lies would otherwise pass (rv32i-soc-asic mutant M18).
        # Not checked on the tohost store, where the ISS stops without advancing.
        if d.pc_next is not None and not halted:
            r = replace(r, pc_next=iss.pc)
            if d.pc_next != iss.pc:
                diffs.append("pc_next")
        if diffs:
            return Result(matched=n, mismatch=(n, r, d, diffs), history=list(hist),
                          injected=injected)
        hist.append((r, d))
        n += 1
    return Result(matched=n, injected=injected)
