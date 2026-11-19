/* SPDX-License-Identifier: GPL-2.0-only
 * Opt-in IOKit interface for premultiplied BGRA source-over. A client
 * can only submit geometry and handles allocated on its own connection.
 */
#if defined(DREAMLTE) && DREAM2LTE_BRINGUP
class S8MaliCompositor : public IOService {
	OSDeclareDefaultStructors(S8MaliCompositor);
public:
	IOLock *hardwareLock;
	IOReturn newUserClient(task_t owningTask, void *securityID, UInt32 type,
	    OSDictionary *properties, IOUserClient **handler) APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(S8MaliCompositor, IOService);

class S8MaliCompositorClient : public IOUserClient {
	OSDeclareDefaultStructors(S8MaliCompositorClient);
	enum { BufferCount = 24 };
	struct Slot {
		S8Mali::RenderBuffer buffer;
		IOMemoryMap *mapping;
	};

	task_t fTask;
	IOLock *fLock;
	S8MaliCompositor *fProvider;
	S8Mali::RenderAddressSpace fSpace;
	Slot fSlots[BufferCount];
	bool fClosed, fPinned;
	Slot *lookup(uint64_t handle);
	void releaseResources();
	IOReturn execute(uint32_t selector, const uint64_t *input, uint64_t *output);

public:
	bool initWithTask(task_t owningTask, void *securityID, UInt32 type) APPLE_KEXT_OVERRIDE;
	bool start(IOService *provider) APPLE_KEXT_OVERRIDE;
	void stop(IOService *provider) APPLE_KEXT_OVERRIDE;
	void free() APPLE_KEXT_OVERRIDE;
	IOReturn clientClose() APPLE_KEXT_OVERRIDE;
	IOReturn externalMethod(uint32_t selector, IOExternalMethodArguments *args,
	    IOExternalMethodDispatch *dispatch, OSObject *target, void *reference) APPLE_KEXT_OVERRIDE;
};
OSDefineMetaClassAndStructors(S8MaliCompositorClient, IOUserClient);

IOReturn S8MaliCompositor::newUserClient(task_t task, void *securityID, UInt32 type,
    __unused OSDictionary *properties, IOUserClient **handler)
{
	*handler = NULL;
	if (type != 0) return kIOReturnUnsupported;
	if (IOUserClient::clientHasPrivilege((void *)task, kIOClientPrivilegeAdministrator) != kIOReturnSuccess)
		return kIOReturnNotPrivileged;
	S8MaliCompositorClient *client = new S8MaliCompositorClient;
	if (!client) return kIOReturnNoMemory;
	if (!client->initWithTask(task, securityID, type) || !client->attach(this)) {
		client->release();
		return kIOReturnNoResources;
	}
	if (!client->start(this)) {
		client->detach(this);
		client->release();
		return kIOReturnBusy;
	}
	*handler = client;
	return kIOReturnSuccess;
}

bool S8MaliCompositorClient::initWithTask(task_t task, void *securityID, UInt32 type)
{
	if (!IOUserClient::initWithTask(task, securityID, type)) return false;
	fTask = task;
	fLock = IOLockAlloc();
	return fLock != NULL;
}

bool S8MaliCompositorClient::start(IOService *provider)
{
	S8MaliCompositor *gpu = OSDynamicCast(S8MaliCompositor, provider);
	if (!gpu || !IOUserClient::start(provider) || !gpu->open(this)) return false;
	IOLockLock(gpu->hardwareLock);
	bool ready = S8Mali::ready;
	IOLockUnlock(gpu->hardwareLock);
	if (!ready || !fSpace.init()) {
		gpu->close(this);
		return false;
	}
	fProvider = gpu;
	return true;
}

void S8MaliCompositorClient::stop(IOService *provider)
{
	IOLockLock(fLock);
	fClosed = true;
	releaseResources();
	if (fProvider) {
		fProvider->close(this);
		fProvider = NULL;
	}
	IOLockUnlock(fLock);
	IOUserClient::stop(provider);
}

IOReturn S8MaliCompositorClient::clientClose()
{
	IOLockLock(fLock);
	fClosed = true;
	IOLockUnlock(fLock);
	terminate();
	return kIOReturnSuccess;
}

void S8MaliCompositorClient::releaseResources()
{
	for (Slot &slot : fSlots) {
		OSSafeReleaseNULL(slot.mapping);
		if (!fPinned) fSpace.release(slot.buffer);
	}
	/* A hardware timeout quarantines mappings used by the last job until
	 * reboot. CPU mappings are removed even when the DMA memory is pinned. */
	if (!fPinned) OSSafeReleaseNULL(fSpace.tables);
}

void S8MaliCompositorClient::free()
{
	releaseResources();
	if (fLock) IOLockFree(fLock);
	IOUserClient::free();
}

S8MaliCompositorClient::Slot *S8MaliCompositorClient::lookup(uint64_t handle)
{
	if (!handle || handle > BufferCount) return NULL;
	Slot &slot = fSlots[handle - 1];
	return slot.buffer.memory ? &slot : NULL;
}

IOReturn S8MaliCompositorClient::execute(uint32_t selector, const uint64_t *in, uint64_t *out)
{
	switch (selector) {
	case 0: /* Version, maximum single allocation, number of handles. */
		out[0] = 1;
		out[1] = (S8Mali::RenderPageCount - 2) * S8Mali::PAGE;
		out[2] = BufferCount;
		return kIOReturnSuccess;
	case 1: { /* Allocate and map a CPU buffer on the owning task. */
		if (!in[0] || in[0] > (S8Mali::RenderPageCount - 2) * S8Mali::PAGE || in[1] > 1)
			return kIOReturnBadArgument;
		for (unsigned index = 0; index < BufferCount; ++index) {
			Slot &slot = fSlots[index];
			if (slot.buffer.memory) continue;
			if (!fSpace.allocate(in[0], in[1] == 1, slot.buffer, kIOMemoryKernelUserShared))
				return kIOReturnNoMemory;
			slot.mapping = slot.buffer.memory->createMappingInTask(fTask, 0, kIOMapAnywhere);
			if (!slot.mapping) {
				fSpace.release(slot.buffer);
				return kIOReturnNoMemory;
			}
			out[0] = index + 1;
			out[1] = slot.mapping->getAddress();
			out[2] = slot.buffer.size;
			return kIOReturnSuccess;
		}
		return kIOReturnNoResources;
	}
	case 2: {
		Slot *slot = lookup(in[0]);
		if (!slot) return kIOReturnBadArgument;
		OSSafeReleaseNULL(slot->mapping);
		fSpace.release(slot->buffer);
		return kIOReturnSuccess;
	}
	case 3: { /* Compose front over back into output. Row pitches in bytes. */
		Slot *front = lookup(in[0]), *back = lookup(in[1]), *output = lookup(in[2]);
		if (!front || !back || !output) return kIOReturnBadArgument;
		for (unsigned i = 3; i < 8; ++i)
			if (in[i] > 0xffffffffu) return kIOReturnBadArgument;
		IOLockLock(fProvider->hardwareLock);
		bool stopped = true;
		bool done = S8Mali::ready &&
		    S8Mali::composite(fSpace, front->buffer, back->buffer, output->buffer,
		        (unsigned)in[3], (unsigned)in[4], (unsigned)in[5], (unsigned)in[6],
		        (unsigned)in[7], stopped);
		if (!stopped) {
			fPinned = true;
			S8Mali::ready = false;
		}
		IOLockUnlock(fProvider->hardwareLock);
		return done ? kIOReturnSuccess : kIOReturnNotReady;
	}
	default:
		return kIOReturnUnsupported;
	}
}

IOReturn S8MaliCompositorClient::externalMethod(uint32_t selector, IOExternalMethodArguments *args,
    __unused IOExternalMethodDispatch *dispatch, __unused OSObject *target, __unused void *reference)
{
	static const unsigned inputs[] = {0, 2, 1, 8};
	static const unsigned outputs[] = {3, 3, 0, 0};
	if (current_task() != fTask) return kIOReturnNotPrivileged;
	if (selector >= sizeof(inputs) / sizeof(inputs[0])) return kIOReturnUnsupported;
	if (args->scalarInputCount != inputs[selector] || args->scalarOutputCount != outputs[selector] ||
	    args->structureInputSize || args->structureOutputSize || args->asyncReference)
		return kIOReturnBadArgument;
	IOLockLock(fLock);
	IOReturn result = fClosed || fPinned || !fProvider ? kIOReturnNotReady :
	    execute(selector, args->scalarInput, args->scalarOutput);
	IOLockUnlock(fLock);
	return result;
}

static void sweet_mali_publish_compositor(IOService *parent, IOLock *hardwareLock)
{
	int enabled = 0;
	if (!S8Mali::ready || !S8Mali::compositionVerified ||
	    !PE_parse_boot_argn("s8mali_compositor", &enabled, sizeof(enabled)) || enabled != 1) return;
	S8MaliCompositor *service = new S8MaliCompositor;
	if (!service) return;
	if (service->init() && service->attach(parent)) {
		service->hardwareLock = hardwareLock;
		if (service->start(parent)) {
			service->registerService();
			IOLog("S8 MALI: S8MaliCompositor IOKit service registered\n");
		} else {
			service->detach(parent);
		}
	}
	service->release();
}
#endif
