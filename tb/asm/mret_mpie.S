# mret_mpie.S — trap entry and mret must move MIE <-> MPIE (privileged spec, mstatus).
#
# Every other co-sim program keeps mstatus.MIE = 0, so MPIE is always 0 and an mret that
# forgets to restore MIE from MPIE is indistinguishable (rv32i-soc-asic's mutant M7
# survived lockstep without this test). Here MIE = 1 with mie = 0: global interrupts on, no source
# enabled, so nothing can be taken, but the bits move:
#   1  trap entry: MPIE <= MIE (1), MIE <= 0           (checked in the handler)
#   2  mret:       MIE <= MPIE (1), MPIE <= 1
#   3  same with MIE = 0 before the trap: MIE stays 0 after mret, MPIE = 1
# Returns 0 on pass, else the check number. Runs on Spike, ISS and RTL.

  .equ MIE_BIT,  0x8
  .equ MPIE_BIT, 0x80

  .text
  .globl main
main:
  addi  sp, sp, -16
  sw    ra, 12(sp)
  la    t0, handler
  csrw  mtvec, t0
  csrw  mie, zero                   # no interrupt source enabled
  csrsi mstatus, MIE_BIT            # global enable on
  li    a0, 1
  ecall                             # handler checks entry (a0 := 0 if fine)
  bnez  a0, done
  li    a0, 2                       # after mret: MIE = 1, MPIE = 1
  csrr  t1, mstatus
  andi  t2, t1, MIE_BIT
  beqz  t2, done
  andi  t2, t1, MPIE_BIT
  beqz  t2, done
  li    a0, 3                       # again with MIE = 0
  csrci mstatus, MIE_BIT
  ecall
  li    a0, 3
  csrr  t1, mstatus
  andi  t2, t1, MIE_BIT
  bnez  t2, done
  andi  t2, t1, MPIE_BIT
  beqz  t2, done
  li    a0, 0
done:
  csrci mstatus, MIE_BIT
  lw    ra, 12(sp)
  addi  sp, sp, 16
  ret

  .align 2
handler:                            # on entry: MIE must be 0; MPIE = previous MIE
  csrr  t3, mstatus
  andi  t4, t3, MIE_BIT
  bnez  t4, 1f                      # MIE still set: leave a0 = 1 (fail)
  li    t5, 1
  bne   a0, t5, 1f                  # only check 1 needs MPIE = 1 here
  andi  t4, t3, MPIE_BIT
  beqz  t4, 1f
  li    a0, 0
1:
  csrr  t3, mepc
  addi  t3, t3, 4
  csrw  mepc, t3
  mret
