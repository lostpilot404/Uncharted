// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: the DLSS Super Resolution plan — preset divisors, the NGX quality class each one
// maps to, and the render/output sizes that follow from them.
//
// Deliberately free of Vulkan and NGX headers: tests/test_dlss_sr.cpp checks these rules on
// any host, so the parts of DLSS SR that do not need a GPU are covered by CI.

#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

namespace Vulkan::DlssSr {

/// Preset order matches BbSettings::Preset: NativeAA, Quality, Balanced, Performance,
/// UltraPerformance. The divisors are NVIDIA's documented DLSS ratios, so the render size the
/// renderer picks always matches the quality class NGX is told about (a mismatched pair is what
/// makes NGX return a feature-creation failure).
inline constexpr int PresetCount = 5;
inline constexpr std::array<float, PresetCount> Scales{1.0f, 1.5f, 1.7f, 2.0f, 3.0f};
inline constexpr std::array<const char*, PresetCount> PresetNames{
    "Native AA (DLAA)", "Quality", "Balanced", "Performance", "Ultra Performance"};

/// Native AA reconstructs at the output size: that is DLAA, not upscaling.
inline constexpr bool IsSrPreset(int preset) {
    return preset > 0 && preset < PresetCount;
}

constexpr float ScaleForPreset(int preset) {
    return Scales[std::clamp(preset, 0, PresetCount - 1)];
}

constexpr const char* NameForPreset(int preset) {
    return PresetNames[std::clamp(preset, 0, PresetCount - 1)];
}

/// A DLSS SR preset asked for by a launcher that must not silently hand back native AA.
constexpr int ClampToSrPreset(int preset) {
    return std::clamp(preset, 1, PresetCount - 1);
}

/// NGX quality class for an output/render ratio (0 DLAA .. 4 Ultra Performance).
constexpr int NgxQualityForScale(float scale) {
    // DLAA only for equal sizes: any real upscale needs at least the Quality class.
    return scale >= 2.9f ? 4 : scale >= 1.95f ? 3 : scale >= 1.65f ? 2 : scale > 1.01f ? 1 : 0;
}

/// The quality class a preset must produce; used to assert plan/NGX agreement in tests.
constexpr int NgxQualityForPreset(int preset) {
    return NgxQualityForScale(ScaleForPreset(preset));
}

struct Plan {
    uint32_t render_w = 0, render_h = 0; ///< the upscaler's input (internal render size)
    uint32_t output_w = 0, output_h = 0; ///< the upscaler's output (the requested size)
    int quality = 0;                     ///< NGX quality class
    int preset = 0;

    bool operator==(const Plan&) const = default;
    /// True when the render size is genuinely below the output size (real upscaling).
    [[nodiscard]] bool Upscales() const { return render_w < output_w || render_h < output_h; }
};

/// Even dimensions for half-resolution effects, matching SceneResolution::ForPreset exactly
/// (tests assert the two agree, so the scene proxy and the DLSS input cannot drift apart).
constexpr uint32_t EvenDiv(uint32_t size, float scale) {
    const long rounded = std::lround(double(size) / double(scale) / 2.0); // NOLINT
    return uint32_t(std::max(1L, rounded) * 2);
}

inline Plan MakePlan(uint32_t out_w, uint32_t out_h, int preset) {
    Plan plan;
    plan.preset = std::clamp(preset, 0, PresetCount - 1);
    plan.output_w = out_w;
    plan.output_h = out_h;
    plan.render_w = EvenDiv(out_w, ScaleForPreset(plan.preset));
    plan.render_h = EvenDiv(out_h, ScaleForPreset(plan.preset));
    plan.quality = NgxQualityForScale(out_w && plan.render_w ? float(out_w) / float(plan.render_w)
                                                             : 1.0f);
    return plan;
}

} // namespace Vulkan::DlssSr
