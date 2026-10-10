/* test_accel.c — drives the matrix accelerator through its APB registers and checks
 * the result against the software reference. Needs MMIO, so it runs on the ISS with
 * the Python accelerator model, and later on the RTL — not on Spike. */
#include <stdint.h>
#include "soc.h"

#define N 4

static uint32_t A[N * N], B[N * N], C_hw[N * N], C_sw[N * N];

int main(void)
{
    uint32_t seed = 12345u;
    for (int i = 0; i < N * N; i++) {
        seed = seed * 1103515245u + 12345u; A[i] = seed;
        seed = seed * 1103515245u + 12345u; B[i] = seed;
    }

    uint32_t params = REG(ACCEL_PARAMS);
    if ((params & 0xFF) != N) return 1;

    accel_matmul(A, B, C_hw, N);
    soft_matmul(A, B, C_sw, N);

    for (int i = 0; i < N * N; i++)
        if (C_hw[i] != C_sw[i]) return 2 + i;
    return 0;
}
