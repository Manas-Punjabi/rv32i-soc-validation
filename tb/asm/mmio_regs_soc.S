# mmio_regs_soc.S — every peripheral register through the core's data port, including
# the ones no other program touches (scripts/reg_census.py): timer MTIME writes,
# MTIMECMP reads, MSIP; UART CTRL and DIV; GPIO SET/CLR. Interrupt-pending side effects
# are made visible in mip (injected from the RTL in lockstep) but never taken: MIE = 0.
#
# Self-checks are on values the register map defines exactly; anything time-dependent is
# only range-checked. _soc: Spike has none of these devices. ISS + RTL only.

  .equ UART, 0x40000000
  .equ TIMER, 0x40002000
  .equ GPIO, 0x40003000

  .text
  .globl main
main:
  li    s0, TIMER
  li    a0, 1                       # 1: MTIME write sticks (then keeps counting)
  sw    zero, 4(s0)                 # MTIME_HI = 0
  li    t1, 0x00100000
  sw    t1, 0(s0)                   # MTIME_LO
  lw    t2, 0(s0)
  bltu  t2, t1, fail
  li    t3, 0x00100100
  bgeu  t2, t3, fail
  li    a0, 2                       # 2: MTIME_HI write / read
  li    t1, 3
  sw    t1, 4(s0)
  lw    t2, 4(s0)
  bne   t1, t2, fail
  sw    zero, 4(s0)
  li    a0, 3                       # 3: MTIMECMP read back
  li    t1, -1
  sw    t1, 0xC(s0)
  li    t2, 0x13572468
  sw    t2, 8(s0)
  lw    t3, 8(s0)
  bne   t2, t3, fail
  lw    t3, 0xC(s0)
  bne   t1, t3, fail
  sw    t1, 8(s0)                   # back to all ones: no timer interrupt pending
  li    a0, 4                       # 4: MSIP raises mip.MSIP, clearing it lowers it
  li    t1, 1
  sw    t1, 0x10(s0)
  lw    t2, 0x10(s0)
  bne   t1, t2, fail
  csrr  t3, mip
  andi  t3, t3, 0x8
  beqz  t3, fail
  # The spec gives no bound on how soon a device store shows in mip, so poll. On this
  # core a csrr right after the store still reads the old value; the
  # number of polls is stored in `polls` for the record, and bounded here.
  sw    zero, 0x10(s0)
  li    t4, 0
1:
  csrr  t3, mip
  andi  t3, t3, 0x8
  beqz  t3, 2f
  addi  t4, t4, 1
  li    t5, 8
  blt   t4, t5, 1b
  j     fail
2:
  la    t5, polls
  sw    t4, 0(t5)

  li    a0, 7                       # 7: pending AND enabled in mie, but MIE = 0:
  li    t1, 0x808                   #    mie.MSIE | mie.MEIE, no trap may be taken
  csrw  mie, t1
  li    t1, 1
  sw    t1, 0x10(s0)                # MSIP -> software interrupt pending + enabled
  li    t2, UART
  li    t1, 2
  sw    t1, 0xC(t2)                 # UART CTRL.tx_ie, tx_ready=1 -> external pending
  nop
  csrr  t3, mip
  li    t1, 0x808
  and   t3, t3, t1
  bne   t3, t1, fail                # both pending (mip injected in lockstep)
  sw    zero, 0xC(t2)
  sw    zero, 0x10(s0)
  csrw  mie, zero                   # (a taken trap would land in crt0's handler: exit 0x10b/0x103)

  li    s0, UART
  li    a0, 5                       # 5: UART CTRL read back, DIV read back
  li    t1, 3
  sw    t1, 0xC(s0)
  lw    t2, 0xC(s0)
  sw    zero, 0xC(s0)
  bne   t1, t2, fail
  li    t1, 16
  sw    t1, 0x10(s0)
  lw    t2, 0x10(s0)
  bne   t1, t2, fail

  li    s0, GPIO
  li    a0, 6                       # 6: GPIO SET / CLR on OUT
  li    t1, 0x0F
  sw    t1, 0(s0)
  li    t1, 0x30
  sw    t1, 8(s0)                   # SET
  li    t1, 0x01
  sw    t1, 0xC(s0)                 # CLR
  lw    t2, 0(s0)
  li    t3, 0x3E
  bne   t2, t3, fail

  li    a0, 0
fail:
  ret

  .data
  .align 2
  .globl polls
polls: .word 0                      # mip polls that still saw MSIP after clearing it
