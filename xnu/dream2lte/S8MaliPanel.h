/* SPDX-License-Identifier: GPL-2.0-only
 * Internal to the dream2lte framebuffer. Map only the observed full-frame
 * IOSurface layout and the panel published by the framebuffer at startup.
 * Called under fPresentLock. No userspace job, address or shader interface.
 */
#if defined(DREAMLTE) && DREAM2LTE_BRINGUP
namespace S8Mali {
enum { PanelW = 1440, PanelH = 2960, SourcePitch = 5888, PanelPitch = 5760,
       LeafBytes = 2 * TABLES_PER_SURFACE * PAGE };
static const uint64_t PanelPhys = 0xcc000000;
static const uint64_t DataXN = 3ULL << 53;
static IOBufferMemoryDescriptor *leafMemory, *mappedSource;
static uint64_t leafPhysical;
static bool panelEnabled, panelHeld;
static unsigned frames;

static unsigned presentationHz(bool copiedOnGpu, bool copiedOnCpu, unsigned current) {
	if (panelHeld || copiedOnCpu) return 10;
	return copiedOnGpu ? 30 : current;
}

static void cleanRange(uintptr_t start, unsigned size) {
	for (uintptr_t a = start; a < start + size; a += 64)
		__asm__ volatile("dc cvac, %0" :: "r"(a) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
}

/* Read-only GPU access to a wired pixel buffer. The caller owns the table,
 * and may only change it after jobs referring to its previous pages stop. */
static bool mapPixelBuffer(uint64_t *leaf, unsigned count, IOBufferMemoryDescriptor *buffer,
    unsigned size) {
	if (!size || size % PAGE || size / PAGE > count || size > buffer->getLength())
		return false;
	bzero(leaf, count * sizeof(*leaf));
	for (unsigned off = 0; off < size;) {
		IOByteCount n = 0;
		uint64_t p = buffer->getPhysicalSegment(off, &n, kIOMemoryMapperNone);
		if (!p || p % PAGE || !n || n % PAGE) return false;
		if (n > size - off) n = size - off;
		if (p > (1ULL << 36) - n ||
		    (p < PanelPhys + PanelPitch * PanelH && p + n > PanelPhys)) return false;
		for (unsigned pos = 0; pos < n; pos += PAGE)
			leaf[(off + pos) / PAGE] = (p + pos) | DataXN | 0x241;
		off += (unsigned)n;
	}
	cleanRange((uintptr_t)leaf, count * sizeof(*leaf));
	return true;
}

static void enablePanel(uint64_t phys, unsigned w, unsigned h, unsigned stride) {
	int opt = 0;
	if (!ready || !PE_parse_boot_argn("s8mali_present", &opt, sizeof(opt)) || opt != 1 ||
	    phys != PanelPhys || w != PanelW || h != PanelH || stride != PanelPitch) return;
	leafMemory = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
	    kIODirectionInOut | kIOMemoryPhysicallyContiguous, LeafBytes, PAGE);
	if (!leafMemory) return;
	IOByteCount n = 0;
	leafPhysical = leafMemory->getPhysicalSegment(0, &n, kIOMemoryMapperNone);
	uintptr_t base = (uintptr_t)leafMemory->getBytesNoCopy();
	if (!leafPhysical || leafPhysical > (1ULL << 36) - LeafBytes || (leafPhysical % PAGE) ||
	    !base || (base % PAGE) || n < LeafBytes) {
		leafMemory->release(); leafMemory = NULL;
		return;
	}
	bzero((void *)base, LeafBytes);
	uint64_t *panel = (uint64_t *)(base + LeafBytes / 2);
	for (unsigned off = 0; off < PanelH * PanelPitch; off += PAGE)
		panel[off / PAGE] = (PanelPhys + off) | DataXN | 0x281; /* write-only */
	cleanRange(base, LeafBytes);
	panelEnabled = true;
	IOLog("S8 MALI: panel presenter armed %ux%u src-bpr=%u dst-bpr=%u\n",
	    PanelW, PanelH, SourcePitch, PanelPitch);
}

/* IOBufferMemoryDescriptor allocated by SweetSurface is wired. Keep a
 * reference as long as its leaf entries can be used by a GPU job. */
static bool mapSource(IOBufferMemoryDescriptor *src) {
	if (src == mappedSource) return true;
	if (mappedSource) { mappedSource->release(); mappedSource = NULL; }
	uint64_t *leaf = (uint64_t *)leafMemory->getBytesNoCopy();
	const unsigned size = SourcePitch * PanelH;
	static_assert(size % PAGE == 0 && size <= TABLES_PER_SURFACE * 512 * PAGE, "source range");
	if (!mapPixelBuffer(leaf, TABLES_PER_SURFACE * 512, src, size)) return false;
	src->retain();
	mappedSource = src;
	return true;
}

static bool presentPanel(SweetSurface *surface, uintptr_t panel, int sx, int sy,
    int dx, int dy, int w, int h) {
	/* A job whose stop could not be confirmed still owns its mappings. */
	if (panelHeld) return true;
	if (!panelEnabled || !ready || sx || sy || dx || dy || w != PanelW || h != PanelH ||
	    surface->width != PanelW || surface->height != PanelH || surface->offset ||
	    surface->bytesPerRow != SourcePitch || surface->planeCount || surface->parentID ||
	    surface->pixelFormat != 0x42475241 || surface->bytesPerElement != 4 ||
	    surface->elementWidth != 1 || surface->elementHeight != 1) return false;
	IOBufferMemoryDescriptor *src = OSDynamicCast(IOBufferMemoryDescriptor, surface->memory);
	if (!src || src->getLength() != SourcePitch * PanelH) return false;
	uintptr_t pixels = (uintptr_t)src->getBytesNoCopy();
	if (!pixels || (pixels % PAGE)) return false;
	uint64_t start = ticks();
	if (!mapSource(src)) {
		panelEnabled = false;
		IOLog("S8 MALI: panel source map failed, using CPU\n");
		return false;
	}
	cleanRange(pixels, SourcePitch * PanelH);
	const Rectangle rect = {PANEL_SRC, PANEL_DST, SourcePitch, PanelPitch, PanelW, PanelH, 0, 0};
	bool quiesced = true;
	bool ok = run(PanelCopy, 0, &rect, leafPhysical, &quiesced);
	if (ok && (frames < 3 || frames % 120 == 0)) {
		/* Sample corners and centre of this frame, including the last row.
		 * The scanout's kernel mapping uses write-combine cache mode. */
		const unsigned x[] = {0, PanelW-1, PanelW/2, 0, PanelW-1};
		const unsigned y[] = {0, 0, PanelH/2, PanelH-1, PanelH-1};
		for (unsigned i = 0; i < 5; i++)
			if (*(volatile uint32_t *)(pixels + y[i]*SourcePitch + 4*x[i]) !=
			    *(volatile uint32_t *)(panel + y[i]*PanelPitch + 4*x[i])) ok = false;
		IOLog("S8 MALI: panel frame=%u samples-ok=%u total-us=%llu\n",
		    frames + 1, ok, (ticks() - start) / 26);
	}
	if (!ok) {
		panelEnabled = false;
		panelHeld = !quiesced;
		ready = false;
		if (quiesced) { mappedSource->release(); mappedSource = NULL; }
		IOLog("S8 MALI: panel disabled, DMA quiesced=%u\n", quiesced);
		return panelHeld;
	}
	frames++;
	return true;
}
} /* namespace S8Mali */
#endif
