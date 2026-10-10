"""test_lockstep.py — guided lockstep, tested without RTL.

A fake "RTL" is built from the ISS with three differences a real core legitimately has:
  * the accelerator takes several STATUS polls to finish (the model is instant)
  * CPI 2: mcycle advances twice per instruction
  * a different marchid
Its trace is written in the core's RVFI dump format (word-aligned addresses, lanes).

Expected:
  1. guided lockstep MATCHES, with values injected   (legitimate differences accepted)
  2. offline comparison DIVERGES                      (why injection is needed)
  3. a fake RTL with a real bug still DIVERGES        (injection doesn't hide bugs)
"""
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.compare import compare, lockstep                  # noqa: E402
from cosim.devices import AccelModel, SocMMIO                 # noqa: E402
from cosim.iss import ISS, MARCHID, SPIKE_IMPL, Halt          # noqa: E402
from cosim.trace import read_rvfi, write_rvfi                 # noqa: E402


class SlowAccel(AccelModel):
    POLLS = 3

    def write(self, off, v):
        super().write(off, v)
        if off == 0 and v & 1:
            self.done, self.pending = False, self.POLLS

    def read(self, off):
        if off == 0x004 and getattr(self, "pending", 0):
            self.pending -= 1
            if self.pending == 0:
                self.done = True
            return 1                                   # busy
        return super().read(off)


class FakeRTL(ISS):
    def __init__(self, **kw):
        mmio = SocMMIO()
        mmio.accel = SlowAccel()
        mmio.devices[0x4000_1000] = mmio.accel
        super().__init__(mmio=mmio, impl={**SPIKE_IMPL, MARCHID: 0x1234}, **kw)

    def _tick(self, before, retired):
        super()._tick(before, retired)
        super()._tick((self.mcycle, self.minstret + 1), False)   # one extra cycle


class BuggyRTL(FakeRTL):                               # + SRA behaves like SRL
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F in (0x13, 0x33) and (insn >> 12) & 7 == 5 and insn >> 25 == 0x20:
            self.x[(insn >> 15) & 31] &= 0x7FFFFFFF
        return super().step()


def rtl_dump(cls, elf):
    rtl = cls()
    rtl.load_program(elf)
    recs = []
    try:
        for r in rtl.run(500_000):
            recs.append(r)
    except Halt:
        pass
    buf = io.StringIO()
    write_rvfi(recs, buf)
    return buf.getvalue()


def ref(elf):
    iss = ISS(mmio=SocMMIO())
    iss.load_program(elf)
    return iss


def main():
    ok = True
    for name in ("test_accel", "test_counters", "test_isa"):
        elf = str(ROOT / "build" / f"{name}.elf")
        dump = rtl_dump(FakeRTL, elf)
        res = lockstep(ref(elf), read_rvfi(io.StringIO(dump)))
        good = res.ok
        print(f"  lockstep  {name:14s} {'MATCH' if res.ok else 'DIVERGED':8s} "
              f"{res.matched:6d} retirements, {res.injected} injected")
        # offline: full ISS trace vs the fake RTL trace
        iss = ref(elf)
        recs = []
        try:
            for r in iss.run(500_000):
                recs.append(r)
        except Halt:
            pass
        off = compare(recs, read_rvfi(io.StringIO(dump)))
        needs = name in ("test_accel", "test_counters")
        print(f"  offline   {name:14s} {'MATCH' if off.ok else 'DIVERGED':8s} "
              f"{'(expected: RTL-only values)' if needs and not off.ok else ''}")
        if needs and off.ok:
            good = False
        ok &= good
    elf = str(ROOT / "build" / "test_isa.elf")
    res = lockstep(ref(elf), read_rvfi(io.StringIO(rtl_dump(BuggyRTL, elf))))
    print(f"  buggy RTL test_isa       {'DIVERGED' if not res.ok else 'MATCH (BAD)'} "
          f"at retirement #{res.matched}")
    ok &= not res.ok
    print("PASS: lockstep accepts legitimate RTL differences and still catches bugs"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
