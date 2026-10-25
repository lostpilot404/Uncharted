Uncharted: The Nathan Drake Collection for Windows
===================================================

A native port: the original PS4 game code runs directly on Windows. No game files are
included. You need your own decrypted dump of the collection (CUSA02343, CUSA02320, ...;
the folder with eboot.bin, big2-ps4_Shipping.elf, big3-ps4_Shipping.elf, sce_sys,
u1data, u2data, u3data).

Requirements
- Windows 10 (1903 or later) or Windows 11, 64-bit.
- A Vulkan 1.3 graphics card with a current driver.
- About 8 GB of free memory (RAM + page file).
  Nothing else: Python and the libraries are inside this folder.

Starting
- Uncharted.exe opens the launcher. On the Game page choose your game folder, pick
  Drake's Fortune, Among Thieves or Drake's Deception on the Play page and press PLAY.
- Play Uncharted.exe starts the game straight away with the settings saved in the
  launcher, without opening it (for shortcuts and Steam). Uncharted.exe --play does the same.
- Play page -> "Add to Steam" adds the collection to your Steam library, with its cover
  from your game folder. "Desktop shortcut" puts it on the desktop.
- Your own covers: put u1.jpg, u2.jpg and u3.jpg (and a wide collection_banner.jpg for the
  top of the Play page) in a folder named art next to Uncharted.exe. Updates keep it.
- Display page -> Frame rate: 60 FPS as on PS4, or 120 FPS. Gameplay keeps its normal speed
  and the cutscene videos still play at 60. Higher rates are experimental. Above your
  display's refresh rate use the Mailbox or Immediate presentation.
- Display page -> Anti-aliasing: TAA, AMD FSR 3.1, AMD FSR 4, NVIDIA DLSS (DLAA) or Intel XeSS smooth the
  games' 1920x1080 picture with their own motion vectors, before their post-processing.
  DLSS needs a GeForce RTX. Upscaling to a larger window or screen stays with FSR 1.
- Display page -> Frame generation: AMD FSR 3 Frame Generation (any GPU, 2x) or NVIDIA DLSS
  Frame Generation (GeForce RTX 40: 2x, RTX 50: up to 4x) shows frames made between two of the
  game's: 60 FPS becomes 120 (or 240). Use the Mailbox or Immediate presentation and a display
  with that refresh rate. Menus and cutscene videos are shown as they are.
- Controls page: keyboard, mouse and controller work together, all remappable.
  Defaults: WASD move, mouse look, left button fire (R2), right button aim (L2), Space Cross,
  C Circle, F Square, E Triangle, Q/G L1/R1, Left Shift/V L3/R3, arrows and wheel d-pad,
  Escape/Enter Options, Tab touchpad. The cursor stays in the game window; hold Left Alt to
  free it. Insert opens the in-game settings menu. Bindings are saved in input.ini.
- Trophies: the three games' trophy lists are read from sce_sys\trophy\trophy01-03.trp in the game
  folder. A trophy the game awards shows as a notice at the top of the screen and on the launcher's
  Trophies page (BB_TROPHY_POPUP=0 in Advanced -> Environment hides the notice). Earned trophies
  are kept in user\trophy\ with the saves, one list per game.

