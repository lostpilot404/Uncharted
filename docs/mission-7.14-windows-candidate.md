# Mission 7.14 — upstream synchronization / Windows review candidate

## Provenance and integration strategy

Initial clean checkout: `arena/ca78c255-uncharted`, HEAD and verified remote main
`31198fcf945016cc35c23931c33cb440c4895461` (PR9 merged). No preexisting dirty files.
Origin: `lostpilot404/Uncharted`; mainstream: `Supermedo/uncharted_pc`.
Mainstream was fetched independently; latest main and v0.3.9 both identify
`754ed2ab4f1fdd21250c8785d4d4f49517d31292`.

The requested two updates are **release-tagged commits**, not two PRs:

* v0.3.8: `ac2bfbc1602623745d8b291a8ced639d6ede6369`, subject `0.3.8`.
  That commit changes README.md and launcher/uncharted_launcher.py. Its preceding
  history supplies resolution/aspect changes, XeSS, controller/button prompts,
  localization, trophies, cheats, diagnostics, Linux and GPU/memory fixes.
* v0.3.9: `754ed2ab4f1fdd21250c8785d4d4f49517d31292`, subject
  `0.3.9: fixes from BilalReloaded's Uncharted port`. Changes README, launcher,
  runtime_memory, runtime_pad and nine shader/renderer files: cloth barriers,
  FMask/format handling, U3 focus reset and lazy Windows section commitment.

Their exact file lists and intervening commits are in `mission-7.14-upstream.json`.
Common ancestor is `b787c1068c3a9a9ba3ec43dd8086af2bf7d35c7d`. A real two-parent
merge on the session branch preserves both histories; no reset/rebase/force push.
Main is not updated. A draft PR is used while the integration has known failures.
No release tag or public release is created.

## Conflict decisions (nine files)

* README: retain fork's DLSS Super Resolution disclosure plus upstream features.
* build.sh: retain local coroutine/DLSS tests AND upstream CPU patch test.
* liverpool.cpp: both implementations already destroy the same coroutine frames;
  accept upstream's equivalent comments without dropping destruction.
* image.cpp: retain VkResult-based cache eviction/retry while adding upstream
  VRAM budget/retry/accounting; counters update only for successful allocations.
  Do not remove the caller's fatal check after recovery is exhausted.
* texture_cache.cpp: retain locked forced collection and combine it with upstream
  urgent/budget collection. Neither trigger may accidentally suppress the other.
* launcher: retain real SR presets/defaults and add upstream XeSS/resolution/UI
  features; do not replace SR with a Native-AA-only label.
* runtime_memory: retain CRIT-05 root ownership/hole-aware reservation and local
  protection restoration. Do not regress to upstream's full-span-only reservation
  and relaunch workaround (659dadf). Include upstream flexible backing-offset fix
  (fa64614), dynamic pool size and v0.3.9 SEC_RESERVE/host_commit behavior.
  No placeholder-leaf experiment is integrated into production.
* tests/test_upscaler_support.cpp: retain portable TestEnv setup plus upstream's
  unvalidated-driver assertions.
* run.sh: preserve the existing tested script contract. Preserve upstream's new
  Uncharted Linux runner, byte-for-byte, as `run-uncharted.sh`; Linux instructions
  use that entry point. This avoids a hybrid script losing mods/restart behavior.

One upstream Windows-path test was made host-independent with PureWindowsPath;
its exact executable/PID/tree/force assertions remain in place.

## Known test regression: integration is NOT approved for merging

After installing Pillow, local unittest discovery runs 177 tests (33 skips) with
six failures: upstream's translation-coverage assertion fails for es/pt/fr/de/it/ja.
Many new upstream strings lack translations; translate() explicitly falls back to
English. Do not fill translations with fake English entries, skip the test or
weaken its assertion merely to make CI green. This affected localization
integration remains unresolved and the candidate must stay in a draft PR.
The first local run also recorded missing Pillow and script/test portability
failures; those failures were retained in the working validation logs, not hidden.

