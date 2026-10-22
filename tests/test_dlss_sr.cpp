// SPDX-License-Identifier: GPL-2.0-or-later
// bbport: DLSS Super Resolution rules that do not need a GPU.
//
// Covers: preset -> render size -> NGX quality agreement, DLAA vs SR distinction, the
// velocity-mode (Uncharted) gate, provider selection, frame-generation independence and the
// settings round trip for BB_SR_VELOCITY / BB_UPSCALE_PRESET.
//
// Built and run on any host (no Vulkan, no NGX): gpu/CMakeLists.txt target dlss-sr-test and
// build.sh --test. Hardware behaviour is NOT covered here - see docs/dlss-sr.md.

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string>

#include "bbport_settings.h"
#include "test_env.h"
#include "video_core/renderer_vulkan/dlss_sr.h"
#include "video_core/renderer_vulkan/scene_resolution.h"

namespace {

using namespace Vulkan::DlssSr;

int Fail(const char* what) {
    std::printf("FAIL: %s\n", what);
    return 1;
}

} // namespace

int main() {
    // --- 1. Preset divisors are NVIDIA's ratios and increase monotonically -------------------
    assert(ScaleForPreset(0) == 1.0f);
    assert(ScaleForPreset(1) == 1.5f);
    assert(ScaleForPreset(2) == 1.7f);
    assert(ScaleForPreset(3) == 2.0f);
    assert(ScaleForPreset(4) == 3.0f);
    for (int p = 1; p < PresetCount; ++p) {
        if (!(ScaleForPreset(p) > ScaleForPreset(p - 1))) return Fail("scales not monotonic");
    }
    assert(!IsSrPreset(0));   // Native AA is DLAA, not upscaling
    for (int p = 1; p < PresetCount; ++p) assert(IsSrPreset(p));
    assert(ClampToSrPreset(0) == 1);   // an SR request can never silently become DLAA
    assert(ClampToSrPreset(9) == 4);

    // --- 2. NGX quality class matches the ratio it will be given ----------------------------
    assert(NgxQualityForScale(1.0f) == 0);    // DLAA
    assert(NgxQualityForScale(1.01f) == 0);   // still native
    assert(NgxQualityForScale(1.5f) == 1);    // Quality
    assert(NgxQualityForScale(1.7f) == 2);    // Balanced
    assert(NgxQualityForScale(2.0f) == 3);    // Performance
    assert(NgxQualityForScale(3.0f) == 4);    // Ultra Performance
    for (int p = 0; p < PresetCount; ++p) {
        if (NgxQualityForPreset(p) != p) return Fail("preset does not map to its own quality");
    }

    // --- 3. The plan changes the INPUT size and keeps the requested OUTPUT size -------------
    struct Case {
        uint32_t w, h;
    };
    const Case outputs[] = {{1280, 720}, {1920, 1080}, {2560, 1440}, {3840, 2160}};
    for (const auto& out : outputs) {
        const auto native = MakePlan(out.w, out.h, 0);
        if (native.output_w != out.w || native.output_h != out.h) return Fail("output size changed");
        if (native.Upscales()) return Fail("Native AA reported an upscale");
        if (native.render_w != out.w || native.render_h != out.h) return Fail("Native AA resized");
        for (int p = 1; p < PresetCount; ++p) {
            const auto plan = MakePlan(out.w, out.h, p);
            if (plan.output_w != out.w || plan.output_h != out.h) {
                return Fail("an SR preset changed the output size");
            }
            if (!plan.Upscales()) return Fail("an SR preset rendered at output size");
            if (plan.render_w >= plan.output_w) return Fail("render width not reduced");
            if (plan.render_w % 2 || plan.render_h % 2) return Fail("odd render dimension");
            if (plan.quality != p) return Fail("plan quality does not match the preset");
        }
        // Bigger presets render fewer pixels.
        for (int p = 2; p < PresetCount; ++p) {
            if (!(MakePlan(out.w, out.h, p).render_w < MakePlan(out.w, out.h, p - 1).render_w)) {
                return Fail("render size does not fall with the preset");
            }
        }
    }
    // Concrete values for the manual test plan (1080p output).
    assert(MakePlan(1920, 1080, 1).render_w == 1280 && MakePlan(1920, 1080, 1).render_h == 720);
    assert(MakePlan(1920, 1080, 3).render_w == 960 && MakePlan(1920, 1080, 3).render_h == 540);
    assert(MakePlan(2560, 1440, 1).render_w == 1706 && MakePlan(2560, 1440, 1).render_h == 960);

    // --- 4. The scene proxy and the DLSS input agree, or the upscaler would resample twice ---
    for (const auto& out : outputs) {
        for (int p = 0; p < PresetCount; ++p) {
            const auto proxy = Vulkan::SceneResolution::ForPreset(p, {out.w, out.h});
            const auto plan = MakePlan(out.w, out.h, p);
            if (proxy.width != plan.render_w || proxy.height != plan.render_h) {
                std::printf("FAIL: scene proxy %ux%u != DLSS input %ux%u (preset %d)\n", proxy.width,
                            proxy.height, plan.render_w, plan.render_h, p);
                return 1;
            }
        }
    }

    // --- 5. Velocity mode (Uncharted): DLSS SR by default, other providers unchanged ------
    const int dlss = BbSettings::UpscalerDlss, fsr3 = BbSettings::UpscalerFsr3;
    const int fsr4 = BbSettings::UpscalerFsr4, taa = BbSettings::UpscalerTaa;
    // Auto (default): DLSS with a scaled preset upscales; Native AA and every other provider
    // keep the game's own render size exactly as before.
    assert(BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, dlss, 1));
    assert(BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, dlss, 4));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, dlss, 0));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, fsr3, 1));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, fsr4, 1));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, taa, 1));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityAuto, taa, 0));
    // Explicit on: FSR 3.1/4 may scale too; TAA never (it has no reconstruction).
    assert(BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityOn, fsr3, 2));
    assert(BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityOn, dlss, 1));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityOn, taa, 2));
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityOn, dlss, 0));
    // Explicit off: nothing scales in velocity mode.
    assert(!BbSettings::VelocityScaledPreset(true, BbSettings::SrVelocityOff, dlss, 1));
    // Outside velocity mode the preset always applies: this change must not alter that.
    for (int up : {dlss, fsr3, fsr4, taa}) {
        for (int mode : {BbSettings::SrVelocityAuto, BbSettings::SrVelocityOff,
                         BbSettings::SrVelocityOn}) {
            assert(BbSettings::VelocityScaledPreset(false, mode, up, 2));
        }
    }

    // --- 6. Settings: provider support, BB_UPSCALE_PRESET, BB_SR_VELOCITY, frame generation --
    using namespace BbSettings;
    // SR-capable providers only.
    assert(SupportsSuperResolution(UpscalerDlss));
    assert(SupportsSuperResolution(UpscalerFsr3));
    assert(SupportsSuperResolution(UpscalerFsr4));
    assert(SupportsSuperResolution(UpscalerFsr411));
    assert(!SupportsSuperResolution(UpscalerTaa));
    assert(!SupportsSuperResolution(UpscalerOff));

    const std::string file = TestEnv::TempFile("bbport-dlss-sr-test-");
    assert(!file.empty());
    const char* const path = file.c_str();
    TestEnv::Set("BB_CONFIG", path);
    for (const char* name : {"BB_UPSCALER", "BB_UPSCALE_PRESET", "BB_SR_VELOCITY", "BB_RENDER_RES",
                             "BB_FRAME_GEN", "BB_FRAME_GEN_MULTIPLIER"}) {
        TestEnv::Unset(name);
    }
    auto& s = Get();

    // A DLSS SR launcher writes upscaler=dlss, preset>=1, sr_velocity=1 and both frame
    // generators off. None of those may turn frame generation on.
    FILE* config = std::fopen(path, "w");
    assert(config);
    std::fprintf(config, "upscaler=dlss\npreset=1\nsr_velocity=1\nframe_gen=off\n"
                         "frame_gen_multiplier=2\n");
    std::fclose(config);
    Load();
    assert(s.upscaler == UpscalerDlss);
    assert(s.preset == Quality);
    assert(s.sr_velocity == BbSettings::SrVelocityOn);
    assert(s.frame_gen == FrameGenOff);
    assert(s.dlssg_max_multiplier == 1);
    // The SR preset reaches the renderer instead of the velocity-mode native override.
    assert(VelocityScaledPreset(true, s.sr_velocity.load(), s.upscaler, RenderPreset()));
    assert(RenderPreset() == Quality);

    // DLSS unavailable on the machine: FSR 3.1 takes over, and frame generation stays off.
    ConfigureDlssSupport(false, "bbport_dlss.dll and nvngx_dlss.dll are not installed");
    assert(s.upscaler == UpscalerFsr3);
    assert(!s.dlss_supported);
    assert(s.dlss_problem.load() != nullptr);
    assert(s.frame_gen == FrameGenOff);
    assert(s.dlssg_max_multiplier == 1);
    assert(SupportsSuperResolution(s.upscaler)); // FSR 3.1 also renders smaller
    // Available: the DLSS choice is kept.
    Load();
    ConfigureDlssSupport(true, "");
    assert(s.upscaler == UpscalerDlss && s.dlss_supported && !s.dlss_problem.load());

    // DLAA chosen explicitly: preset 0, sr_velocity auto - and still no frame generation.
    config = std::fopen(path, "w");
    assert(config);
    std::fprintf(config, "upscaler=dlss\npreset=0\nsr_velocity=auto\nframe_gen=off\n");
    std::fclose(config);
    Load();
    assert(s.preset == NativeAA && s.sr_velocity == BbSettings::SrVelocityAuto);
    assert(!VelocityScaledPreset(true, s.sr_velocity.load(), s.upscaler, RenderPreset()));
    assert(MakePlan(1920, 1080, NativeAA).Upscales() == false);
    assert(s.frame_gen == FrameGenOff);

    // TAA is native-resolution by construction: an SR preset must not be applied to it.
    s.upscaler = UpscalerTaa;
    s.preset = Performance;
    assert(RenderPreset() == NativeAA);
    assert(!VelocityScaledPreset(true, BbSettings::SrVelocityOn, s.upscaler, RenderPreset()));

    // Save/Load round trip keeps sr_velocity and the preset.
    s.upscaler = UpscalerDlss;
    s.preset = Balanced;
    s.sr_velocity = BbSettings::SrVelocityOn;
    Save();
    s.upscaler = UpscalerOff;
    s.preset = NativeAA;
    s.sr_velocity = BbSettings::SrVelocityAuto;
    Load();
    assert(s.upscaler == UpscalerDlss && s.preset == Balanced);
    assert(s.sr_velocity == BbSettings::SrVelocityOn);

    // Environment overrides used by the launcher.
    TestEnv::Set("BB_UPSCALER", "dlss");
    TestEnv::Set("BB_UPSCALE_PRESET", "3");
    TestEnv::Set("BB_SR_VELOCITY", "1");
    TestEnv::Set("BB_FRAME_GEN", "off");
    Load();
    assert(s.upscaler == UpscalerDlss && s.preset == Performance && s.sr_velocity);
    assert(s.frame_gen == FrameGenOff);
    assert(MakePlan(1920, 1080, s.preset).render_w == 960);

    std::remove(path);
    std::puts("DLSS SR: presets, resolution plan, velocity-mode gate, provider selection and "
              "frame-generation independence PASS");
    return 0;
}
