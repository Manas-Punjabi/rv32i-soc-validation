/* soc.h — SoC memory map and accelerator driver interface.
 * Keep this file and the memory map in docs/architecture.md in sync with rtl/soc_top.sv.
 */
#ifndef SOC_H
#define SOC_H
#include <stdint.h>

#define SOC_BASE        0x40000000u          /* peripheral window base       */
#define SLAVE_STRIDE    0x00001000u          /* 4 KB per slave               */

#define UART_BASE       (SOC_BASE + 0u * SLAVE_STRIDE)   /* slave 0 */
#define ACCEL_BASE      (SOC_BASE + 1u * SLAVE_STRIDE)   /* slave 1 */
#define TIMER_BASE      (SOC_BASE + 2u * SLAVE_STRIDE)   /* slave 2 */
#define GPIO_BASE       (SOC_BASE + 3u * SLAVE_STRIDE)   /* slave 3 */

#define REG(a)          (*(volatile uint32_t *)(a))

/* ---- matrix accelerator (slave 1) ---- */
#define ACCEL_CTRL      (ACCEL_BASE + 0x000u)  /* W: bit0 start               */
#define ACCEL_STATUS    (ACCEL_BASE + 0x004u)  /* R: bit0 busy, bit1 done     */
                                               /* W: bit1 clears done         */
#define ACCEL_PARAMS    (ACCEL_BASE + 0x008u)  /* R: [7:0] N, [15:8] DW       */
#define ACCEL_A(i)      (ACCEL_BASE + 0x400u + 4u * (i))
#define ACCEL_B(i)      (ACCEL_BASE + 0x800u + 4u * (i))
#define ACCEL_C(i)      (ACCEL_BASE + 0xC00u + 4u * (i))

#define ACCEL_START     0x1u
#define ACCEL_BUSY      0x1u
#define ACCEL_DONE      0x2u

/* ---- UART (slave 0), 8N1, baud = clk / DIV ---- */
#define UART_TXDATA     (UART_BASE + 0x000u)   /* W: [7:0], only when TX_READY  */
#define UART_RXDATA     (UART_BASE + 0x004u)   /* R: [7:0], read pops           */
#define UART_STATUS     (UART_BASE + 0x008u)   /* R: flags; W: bit3/4 clear     */
#define UART_CTRL       (UART_BASE + 0x00Cu)   /* RW: bit0 rx_ie, bit1 tx_ie    */
#define UART_DIV        (UART_BASE + 0x010u)   /* RW: [15:0] clocks per bit     */
#define UART_TX_READY   0x01u
#define UART_TX_IDLE    0x02u
#define UART_RX_VALID   0x04u
#define UART_RX_OVERRUN 0x08u
#define UART_RX_FRAME   0x10u

/* ---- timer (slave 2), CLINT-style ---- */
#define TIMER_MTIME_LO     (TIMER_BASE + 0x000u)
#define TIMER_MTIME_HI     (TIMER_BASE + 0x004u)
#define TIMER_MTIMECMP_LO  (TIMER_BASE + 0x008u)
#define TIMER_MTIMECMP_HI  (TIMER_BASE + 0x00Cu)
#define TIMER_MSIP         (TIMER_BASE + 0x010u)

/* ---- GPIO (slave 3) ---- */
#define GPIO_OUT        (GPIO_BASE + 0x000u)
#define GPIO_IN         (GPIO_BASE + 0x004u)
#define GPIO_SET        (GPIO_BASE + 0x008u)
#define GPIO_CLR        (GPIO_BASE + 0x00Cu)

static inline void uart_putc(char c)
{
    while (!(REG(UART_STATUS) & UART_TX_READY))
        ;
    REG(UART_TXDATA) = (uint32_t)(unsigned char)c;
}

static inline void uart_puts(const char *s)
{
    while (*s)
        uart_putc(*s++);
}

/* 64-bit mtime, read HI/LO/HI to survive a carry. This is the benchmark clock:
 * it is memory-mapped, so a co-simulation harness can feed the same value to
 * the ISS, which rdcycle cannot. */
static inline uint64_t timer_now(void)
{
    uint32_t hi, lo, hi2;
    do {
        hi  = REG(TIMER_MTIME_HI);
        lo  = REG(TIMER_MTIME_LO);
        hi2 = REG(TIMER_MTIME_HI);
    } while (hi != hi2);
    return ((uint64_t)hi << 32) | lo;
}

/* race-free compare update on a 32-bit bus */
static inline void timer_set_cmp(uint64_t t)
{
    REG(TIMER_MTIMECMP_HI) = 0xFFFFFFFFu;
    REG(TIMER_MTIMECMP_LO) = (uint32_t)t;
    REG(TIMER_MTIMECMP_HI) = (uint32_t)(t >> 32);
}

/* Multiply n x n matrices. Blocks until the accelerator reports done. */
static inline void accel_matmul(const uint32_t *a, const uint32_t *b,
                                uint32_t *c, unsigned n)
{
    for (unsigned i = 0; i < n * n; i++) {
        REG(ACCEL_A(i)) = a[i];
        REG(ACCEL_B(i)) = b[i];
    }
    REG(ACCEL_CTRL) = ACCEL_START;
    while (!(REG(ACCEL_STATUS) & ACCEL_DONE))
        ;
    for (unsigned i = 0; i < n * n; i++)
        c[i] = REG(ACCEL_C(i));
    REG(ACCEL_STATUS) = ACCEL_DONE;      /* clear sticky done */
}

/* Reference implementation, for the benchmark comparison. */
static inline void soft_matmul(const uint32_t *a, const uint32_t *b,
                               uint32_t *c, unsigned n)
{
    for (unsigned i = 0; i < n; i++)
        for (unsigned j = 0; j < n; j++) {
            uint32_t acc = 0;
            for (unsigned k = 0; k < n; k++)
                acc += a[i * n + k] * b[k * n + j];
            c[i * n + j] = acc;
        }
}

#endif /* SOC_H */
