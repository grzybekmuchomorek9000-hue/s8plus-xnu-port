#pragma once

#include <stdint.h>

struct dream2lte_mali_result {
	uint32_t initial_status;
	uint32_t powered_status;
	uint32_t gpu_id;
	uint32_t final_status;
};

struct dream2lte_mali_result dream2lte_mali_probe(void);

struct dream2lte_mali_job_result {
	uint32_t stage;
	uint32_t irq;
	uint32_t exception;
	uint32_t value;
};

struct dream2lte_mali_job_result dream2lte_mali_job_probe(void);
