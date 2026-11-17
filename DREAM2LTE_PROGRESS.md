# SM-G955F / dream2lte adaptation

Status: iOS 16.7.16 booted from USERDATA to the Home Screen on the
SM-G955F. The FTS8 touch trial reached the Home Screen and opened Settings
in response to user input.

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

## FTS8 touch trial

- TWRP confirmed digital ID `36 70` on I2C 5 at 0x49 and input axes
  0–1439 and 0–2959. The captured DT sets AVDD (LDO35) to 3.3 V and IO
  (LDO34) to 1.85 V. The order and FIFO protocol were checked against
  Samsung's `stm/fts_ts.c` in exynos-linux-stable/dreamlte
  at `baef434858eda94d675e56b9f47a1e4fb6f0b53e`.
- The opt-in loader powers the two rails and prepares USI09 before setting
  `s8touch=1`. The XNU worker identifies the chip and sends contacts using
  the existing iOS HID service. Local samples passed contact, motion,
  release and input bounds checks. ARM64 emulation checked power sequencing
  and the failed-readback exit.
- GitHub Actions run `43974446505` built and packaged the kernel from
  `aeac4945e666a55a4156c1dc2bcbee878419a9a1`. Local candidate
  `s8plus-ios-fts8-boot.img` SHA-256
  `9b8ec10bad50a6817207320819019e92b7fce6144358aa05c08c976c3ec83d94`.
  In TWRP the complete BOOT partition readback matched SHA-256
  `d30178f6bc5875cb3ba50e9bc7de6b5a7105dafdf9504b62662b209308284ac6`.
  The phone was rebooted for a physical touch test. The user sent a photo
  of the iOS Home Screen after being asked to swipe up from the lock
  screen, then a photo of the open Settings app after being asked to tap
  its icon. This verifies navigation by swipe and tap on the SM-G955F.
  The known lock-screen BOOT remains available locally for rollback.

## UI performance and Mali

- The user reported 10–30 second delays opening or scrolling menus. The
  one-core FTS8 console ring shows contact-down and contact-up events
  reaching HID, an HFS swap-file error, and repeated relaunches of services
  including bluetoothd and fairplayd.H2. It gives no direct input-latency
  measurement. The existing native software display bridge targets 10 Hz.
- Prepared independent BOOT trials with `--a53-cores 2` and
  `--present-hz 20`. Both use the working FTS8 loader, XNU from run
  `43974446505`, and the current iOS USERDATA. Binary comparison verified
  the selected boot argument, a fresh ADT random seed, and the recomputed
  Android BOOT digest as the only changes relative to the working image.
- Wrote and verified the two-core BOOT on the device. The padded partition
  SHA-256 is `77b6ee3b7d2644f9561d3274059a5a0d9aefeed98af75be3dc5f2a4333ad96c5`.
  The console reports 2/2 Cortex-A53 CPUs online with SGI1 verified,
  and FTS8 contacts reached HID. The screen froze during the unlock
  animation, so the trial was unstable in use.
- Prepared and flashed a separate `--ram-mib 768` candidate using one
  active core and the same FTS8 driver. Its memory base and current iOS
  partition match the one-core baseline. Padded BOOT SHA-256
  `c83e77c655e6c675fec11bdbf4b7c7752d62163b791aeefa5c55911d230be321`.
  The phone reached the UI, but the user still reported stuttering.
  The one-core 512 MiB FTS8 BOOT is available as the rollback image.
- TWRP identified the GPU at `0x13900000` as Mali Bifrost architecture
  6.0.10. The captured Linux DT also identifies G3D power domain
  `0x164840e0` and the JOB, MMU and GPU interrupts. This is Android driver
  evidence. The iOS bridge currently copies software-rendered frames to the
  display buffer. Mesa Panfrost provides Mali GLES/Vulkan building blocks,
  while providing hardware rendering to native iOS would require an XNU
  driver and an iOS graphics integration layer.

## Mali-G71 power and identification probe

- Built an opt-in ARM64 loader probe using the Exynos8895 G3D sequence
  in Samsung's `pmucal_local_exynos8895.h` (exynos-linux-stable/dreamlte
  commit `baef434858eda94d675e56b9f47a1e4fb6f0b53e`). The probe checks PMU
  status, brings up an initially off G3D domain, reads the Mali GPU ID,
  and restores the former state after a successful power-off wait. It
  records results in the loader console and XNU boot arguments.
- ARM64 emulation checked the complete on/read/off sequence, already-on
  and transitional states, and both power-transition timeout cases.
