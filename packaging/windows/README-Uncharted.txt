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

The launcher
- Uncharted.exe opens on PLAY. Choose the game folder, pick Drake's Fortune,
  Among Thieves or Drake's Deception, check the readiness line, then press PLAY.
  If a game is missing, PLAY tells you which folder to set.
- SETTINGS holds every option in one page: Game, Display, Graphics, Audio, Controls,
  Fixes, Cheats, Advanced and Support. Press "Save settings" (or close the launcher)
  to write them. Settings live in %APPDATA%\uncharted-launcher\settings.json.
- Play Uncharted.exe starts the game straight away with the saved settings, without
  opening the launcher (for shortcuts and Steam). Uncharted.exe --play does the same.
- SETTINGS -> Game: game and saves folders, per-game folders, language, player name and
  the three trophy lists (from sce_sys\trophy\trophy01-03.trp). A trophy the game awards
  shows a notice on screen; earned trophies are kept in user\trophy\ with the saves.
  Set BB_TROPHY_POPUP=0 in Advanced -> Environment to hide the in-game notice.
- SETTINGS -> Display: Frame rate (60 FPS as on PS4, or 120; higher rates are experimental),
  Presentation (use Mailbox or Immediate above your display's refresh rate), Render resolution,
  Aspect ratio, Window size, Upscaling (FSR 1) and Sharpening (RCAS). Cutscene videos always
  play at 60.
- SETTINGS -> Graphics: Anti-aliasing (TAA, AMD FSR 3.1, FSR 4, NVIDIA DLSS/DLAA or Intel
  XeSS) with the games' own motion vectors, Render size, sharpness and Frame generation
  (AMD FSR 3: 2x on any GPU; NVIDIA DLSS: 2x on RTX 40, up to 4x on RTX 50). Menus and
  cutscene videos are not generated. Frame generation is off by default.
- SETTINGS -> Audio: Stereo, 5.1 or 7.1 speaker layout (Automatic follows Windows).
- SETTINGS -> Controls: keyboard, mouse and controller work together, all remappable.
  Defaults: WASD move, mouse look, left button fire (R2), right button aim (L2), Space Cross,
  C Circle, F Square, E Triangle, Q/G L1/R1, Left Shift/V L3/R3, arrows and wheel d-pad,
  Escape/Enter Options, Tab touchpad. The cursor stays in the game window; hold Left Alt to
  free it. Insert opens the in-game settings menu. Bindings are saved in input.ini.
- SETTINGS -> Advanced: interface language, update checks, environment variables,
  "Add to Steam", "Desktop shortcut", "Clear shader cache" and maintenance actions. Steam
  artwork comes from your game folder, or from an image you choose there. Custom
  collection images (Collection image) remain configurable in Settings -> Advanced.
- SETTINGS -> Support: the live runtime log, Copy (sanitized), Open logs folder,
  Report a problem and Export diagnostics. See LOCAL DIAGNOSTICS below.

Data
- Saves and shader caches: user\ next to Uncharted.exe (the launcher can pick another folder).
- Launcher options: %APPDATA%\uncharted-launcher. Generated files (prepared game image): out\.
- The log of the last run: user\last_run.log (SETTINGS -> Support shows it live).

Updates
- The launcher looks for a new version when it starts and offers it on the left: Update
  downloads it, closes the launcher, installs it over this folder and opens it again.
  Saves, settings, input.ini and out\ are kept. SETTINGS -> Advanced turns the check off
  or runs it now.

Upscaling and DLSS
- Anti-aliasing (SETTINGS -> Graphics) picks the temporal reconstructor. Render size picks
  what it renders before reconstructing 1920 x 1080: Native AA (no upscaling, DLSS calls it
  DLAA), Quality (1280 x 720), Balanced, Performance (960 x 540) or Ultra Performance
  (640 x 360). DLSS Super Resolution needs a GeForce RTX 20 series or newer and a current
  driver; with Quality the game really draws 1280 x 720 and DLSS builds the 1080p image.
- The package ships bbport_dlss.dll (MIT, built from gpu/dlss_bridge) and NVIDIA's
  nvngx_dlss.dll + nvngx_dlssg.dll with their licence in licenses\. If they are missing the
  option is greyed out and FSR 3.1 is used instead; user\last_run.log says why.
- To be sure frame generation stays off, leave it at Off and check that user\last_run.log
  has no "Frame Generation available" line. DLSS Frame Generation needs an RTX 40/50 card;
  Super Resolution does not. More: docs/dlss-sr.md in the source tree.

Known problems
- Uncharted 3, Cartagena square (chapter 2): at high frame rates the game could close with
  "GPU crash: the graphics device was lost". Fixed in 0.3. If it still happens, set Frame
  rate to 60 in SETTINGS -> Display.
- Uncharted 3: green or black shapes can flash for one frame on some characters (also in
  shadPS4). Not fixed yet.
- Frame rates above 120 are experimental. On 4 GB graphics cards keep Anti-aliasing and
  Frame generation off.

Installation and limitations
- Extract the entire ZIP to a new, writable folder. Uncharted.exe is the real launcher,
  NOT a standalone executable: keep _internal, bin, scripts, launcher and licenses beside it.
- Install the Microsoft Visual C++ 2015-2022 Redistributable (x64), particularly for XeSS,
  and a current GPU driver. FSR 4 requires separately supplied assets; use Off/TAA/FSR 3.1
  if they are absent. No game dump is included or downloadable from this package.
- BUILD-METADATA.json identifies the exact source, upstream commits, tools and file hashes.
  SHA256SUMS.txt covers the package files. CI launch evidence is a separate artifact.
- This is not a production-safety certification. CRIT-06 sibling unmap gaps, intentional
  invalidation, stale async pointers and mixed-protection widening in the Windows memory
  manager remain unresolved. The placeholder-leaf experiment is NOT used by this runtime.
- Launcher text coverage is checked for all six supported non-English languages; English
  fallback remains available for unknown text.
- Disable 'Look for a new version' in SETTINGS -> Advanced before testing a review build;
  an upstream auto-update would replace the fork's unmerged work.
- Test with a COPY of your saves. Start at 1080p, 60 FPS, AA and frame generation off.
  Choose the game folder, verify each available title, check Display/Controls, save settings,
  then PLAY. GPU and gameplay checks require your decrypted game data and cannot be
  established by CI. If launch fails, preserve user\last_run.log, the Support log,
  BUILD-METADATA.json and the exact ZIP checksum.

LOCAL DIAGNOSTICS (no telemetry uploads)
  SETTINGS -> Support -> Export diagnostics previews a bounded, sanitized local ZIP. Inspect
  every member, then Save ZIP to a NEW filename or Cancel. Redaction is best effort:
  unknown private runtime text may remain. No files are uploaded automatically.
  Open logs folder opens %APPDATA%/uncharted-launcher/diagnostics.
  Report a problem asks before opening GitHub and never attaches your logs.
  Current/previous runtime and launcher history are size-limited. Native dumps, game
  contents, saves and full settings are excluded. Driver version may be unavailable.
  Explicit CLI alternative: Uncharted.exe --export-diagnostics C:\Reports\new-report.zip
  Inspect the ZIP yourself before sharing. Directory must exist; no overwrite.
  Better diagnostics do not fix the known CRIT-06 memory-safety limitations.

Port by SuperMedo.
