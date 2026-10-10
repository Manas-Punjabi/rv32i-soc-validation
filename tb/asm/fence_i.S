# fence_i.S — does fence.i make a store to nearby code visible to the next fetches?
#
# Each case stores a new instruction over one that sits `pad` instructions after a
# fence.i, then falls through into it. Per the Zifencei spec, instruction fetches
# after fence.i must observe every earlier store, so the patched instruction
# (addi a0, x0, 1) must run, not the original (addi a0, x0, 2).
#
# riscv-tests' rv32ui fence_i modifies code far from the fetch point and jumps to it;
# this test aims at the window a prefetching front-end has already read: the reads in
# flight, the fetch queue, and ID.
#
# Returns a bitmask: bit n set = case pad=n executed the stale instruction. 0 = pass.
# Runs on Spike, the ISS and the RTL (no MMIO).

  .section .rodata
  .align 2
patched:  addi a0, x0, 1            # assembled here, copied over code below

  .text
  .globl main
main:
  li    s0, 0                       # failure bitmask
  lw    s1, patched                 # the new instruction word
  li    s2, 1                       # expected a0

.macro CASE n, pad
  la    t0, 1f
  li    a0, 0
  sw    s1, 0(t0)                   # overwrite the instruction at 1:
  fence.i
  .rept \pad
  nop
  .endr
1:
  addi  a0, x0, 2                   # stale version; must have been replaced
  beq   a0, s2, 2f
  ori   s0, s0, (1 << \n)
2:
.endm

  CASE 0, 0
  CASE 1, 1
  CASE 2, 2
  CASE 3, 3
  CASE 4, 6

  mv    a0, s0
  ret