- Prepared `s8plus-ios-mali-probe-boot.img` SHA-256
  `875301f68bda33e23792f7ae237ac0f150048641220dd43194190a13f60c3b6e`.
  Wrote and verified BOOT with SHA-256
  `7b5aeb15c2b8f8b16a8059bc328f6847b7e23ae33419c2810fcb1223f59a22ed`.
  The physical SM-G955F returned GPU ID `0x60a00001` and G3D status
  `0x0003000f` before and after the read. The domain was already on,
  so this run exercised direct GPU register access without a transition.

## Mali-G71 job manager trial

- Added a separate opt-in WRITE_VALUE command. It uses the legacy
  Panfrost LPAE format and a private mapped page in the verified high RAM
  bank, above the XNU window. The descriptor and result are in that page.
  The result is a completed zero write into an initially nonzero word.
- Used Linux Panfrost `141b164bbe186423e295b139e40e8e2d11cf1341` for
  the MMU and job registers, and Mesa `e24dc5bd1e7fe6101bdc866fb16a15a8fcae1aae`
  genxml v6 for the descriptor. ARM64 simulation exercised success,
  register restoration, busy GPU, MMU fault and timeout cases.
- Candidate `s8plus-ios-mali-job-v2-boot.img` SHA-256
  `9b1559b46427c9f1cad3ff766b40dc9edb83964b62976aef53554b613c2c78dd`.
  Verified the flashed BOOT with SHA-256
  `d40085bd9d573accf0c3d8498a4dfd622d8c0979964dfa7313ec391833fc3156`.
  The actual SM-G955F reported stage `4`, job-done IRQ `0x1` and
  descriptor completion `0x1`. The GPU changed the output word from
  `0xdeadbeef` to `0x00000000`. XNU mounted iOSRoot afterward and the
  log includes FTS8 HID input. This confirms a submitted job and real GPU
  memory access. The UI still uses the software framebuffer path.
- Restored and verified the one-core 768 MiB FTS8 BOOT
  (`c83e77c655e6c675fec11bdbf4b7c7752d62163b791aeefa5c55911d230be321`)
  and requested a normal iOS reboot after capturing the logs.

## Mali runtime and offscreen rendering trial (2026-11-17)

- The user requested continued GPU acceleration work and returned the phone
  to TWRP. Recovery reports SM-G955F/dream2lte and TWRP 3.7.1_12-0.
- Patch 0125 adds an opt-in `s8mali_runtime=1` path within XNU's framebuffer
  service. It allocates its own 28 KiB contiguous wired DMA buffer rather
  than reusing the loader's physical scratch address. The four-level GPU
  page table maps only the command page and the offscreen target page.
- The trial first repeats WRITE_VALUE under XNU, then requests a 16x16
  RGBA8 hardware clear through the fragment job slot. It checks all 256
  result pixels, the unused target-page tail and an unmapped canary page.
  Two later color changes run on the framebuffer work loop. There is no
  IOAccelerator registration or GPU rendering of CoreAnimation yet.
- The ARM64 code compiles with Zig and eight Unicorn/MMIO-model scenarios
  pass: success, busy hardware, wrong ID, power timeout, MMU timeout,
  job timeout, MMU fault and buffer overrun. These check driver control
  flow and containment; the model does not validate real GPU descriptors.
- Kernel compilation and actual hardware rendering remain unverified.
- Kernel CI run `44128015721` uses commit `0cf8621` and is queued for
  `macos-15-intel`. A local object compile against the captured XNU headers
  passed; that local check stubs Apple's allocator type-analysis builtins
  and is not a substitute for the genuine Xcode build.
- While waiting for CI, prepared a loader-only fragment-clear test against
  the verified FTS8 kernel, preserving its iPhone product ADT and 768 MiB
  memory window. It renders a 16x16 green target and checks all pixels plus
  the unused remainder of the private command page. This is separate from
  the XNU runtime test and does not accelerate the UI.
- Wrote `s8plus-ios-mali-clear-v2-boot.img`, SHA-256
  `598760c3ed17ddadad54bd68f4fb90ddbc2ad8023db1b4f9158c239d990edc02`.
  Full padded BOOT readback matched
  `03c474792002b6d27bc539f28fda5049db447d65bebf9d0e62e1049bbe5e9743`.
  Reboot requested; hardware result is pending return to TWRP.
