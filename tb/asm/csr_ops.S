# csr_ops.S — CSR and SYSTEM paths no other program reaches (coverage holes,
# confirmed by scripts/reg_census.py and Verilator line coverage of rv32i_csr.sv and
# rv32i_decode.sv):
#   1-2  software writes to mcause and mtval, read back
#   3    mstatush: reads 0 on RV32, writes ignored
#   4    mie read back
#   5-6  mcycle / mcycleh written, read back (in range: the counter keeps running)
#   7    mcountinhibit.CY freezes mcycle; clearing it restarts it
#   13   mcountinhibit.IR freezes minstret; clearing it restarts it
#   8-12 SYSTEM encodings this core must trap as illegal (mcause 2, mtval = the word):
#        sret, uret, dret, sfence.vma, and funct3 = 100 (hypervisor loads/stores)
# Returns 0 on pass, else the check number. Runs on Spike, ISS and RTL (counter values
# are injected from the RTL in lockstep; the self-checks are range checks).

  .text
  .globl main
main:
  addi  sp, sp, -16
  sw    ra, 12(sp)
  la    t0, handler
  csrw  mtvec, t0
  li    s1, 0                       # traps seen
  li    a0, 1                       # 1: mcause: a pattern, all ones, all zeros
  li    t1, 0x12345678
  csrw  mcause, t1
  csrr  t2, mcause
  bne   t1, t2, fail
  li    t1, -1
  csrw  mcause, t1
  csrr  t2, mcause
  bne   t1, t2, fail
  csrw  mcause, zero
  csrr  t2, mcause
  bnez  t2, fail
  li    a0, 2                       # 2: mtval: same three
  li    t1, 0x0badf00d
  csrw  mtval, t1
  csrr  t2, mtval
  bne   t1, t2, fail
  li    t1, -1
  csrw  mtval, t1
  csrr  t2, mtval
  bne   t1, t2, fail
  csrw  mtval, zero
  csrr  t2, mtval
  bnez  t2, fail
  li    a0, 3                       # 3: mstatush
  li    t1, -1
  csrw  mstatush, t1
  csrr  t2, mstatush
  bnez  t2, fail
  li    a0, 4                       # 4: mie (MSIE|MTIE|MEIE writable; MIE=0 in mstatus)
  li    t1, 0x888
  csrw  mie, t1
  csrr  t2, mie
  csrw  mie, zero
  bne   t1, t2, fail
  li    a0, 5                       # 5: mcycle written, then read back >= written
  li    t1, 0x10000
  csrw  mcycle, t1
  csrr  t2, mcycle
  bltu  t2, t1, fail
  li    t3, 0x10100
  bgeu  t2, t3, fail
  li    a0, 6                       # 6: mcycleh
  li    t1, 7
  csrw  mcycleh, t1
  csrr  t2, mcycleh
  bne   t1, t2, fail
  csrw  mcycleh, zero
  li    a0, 7                       # 7: mcountinhibit.CY stops mcycle
  csrsi mcountinhibit, 1
  csrr  t1, mcycle
  nop
  nop
  csrr  t2, mcycle
  csrci mcountinhibit, 1
  bne   t1, t2, fail
  csrr  t3, mcycle
  beq   t2, t3, fail                # running again
  li    a0, 13                      # 13: mcountinhibit.IR stops minstret
  # written first: Spike's boot ROM retires 5 instructions at 0x1000 that minstret
  # counts and the trace drops, so its reset-time value differs from the ISS's
  li    t1, 0x2000
  csrw  minstret, t1
  csrsi mcountinhibit, 4
  csrr  t1, minstret
  nop
  nop
  csrr  t2, minstret
  csrci mcountinhibit, 4
  bne   t1, t2, fail
  csrr  t3, minstret
  beq   t2, t3, fail                # counting again

  .macro ILLEGAL n, word
  li    a0, \n
  la    t4, 1f
  li    s2, \word
1:
  .word \word
  beq   s1, zero, fail              # handler bumped s1 (and checked cause/tval)
  li    s1, 0
  .endm
  ILLEGAL 8,  0x10200073            # sret
  ILLEGAL 9,  0x00200073            # uret
  ILLEGAL 10, 0x7b200073            # dret
  ILLEGAL 11, 0x12000073            # sfence.vma x0, x0
  ILLEGAL 12, 0x00004073            # SYSTEM funct3 = 100

  li    a0, 0
fail:
  lw    ra, 12(sp)
  addi  sp, sp, 16
  ret

# expects: t4 = address of the faulting word, s2 = the word. Checks mcause = 2 (illegal),
# mepc = t4, mtval = the word; on a mismatch leaves s1 = 0, so the caller fails.
  .align 2
handler:
  csrr  t5, mcause
  li    t6, 2
  bne   t5, t6, 1f
  csrr  t5, mepc
  bne   t5, t4, 1f
  csrr  t5, mtval
  bne   t5, s2, 1f
  li    s1, 1
1:
  csrr  t5, mepc
  addi  t5, t5, 4
  csrw  mepc, t5
  mret
