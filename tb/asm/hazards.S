# hazards.S — forwarding bins that neither generator reaches (coverage holes).
#
#   LINK -> csr.rs1, distance 1, 2 and 3: a jal's link register is the operand of a
#   CSR write that retires 1, 2 or 3 instructions later. Read back through mscratch.
#
# Returns 0 on pass, else the number of the failing check. Runs on Spike, ISS and RTL.

  .text
  .globl main
main:
  li    a0, 0

.macro LINK_CSR n, fill
  jal   t0, 1f
1:
  .rept \fill
  addi  t1, t1, 1                   # filler: never writes t0
  .endr
  csrrw x0, mscratch, t0            # t0 = address of 1: (the jal's pc + 4)
  csrr  t2, mscratch
  la    t3, 1b
  beq   t2, t3, 2f
  li    a0, \n
  ret
2:
.endm

  LINK_CSR 1, 0
  LINK_CSR 2, 1
  LINK_CSR 3, 2
  ret
