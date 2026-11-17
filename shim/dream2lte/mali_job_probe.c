/* SPDX-License-Identifier: GPL-2.0-only */
#include "mali_probe.h"

/* G71 job manager and legacy LPAE, per Linux Panfrost and Mesa genxml v6. */
#define MALI_BASE           0x13900000UL
#define MALI_G71_ID         0x60a00001u
#define MMU_AS0             0x2400u
#define JOB_MMIO            0x1800u
#define JOB_RAW             0x1000u
#define JOB_CLEAR           0x1004u
#define JOB_ACTIVE          0x1010u
#define MMU_RAW             0x2000u
#define MMU_CLEAR           0x2004u
#define WAIT_TICKS          1300000UL
#define PAGE               4096UL

/* Top of the device's verified high RAM bank, above XNU's RAM window. */
#define SCRATCH_PA          0x8fffe0000UL
#define DESCRIPTOR_VA       0x1000u
#ifdef DREAM2LTE_MALI_CLEAR
#define OUTPUT_OFFSET       0x800u
#define JOB_TYPE            9u
#define EXPECTED_OUTPUT     0xff00ff00u
#else
#define OUTPUT_OFFSET       0x100u
#define JOB_TYPE            2u
#define EXPECTED_OUTPUT     0u
#endif
#define OUTPUT_INITIAL      0xdeadbeefu

static uint32_t gpu_read(unsigned offset)
{
	return *(volatile uint32_t *)(MALI_BASE + offset);
}

static void gpu_write(unsigned offset, uint32_t value)
{
	*(volatile uint32_t *)(MALI_BASE + offset) = value;
	__asm__ volatile("dsb sy" ::: "memory");
}

static uint64_t stamp(void)
{
	uint64_t value;
	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(value));
	return value;
}

static int wait_bits(unsigned reg, uint32_t mask, uint32_t expected)
{
	uint64_t start = stamp();
	do {
		if ((gpu_read(reg) & mask) == expected)
			return 1;
	} while (stamp() - start < WAIT_TICKS);
	return 0;
}

