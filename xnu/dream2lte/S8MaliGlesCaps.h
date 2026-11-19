/* SPDX-License-Identifier: GPL-2.0-only
 * Read-only Panfrost capability ABI for the G71 on SM-G955F.
 * Register definitions follow Linux Panfrost, revision 141b164bbe18.
 */
#if defined(DREAMLTE) && DREAM2LTE_BRINGUP
namespace S8Mali {
enum GpuProperty {
	GpuProduct = 0, GpuRevision = 1, GpuShaderMask = 2, GpuTilerMask = 3,
	GpuL2Mask = 4, GpuStackMask = 5, GpuAddressSpaces = 6, GpuJobSlots = 7,
	GpuFirstJobFeature = 22, GpuCoreGroups = 38, GpuThreadTLS = 39,
	GpuAFBC = 40, GpuSelectedCoherency = 44,
};

static uint64_t readRegisterPair(unsigned low) {
	return rd(low) | ((uint64_t)rd(low + 4) << 32);
}

static bool getGpuProperty(uint64_t param, uint64_t &out) {
	/* Panfrost DRM UAPI indices 0–21. The switch handles fields that
	 * differ from a single register and the features derived by Linux. */
	static const unsigned offsets[] = {
		0x000, 0x000, 0x100, 0x110, 0x120, 0xe00, 0x018, 0x01c,
		0x004, 0x008, 0x00c, 0x010, 0x014, 0x0ac, 0x0a0, 0x0a4,
		0x0a8, 0x300, 0x0b0, 0x0b4, 0x0b8, 0x0bc,
	};
	switch (param) {
	case GpuProduct:
		out = rd(0) >> 16;
		break;
	case GpuRevision:
		out = rd(0) & 0xffff;
		break;
	case GpuShaderMask:
	case GpuTilerMask:
	case GpuL2Mask:
	case GpuStackMask:
		out = readRegisterPair(offsets[param]);
		break;
	case GpuSelectedCoherency:
		out = 31; /* COHERENCY_NONE. Driver uses explicit cache maintenance. */
		break;
	case GpuCoreGroups:
		out = __builtin_popcountll(readRegisterPair(offsets[GpuL2Mask]));
		break;
	case GpuThreadTLS:
		out = rd(0x310);
		break;
	case GpuAFBC:
		out = rd(0x04c);
		break;
	default:
		if (param < GpuFirstJobFeature)
			out = rd(offsets[param]);
		else if (param < GpuCoreGroups)
			out = rd(0x0c0 + 4 * (unsigned)(param - GpuFirstJobFeature));
		else
			return false;
	}
	return true;
}

static void logGlesCaps() {
	uint64_t product, revision, shaders, tilers, groups, spaces, slots;
	getGpuProperty(GpuProduct, product);
	getGpuProperty(GpuRevision, revision);
	getGpuProperty(GpuShaderMask, shaders);
	getGpuProperty(GpuTilerMask, tilers);
	getGpuProperty(GpuCoreGroups, groups);
	getGpuProperty(GpuAddressSpaces, spaces);
	getGpuProperty(GpuJobSlots, slots);
	IOLog("S8 MALI: GLES product=%llx rev=%llx shader=%llx tiler=%llx groups=%llu as=%llx js=%llx\n",
	    product, revision, shaders, tilers, groups, spaces, slots);
}
} /* namespace S8Mali */
#endif
