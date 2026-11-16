# SM-G955F / dream2lte adaptation

Status: standalone display probe confirmed on the SM-G955F. XNU has not
run on the phone.

The user reports a European Exynos S8+, TWRP, and a One UI 8 port.
The supplied folder also includes universal8895-Repartitioner-80000.zip.
Its presence does not prove it was used. The current partition layout must
be read from this device; never use the donor's sda7/sda24 assumptions.

Implemented: read-only TWRP evidence collector, separate ADT board identity,
and opt-in conservative loader mode that skips peripheral probes.
The legacy DREAMLTE kernel implementation is still the donor. No new
memory map, GPU, display-panel or touch support has been established.

## Read-only evidence (2026-11-16)

- SM-G955F rev05, TWRP 3.7.1_12-0. BOOT = sda7 (40 MiB), RECOVERY =
  sda8, USERDATA = sda28; sda24 is OMR. The S8 install instructions would
  target the wrong partition if followed literally.
- The 2 GiB high RAM bank matches the donor. TWRP reports bootloader
  framebuffer 0xcc000000 and 5760-byte stride at 32 bpp. The live DT
  puts DECON at 0x12860000 and reports mode 2 and trigger mode 0.
- TWRP dmesg reports ramoops length 0x8000 at 0x92000000 and identifies
  an FTS touch driver on I2C 5 at 0x49. The donor's hardcoded pstore
  addresses and touch-controller setup cannot be assumed compatible.
- A complete One UI BOOT backup was copied to the private capture
  directory. Its SHA-256 matches the live partition.
- The local display probe preserves the BOOT ramdisk and DTBH. The builder
  verified its ARM64 header, exact DTBH preservation, and size against BOOT.
  The probe writes three color bands to the framebuffer and triggers DECON.
  It contains no storage or ramoops access and does not enter iOS.

## On-device display test (2026-11-16)

- Transferred the probe, checked its SHA-256, wrote it to BOOT only, and
  verified the expected SHA-256 of the complete partition before reboot.
- User observed the color bands and returned to TWRP. This confirms the
  S-Boot handoff and the probe's framebuffer/DECON path on this handset.
- Restored the full One UI BOOT backup in TWRP. The SHA-256 of the restored
  partition matches the original. The user confirmed One UI booted.

## First XNU trial preparation

- Built the S8+ loader locally for Cortex-A53. This variant suppresses the
  donor's ramoops writes and does not link its peripheral probes.
- Generated the S8+ Apple Device Tree with the verified framebuffer and
  a 512 MiB window within the high RAM bank. It has no internal UFS node.
- The XNU workflow now supports `target_board=dream2lte`. This selection
  disables the XNU assembly entry marker and console writes into the donor's
  ramoops range. Use `xnu-kernel-dream2lte`, never the generic S8 artifact,
  for this trial. The workflow selects Xcode 16 on macos-15 and reuses patch
  0083 for the updated IORPC interface. This kernel build is pending.
- Fixed initial boot arguments for packaging: `debug=0xc serial=3 cpus=1
  keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1
  wdt_test=no_user_checkin a5touchprobe=0 s8touch=0 a5keys=0
  sweetbattery=0 sweetfb_native=1`. Do not supply an iOS root or a storage
  boot argument. The test targets the first on-screen XNU messages.
