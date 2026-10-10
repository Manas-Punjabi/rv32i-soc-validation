# wfi_soc.S — WFI must not hang when a locally enabled interrupt is pending.
#
# Privileged spec, WFI: execution must resume when an interrupt is pending and enabled
# in mie, regardless of mstatus.MIE. Here MIE stays 0 (crt0), mie.MTIE = 1 and the
# timer compare is set to fire shortly, so the interrupt becomes pending but is never
# taken: the hart must simply continue after the wfi. A no-op WFI (this core) and a
# waiting WFI must both pass; a WFI that waits for MIE would hang and time out.
#
# _soc: uses the memory-mapped timer, which Spike does not model. ISS + RTL only.

  .equ TIMER,   0x40002000
  .equ MTIME,   0x0
  .equ CMP_LO,  0x8
  .equ CMP_HI,  0xC

  .text
  .globl main
main:
  li    t0, TIMER
  lw    t1, MTIME(t0)
  addi  t1, t1, 40                  # fire in ~40 cycles
  li    t2, -1
  sw    t2, CMP_HI(t0)              # race-free 64-bit compare update
  sw    t1, CMP_LO(t0)
  sw    zero, CMP_HI(t0)
  li    t3, 0x80                    # mie.MTIE
  csrw  mie, t3
  wfi
  wfi                               # a second one, now certainly with MTIP pending
  csrw  mie, zero                   # restore: timer disabled, compare to all ones
  sw    t2, CMP_HI(t0)
  sw    t2, CMP_LO(t0)
  li    a0, 0
  ret
