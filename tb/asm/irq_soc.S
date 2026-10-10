# irq_soc.S — machine interrupts from the SoC's own devices, taken and returned from.
#
#   1  software interrupt (timer MSIP) taken, handler clears it
#   2  timer interrupt (mtimecmp) taken while a loop runs; the loop's result is checked,
#      so a wrong mepc or a clobbered register shows
#   3  external interrupt (UART tx_ie with TX ready) taken, handler masks it
#   4  all three pending at once, then MIE set: they must be taken in the spec's
#      priority order MEI (11), MSI (3), MTI (7). rv32i-soc-asic mutant M8 swaps the first two.
# The handler records each mcause in irq_log and saves every register it touches.
# Returns 0 on pass, else the check number.
#
# _soc: device interrupts. On a SoC without the RVFI interrupt side band
# (rvfi_intr, rvfi_ext_mcause, rvfi_ext_mip) lockstep can only match up to the first
# interrupt.

  .equ UART,  0x40000000
  .equ TIMER, 0x40002000
  .equ MIE_BIT, 0x8

  .text
  .globl main
main:
  addi  sp, sp, -16
  sw    ra, 12(sp)
  la    t0, handler
  csrw  mtvec, t0
  li    s0, TIMER
  li    s1, UART
  li    t0, -1
  sw    t0, 0xC(s0)                 # mtimecmp = all ones: timer quiet
  sw    t0, 0x8(s0)
  la    s2, irq_log
  sw    zero, 0(s2)                 # irq_log[0] = count

  li    a0, 1                       # 1: software interrupt
  li    t0, 0x8                     # mie.MSIE
  csrw  mie, t0
  csrsi mstatus, MIE_BIT
  li    t0, 1
  sw    t0, 0x10(s0)                # MSIP = 1 -> taken within a few cycles
  li    t4, 1
  call  wait_count
  bnez  t1, fail
  lw    t0, 4(s2)
  li    t1, 0x80000003
  bne   t0, t1, fail

  li    a0, 2                       # 2: timer interrupt during a loop
  li    t0, 0x80                    # mie.MTIE
  csrw  mie, t0
  lw    t0, 0(s0)                   # mtime low
  addi  t0, t0, 60
  sw    zero, 0xC(s0)               # mtimecmp = mtime + 60 (hi = 0 first: race-free
  sw    t0, 0x8(s0)                 #  enough, mtime is far below 2^32 here)
  li    t2, 0                       # sum 1..200 while the interrupt lands
  li    t3, 200
1:
  add   t2, t2, t3
  addi  t3, t3, -1
  bnez  t3, 1b
  li    t3, 20100
  bne   t2, t3, fail
  li    t4, 2
  call  wait_count                  # (already taken; returns at once if so)
  bnez  t1, fail
  lw    t0, 8(s2)
  li    t1, 0x80000007
  bne   t0, t1, fail

  li    a0, 3                       # 3: external interrupt (UART tx_ie, TX ready)
  li    t0, 0x800                   # mie.MEIE
  csrw  mie, t0
  li    t0, 2
  sw    t0, 0xC(s1)                 # UART CTRL.tx_ie
  li    t4, 3
  call  wait_count
  bnez  t1, fail
  lw    t0, 12(s2)
  li    t1, 0x8000000b
  bne   t0, t1, fail

  li    a0, 4                       # 4: all three pending, then MIE: order 11, 3, 7
  csrci mstatus, MIE_BIT
  li    t0, 1
  sw    t0, 0x10(s0)                # MSIP
  li    t0, 2
  sw    t0, 0xC(s1)                 # UART tx_ie
  sw    zero, 0xC(s0)               # mtimecmp = 0: timer pending (high word first: the
  sw    zero, 0x8(s0)               #  last timer handler left it all ones)
  li    t0, 0x888
  csrw  mie, t0
  nop
  nop                               # let the lines settle (mip lags a device store)
  csrsi mstatus, MIE_BIT
1:
  lw    t0, 0(s2)
  li    t1, 6
  blt   t0, t1, 1b                  # wait for three more (6 in total)
  csrci mstatus, MIE_BIT
  lw    t0, 16(s2)
  li    t1, 0x8000000b
  bne   t0, t1, fail
  lw    t0, 20(s2)
  li    t1, 0x80000003
  bne   t0, t1, fail
  lw    t0, 24(s2)
  li    t1, 0x80000007
  bne   t0, t1, fail

  li    a0, 0
fail:
  csrci mstatus, MIE_BIT
  csrw  mie, zero
  lw    ra, 12(sp)
  addi  sp, sp, 16
  ret

# wait until irq_log[0] >= t4, bounded; t1 = 0 on success. An absolute count: an
# interrupt can land before the wait starts (it usually does, a few cycles after the
# store that raises it).
wait_count:
  li    t6, 400
1:
  lw    t1, 0(s2)
  bge   t1, t4, 2f
  addi  t6, t6, -1
  bnez  t6, 1b
  li    t1, 1
  ret
2:
  li    t1, 0
  ret

# Interrupt handler: log mcause, silence the source, mret. Saves what it uses.
  .align 2
handler:
  addi  sp, sp, -16
  sw    t0, 0(sp)
  sw    t1, 4(sp)
  sw    t2, 8(sp)
  csrr  t0, mcause
  bgez  t0, 9f                      # an exception here is a bug: stop
  la    t1, irq_log
  lw    t2, 0(t1)
  addi  t2, t2, 1
  sw    t2, 0(t1)                   # count
  slli  t2, t2, 2
  add   t2, t2, t1
  sw    t0, 0(t2)                   # irq_log[count] = mcause
  andi  t0, t0, 0xF
  li    t1, 3
  beq   t0, t1, 3f
  li    t1, 7
  beq   t0, t1, 7f
  li    t1, UART                    # 11: mask the UART TX interrupt
  sw    zero, 0xC(t1)
  j     8f
3:
  li    t1, TIMER                   # 3: clear MSIP
  sw    zero, 0x10(t1)
  j     8f
7:
  li    t1, TIMER                   # 7: timer far in the future
  li    t2, -1
  sw    t2, 0xC(t1)
  sw    t2, 0x8(t1)
8:
  lw    t0, 0(sp)
  lw    t1, 4(sp)
  lw    t2, 8(sp)
  addi  sp, sp, 16
  mret
9:
  li    a0, 99
  j     _exit

  .bss
  .align 2
irq_log: .space 32
