"""randgen.py — random RV32I programs for co-simulation.

Directed tests find the bugs you thought of; random instruction streams find the
pipeline hazards you didn't: back-to-back dependencies, load-use pairs, branches over
dependent instructions, stores followed by loads to the same address.

Generated programs are always legal and always terminate:
  * x31 holds a data-buffer base and is never written
  * loads/stores use aligned offsets inside that buffer
  * branches and jumps only go forward, to a label a few instructions ahead
  * the program ends with a jump to `_exit` (from crt0.S) with a0 = 0
The pass/fail signal is the trace comparison, not the exit code.
"""
import argparse
import random

REGS = list(range(1, 31))          # x31 reserved as the buffer base
BUF_WORDS = 64

ALU_R = ["add", "sub", "sll", "slt", "sltu", "xor", "srl", "sra", "or", "and"]
ALU_I = ["addi", "slti", "sltiu", "xori", "ori", "andi"]
SHIFT_I = ["slli", "srli", "srai"]
BRANCH = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]
LOADS = [("lb", 1), ("lbu", 1), ("lh", 2), ("lhu", 2), ("lw", 4)]
STORES = [("sb", 1), ("sh", 2), ("sw", 4)]


def gen(seed: int, length: int = 400, hazard_bias: float = 0.5) -> str:
    rng = random.Random(seed)
    last_rd = [1]
    lines = [
        f"# random RV32I program, seed={seed}, length={length}",
        "    .section .text",
        "    .globl main",
        "main:",
        "    la   x31, rbuf",
    ]
    for r in REGS:
        lines.append(f"    li   x{r}, {rng.randint(-2**31, 2**31 - 1)}")

    def src():
        # bias toward the most recent destinations to provoke forwarding and stalls;
        # occasionally read x0, which must always read as zero
        k = rng.random()
        if k < 0.05:
            return 0
        if k < hazard_bias:
            return rng.choice(last_rd[-3:])
        return rng.choice(REGS)

    def dst():
        # occasionally target x0: the write must be discarded, and the next reader of
        # x0 (biased in via last_rd) must still see zero. Found by mutation testing:
        # without this, a core that doesn't hard-wire x0 passes every random test.
        r = 0 if rng.random() < 0.05 else rng.choice(REGS)
        last_rd.append(r)
        return r

    label = 0
    pending = []                   # (countdown, label) for forward branch targets
    for _ in range(length):
        k = rng.random()
        if k < 0.30:
            lines.append(f"    {rng.choice(ALU_R)} x{dst()}, x{src()}, x{src()}")
        elif k < 0.48:
            lines.append(f"    {rng.choice(ALU_I)} x{dst()}, x{src()}, {rng.randint(-2048, 2047)}")
        elif k < 0.56:
            lines.append(f"    {rng.choice(SHIFT_I)} x{dst()}, x{src()}, {rng.randint(0, 31)}")
        elif k < 0.60:
            lines.append(f"    lui  x{dst()}, {rng.randint(0, 0xFFFFF)}")
        elif k < 0.62:
            lines.append(f"    auipc x{dst()}, {rng.randint(0, 0xFFFFF)}")
        elif k < 0.74:
            op, sz = rng.choice(LOADS)
            off = rng.randrange(0, BUF_WORDS * 4, sz)
            lines.append(f"    {op} x{dst()}, {off}(x31)")
        elif k < 0.86:
            op, sz = rng.choice(STORES)
            off = rng.randrange(0, BUF_WORDS * 4, sz)
            lines.append(f"    {op} x{src()}, {off}(x31)")
        elif k < 0.97:
            label += 1
            lines.append(f"    {rng.choice(BRANCH)} x{src()}, x{src()}, L{label}")
            pending.append([rng.randint(1, 6), label])
        else:
            label += 1
            lines.append(f"    jal  x{dst()}, L{label}")
            pending.append([rng.randint(1, 4), label])
        for p in pending:
            p[0] -= 1
        for p in [p for p in pending if p[0] <= 0]:
            lines.append(f"L{p[1]}:")
            pending.remove(p)
    for _, lbl in pending:
        lines.append(f"L{lbl}:")
    lines += [
        "    li   a0, 0",
        "    j    _exit",
        "    .section .bss",
        "    .align 4",
        f"rbuf: .space {BUF_WORDS * 4}",
    ]
    return "\n".join(lines) + "\n"


