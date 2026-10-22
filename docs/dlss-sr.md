# DLSS Super Resolution

bbport's `dlss` upscaler reconstructs a 1920 × 1080 frame from a smaller one with NVIDIA NGX
(DLSS Super Resolution). Native AA (DLSS "DLAA") is the same provider at the output size and
upscales nothing; the two are labelled separately in the launcher, the in-game menu and the log.

| Piece | File |
| --- | --- |
| Preset → render size → NGX quality class (testable without a GPU) | `gpu/shadps4/video_core/renderer_vulkan/dlss_sr.h` |
| Scene proxies (`SceneTargets`) that make the game render smaller | `vk_scene_resolution.{h,cpp}`, `scene_resolution.h` |
| Frame inputs, placement, fallback | `vk_temporal_upscaler.cpp` (`UseDlss`, `RecordDlss`, `RunScaled`) |
| NGX feature creation/evaluation, extension gates | `vk_dlss.{h,cpp}` |
| MIT C bridge (the only code that links the NVIDIA SDK) | `gpu/dlss_bridge/` |
| Settings, fallback, menu strings | `gpu/shim/bbport_settings.{h,cpp}`, `bbport_overlay.cpp` |
| Build (MSVC) + package | `packaging/windows/build_dlss.sh`, `package.sh` |
| Rules tested on every CI run | `tests/test_dlss_sr.cpp`, `tests/test_launcher_dlss.py` |

## Why it was DLAA-only in Uncharted

Uncharted drives the camera (`CameraMotion::VelocityMode`): the game itself decides the render
size. `TemporalUpscaler::OnFrameStart` therefore forced `preset = 0` for those sessions, and
`SceneResolution::ForPreset(0, 1920x1080)` is `1920x1080` — so `Dlss::QualityForScale(1.0)`
returned DLAA, the bridge was always asked for `NVSDK_NGX_PerfQuality_Value_DLAA`, and no
frame was ever upscaled. It was labelled "DLSS" while doing native-resolution anti-aliasing.

## The fix: `sr_velocity`

`BbSettings::sr_velocity` decides whether a velocity-mode session may use the chosen preset:

| `sr_velocity` | Environment | Behaviour in Uncharted |
| --- | --- | --- |
| `auto` (default) | `BB_SR_VELOCITY=auto` (or unset) | DLSS with a preset above Native AA renders smaller (Quality = 1280 × 720 → 1920 × 1080). FSR 3.1/4/TAA keep the game's own size, exactly as before. |
| `1` | `BB_SR_VELOCITY=1` | Any provider that reconstructs (DLSS, FSR 3.1, FSR 4) may render smaller. |
| `0` | `BB_SR_VELOCITY=0` | Never; the game's own render size (Native AA only). |

The default only changes behaviour for a combination that previously did nothing useful: DLSS
plus a scaled preset. Native AA and every FSR/TAA configuration behave as before.

`VelocityScaledPreset()` in `gpu/shim/bbport_settings.h` is the single place that decides; the
renderer asks it once per frame:

```cpp
const int requested = BbSettings::RenderPreset();
const int preset = BbSettings::VelocityScaledPreset(velocity, settings.sr_velocity.load(),
                                                    upscaler, requested) ? requested : 0;
```

## Presets and the sizes they produce

The divisors are NVIDIA's own DLSS ratios (`DlssSr::Scales`), and the NGX quality class is
derived from the ratio actually used (`DlssSr::NgxQualityForScale`) — a mismatch between the
render size and the quality class is what makes NGX reject a feature:

| Preset | Scale | 1920 × 1080 output | NGX quality |
| --- | --- | --- | --- |
| Native AA (DLAA) | 1.0 | 1920 × 1080 | `DLAA` |
| Quality | 1.5 | 1280 × 720 | `MaxQuality` |
| Balanced | 1.7 | 1130 × 636 | `Balanced` |
| Performance | 2.0 | 960 × 540 | `MaxPerf` |
| Ultra Performance | 3.0 | 640 × 360 | `UltraPerformance` |

`SceneResolution::ForPreset` produces the same numbers; `tests/test_dlss_sr.cpp` asserts the
scene proxy size and the DLSS input size are equal for every preset and every common output
size, so the two cannot drift apart.

## Frame inputs

`RecordDlss` gets the reduced scene proxy, the reduced hardware depth, motion vectors and
jitter, in the layout FSR 3.1 already used:

- colour: the scene proxy, `R16G16B16A16Sfloat`, top-left `w × h` of the aligned target;
- depth: the scene depth proxy at the same size, in the games' own D3D convention — 0 at the
  near plane, 1.0 for the sky (`docs/upscaler.md`: projection z scale `1.00002`, offset
  `-0.0500679`, near 0.05; `camera_motion.comp` treats `depth >= 1.0` as sky). `DepthInverted`
  is therefore **off**, matching the FSR 3.1 path and the frame generator's own default.
  `BB_DLSS_DEPTH_INVERTED=1` / `dlss_depth_inverted=1` is the opt-in for a machine that shows
  the opposite convention — next to motion-vector sign it is the only DLSS input a CI runner
  cannot confirm;