- Recovered `ios-mali-clear-20261117-result.txt` from TWRP. The loader
  fragment job reached stage 3, error IRQ `0x10000`, descriptor exception
  `0x20258` (DATA_INVALID_FAULT) and unchanged output `0xdeadbeef`.
  The attempted clear did not render. iOS subsequently mounted root and
  logged framebuffer swaps and FTS8 input.
- CI `44128015721` completed successfully. Downloaded the dedicated
  dream2lte kernel and verified manifest SHA-256
  `ad816f0e6830ab2298256fc03ec95b0d3257c3db3deb18414c75065d15b725b0`.
- Wrote the XNU runtime trial using the original touch loader, without the
  preboot GPU job. Candidate `s8plus-ios-mali-runtime-v1-boot.img` SHA-256
  `1974b47772017946a22f0aa5622a111c71e2359b02ecb251af4117138f8663ac`.
  Full BOOT readback matched
  `41929c40acc5ed5abb5bfa972b58d9cfb87f8b068f3c6b692cc980e2082b9102`.
  Requested reboot and a physical return to TWRP after the logo. The
  runtime test result is pending. CoreAnimation continues to use the CPU.
- Prepared an unflashed loader clear v3 with four variants of legacy
  empty-tile and RT dithering flags and descriptor fault-pointer logging.
  Candidate SHA-256
  `ecab79ad4eb31aa4d71d220bdf59417ebe4a42b10204fdf4b28e4daf25c4bce2`.
- Recovered `ios-mali-runtime-v1-result.txt`. XNU allocated its DMA buffer
  at `0x883514000`, but the address-space UPDATE timed out at stage 2,
  before job submission. The driver retained the mapping/allocation and
  disabled further jobs (`cleanup=0`). iOS proceeded to mount root.
  This does not verify a GPU job under XNU. Added readback diagnostics for
  GPU status/fault, AS0 TTBR/config/status/fault address and core power.
- Flashed the prepared loader clear v3 plus the runtime-v1 kernel.
  Full BOOT readback SHA-256 is
  `aea587381f9ce2b9d18a41174b471f00ad4a9ae2414c453bbe99a9c7a73e26ef`.
  Requested return to TWRP after boot. Its result is pending.
- Recovered clear-v3 log. Baseline returned DATA_INVALID_FAULT with
  descriptor fault pointer `0x1200` (the framebuffer descriptor). The next
  variant, which removes framebuffer word 12 bit 29 (legacy empty tile
  write), completed with exception 1 and green first pixel `0xff00ff00`.
  Boot argument space truncated the remaining variant results.
- Removed that flag and shortened diagnostics. Flashed clear-v4 with the
  runtime-disabled path, full BOOT readback
  `66c65bc5746cfd0871c884302226a7e0ddd5cc863f88d09bcc64876325a4973e`.
  Recovered `ios-mali-clear-v4-result.txt`: stage 4, done IRQ 1, exception
  1, output `0xff00ff00`, fault pointer 0. Stage 4 requires all 256 pixels
  to match and the unused tail of the mapped output page to retain its poison.
  This verifies hardware offscreen rendering in the loader on this G71.
  It does not yet verify rendering inside XNU or acceleration of the UI.
- Applied the proven framebuffer flag fix to the XNU runtime source.
  CI `44181652355` is building the earlier diagnostic revision `d560570`.
  That revision still has the former clear descriptor, but the AS0 UPDATE
  diagnostics run before submitting a fragment job and remain useful.
- Read-only inspection of the original 20H392 dyld cache found the QuartzCore
  OpenGL renderer and `CA_ENABLE_OGL`. ARM64 disassembly at unslid
  `0x1883da844` shows the setting defaults to enabled when absent. Merely
  setting it to 1 is therefore not a GPU integration. QuartzCore and
  OpenGLES still look for IOAcceleratorES/AGXAccelerator services. The local
  inspection output and code bytes stay in ignored research directories.
  No graphics framework or iOS filesystem was modified in this analysis.
- CI `44181652355` completed successfully for `d560570`. Packaged and
  flashed `s8plus-ios-mali-runtime-diag-v2-boot.img`, image SHA-256
  `50563586ef80d560c878dbb1e668e640c08a1a370601f8caee48c34509f1d7e2`.
  Full BOOT readback matched
  `f8aa02d849537e4fbb948b93fc7a0ae1e21611754dc18528512599553f42b1f8`.
  The user was asked to return to TWRP after the Apple logo. GPU runtime
  diagnostic readback is pending. The local analysis VM was powered off.