# ---- stress mode ---------------------------------------------------------------
# gen() above is kept byte-stable: its programs and the numbers recorded for them must
# not move. gen_stress() adds what gen() never produces, each aimed at a part of
# rv32i-soc-asic's pipeline that the plain stream leaves alone:
#   loops   backward branches (bounded countdown loops)        branch/redirect + refetch
#   csr     csrrw/csrrs/csrrc(+i) chains on mscratch, reads of mepc/mcause/mstatus
#                                                               CSR read in EX, forwarding
#   traps   illegal word, ecall, ebreak, misaligned lw/sh, inside hazard chains; the
#           handler skips the faulting instruction (mepc += 4)  trap vs stall/redirect
#   mmio    (soc=True only) GPIO and timer loads/stores mixed into the chains: APB
#           stalls next to RAM wait states and load-use stalls. Spike has no such
#           devices, so soc programs run on the ISS and the RTL only.
#   ptr     (ptr=True) a load / jal link / CSR result used 1-3 instructions
#           later as a load, store or jalr base (pointer chasing)
#   irq     (irq=True; implies soc) software and timer interrupts raised at
#           random points; needs a SoC with the RVFI interrupt side band
# Registers: x31 buffer base, x30 loop counter, x29 trap-handler scratch, x28 GPIO base,
# x27 timer base, x26 pointer-table base (ptr only). None of them is ever a destination
# of random code.
GPIO_BASE, TIMER_BASE = 0x4000_3000, 0x4000_2000
SREGS = list(range(1, 27))
SCRATCH_CSRS = ["mscratch"]
READ_CSRS = ["mscratch", "mepc", "mcause", "mtval", "mstatus", "misa", "mtvec"]


