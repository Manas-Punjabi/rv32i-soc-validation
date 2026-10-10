/* test_counters.c — reads cycle and instret around a loop and stores the deltas.
 * The values are implementation-specific (they depend on the pipeline), so they can
 * only be compared in lockstep with injection: the ISS takes the RTL's counter reads,
 * then independently computes and stores the same deltas. marchid is read too: ID
 * registers are implementation-defined and injected the same way. */
#include <stdint.h>

static inline uint32_t rdcycle(void)   { uint32_t v; __asm__ volatile("csrr %0, mcycle"   : "=r"(v)); return v; }
static inline uint32_t rdinstret(void) { uint32_t v; __asm__ volatile("csrr %0, minstret" : "=r"(v)); return v; }

volatile uint32_t cycles, instrs, arch;

int main(void)
{
    uint32_t c0 = rdcycle(), i0 = rdinstret();
    __asm__ volatile("csrr %0, marchid" : "=r"(arch));
    volatile uint32_t acc = 0;
    for (int k = 0; k < 50; k++) acc += (uint32_t)k;
    uint32_t c1 = rdcycle(), i1 = rdinstret();
    cycles = c1 - c0;
    instrs = i1 - i0;
    if (instrs == 0 || cycles < instrs) return 1;    /* CPI >= 1 on any real core */
    return acc == 1225 ? 0 : 2;
}
