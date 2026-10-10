"""mutate_iss.py — does lockstep co-sim actually catch bugs?

Injects one bug at a time into a copy of the ISS, uses it as a stand-in DUT, and runs
it in lockstep against the unmodified ISS on test_isa, the Spike-compatible directed
tests (build/d_*.elf) and random programs.
Each mutant is modeled on a classic pipeline bug. A mutant is "killed" if any program
diverges. This is the co-sim equivalent of matmul-accel-asic's testbench mutation test.
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cosim.compare import compare          # noqa: E402
from cosim.iss import ISS, Halt, sext      # noqa: E402
from cosim.devices import SocMMIO          # noqa: E402
from cosim.randgen import gen              # noqa: E402

SW = ROOT / "sw"
CC = ["riscv64-unknown-elf-gcc", "-march=rv32i_zicsr", "-mabi=ilp32", "-nostdlib",
      "-nostartfiles", "-Wl,--no-warn-rwx-segments", "-T", str(SW / "link.ld")]


def build_random(seed: int, out: pathlib.Path) -> pathlib.Path:
    src = out / f"rand{seed}.S"
    src.write_text(gen(seed, 400))
    elf = out / f"rand{seed}.elf"
    subprocess.run(CC + ["-o", str(elf), str(SW / "crt0.S"), str(src)], check=True)
    return elf


def trace(iss_cls, elf):
    iss = iss_cls(mmio=SocMMIO())
    iss.load_program(str(elf))
    recs = []
    try:
        for r in iss.run(200_000):
            recs.append(r)
    except (Halt, MemoryError, TimeoutError):
        pass
    return recs


# ---- mutants: each overrides one behavior, the way a real RTL bug would ----------
def mutant(name, patch):
    cls = type(name, (ISS,), {})
    patch(cls)
    return name, cls


def m_sra_logical(cls):          # arithmetic right shift implemented as logical
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F in (0x13, 0x33) and (insn >> 12) & 7 == 5 and insn >> 25 == 0x20:
            self.x[(insn >> 15) & 31] &= 0x7FFFFFFF if self.x[(insn >> 15) & 31] >> 31 else 0xFFFFFFFF
        return orig(self)
    cls.step = step

def m_lb_zero_extend(cls):       # LB loses its sign extension
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        r = orig(self)
        if insn & 0x7F == 0x03 and (insn >> 12) & 7 == 0 and r.rd:
            v = r.rd_val & 0xFF
            self.x[r.rd] = v
            return type(r)(**{**r.__dict__, "rd_val": v})
        return r
    cls.step = step

def m_stale_forward(cls):        # missing forwarding: an ALU op reads the value from
    orig = ISS.step              # before the immediately preceding write
    def step(self):
        insn = self.fetch(self.pc)
        prev = getattr(self, "_prev", None)
        if prev and insn & 0x7F == 0x33 and ((insn >> 15) & 31) == prev[0] and prev[0]:
            saved = self.x[prev[0]]
            self.x[prev[0]] = prev[1]           # old value
            r = orig(self)
            if r.rd != prev[0]:
                self.x[prev[0]] = saved
        else:
            r = orig(self)
        self._prev = (r.rd, self._old) if r.rd else None
        return r
    cls.step = step
    orig_step = cls.step
    def step2(self):
        insn = self.fetch(self.pc)
        rd = (insn >> 7) & 31
        self._old = self.x[rd]
        return orig_step(self)
    cls.step = step2

def m_bltu_signed(cls):          # unsigned branch compares as signed
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F == 0x63 and (insn >> 12) & 7 == 6:
            insn2 = (insn & ~(7 << 12)) | (4 << 12)         # bltu -> blt
            off = self.pc - 0x80000000
            self.ram[off:off + 4] = insn2.to_bytes(4, "little")
            r = orig(self)
            self.ram[off:off + 4] = insn.to_bytes(4, "little")
            return type(r)(**{**r.__dict__, "insn": insn})
        return orig(self)
    cls.step = step

def m_sh_wrong_lane(cls):        # halfword store writes the low byte only
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F == 0x23 and (insn >> 12) & 7 == 1:
            rs2 = (insn >> 20) & 31
            saved = self.x[rs2]
            self.x[rs2] = saved & 0xFF
            r = orig(self)
            self.x[rs2] = saved
            return r
        return orig(self)
    cls.step = step

def m_jalr_no_clear(cls):        # JALR target lsb not cleared: only visible on odd
    orig = ISS.step              # targets, which compilers never emit (test_isa check 18)
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F == 0x67:
            r = orig(self)
            rs1 = self.x[(insn >> 15) & 31]
            tgt = (rs1 + sext(insn >> 20, 12)) & 0xFFFFFFFF
            if tgt & 1:
                self.pc = tgt
            return r
        return orig(self)
    cls.step = step

def m_x0_writable(cls):          # writes to x0 are not suppressed
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        r = orig(self)
        if (insn >> 7) & 31 == 0 and insn & 0x7F in (0x13, 0x33, 0x37) and not r.trap:
            self.x[0] = 1
        return r
    cls.step = step

def m_slt_unsigned(cls):         # SLT compares as unsigned
    orig = ISS.step
    def step(self):
        insn = self.fetch(self.pc)
        if insn & 0x7F == 0x33 and (insn >> 12) & 7 == 2 and insn >> 25 == 0:
            insn2 = (insn & ~(7 << 12)) | (3 << 12)
            off = self.pc - 0x80000000
            self.ram[off:off + 4] = insn2.to_bytes(4, "little")
            r = orig(self)
            self.ram[off:off + 4] = insn.to_bytes(4, "little")
            return type(r)(**{**r.__dict__, "insn": insn})
        return orig(self)
    cls.step = step


def m_stale_prefetch(cls):       # fence.i is a no-op behind a 3-deep prefetch queue
    # Models a fence.i read-ahead bug: words fetched ahead are kept
    # in a queue and used even if a later store changed memory; only a non-sequential
    # pc (a redirect) refills from memory. fence.i does not flush it.
    orig_fetch = ISS.fetch
    def fetch(self, pc):
        q = self.__dict__.setdefault("_pf", [])
        if q and q[0][0] != pc:
            q.clear()                                   # redirect: drop the prefetch
        word = q.pop(0)[1] if q else orig_fetch(self, pc)
        nxt = q[-1][0] + 4 if q else pc + 4
        while len(q) < 3:                               # top up, reading memory *now*
            try:
                q.append((nxt, orig_fetch(self, nxt)))
            except MemoryError:
                break
            nxt += 4
        return word
    cls.fetch = fetch                                   # ISS.step fetches once per insn


MUTANTS = [
    mutant("C1_sra_as_srl", m_sra_logical),
    mutant("C2_lb_zero_extends", m_lb_zero_extend),
    mutant("C3_missing_forward", m_stale_forward),
    mutant("C4_bltu_as_blt", m_bltu_signed),
    mutant("C5_sh_low_byte_only", m_sh_wrong_lane),
    mutant("C6_jalr_lsb_kept", m_jalr_no_clear),
    mutant("C7_x0_writable", m_x0_writable),
    mutant("C8_slt_as_sltu", m_slt_unsigned),
    mutant("C9_fencei_stale_fetch", m_stale_prefetch),
]
EXPECTED_SURVIVORS = set()


def main(n_random=8):
    with tempfile.TemporaryDirectory() as d:
        out = pathlib.Path(d)
        # test_isa, the directed tests that run without MMIO, then random programs
        directed = sorted(p for p in (ROOT / "build").glob("d_*.elf")
                          if not p.name.endswith("_soc.elf"))
        elfs = ([ROOT / "build" / "test_isa.elf"] + directed
                + [build_random(s, out) for s in range(1, n_random + 1)])
        refs = [trace(ISS, e) for e in elfs]
        killed, survived, bad = 0, [], []
        for name, cls in MUTANTS:
            hit = None
            for e, ref in zip(elfs, refs):
                res = compare(ref, trace(cls, e))
                if not res.ok:
                    hit = (e.name, res.matched)
                    break
            if hit:
                killed += 1
                print(f"  killed   {name:22s} by {hit[0]} after {hit[1]} matching")
            else:
                survived.append(name)
                tag = "(expected)" if name in EXPECTED_SURVIVORS else "UNEXPECTED"
                print(f"  SURVIVED {name:22s} {tag}")
                if name not in EXPECTED_SURVIVORS:
                    bad.append(name)
        print(f"killed {killed} / {len(MUTANTS)} over {len(elfs)} programs")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
