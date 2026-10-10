/* test_soc_map.c — soc_top's address decode, as documented in rtl/soc_top.sv:
 *   0x8xxx_xxxx  RAM, aliased (address bits above the RAM size are ignored)
 *   elsewhere    (outside RAM and the 0x4xxx_xxxx peripheral window) reads return 0,
 *                writes are dropped, no trap
 * Runs on the ISS and on the RTL. Not on Spike, which raises an access fault for an
 * unmapped address; that difference is why the ISS models this decode at all. */
#include <stdint.h>

#define RAM_BYTES  0x10000u                    /* 64 KB: soc_top MEM_WORDS = 16384 */
#define UNMAPPED   0x10000000u

static volatile uint32_t word;

int main(void)
{
    volatile uint32_t *stray = (volatile uint32_t *)UNMAPPED;
    volatile uint32_t *alias = (volatile uint32_t *)((uintptr_t)&word + RAM_BYTES);

    *stray = 0xDEADBEEFu;                      /* dropped, no trap */
    if (*stray != 0) return 1;                 /* reads as 0 */
    *(volatile uint32_t *)0x20001234u = 1;     /* another hole */

    word = 0x12345678u;
    if (*alias != 0x12345678u) return 2;       /* RAM + 64 KB reads the same word */
    *alias = 0xCAFEF00Du;
    if (word != 0xCAFEF00Du) return 3;         /* and writes through the alias */
    return 0;
}