def gen_stress(seed: int, length: int = 400, soc: bool = False,
               hazard_bias: float = 0.6, ptr: bool = False, irq: bool = False) -> str:
    rng = random.Random(seed)
    last_rd = [1]
    label = [0]
    # ptr=True reserves x26 as the pointer-table base; with ptr=False the
    # output is byte-identical to the plain stress programs
    regs = list(range(1, 26)) if ptr else SREGS
    code_ptrs = []                 # labels whose addresses go into the pointer table
    lines = [
        f"# stress program, seed={seed}, length={length}, soc={soc}"
        + (", ptr" if ptr else "") + (", irq" if irq else ""),
        "    .section .text",
        "    .globl main",
        "main:",
        "    la   x31, rbuf",
        "    la   x29, rg_trap",
        "    csrw mtvec, x29",
        f"    li   x28, {GPIO_BASE:#x}",
        f"    li   x27, {TIMER_BASE:#x}",
    ] + (["    la   x26, rptr"] if ptr else []) + ([
        # software and timer interrupts enabled, timer parked far ahead
        "    li   x29, -1",
        "    sw   zero, 0xC(x27)",
        "    sw   x29, 0x8(x27)",
        "    li   x29, 0x88",
        "    csrw mie, x29",
        "    csrsi mstatus, 8",
    ] if irq else [])
    for r in regs:
        lines.append(f"    li   x{r}, {rng.randint(-2**31, 2**31 - 1)}")

    def src():
        k = rng.random()
        if k < 0.05:
            return 0
        if k < hazard_bias:
            return rng.choice(last_rd[-3:])
        return rng.choice(regs)

    def dst():
        r = 0 if rng.random() < 0.05 else rng.choice(regs)
        last_rd.append(r)
        return r

    def new_label():
        label[0] += 1
        return f"S{label[0]}"

    def straight():
        """One instruction that never changes control flow (except by trapping)."""
        k = rng.random()
        if k < 0.26:
            return f"    {rng.choice(ALU_R)} x{dst()}, x{src()}, x{src()}"
        if k < 0.40:
            return f"    {rng.choice(ALU_I)} x{dst()}, x{src()}, {rng.randint(-2048, 2047)}"
        if k < 0.46:
            return f"    {rng.choice(SHIFT_I)} x{dst()}, x{src()}, {rng.randint(0, 31)}"
        if k < 0.49:
            return f"    lui  x{dst()}, {rng.randint(0, 0xFFFFF)}"
        if k < 0.60:
            op, sz = rng.choice(LOADS)
            return f"    {op} x{dst()}, {rng.randrange(0, BUF_WORDS * 4, sz)}(x31)"
        if k < 0.70:
            op, sz = rng.choice(STORES)
            return f"    {op} x{src()}, {rng.randrange(0, BUF_WORDS * 4, sz)}(x31)"
        if k < 0.80:                                     # CSR chain element
            j = rng.random()
            if j < 0.35:
                op = rng.choice(["csrrw", "csrrs", "csrrc"])
                return f"    {op} x{dst()}, {rng.choice(SCRATCH_CSRS)}, x{src()}"
            if j < 0.55:
                op = rng.choice(["csrrwi", "csrrsi", "csrrci"])
                return f"    {op} x{dst()}, {rng.choice(SCRATCH_CSRS)}, {rng.randint(0, 31)}"
            return f"    csrr x{dst()}, {rng.choice(READ_CSRS)}"
        if k < 0.86:                                     # trap in the middle of a chain
            return rng.choice([
                "    .word 0x00000000",                  # defined illegal
                "    ecall",
                "    ebreak",
                f"    lw   x{dst()}, {rng.randrange(0, BUF_WORDS * 4 - 4, 4) + rng.choice([1, 2, 3])}(x31)",
                f"    sh   x{src()}, {rng.randrange(0, BUF_WORDS * 4 - 2, 2) + 1}(x31)",
            ])
        if soc and k < 0.95:                             # MMIO: APB stalls
            return rng.choice([
                f"    lw   x{dst()}, 0(x28)",            # GPIO OUT
                f"    lw   x{dst()}, 4(x28)",            # GPIO IN (synchronizer latency)
                f"    sw   x{src()}, 0(x28)",            # GPIO OUT write
                f"    lw   x{dst()}, 0(x27)",            # mtime low
                f"    lbu  x{dst()}, 0(x28)",            # sub-word load from APB
            ])
        return f"    auipc x{dst()}, {rng.randint(0, 0xFFFFF)}"

    def filler(avoid):
        """One ALU instruction that writes none of `avoid` (keeps a distance exact)."""
        d = rng.choice([r for r in regs if r not in avoid])
        return f"    {rng.choice(ALU_R)} x{d}, x{rng.choice(regs)}, x{rng.choice(regs)}"

    def ptr_block():
        """Address-generation hazards (former coverage holes): a register produced by
        a load, a jal link or a CSR read is used 1-3 retirements later as the base of a
        load, a store or a jalr. Every address is legal by construction:
          data pointers  rptr[0..47] = rbuf + 4*j, offsets 0..60 stay inside rbuf
          code pointers  rptr[48..]  = a label just ahead, so jalr only goes forward
          link pointers  jal xK, 1f jumps over two scratch words; xK points at them,
                         so loads/stores through xK touch data that never executes"""
        k = rng.choice(regs)
        d = rng.randint(1, 3)
        prod = rng.choice(["LOAD", "LINK", "CSR"])
        use = rng.choice(["load", "store", "jalr"])
        out, tgt = [], None
        if prod in ("LOAD", "CSR"):
            base = k if prod == "LOAD" else rng.choice([r for r in regs if r != k])
            if use == "jalr":
                tgt = new_label()
                code_ptrs.append(tgt)
                out.append(f"    lw   x{base}, {4 * (47 + len(code_ptrs))}(x26)")
            else:
                out.append(f"    lw   x{base}, {4 * rng.randint(0, 47)}(x26)")
            if prod == "CSR":
                out += [f"    csrw mscratch, x{base}", f"    csrr x{k}, mscratch"]
        else:
            skip = new_label()
            out += [f"    jal  x{k}, {skip}", "    .word 0", "    .word 0", f"{skip}:"]
        out += [filler({k}) for _ in range(d - 1)]
        if use in ("load", "store"):
            op, sz = rng.choice(LOADS if use == "load" else STORES)
            off = rng.randrange(0, 8 if prod == "LINK" else 61, sz)
            data = rng.choice(regs)
            out.append(f"    {op} x{data}, {off}(x{k})")
        elif prod == "LINK":
            # k = address of the scratch words; skip label is 8 bytes on, then d-1
            # fillers, the jalr, and one instruction it jumps over
            out.append(f"    jalr x{rng.choice(regs + [0])}, {8 + 4 * (d - 1) + 8}(x{k})")
            out.append("    addi x0, x0, 0")
        else:
            out.append(f"    jalr x{rng.choice(regs + [0])}, 0(x{k})")
            out.append("    addi x0, x0, 0")
            out.append(f"{tgt}:")
        return out

    def irq_event():
        """Raise a software interrupt now, or arm the timer to fire a few to a few dozen
        cycles later, i.e. somewhere in whatever the program is doing then. x29 is the
        trap handler's scratch register; an interrupt between the two instructions may
        change it, which the lockstep sees identically on both sides."""
        if rng.random() < 0.5:
            return ["    li   x29, 1", "    sw   x29, 0x10(x27)"]
        return ["    lw   x29, 0(x27)", f"    addi x29, x29, {rng.randint(3, 60)}",
                "    sw   x29, 0x8(x27)"]

    pending = []                   # forward branch targets, as in gen()
    n = 0
    while n < length:
        if irq and rng.random() < 0.05:
            ev = irq_event()
            lines += ev
            n += len(ev)
            continue
        if ptr and rng.random() < 0.15:
            block = ptr_block()
            lines += block
            n += sum(1 for l in block if not l.endswith(":"))
            continue
        k = rng.random()
        if k < 0.08:                                     # bounded backward loop
            top = new_label()
            body = [straight() for _ in range(rng.randint(1, 5))]
            lines += [f"    li   x30, {rng.randint(2, 5)}", f"{top}:"] + body + [
                "    addi x30, x30, -1",
                f"    bnez x30, {top}",
            ]
            n += len(body) + 3
        elif k < 0.18:                                   # forward branch or jump
            lbl = new_label()
            if rng.random() < 0.8:
                lines.append(f"    {rng.choice(BRANCH)} x{src()}, x{src()}, {lbl}")
            else:
                lines.append(f"    jal  x{dst()}, {lbl}")
            pending.append([rng.randint(1, 5), lbl])
            n += 1
        else:
            lines.append(straight())
            n += 1
        for p in pending:
            p[0] -= 1
        for p in [p for p in pending if p[0] <= 0]:
            lines.append(f"{p[1]}:")
            pending.remove(p)
    for _, lbl in pending:
        lines.append(f"{lbl}:")
    lines += [
        "    li   a0, 0",
        "    j    _exit",
        "    .align 2",
        "rg_trap:",                                      # skip the faulting instruction
    ] + ([
        "    csrr x29, mcause",                          # interrupt: silence it, resume
        "    bgez x29, 1f",
        "    andi x29, x29, 4",                          # 3 = software, 7 = timer
        "    bnez x29, 2f",
        "    sw   zero, 0x10(x27)",
        "    mret",
        "2:  li   x29, -1",
        "    sw   x29, 0x8(x27)",
        "    mret",
        "1:",
    ] if irq else []) + [
        "    csrr x29, mepc",
        "    addi x29, x29, 4",
        "    csrw mepc, x29",
        "    mret",
        "    .section .bss",
        "    .align 4",
        f"rbuf: .space {BUF_WORDS * 4}",
    ]
    if ptr:
        lines += ["    .section .data", "    .align 2", "rptr:"]
        lines += [f"    .word rbuf+{4 * j}" for j in range(48)]
        lines += [f"    .word {l}" for l in code_ptrs]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("seed", type=int)
    ap.add_argument("-n", "--length", type=int, default=400)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--stress", action="store_true", help="stress mode")
    ap.add_argument("--soc", action="store_true", help="stress mode with MMIO (no Spike)")
    ap.add_argument("--ptr", action="store_true", help="stress + address-generation hazards")
    ap.add_argument("--irq", action="store_true",
                    help="soc stress + random software/timer interrupts")
    a = ap.parse_args()
    if a.stress or a.soc or a.ptr or a.irq:
        src = gen_stress(a.seed, a.length, soc=a.soc or a.irq, ptr=a.ptr, irq=a.irq)
    else:
        src = gen(a.seed, a.length)
    open(a.out, "w").write(src)