Data
- Saves and shader caches: user\ next to Uncharted.exe (the launcher can pick another folder).
- Launcher options: %APPDATA%\uncharted-launcher. Generated files (prepared game image): out\.
- The log of the last run: user\last_run.log (the launcher's Log page shows it live).

Updates
- The launcher looks for a new version when it starts and offers it on the left: Update
  downloads it, closes the launcher, installs it over this folder and opens it again.
  Saves, settings, input.ini and out\ are kept. Advanced page -> Updates turns the check off
  or runs it now.

Upscaling and DLSS
- Anti-aliasing (Display page) picks the temporal reconstructor: TAA, AMD FSR 3.1, FSR 4,
  or NVIDIA DLSS. Render size picks what it renders before reconstructing 1920 x 1080:
  Native AA (no upscaling, DLSS calls it DLAA), Quality (1280 x 720), Balanced,
  Performance (960 x 540) or Ultra Performance (640 x 360).
- DLSS Super Resolution needs a GeForce RTX 20 series or newer and a current driver. It is
  NVIDIA's upscaler, not a renamed DLAA: with Render size Quality the game really draws
  1280 x 720 and DLSS builds the 1920 x 1080 image. Uncharted's camera decides the render
  size, so bbport uses the chosen preset for DLSS automatically (BB_SR_VELOCITY=auto); set
  BB_SR_VELOCITY=0 to keep the game's own 1920 x 1080.
- The package ships bbport_dlss.dll (MIT, built from gpu/dlss_bridge) and NVIDIA's
  nvngx_dlss.dll + nvngx_dlssg.dll with their licence in licenses\. If they are missing the
  option is greyed out and FSR 3.1 is used instead; user\last_run.log says why.
- Frame generation is off by default and DLSS Super Resolution does not turn it on. To be
  sure it stays off, leave Frame generation at Off and check that user\last_run.log has no
  "Frame Generation available" line. DLSS Frame Generation needs an RTX 40/50 card; Super
  Resolution does not.
- More: docs/dlss-sr.md in the source tree and Display page help text.

Known problems
- Uncharted 3, Cartagena square (chapter 2): at high frame rates the game could close with
  "GPU crash: the graphics device was lost". Fixed in 0.3. If it still happens, set Frame
  rate to 60 on the Display page.
- Uncharted 3: green or black shapes can flash for one frame on some characters (also in
  shadPS4). Not fixed yet.

Port by SuperMedo.

Mission 7.14 review-candidate notes
- Extract the entire ZIP to a new, writable folder. Uncharted.exe is the real launcher,
  NOT a standalone executable: keep _internal, bin, scripts, launcher and licenses beside it.
- Install the Microsoft Visual C++ 2015-2022 Redistributable (x64), particularly for XeSS,
  and a current GPU driver. FSR 4 requires separately supplied assets; use Off/TAA/FSR 3.1
  if they are absent. No game dump is included or downloadable from this package.
- BUILD-METADATA.json identifies the exact source, upstream commits, tools and file hashes.
  SHA256SUMS.txt covers the package files. CI launch evidence is a separate artifact.
- This is a draft integration candidate, not a production-safety certification. CRIT-06
  sibling unmap gaps, intentional invalidation, stale async pointers and mixed-protection
  widening remain unresolved. The placeholder-leaf experiment is NOT used by this runtime.
- Upstream localization coverage is incomplete; untranslated text falls back to English.
- Disable 'Look for a new version' on Advanced before testing. Do not install an upstream
  auto-update over this review build: it would replace the fork's unmerged work.
- Test with a COPY of your saves. Start at 1080p, 60 FPS, AA and frame generation off.
  Choose the Game folder, verify each available title, open Display/Controls, save settings,
  then PLAY. Record cutscene/menu/gameplay behavior, controller input and clean shutdown.
  GPU and gameplay checks require your decrypted game data and cannot be established by CI.
- If launch fails, preserve user\last_run.log, the Log page output, BUILD-METADATA.json and
  the exact ZIP checksum. Missing DLL errors can indicate incomplete extraction or drivers.

LOCAL DIAGNOSTICS (no telemetry uploads)
  Log -> Export diagnostics previews a bounded, sanitized local ZIP. Inspect every
  member, then Save ZIP to a NEW filename or Cancel. Redaction is best effort:
  unknown private runtime text may remain. No files are uploaded automatically.
  Log -> Open logs folder opens %APPDATA%/uncharted-launcher/diagnostics.
  Report a problem asks before opening GitHub and never attaches your logs.
  Current/previous runtime and launcher history are size-limited. Native dumps,
  game contents, saves and full settings are excluded. Driver version may be unavailable.
  Explicit CLI alternative: Uncharted.exe --export-diagnostics C:\Reports\new-report.zip
  Inspect the ZIP yourself before sharing. Directory must exist; no overwrite.
  Better diagnostics do not fix the known CRIT-06 memory-safety limitations.