- Recovered `ios-mali-runtime-diag-v2-result.txt`. At the stage-2 timeout,
  GPU ID remained correct, GPU fault and MMU fault were zero, TTBR readback
  matched `0x883512007`, and AS0 status was already zero in the immediate
  diagnostic readback. L2/shader ready were both 1. This suggests a polling
  timing issue but does not establish its cause or successful submission.
- Replaced IODelay in bounded GPU polling with the same architectural
  counter and `yield` approach as the working loader. Added a final status
  sample at the deadline and refreshed IRQ/fault status after polling.
  The next kernel also contains the hardware-proven framebuffer flag fix.
  Eight simulated scenarios and the local XNU object compile pass.
- Added a ninth simulated scenario that completes AS0 UPDATE at the
  polling deadline. It passes with the final register sample.
- CI `44186349435` completed successfully in 16m42s for `ece05c2`.
  Flashed `s8plus-ios-mali-runtime-v3-boot.img`, image SHA-256
  `6b798679c76f39f777431bb489a706a9760acaa68f87e54a622f36427d5f7cbb`.
  Full BOOT readback matched
  `bd7ee181400803f636dc1d5743d4e54a6283076fbbfb2364f65cb76ce46491a7`.
  Requested reboot and physical return to TWRP after the Apple logo.
  Runtime-v3 GPU job/rendering result is pending.
- Recovered `ios-mali-runtime-v3-result.txt`. AS0 setup now passes.
  WRITE_VALUE descriptor completion is 1 and first output word is 0 in
  XNU-owned RAM. The raw done IRQ was not observed and one extra word
  differed from the test expectation. Thus runtime memory access is
  observed, but this run did not pass full validation or attempt clear16.
  CNTPCT-based elapsed output was implausible (`1114114168` microseconds).
- Changed the diagnostic write from ZERO (implicit width) to explicit
  IMMEDIATE32, kept strict whole-buffer bounds checks, and added completion
  detection through the DMA descriptor plus inactive/empty slot state.
  Error IRQs remain failures. Runtime timing now uses mach_absolute_time.
  Eleven model scenarios pass, including missing done IRQ and error IRQ.
  Local XNU object compilation passes. Hardware validation is pending.
- Runtime-v4 kernel CI `44190588307` builds commit `297f26a` and is in
  progress. It includes the explicit-width write, descriptor polling and
  kernel-clock changes. The phone remains in TWRP with runtime-v3 BOOT.
- Added an independent offline Mesa compiler fixture for a one-invocation
  compute shader that copies 16 bytes from GPU VA 0x2000 to 0x2200.
  Build configuration selects the actual NIR implementation despite using
  no Gallium/Vulkan driver. CI `44192809775` succeeded with the pinned Mesa
  revision `e24dc5bd1e7fe6101bdc866fb16a15a8fcae1aae`.
  Artifact `mali-g71-copy-shader` contains a 160-byte G71 program, 64 work
  registers, zero preload/TLS/WLS. Program SHA-256
  `f69885686735ce54ee3b49bb9e6537c22d6ce42ee2765281ef254d71508ca59b`.
  It has not been submitted on the phone. This is preparation for shader
  execution and future framebuffer copies, not UI acceleration.
- CI `44190588307` completed successfully for `297f26a`. Flashed
  `s8plus-ios-mali-runtime-v4-boot.img`, image SHA-256
  `6374238d9884072bfa22e396ac87ed2f2dc7c83ffd930831efe35f5b5a1aea3b`.
  Full BOOT readback matched
  `9037fdfd8386faaaaf7f0d011603788a31ed55ff4a36607fadbf3e46c2624f47`.
  Requested reboot and return to TWRP after the Apple logo. Runtime-v4
  result is pending. The compiled compute-copy shader is not in this image.
- Recovered `ios-mali-runtime-v4-result.txt`. WRITE_VALUE IMMEDIATE32
  passed under XNU: stage 4, descriptor completion 1, first word 0,
  zero mismatches across target/tail/canary and successful cleanup.
  Kernel-clock measurement reports 19 microseconds. Raw IRQ was zero at
  the recorded sample. The subsequent fragment clear was skipped by the
  busy/incompatible guard. Thus a fully validated runtime GPU memory job
  works; runtime fragment rendering and UI acceleration are still pending.
- Added JOB raw/active/NEXT readback on guarded exits and a final drain of
  this job slot's late interrupt after cleanup. A late-IRQ model scenario
  reproduces the previous submission blocking and passes with the drain.
  Twelve model cases and the local XNU object compile pass. The actual
  reason for the hardware guard remains to be verified by the next log.
