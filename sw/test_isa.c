/* test_isa.c — exercises every RV32I instruction class with self-checking results.
 * Returns 0 on pass, or the number of the first failing check. Runs anywhere:
 * no MMIO, so Spike and the ISS must produce identical retirement traces. */
#include <stdint.h>

static volatile uint32_t sink;
static uint8_t  buf8[16];
static uint16_t buf16[8];
static uint32_t buf32[8];

#define CHECK(n, cond) do { if (!(cond)) return (n); } while (0)

static uint32_t fib(uint32_t n) { return n < 2 ? n : fib(n - 1) + fib(n - 2); }

int main(void)
{
    volatile int32_t a = -7, b = 3;
    volatile uint32_t ua = 0xF0000001u, ub = 5;

    /* arithmetic and logic */
    CHECK(1, a + b == -4);
    CHECK(2, a - b == -10);
    CHECK(3, (a ^ b) == (-7 ^ 3));
    CHECK(4, (ua | ub) == 0xF0000005u);
    CHECK(5, (ua & 0xFFu) == 1u);
    /* comparisons, signed vs unsigned */
    CHECK(6, a < b);
    CHECK(7, !((uint32_t)a < (uint32_t)b));
    /* shifts, including arithmetic right shift of a negative */
    CHECK(8, (ua >> 4) == 0x0F000000u);
    CHECK(9, (a >> 1) == -4);
    CHECK(10, (ub << 30) == 0x40000000u);

    /* loads and stores at every width, with sign extension */
    for (int i = 0; i < 16; i++) buf8[i] = (uint8_t)(0x80 + i);
    for (int i = 0; i < 8; i++)  buf16[i] = (uint16_t)(0x8000 + i);
    for (int i = 0; i < 8; i++)  buf32[i] = 0xDEAD0000u + i;
    CHECK(11, (int8_t)buf8[3] == -125);
    CHECK(12, buf8[3] == 0x83);
    CHECK(13, (int16_t)buf16[2] == -32766);
    CHECK(14, buf16[2] == 0x8002);
    CHECK(15, buf32[7] == 0xDEAD0007u);

    /* control flow: recursion exercises jal/jalr and the stack */
    CHECK(16, fib(12) == 144);

    /* software multiply (RV32I has no MUL): the benchmark baseline */
    uint32_t acc = 0;
    for (uint32_t i = 0; i < 10; i++) acc += i * i;
    CHECK(17, acc == 285);

    /* JALR must clear bit 0 of the target (spec). Compilers never emit odd targets,
     * so random and compiled tests can't reach this; found by co-sim mutation test C6. */
    uint32_t landed;
    __asm__ volatile(
        "la   t0, 1f\n"
        "addi t0, t0, 1\n"          /* deliberately odd target */
        "jalr zero, 0(t0)\n"
        "li   %0, 0\n"             /* skipped if the jump lands correctly */
        "j    2f\n"
        "1:   li %0, 1\n"
        "2:\n"
        : "=r"(landed) : : "t0");
    CHECK(18, landed == 1);

    sink = acc;
    return 0;
}
