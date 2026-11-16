# SM-G955F / dream2lte adaptation

Status: standalone display probe and handoff loader execution confirmed on
the SM-G955F. V3 reached the XNU entry address in emulation and awaits a
device trial.

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
  for this trial. The workflow selects Xcode 16 on macos-15-intel and reuses patch
  0083 for the updated IORPC interface.
- Fixed initial boot arguments for packaging: `debug=0xc serial=3 cpus=1
  keepsyms=1 -enable_kprintf_spam -noprogress wdt=-1
  wdt_test=no_user_checkin a5touchprobe=0 s8touch=0 a5keys=0
  sweetbattery=0 sweetfb_native=1`. Do not supply an iOS root or a storage
  boot argument. The test targets the first on-screen XNU messages.
- Code published to https://github.com/grzybekmuchomorek9000-hue/s8plus-xnu-port
  with the user's authorization. The Intel run 43920842378 succeeded. No
  phone captures or BOOT backups were staged for GitHub.
- Assembled `research/dream2lte-capture/s8plus-xnu-handoff.img`
  (16,863,248 bytes), SHA-256
  `cc863856e97d96791a071f0130f167436ff543b68863a37bc71746bfd6f7c459`.
  Verified the CI kernel checksum, original phone DTBH, ARM64 loader
  header, partition fit, and storage-free test settings. The disassembler
  reported a single apparent LSE opcode inside the K256 SHA-256 constant
  table, not executable code.
- Downloaded and verified iPhone X iOS 16.7.16 build 20H392 and extracted
  OS.dmg, SystemOS.dmg, AppOS.dmg and d22ap.adt in `research/ipsw-x`.
  The HFS+ system image requires a Linux environment. WSL is not installed
  on this Windows host. These Apple files are excluded from Git.

## First XNU attempt on device

- Confirmed the phone in TWRP and matched its BOOT SHA-256 to the One UI
  backup. Wrote the 16,863,248-byte handoff image and verified all of BOOT
  before rebooting. Only BOOT was written.
- The phone was seen again in TWRP. Restored One UI BOOT from the backup and
  confirmed the restored SHA-256 matched the original. Rebooted normally.
- No file in TWRP's pstore. The user's photo shows a short, corrupted
  yellow/white/red framebuffer console. This is the handoff loader's
  output. The photo does not establish that XNU executed.

## Loader correction after the first device attempt

- Confirmed that the Windows build's Zig objcopy output placed sections
  differently from their ELF load addresses. An ARM64 emulation reached a
  branch into zero bytes at the compiled FDT reader address. The build now
  flattens ELF PT_LOAD segments explicitly and keeps their exact offsets.
- The ARM64 wrapper also aligns the C loader to 4 KiB, required by its
  page-relative loads and aligned vector table, and preserves the S-Boot DTB
  pointer. It reports the original S-Boot load address on the screen.
- V3 was built locally from the same XNU artifact. SHA-256
  `ccec990568d2d15a40aaad858d06cb99ca259a12c218d9d6fcac43ae2ab47532`.
  Emulated at three load positions, including +0x800. Verified the copied
  loader byte for byte, the DTB pointer, the V3 screen glyphs, and the
  handoff of the real packaged segments and boot arguments to XNU's entry
  address. Emulation stopped at that address before executing XNU.
- Next device test: flash V3 BOOT only, photograph its final screen, return
  to TWRP, and restore the verified One UI BOOT backup.
