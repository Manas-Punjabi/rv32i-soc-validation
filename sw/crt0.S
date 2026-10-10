# crt0.S — reset entry for soc_top firmware.
#
# Zeroes x1-x31, sets up the stack, clears .bss, installs a trap handler, runs main(),
# then ends the simulation the HTIF way: tohost = (ret << 1) | 1, so ret 0 is a pass.
# Interrupts stay disabled (mstatus.MIE = 0): firmware polls. That is also what
# rv32i-soc-validation's co-simulation requires, since Spike does not model our timer.
#
# Why the zeroing: the register file has no reset (legal, and cheaper), so without it
# main()'s prologue saves never-written callee-saved registers, and their values depend
# on power-up state. riscv-tests do the same (INIT_XREG in env/p/riscv_test.h).
  .section .text.init
  .globl _start
_start:
  .irp r, 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
  li    x\r, 0
  .endr
  la    sp, _stack_top
  la    t0, trap_handler
  csrw  mtvec, t0
  csrw  mstatus, zero           # MIE = 0
  csrw  mie, zero

  # clear .bss
  la    t0, _bss_start
  la    t1, _bss_end
1:
  bgeu  t0, t1, 2f
  sw    zero, 0(t0)
  addi  t0, t0, 4
  j     1b
2:
  li    a0, 0
  li    a1, 0
  call  main
  # fall through: exit with main's return value

  .globl _exit
_exit:
  slli  a0, a0, 1
  ori   a0, a0, 1
  la    t0, tohost
  sw    a0, 0(t0)
  sw    zero, 4(t0)
3:
  j     3b

# Any trap is a firmware bug here. Report it as exit code (0x100 | mcause) so
# the failure is distinguishable from a normal non-zero return.
  .align 2
trap_handler:
  csrr  a0, mcause
  andi  a0, a0, 0xff
  ori   a0, a0, 0x100
  j     _exit
