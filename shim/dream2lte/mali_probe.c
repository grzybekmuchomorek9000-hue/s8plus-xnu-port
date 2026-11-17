/* SPDX-License-Identifier: GPL-2.0-only */
#include "mali_probe.h"

/* SM-G955F DT and Samsung Exynos8895 pmucal_local: G3D on sequence. */
#define G3D_CONFIG       0x164840e0UL
#define G3D_STATUS       0x164840e4UL
#define G3D_MUX          0x13800100UL
#define G3D_PLL_CON0     0x13800140UL
#define G3D_PLL_CON2     0x13800148UL
#define MALI_GPU_ID      0x13900000UL
#define G3D_POWER_MASK   0xfu
#define G3D_SELECT       (1u << 4)
#define POWER_TIMEOUT    1300000UL /* 50 ms at 26 MHz */

static uint32_t read32(uintptr_t address)
{
	return *(volatile uint32_t *)address;
}

static void write32(uintptr_t address, uint32_t value)
{
	*(volatile uint32_t *)address = value;
	__asm__ volatile("dsb sy" ::: "memory");
}

static uint64_t ticks(void)
{
	uint64_t value;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(value));
	return value;
}

static uint32_t wait_power(uint32_t expected)
{
	uint64_t started = ticks();
	uint32_t status;
	do {
		status = read32(G3D_STATUS);
		if ((status & G3D_POWER_MASK) == expected)
			break;
	} while (ticks() - started < POWER_TIMEOUT);
	return status;
}

struct dream2lte_mali_result dream2lte_mali_probe(void)
{
	uint32_t status = read32(G3D_STATUS);
	struct dream2lte_mali_result result = {status, status, UINT32_MAX, status};
	if ((status & G3D_POWER_MASK) == G3D_POWER_MASK) {
		result.gpu_id = read32(MALI_GPU_ID);
		return result;
	}
	uint32_t config = read32(G3D_CONFIG);
	if ((status | config) & G3D_POWER_MASK)
		return result; /* Domain already transitioning. */

	uint32_t pll2 = read32(G3D_PLL_CON2);
	uint32_t pll0 = read32(G3D_PLL_CON0);
	uint32_t mux = read32(G3D_MUX);
	write32(G3D_PLL_CON2, 0x30000003);
	write32(G3D_PLL_CON0, pll0 | G3D_SELECT);
	write32(G3D_MUX, mux | G3D_SELECT);
	write32(G3D_CONFIG, config | G3D_POWER_MASK);
	result.powered_status = wait_power(G3D_POWER_MASK);
	if ((result.powered_status & G3D_POWER_MASK) == G3D_POWER_MASK)
		result.gpu_id = read32(MALI_GPU_ID);

	write32(G3D_CONFIG, config);
	result.final_status = wait_power(0);
	if (!(result.final_status & G3D_POWER_MASK)) {
		write32(G3D_MUX, mux);
		write32(G3D_PLL_CON0, pll0);
		write32(G3D_PLL_CON2, pll2);
	}
	return result;
}
