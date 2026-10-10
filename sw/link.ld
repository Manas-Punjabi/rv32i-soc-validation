/* link.ld — firmware image for soc_top.
 *
 * RAM is one region at 0x8000_0000 (soc_mem / Spike's default RAM base). The
 * layout matches tb_soc's 64 KB default; shrink LENGTH to match a smaller
 * MEM_WORDS and the link fails loudly instead of running off the end.
 *
 * .tohost sits in its own 64-byte-aligned section (HTIF convention), so the
 * same image terminates on RTL and on Spike (Spike needs both tohost and fromhost).
 */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY
{
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 64K
}

STACK_SIZE = 4K;

SECTIONS
{
  .text : {
    *(.text.init)          /* crt0 first: the reset vector is the start of RAM */
    *(.text .text.*)
    *(.rodata .rodata.*)
    . = ALIGN(4);
  } > RAM

  /* Plain assignments, not PROVIDE: PROVIDE only defines a symbol something
   * references, nothing references fromhost, and Spike's HTIF needs BOTH symbols
   * (fesvr/htif.cc) or it never sees the exit and runs forever. */
  .tohost : ALIGN(64) {
    tohost = .;
    . += 8;
    fromhost = .;
    . += 8;
  } > RAM

  .data : ALIGN(4) {
    *(.data .data.*)
    *(.sdata .sdata.*)
    . = ALIGN(4);
  } > RAM

  .bss (NOLOAD) : ALIGN(4) {
    PROVIDE(_bss_start = .);
    *(.bss .bss.*)
    *(.sbss .sbss.*)
    *(COMMON)
    . = ALIGN(4);
    PROVIDE(_bss_end = .);
  } > RAM

  /* stack grows down from the top of what we reserve */
  .stack (NOLOAD) : ALIGN(16) {
    . += STACK_SIZE;
    PROVIDE(_stack_top = .);
  } > RAM

  PROVIDE(_end = .);
}