static void prepare_job(void)
{
	volatile uint64_t *tables = (volatile uint64_t *)SCRATCH_PA;
	for (unsigned i = 0; i < 5 * PAGE / sizeof(*tables); i++)
		tables[i] = 0;
	for (unsigned level = 0; level < 3; level++)
		tables[level * PAGE / 8] = (SCRATCH_PA + (level + 1) * PAGE) | 3;
	/* Legacy Mali L3 uses type 1, outer shareable, with read/write permissions. */
	tables[3 * PAGE / 8 + 1] = (SCRATCH_PA + 4 * PAGE) | 0x2c1u;
	volatile uint32_t *job = (volatile uint32_t *)(SCRATCH_PA + 4 * PAGE);
	job[4] = (1u << 16) | (JOB_TYPE << 1) | 1u;
#ifdef DREAM2LTE_MALI_CLEAR
	job[10] = 0x1201;
	volatile uint32_t *fb = job + 0x200 / 4;
	fb[4] = 0x1400;
	fb[8] = fb[10] = 15 | (15u << 16);
	fb[11] = (2u << 6) | (8u << 9) | (1u << 24);
	fb[12] = 1u << 16; /* G71 rejects the legacy empty-tile write flag. */
	volatile uint32_t *rt = job + 0x280 / 4;
	rt[0] = 1u << 26;
	rt[1] = 1u | (19u << 3) | (2u << 10) | (0x688u << 16) | (1u << 31);
	rt[8] = DESCRIPTOR_VA + OUTPUT_OFFSET;
	rt[10] = 64;
	for (unsigned i = 12; i < 16; i++) rt[i] = EXPECTED_OUTPUT;
	job[0x400 / 4] = job[0x480 / 4] = 0x00800080;
	for (unsigned i = OUTPUT_OFFSET / 4; i < PAGE / 4; i++) job[i] = OUTPUT_INITIAL;
#else
	job[8] = DESCRIPTOR_VA + OUTPUT_OFFSET;
	job[10] = 3;                        /* Write Value Type: zero */
	job[OUTPUT_OFFSET / 4] = OUTPUT_INITIAL;
#endif
	for (uintptr_t address = SCRATCH_PA; address < SCRATCH_PA + 5 * PAGE; address += 64)
		__asm__ volatile("dc cvac, %0" :: "r"(address) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
}

struct dream2lte_mali_job_result dream2lte_mali_job_probe(void)
{
	struct dream2lte_mali_job_result result = {0, 0, 0, OUTPUT_INITIAL, 0, 0};
	/* Use only an idle GPU and an unconfigured address space. */
	if (gpu_read(0) != MALI_G71_ID || ((gpu_read(0x14) >> 8) & 0xff) < 36 ||
	    !(gpu_read(0x18) & 1u) || gpu_read(JOB_ACTIVE) || gpu_read(JOB_RAW) ||
	    gpu_read(MMU_RAW) || (gpu_read(MMU_AS0 + 0x28) & 1u) ||
	    gpu_read(MMU_AS0) || gpu_read(MMU_AS0 + 4) ||
	    gpu_read(MMU_AS0 + 0x30) > 1 || gpu_read(MMU_AS0 + 0x34))
		return result;

	unsigned slot = 0;
	uint32_t slots = gpu_read(0x1c);
	for (; slot < 3; slot++) {
		if ((slots & (1u << slot)) && (gpu_read(0xc0 + slot * 4) & (1u << JOB_TYPE)))
			break;
	}
	unsigned js = JOB_MMIO + slot * 0x80;
	if (slot == 3 || gpu_read(js + 0x60))
		return result;

	const unsigned mmu_saved[] = {0, 4, 8, 12, 0x30, 0x34};
	const unsigned job_saved[] = {0x40, 0x44, 0x50, 0x54, 0x58};
	uint32_t mmu_values[6], job_values[5];
	for (unsigned i = 0; i < 6; i++)
		mmu_values[i] = gpu_read(MMU_AS0 + mmu_saved[i]);
	for (unsigned i = 0; i < 5; i++)
		job_values[i] = gpu_read(js + job_saved[i]);

	uint32_t l2 = gpu_read(0x120);
	uint32_t shader = gpu_read(0x100);
	if (!l2 || !shader || gpu_read(0x124) || gpu_read(0x104) ||
	    gpu_read(0x220) || gpu_read(0x200))
		return result;
	uint32_t ready = gpu_read(0x140);
	uint32_t affinity = ready ? ready : (shader & (0u - shader));
	uint32_t enable_l2 = l2 & ~gpu_read(0x160);
	uint32_t enable_shader = affinity & ~ready;
	result.stage = 1;
	if (enable_l2)
		gpu_write(0x1a0, enable_l2);
	if (!wait_bits(0x160, l2, l2)) {
		enable_shader = 0;
		goto power_off;
	}
	if (enable_shader)
		gpu_write(0x180, enable_shader);
	if (!wait_bits(0x140, affinity, affinity))
		goto power_off;

	prepare_job();
	result.stage = 2;
	gpu_write(MMU_AS0, (uint32_t)SCRATCH_PA | 7u);
	gpu_write(MMU_AS0 + 4, (uint32_t)(SCRATCH_PA >> 32));
	gpu_write(MMU_AS0 + 8, 0x00888d88);
	gpu_write(MMU_AS0 + 12, 0);
	gpu_write(MMU_AS0 + 0x30, 0);      /* Legacy LPAE */
	gpu_write(MMU_AS0 + 0x34, 0);
	gpu_write(MMU_AS0 + 0x18, 1);     /* UPDATE */
	if (!wait_bits(MMU_AS0 + 0x28, 1, 0))
		return result;

	result.stage = 3;
	gpu_write(js + 0x40, DESCRIPTOR_VA);
	gpu_write(js + 0x44, 0);
	gpu_write(js + 0x50, affinity);
	gpu_write(js + 0x54, 0);
	gpu_write(js + 0x58, (8u << 16) | (3u << 12) | (3u << 8));
	gpu_write(js + 0x60, 1);          /* START */
	uint32_t irq_mask = 0x10001u << slot;
	uint64_t start = stamp();
	do {
		result.irq = gpu_read(JOB_RAW) & irq_mask;
	} while (!result.irq && !gpu_read(MMU_RAW) && stamp() - start < WAIT_TICKS);

	if (gpu_read(js + 0x60)) {
		gpu_write(js + 0x60, 0);     /* Cancel a job still in the next slot. */
		if (gpu_read(js + 0x60))
			return result;
	}
	if (gpu_read(JOB_ACTIVE) & irq_mask) {
		gpu_write(js + 0x20, 3);     /* HARD_STOP only the probe's job. */
		if (!wait_bits(JOB_ACTIVE, irq_mask, 0))
			return result;           /* Leave the job's private mapping intact. */
	}
	if (result.irq)
		gpu_write(JOB_CLEAR, result.irq);
	uint32_t mmu_fault = gpu_read(MMU_RAW) & 0x10001u;
	if (mmu_fault)
		gpu_write(MMU_CLEAR, mmu_fault);
	volatile uint32_t *job = (volatile uint32_t *)(SCRATCH_PA + 4 * PAGE);
	for (uintptr_t address = (uintptr_t)job; address < (uintptr_t)job + PAGE; address += 64)
		__asm__ volatile("dc ivac, %0" :: "r"(address) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
	result.exception = job[0];
	result.fault_low = job[2];
	result.fault_high = job[3];
	result.value = job[OUTPUT_OFFSET / 4];
	unsigned errors = 0;
#ifdef DREAM2LTE_MALI_CLEAR
	for (unsigned i = 0; i < (PAGE - OUTPUT_OFFSET) / 4; i++)
		if (job[OUTPUT_OFFSET / 4 + i] != (i < 256 ? EXPECTED_OUTPUT : OUTPUT_INITIAL)) errors++;
#endif
	if (result.irq == (1u << slot) && result.exception == 1 && result.value == EXPECTED_OUTPUT && !errors)
		result.stage = 4;
	for (unsigned i = 0; i < 5; i++)
		gpu_write(js + job_saved[i], job_values[i]);

	for (unsigned i = 0; i < 6; i++)
		gpu_write(MMU_AS0 + mmu_saved[i], mmu_values[i]);
	gpu_write(MMU_AS0 + 0x18, 1);
	if (!wait_bits(MMU_AS0 + 0x28, 1, 0))
		return result;
power_off:
	if (enable_shader) {
		gpu_write(0x1c0, enable_shader);
		if (!wait_bits(0x140, enable_shader, 0))
			return result;
	}
	if (enable_l2) {
		gpu_write(0x1e0, enable_l2);
		(void)wait_bits(0x160, enable_l2, 0);
	}
	return result;
}
