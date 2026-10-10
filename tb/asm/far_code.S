# far_code.S — code, a trap handler and data above 48 KB of the 64 KB RAM.
#
# Every other program fits in the first 16 KB, so pc / mtvec / mepc bits 10-15 and data
# address bits 14-15 never toggle (RTL toggle coverage). Here:
#   1  jal to a function at the far end of RAM, and return (long jal offsets, high pc)
#   2  a trap taken there: mtvec and mepc above 0x8000_c000
#   3  stores and loads to far data, read back
# Returns 0 on pass, else the check number. Runs on Spike, ISS and RTL.

  .text
  .globl main
main:
  addi  sp, sp, -16
  sw    ra, 12(sp)
  li    a0, 1
  jal   ra, far_func                # 1: far call (returns a0 = 0x5a)
  li    t1, 0x5a
  bne   a0, t1, fail
  li    a0, 2                       # 2: trap taken in far code, handler far too
  la    t0, far_handler
  csrw  mtvec, t0
  jal   ra, far_trap
  li    t1, 0x77
  bne   a0, t1, fail
  li    a0, 3                       # 3: far data
  la    t0, far_data
  li    t1, 0xa5a55a5a
  sw    t1, 0(t0)
  sh    t1, 6(t0)
  sb    t1, 9(t0)
  lw    t2, 0(t0)
  bne   t1, t2, fail
  lhu   t2, 6(t0)
  li    t3, 0x5a5a
  bne   t2, t3, fail
  lbu   t2, 9(t0)
  li    t3, 0x5a
  bne   t2, t3, fail
  li    a0, 0
fail:
  lw    ra, 12(sp)
  addi  sp, sp, 16
  ret

  .space 0xC000                     # push what follows beyond 48 KB (plus the code above)
  .align 6
far_func:
  li    a0, 0x5a
  ret
far_trap:
  ecall                             # -> far_handler, mepc = this address
  ret
  .align 2
far_handler:
  csrr  t5, mepc
  addi  t5, t5, 4
  csrw  mepc, t5
  li    a0, 0x77
  mret

  .bss
  .align 4
  .space 0x0                        # (far_data sits after the far code in .bss)
far_data:
  .space 16