- motion: `R16G16Sfloat`, render-resolution pixels, current → previous; toggle `1 << 26`
  flips the sign for a build or driver with the opposite convention;
- jitter: the pattern FSR 3.1 uses, in render pixels (toggle `1 << 25` turns jitter off),
  with the history reset whenever the preset, output size, provider or scene changes
  (`reset = true`);
- timing: measured frame time, clamped to 1–200 ms.

The upscaler runs on the scene image before the game's own colour post-processing
(`BB_UPSCALE_BEFORE_CS`, `BB_HUDLESS_PS`), not on the composed HUD: `UiComposition` keeps UI at 1920 × 1080 and composites it after upscaling.
Menu/movie frames do not feed DLSS history (they are shown alone), and `reset` is set on the
transition back to 3D.

## Availability, failure and fallback

1. `bbport_dlss.dll` (the MIT bridge) **and** `nvngx_dlss.dll` (NVIDIA) must be next to
   `bb-probe.exe`. Missing either → `Dlss::Get()` is null, the menu shows the option greyed out
   with the reason, and `dlss` falls back to FSR 3.1.
2. `BB_DLSS=0` turns the provider off regardless of the files.
3. The bridge asks NGX for the Vulkan instance/device extensions it needs; the renderer enables
   them. If the device cannot provide them, or NGX reports the feature unsupported,
   `vk_dlss.cpp` disables the provider with "this GPU or driver does not support DLSS (GeForce
   RTX needed)" and the setting falls back to FSR 3.1 before the first frame.
4. Either DLL present but NGX reports a driver too old → "DLSS needs a newer NVIDIA driver".
5. A feature that fails at runtime falls back to FSR 3.1 for the session, resets history and
   reports the failure in the menu (`BbSettings::dlss_problem`).

Super Resolution availability comes from `NVSDK_NGX_Parameter_SuperSampling_Available`, which is
independent of Frame Generation. **Frame Generation has its own gate** (`..._FrameGeneration_*`
parameters, its own DLL `nvngx_dlssg.dll` and its own capability check), so an RTX 20 series
card (Ampere RTX 2050 included) can run DLSS Super Resolution; only Frame Generation is refused
on pre-Ada cards.

## Frame generation

`BbSettings::frame_gen` defaults to `FrameGenOff`; `BB_FRAME_GEN=off` is written by both
launchers by default. Choosing a DLSS SR preset does not touch it (`tests/test_dlss_sr.cpp`,
`tests/test_launcher_dlss.py` and the Rust launch tests assert this). Without DLSS FG,
`dlssg_max_multiplier` stays 1 and no `nvngx_dlssg` code path runs.

## Licensing

- `gpu/dlss_bridge/` is MIT (see `LICENSE.txt` there) and is the only code compiled against the
  NVIDIA SDK. It is built with MSVC (`packaging/windows/build_dlss.sh`) because the NGX static
  library is MSVC-only.
- `nvngx_dlss.dll` / `nvngx_dlssg.dll` are NVIDIA's, redistributed under the terms in
  `NVIDIA-DLSS-LICENSE.txt` copied from the DLSS SDK by `build_dlss.sh` and shipped in
  `licenses/`. They are never committed to this repository.
- The package's README lists what ships and what the user must supply (a DLSS SDK checkout for
  building the bridge; the game itself is never redistributed).

## Verifying it (Windows, RTX 2050 or newer)

Automated coverage stops at the rules above: no CI runner has an NVIDIA GPU, so the GPU paths
are unverified until someone runs them.

1. Install the package, set *Anti-aliasing* to **NVIDIA DLSS (GeForce RTX)** and *Render size*
   to **Quality (1.5×, 1280 × 720)**. *Frame generation* stays **Off**.
2. Start the game. The log must print
   `Upscaler: DLSS Quality, render 1280x720 -> output 1920x1080`.
   The same size twice, or `(DLAA: native resolution)`, means SR is not active — report the log.
3. In the in-game menu (Insert), *Активный рендер сцены* must read `1280 x 720`.
4. Set *Render size* to *Native AA*: the log switches to `render 1920x1080 ... (DLAA: native
   resolution)` — DLAA is not upscaling.
5. Compare frame time and image quality against FSR 3.1 Quality at the same output size. If
   edges smear or ghost, try `BB_DLSS_DEPTH_INVERTED=1` (restart) and report which was better:
   the depth convention is one of the two DLSS inputs CI cannot check. The other is the
   motion-vector sign: it is fixed at current → previous and can only be flipped with the
   internal debug toggle bit `1 << 26` (`bbport_toggles.h`), not from the launcher.
6. The DLSS render-preset letter (A..K) stays at the SDK default for the quality class; only
   the quality class is chosen by the preset.
7. Confirm Frame generation stayed off: the menu's frame-generation entry is *Выкл*, and the
   log has no `Frame Generation available` line.
