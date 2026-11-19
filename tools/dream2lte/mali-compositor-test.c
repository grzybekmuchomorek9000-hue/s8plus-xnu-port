/* SPDX-License-Identifier: MIT */
#include <CoreFoundation/CoreFoundation.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>

/* The iPhoneOS SDK exports these IOKit functions without a public header. */
extern CFMutableDictionaryRef IOServiceMatching(const char *name);
extern mach_port_t IOServiceGetMatchingService(mach_port_t mainPort, CFDictionaryRef matching);
extern kern_return_t IOServiceOpen(mach_port_t service, task_port_t task, uint32_t type,
    mach_port_t *connection);
extern kern_return_t IOServiceClose(mach_port_t connection);
extern kern_return_t IOObjectRelease(mach_port_t object);
extern kern_return_t IOConnectCallScalarMethod(mach_port_t connection, uint32_t selector,
    const uint64_t *input, uint32_t inputCount, uint64_t *output, uint32_t *outputCount);

enum { WIDTH = 128, HEIGHT = 96, FRONT_PITCH = 512, BACK_PITCH = 544, OUTPUT_PITCH = 560 };
static const uint32_t untouched = 0xdeadbeef;

struct buffer {
    uint64_t handle;
    uint32_t *pixels;
    size_t words;
};

static int allocate(mach_port_t gpu, unsigned pitch, int writable, struct buffer *buffer)
{
    const uint64_t input[] = { (uint64_t)pitch * HEIGHT, (uint64_t)writable };
    uint64_t output[3] = {0};
    uint32_t count = 3;
    kern_return_t result = IOConnectCallScalarMethod(gpu, 1, input, 2, output, &count);
    if (result != KERN_SUCCESS || count != 3 || !output[0] || !output[1] ||
        output[2] < input[0] || output[2] % 4) {
        fprintf(stderr, "Allocation failed: 0x%x\n", (unsigned)result);
        return 0;
    }
    *buffer = (struct buffer){output[0], (uint32_t *)(uintptr_t)output[1], output[2] / 4};
    return 1;
}

static int free_buffer(mach_port_t gpu, struct buffer *buffer)
{
    if (!buffer->handle)
        return 1;
    uint32_t count = 0;
    return IOConnectCallScalarMethod(gpu, 2, &buffer->handle, 1, NULL, &count) == KERN_SUCCESS;
}

static int check(mach_port_t gpu, const struct buffer *front, const struct buffer *back,
    const struct buffer *output)
{
    for (size_t i = 0; i < output->words; ++i)
        output->pixels[i] = untouched;
    for (unsigned y = 0; y < HEIGHT; ++y) {
        for (unsigned x = 0; x < WIDTH; ++x) {
            front->pixels[y * FRONT_PITCH / 4 + x] = ((x ^ y) & 1) ? 0x80800000u : 0;
            back->pixels[y * BACK_PITCH / 4 + x] = 0xff0000ffu;
        }
    }
    const uint64_t args[] = {front->handle, back->handle, output->handle, WIDTH, HEIGHT,
        FRONT_PITCH, BACK_PITCH, OUTPUT_PITCH};
    uint32_t count = 0;
    kern_return_t result = IOConnectCallScalarMethod(gpu, 3, args, 8, NULL, &count);
    if (result != KERN_SUCCESS) {
        fprintf(stderr, "GPU composition failed: 0x%x\n", (unsigned)result);
        return 0;
    }
    for (size_t i = 0; i < output->words; ++i) {
        size_t y = i / (OUTPUT_PITCH / 4), x = i % (OUTPUT_PITCH / 4);
        uint32_t expected = y < HEIGHT && x < WIDTH ?
            (((x ^ y) & 1) ? 0xff80007fu : 0xff0000ffu) : untouched;
        if (output->pixels[i] != expected) {
            fprintf(stderr, "Bad pixel at %zu,%zu: got %08x, expected %08x\n",
                    x, y, output->pixels[i], expected);
            return 0;
        }
    }
    return 1;
}

int main(void)
{
    mach_port_t service = IOServiceGetMatchingService(MACH_PORT_NULL, IOServiceMatching("S8MaliCompositor"));
    if (!service) {
        fputs("S8MaliCompositor unavailable\n", stderr);
        return 1;
    }
    mach_port_t gpu = MACH_PORT_NULL;
    kern_return_t result = IOServiceOpen(service, mach_task_self(), 0, &gpu);
    IOObjectRelease(service);
    if (result != KERN_SUCCESS) {
        fprintf(stderr, "Opening S8MaliCompositor failed: 0x%x\n", (unsigned)result);
        return 1;
    }

    uint64_t info[3] = {0};
    uint32_t count = 3;
    result = IOConnectCallScalarMethod(gpu, 0, NULL, 0, info, &count);
    struct buffer front = {0}, back = {0}, output = {0};
    int ok = result == KERN_SUCCESS && count == 3 && info[0] == 1 && info[2] >= 3 &&
        allocate(gpu, FRONT_PITCH, 0, &front) && allocate(gpu, BACK_PITCH, 0, &back) &&
        allocate(gpu, OUTPUT_PITCH, 1, &output) && check(gpu, &front, &back, &output);
    int freed_output = free_buffer(gpu, &output);
    int freed_back = free_buffer(gpu, &back);
    int freed_front = free_buffer(gpu, &front);
    result = IOServiceClose(gpu);
    if (!ok || !freed_output || !freed_back || !freed_front || result != KERN_SUCCESS)
        return 1;
    puts("Mali-G71 IOKit composition verified: 12288 pixels and padding");
    return 0;
}
