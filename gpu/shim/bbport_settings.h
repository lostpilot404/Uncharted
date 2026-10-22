// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: user settings changed at run time from the in-game menu (bbport_overlay.h) and kept
// in bbport.ini (BB_CONFIG overrides the path). Environment variables override the file at
// start. Readers load the atomics every frame; writers are the menu and Load().

#pragma once

#include <atomic>

namespace BbSettings {

enum Upscaler : int { UpscalerOff = 0, UpscalerFsr3 = 1, UpscalerFsr4 = 2, UpscalerFsr411 = 3,
                      UpscalerTaa = 4, UpscalerDlss = 5, UpscalerCount };
/// FSR 4 v07 or FSR 4.1.1: the same inputs, settings and placement in the frame.
inline bool IsFsr4(int upscaler) {
    return upscaler == UpscalerFsr4 || upscaler == UpscalerFsr411;
}
/// Providers that reconstruct an output-size image from a smaller render: DLSS Super
/// Resolution and FSR 3.1/4. TAA and "off" are native-resolution only, so a scaled preset
/// must never be applied to them (it would upscale without any reconstruction).
inline bool SupportsSuperResolution(int upscaler) {
    return upscaler == UpscalerDlss || upscaler == UpscalerFsr3 || IsFsr4(upscaler);
}
/// Whether a velocity-mode session (Uncharted: CameraMotion::VelocityMode) may run a scaled
/// preset instead of the game's own render size. Velocity-mode sessions used to be pinned to
/// Native AA, which left DLSS with nothing to upscale (DLAA only).
///   auto (-1, default): DLSS Super Resolution with a scaled preset - the one provider whose
///          upscaling this change is for; every other provider keeps the previous behaviour.
///   on (1): any provider that reconstructs (DLSS, FSR 3.1, FSR 4) with a scaled preset.
///   off (0): never; the game's own render size, for cards where the smaller scene targets hurt.
inline constexpr int SrVelocityAuto = -1;
inline constexpr int SrVelocityOff = 0;
inline constexpr int SrVelocityOn = 1;
inline bool VelocityScaledPreset(bool velocity_mode, int sr_velocity, int upscaler, int preset) {
    if (!velocity_mode) return true;
    if (!SupportsSuperResolution(upscaler) || preset <= 0) return false; // nothing to upscale
    if (sr_velocity == SrVelocityAuto) return upscaler == UpscalerDlss;
    return sr_velocity == SrVelocityOn;
}
enum Preset : int { NativeAA = 0, Quality, Balanced, Performance, UltraPerformance, PresetCount };
/// Frame generation (vk_frame_generation.h): frames generated between two of the game's.
enum FrameGen : int { FrameGenOff = 0, FrameGenFsr3 = 1, FrameGenDlss = 2, FrameGenCount };
enum DebugView : int { DebugNone = 0, DebugReactive = 1, DebugMotion = 2, DebugViewCount };

/// Game effects switched by the community patches at start (patches.py EFFECTS): ini key,
/// menu label, default (the game's own behaviour).
struct Effect {
    const char* key;
    const char* label;
    bool default_on;
};
inline constexpr Effect Effects[] = {
    {"effect_chromatic_aberration", "Хроматическая аберрация", true},
    {"effect_dof", "Глубина резкости (DoF)", true},
    {"effect_motion_blur", "Размытие в движении", true},
    {"effect_ssao", "Затенение SSAO", true},
    {"effect_game_aa", "Собственное сглаживание игры", true},
    {"effect_dynamic_shadows", "Тени от динамических источников", true},
    {"effect_ssr", "Отражения SSR (не было в игре)", false},
    {"skip_intro", "Пропуск заставок при запуске", false},
    {"debug_camera", "Свободная камера (Cross + L3)", false},
    {"debug_menu", "Debug menu (нужны файлы шрифтов)", false},
    {"cheat_no_death", "Чит: бессмертие (не ниже 1 HP)", false},
    {"cheat_stealth", "Чит: враги не замечают", false},
    {"cheat_silent", "Чит: враги не слышат", false},
    {"cheat_rally_no_decay", "Чит: Rally не угасает", false},
    {"cheat_enemy_control", "Чит: управление врагом (R3 / L3)", false},
    {"tweak_no_rally", "Без Rally (возврата HP)", false},
    {"tweak_camera_distance", "Камера дальше", false},
    {"tweak_no_camera_rotation", "Без автоповорота камеры", false},
    {"tweak_easy_run", "Бег с меньшим наклоном стика", false},
    {"tweak_ragdoll", "Физика тел как в Dark Souls", false},
};
inline constexpr int EffectCount = int(sizeof(Effects) / sizeof(Effects[0]));
/// Live output resolutions: the upscaler's output and the UI host targets.
inline constexpr int OutputWidths[] = {1280, 1920, 2560, 3840};
inline constexpr int OutputHeights[] = {720, 1080, 1440, 2160};
inline constexpr int OutputCount = 4;
inline constexpr int OutputDefault = 1; ///< 1920x1080, the game's own size

struct Values {
    std::atomic<int> upscaler{UpscalerFsr3};
    std::atomic<int> preset{NativeAA};
    std::atomic<bool> sharpen{true};
    std::atomic<float> sharpness{0.3f};
    std::atomic<bool> jitter{true};
    std::atomic<bool> reactive{false};
    std::atomic<bool> object_motion{true};
    std::atomic<float> reactive_scale{1.0f};
    std::atomic<float> reactive_threshold{0.2f};
    std::atomic<float> reactive_max{0.9f};
    std::atomic<int> debug_view{DebugNone};
    std::atomic<bool> show_fps{false};
    // FSR 4 checks (menu): the provider's auto exposure, the jitter sign it is given.
    std::atomic<bool> fsr4_auto_exposure{true};
    std::atomic<bool> fsr4_invert_jitter{false};
    std::atomic<int> active_render_width{1920}, active_render_height{1080};
    /// Applied at start (patches.py); the menu shows when a restart is needed.
    std::atomic<bool> effects[EffectCount]{};
    std::atomic<int> model_lod{0}; ///< -2 highest .. 2 lowest, 0 the game's
    std::atomic<int> output_res{OutputDefault}; ///< index into OutputWidths
    /// Live resolution and preset changes (run.sh): 0 off by default (startup patch, fastest
    /// on the Steam Deck and older GPUs), -1 auto (strong discrete GPUs), 1 on. On restart.
    std::atomic<int> live_resolution{0};
    /// Velocity-mode sessions (Uncharted): may a scaled preset be used? SrVelocityAuto by
    /// default (DLSS Super Resolution only); BB_SR_VELOCITY / sr_velocity in bbport.ini
    /// override (auto|0|1). See VelocityScaledPreset().
    std::atomic<int> sr_velocity{SrVelocityAuto};
    /// Why FSR 4 cannot run (assets, device features), or null. Set by the renderer.
    std::atomic<const char*> fsr4_problem{nullptr};
    std::atomic<bool> fsr4_supported{false}, fsr411_supported{false};
    /// DLSS depth convention. The games write D3D depth, 0 at the near plane and 1.0 for the
    /// sky (docs/upscaler.md: projection z scale 1.00002 / offset -0.0500679, near 0.05), and
    /// FSR 3.1 and the frame-generation path already assume the same, so DepthInverted stays
    /// off. BB_DLSS_DEPTH_INVERTED=1 / dlss_depth_inverted=1 exists only to test the opposite
    /// convention on a GPU, which is the one input CI cannot check.
    std::atomic<bool> dlss_depth_inverted{false};
    /// DLSS (bbport_dlss.dll, NVIDIA RTX) is ready, or why not (null before the device exists).
    std::atomic<bool> dlss_supported{false};
    std::atomic<const char*> dlss_problem{nullptr};
    /// Frame generation: provider and output frames per game frame (2 = one generated frame,
    /// up to 4 with DLSS on RTX 50). dlssg_max_multiplier: 1 without DLSS Frame Generation.
    std::atomic<int> frame_gen{FrameGenOff};
    std::atomic<int> frame_gen_multiplier{2};
    std::atomic<int> dlssg_max_multiplier{1};
    std::atomic<const char*> frame_gen_problem{nullptr};

    /// Startup settings for the explicit BB_RENDER_RES compatibility patch only.
    int startup_preset = NativeAA;
    int startup_upscaler = UpscalerFsr3;
    bool startup_object_motion = true;
    bool startup_effects[EffectCount]{};
    int startup_model_lod = 0;
    int startup_output_res = OutputDefault;
    int startup_live_resolution = 0;
};

Values& Get();

/// Reads the file, then the environment overrides. Called once at start.
void Load();
/// Checks the loaded choice before the first frame; unsupported FSR 4 uses FSR 3.1.
void ConfigureUpscalerSupport(bool fsr4, bool fsr411);
/// After device creation: DLSS availability; a DLSS setting falls back to FSR 3.1 without it.
void ConfigureDlssSupport(bool available, const char* problem);
/// Startup-patched scene dimensions cannot change until run.sh prepares a new image.
bool FixedRenderSession();
int RenderPreset();
bool ResolutionNeedsRestart();
/// Writes the file (menu changes).
void Save();

/// Render resolution divisor of a preset (1.0 native, 1.5 quality, ...).
float PresetScale(int preset);
const char* PresetName(int preset);
const char* UpscalerName(int upscaler);
const char* FrameGenName(int frame_gen);

} // namespace BbSettings