Local C++ upscaler support / DLSS SR tests and C mutex/cond test pass with warnings
as errors. Native build and Windows memory CI results must be read from the actual
candidate run, not inferred from these host tests or historical runs.

## Real application and build

`Uncharted.exe` is the PyInstaller-frozen Python/Tk launcher, not the Rust prototype.
`Play Uncharted.exe` starts it with --play; bin/bb-probe.exe is the native runtime.
A **portable directory ZIP** is required: the launcher needs _internal, runtime
DLLs, scripts, artwork/fonts and licenses. The EXE alone is not a working product.
No game dump is included. No installer is invented.

Build uses MSYS2 CLANG64: `bash build.sh` (RelWithDebInfo, PGO off, LTO off),
MSVC x64: `bash packaging/windows/build_dlss.sh`, pinned optional XeSS fetch, then
Windows Python 3.12/PyInstaller: `bash packaging/windows/package.sh`.
The existing package workflow checks out the exact PR HEAD, recursively includes
submodules and full history, records source/SDK/tool versions and file hashes in
BUILD-METADATA.json and SHA256SUMS.txt, verifies ZIP structure and uploads the ZIP
plus SHA256. It preserves native launch/build evidence even when validation fails.
Dependencies fetched by existing package managers are recorded, not claimed to be
fully hermetic/reproducible from version numbers alone.

## Native launch versus gameplay/manual verification

The package smoke step extracts the actual final ZIP into a new directory and
verifies its file hashes. It starts the real Uncharted.exe with **no special test
entry point**, observes the expected application window and asks it to close
normally, recording exit code/stdout/stderr. It separately executes the shipped
bb-probe.exe --controller-info. Missing desktop access is explicitly BLOCKED,
not a successful launch; a crash or nonzero normal exit fails validation.
No fake game or executable is used. These are automated launch checks, not manual
verification and not a game rendering test. Full game execution remains blocked
without a user-owned decrypted game dump and compatible Vulkan GPU.

## User prerequisites / remaining manual checklist

Extract the whole package into a writable folder, preserving its subdirectories.
Use Windows x64, current Vulkan 1.3 GPU drivers, a suitable CPU, at least 6 GB VRAM
recommended and adequate RAM/system-managed pagefile. Install VC++ 2015–2022 x64
Redistributable for optional XeSS. FSR4 assets are optional and not guaranteed.
The upstream advertised OS minimum is Windows 10 1903; this is NOT qualified on
all such releases: production still imports CreateFileMapping2 and older-OS
availability remains unresolved. Record the actual CI OS and your own build.

1. Verify the ZIP SHA256 and BUILD-METADATA.json source.
2. Use a copy of saves, open Uncharted.exe, disable upstream auto-updates for this
   fork candidate, and verify Play/Game/Display/Controls pages and settings persistence.
3. Supply your own decrypted collection/game folders, inspect detected titles.
4. Start at 1080p/60 FPS, AA/frame generation off; test menus, cutscenes, basic
   gameplay, keyboard/mouse/controller, audio, stop/restart and clean exit.
5. Then test AA/SR, higher resolutions and per-game transitions separately.
6. Retain user/last_run.log, GPU/driver/OS details, settings and exact source/hash.

No human manual testing was performed in this sandbox. Headless CI cannot certify
visual correctness, GPU compatibility, performance, saves or long-session safety.

## Production safety remains limited

CRIT-06's unaffected-sibling absent-view window remains in production. Intentional
target invalidation and stale mapping generations are separate hazards. Mixed
PAGE_NOACCESS siblings may temporarily become PAGE_READONLY; eliminating the
writable reconstruction window did not solve that widening. No global rendezvous,
reader protocol or experimental fine-leaf memory manager was added. Upstream lazy
commit is a real production change requiring native regression evidence and
manual memory-pressure testing, not a claim of arbitrary-failure recovery.
