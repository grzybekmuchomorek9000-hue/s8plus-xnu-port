# SM-G955F / dream2lte adaptation

Status: iOS 16.7.16 booted from USERDATA to the graphical lock screen on
the SM-G955F. Touch was disabled for this trial.

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

## V3 and read-only UFS trials

- V3 ran on the phone. The photo shows the XNU console, AppleCLCD,
  successful SGI1 on the single A53 core, VM bootstrap completion, and
  registration of Guarded vnode policy (vnguard). One UI BOOT was
  restored in TWRP and verified after V3.
- TWRP reports UFS logical blocks of 4096 bytes and USERDATA at sda28,
  starting at sector 18863104 (512-byte sysfs units). The board DT confirms
  UFS ranges 0x11120000/0x200 and 0x11121100/0x200.
- A second trial enabled the UFS ADT node and selected USERDATA through
  the existing XNU driver with `ufsrw=0 rootrw=0 sweetlog=0`. The photo
  initially showed vnguard, then a later photo showed `BSD root: ufsroot0`
  and repeated rejection of an unknown volume signature by HFS. This
  confirms root device selection and a read from the existing Android
  USERDATA volume. iOS files had not been written there. Restored the
  verified One UI BOOT backup in TWRP and requested a normal reboot.
- An optional diagnostic BOOT image is
  `research/dream2lte-capture/s8plus-xnu-startup-trace.img`.
  It adds `startup_debug=1`; the later photo made this trial unnecessary.
  SHA-256 `19657a958431a2dd4a24f1113a2e336ac33d393daf52f7ded4d090d3a8b04e27`.
  Verified the loader's handoff and parameters in ARM64 emulation.
## iOS 16.7.16 userland images

- Built the iPhone X 20H392 OS, SystemOS and AppOS into the 16 GiB journaled
  HFSX image `research/linux-builder/ios16-root-20H392.img` in a local
  Debian 13 guest. The HFS check passed and the image contains executable
  ARM64 launchd and SpringBoard, the cryptex shared-cache hard link and the
  required log directory ownership. The SHA-256 verified after export to
  Windows is `c2e7f104ca7cf99c756b0df9aba64457e0bd7a77403c4751d0ed5cbdd1af5974`.
- The BOOT candidate `research/dream2lte-capture/s8plus-ios-userland-boot.img`
  uses the V3 loader and XNU CI run 43920842378. SHA-256
  `9264ec8e898c234c305e19c5278183862e5a236eac8764aacfe2e937077e591e`.
  It selects USERDATA with writes enabled, retains one A53 core, and keeps
  the unported S8+ touch controller disabled. The candidate is untested on hardware.
- The full One UI USERDATA backup is separate from the 40 MiB BOOT backup.
  `tools/dream2lte/backup-userdata.py` reads the unmounted, exact-size SM-G955F
  partition through TWRP, can resume a partial local file, and requires the
  final phone and disk SHA-256 to match. Keep backups and iOS images local.

## First iOS userland trial

- One UI's intact BOOT hash matched both its local backup and the BOOT member
  in the available dream2lte ROM installer. The user authorized proceeding
  without completing the USERDATA backup after repeated USB disconnections.
- In TWRP, wrote the 16 GiB HFSX image to USERDATA sda28 and verified all 128
  chunks as well as the SHA-256 of the complete image extent. Wrote the
  padded iOS BOOT candidate to sda7 and verified the whole 40 MiB partition
  with SHA-256 `34953193e01b50885437e90193010a381ec5d27bf0580a31baae0f01ab3774e1`.
  RECOVERY was left intact. Requested a normal reboot after a final BOOT
  readback.
- The first post-reboot photo shows iOS userspace running, including
  `xpcproxy` execution and AppleKeyStore calls from `securityd`,
  `bluetoothd` and other system processes. This confirms filesystem
  access and userspace execution.
- The user reported the Apple logo after the verbose text. A subsequent
  photo shows the graphical iOS lock screen with date, time, battery
  indicator and a camera shortcut. The displayed date was incorrect.
  Unlock, touch, and GPU acceleration were not verified.
- If diagnostics are needed in TWRP, read the console ring from the last
  1025 4 KiB blocks of USERDATA sda28.
- The captured DT lists `tsp_stm/fts8cd56_dream2.fw` and a 1440×2960
  coordinate range for the controller at 0x49 on the 0x108e0000 HSI2C
  bus. Use these device values when investigating touch.
