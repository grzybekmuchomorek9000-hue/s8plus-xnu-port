/* SPDX-License-Identifier: GPL-2.0-only
 * GPU address allocation for XNU-owned render buffers. The job runner
 * may attach a context's leaves only while it exclusively owns the GPU.
 */
#if defined(DREAMLTE) && DREAM2LTE_BRINGUP
namespace S8Mali {
enum { RenderBase = 0x02000000, RenderLeafCount = 32, LeafEntries = PAGE / 8,
       RenderPageCount = RenderLeafCount * LeafEntries, RenderTableBytes = RenderLeafCount * PAGE };

struct RenderAddressSpace;

struct RenderBuffer {
	RenderAddressSpace *owner;
	IOBufferMemoryDescriptor *memory;
	uint32_t startPage, size;

	uint32_t gpuAddress() const { return RenderBase + startPage * PAGE; }
	uintptr_t cpuAddress() const { return (uintptr_t)memory->getBytesNoCopy(); }
};

struct RenderAddressSpace {
	IOBufferMemoryDescriptor *tables;
	uint64_t *leaves;
	uint64_t physical;

	bool init() {
		if (tables) return false;
		tables = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
		    kIODirectionInOut | kIOMemoryPhysicallyContiguous, RenderTableBytes, PAGE);
		if (!tables) return false;
		IOByteCount length = 0;
		physical = tables->getPhysicalSegment(0, &length, kIOMemoryMapperNone);
		leaves = (uint64_t *)tables->getBytesNoCopy();
		if (!leaves || (uintptr_t)leaves % PAGE || !physical || physical % PAGE ||
		    physical > (1ULL << 36) - RenderTableBytes || length < RenderTableBytes) {
			tables->release();
			tables = NULL;
			return false;
		}
		bzero(leaves, RenderTableBytes);
		cleanGpuRange((uintptr_t)leaves, RenderTableBytes);
		return true;
	}

	bool allocate(uint64_t requested, bool writable, RenderBuffer &buffer) {
		if (!tables || buffer.memory || !requested ||
		    requested > (uint64_t)(RenderPageCount - 2) * PAGE) return false;
		unsigned pages = (unsigned)((requested + PAGE - 1) / PAGE);
		unsigned free = 0, found = RenderPageCount;
		for (unsigned i = 0; i < RenderPageCount; ++i) {
			free = leaves[i] ? 0 : free + 1;
			if (free == pages + 2) {
				found = i - pages;
				break;
			}
		}
		if (found == RenderPageCount) return false;
		unsigned size = pages * PAGE;
		IOBufferMemoryDescriptor *memory = IOBufferMemoryDescriptor::inTaskWithOptions(
		    kernel_task, kIODirectionInOut, size, PAGE);
		if (!memory) return false;
		uintptr_t cpu = (uintptr_t)memory->getBytesNoCopy();
		if (!cpu || cpu % PAGE || memory->getLength() < size ||
		    !map(leaves + found, memory, size, writable)) {
			bzero(leaves + found, pages * sizeof(*leaves));
			cleanGpuRange((uintptr_t)(leaves + found), pages * sizeof(*leaves));
			memory->release();
			return false;
		}
		bzero((void *)cpu, size);
		buffer = {this, memory, found, size};
		return true;
	}

	/* Call only after the job runner has retired GPU translations. */
	bool release(RenderBuffer &buffer) {
		if (!buffer.memory || buffer.owner != this) return false;
		unsigned bytes = (buffer.size / PAGE) * sizeof(*leaves);
		bzero(leaves + buffer.startPage, bytes);
		cleanGpuRange((uintptr_t)(leaves + buffer.startPage), bytes);
		buffer.memory->release();
		buffer = {};
		return true;
	}

private:
	static bool map(uint64_t *output, IOBufferMemoryDescriptor *memory, unsigned size, bool writable) {
		const uint64_t flags = GpuDataXN | (writable ? 0x2c1 : 0x241);
		for (unsigned offset = 0; offset < size;) {
			IOByteCount length = 0;
			uint64_t page = memory->getPhysicalSegment(offset, &length, kIOMemoryMapperNone);
			if (!page || page % PAGE || !length || length % PAGE) return false;
			if (length > size - offset) length = size - offset;
			if (page > (1ULL << 36) - length) return false;
			for (unsigned i = 0; i < length; i += PAGE)
				output[(offset + i) / PAGE] = (page + i) | flags;
			offset += (unsigned)length;
		}
		cleanGpuRange((uintptr_t)output, (size / PAGE) * sizeof(*output));
		return true;
	}
};
} /* namespace S8Mali */
#endif
