"""The launcher's DLSS / super-resolution settings reach the runtime as the right environment.

Covers the user-visible contract: a scaled preset is passed as BB_UPSCALE_PRESET with
BB_SR_VELOCITY so Uncharted's velocity-mode camera can use it, Native AA pins DLAA without
upscaling, an untouched preset leaves bbport.ini alone, and frame generation stays off unless
the player chooses it.
"""
import importlib.util
import os
import unittest
from unittest import mock

from paths import ROOT

LAUNCHER = ROOT / 'launcher' / 'uncharted_launcher.py'
spec = importlib.util.spec_from_file_location('uncharted_launcher', LAUNCHER)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def settings(**overrides):
    s = dict(launcher.APP_DEFAULTS)
    s['key_bindings'] = dict(launcher.DEFAULT_KEYS)
    s['pad_bindings'] = dict(launcher.DEFAULT_PAD)
    s.update(overrides)
    return s


class DlssEnvironmentTests(unittest.TestCase):
    def environment(self, **overrides):
        s = settings(game_dir=str(ROOT), **overrides)
        keys = ('BB_SR_VELOCITY', 'BB_UPSCALE_PRESET', 'BB_FRAME_GEN', 'BB_UPSCALER')
        # A caller's environment must not decide what the launcher is meant to pass on.
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(launcher, 'write_input_ini', lambda _s: ROOT / 'input.ini'):
            for key in keys:
                os.environ.pop(key, None)
            return launcher.game_environment(s)

    def test_defaults_leave_the_preset_and_frame_generation_alone(self):
        env = self.environment()
        self.assertEqual(env['BB_UPSCALER'], 'off')
        # Unset: the preset saved by the in-game menu (bbport.ini) stays in charge.
        self.assertNotIn('BB_UPSCALE_PRESET', env)
        self.assertNotIn('BB_SR_VELOCITY', env)
        self.assertEqual(env['BB_FRAME_GEN'], 'off')
        self.assertEqual(launcher.APP_DEFAULTS['frame_generation'], 'off')

    def test_dlss_quality_is_real_super_resolution(self):
        env = self.environment(anti_aliasing='dlss', aa_preset='1')
        self.assertEqual(env['BB_UPSCALER'], 'dlss')
        self.assertEqual(env['BB_UPSCALE_PRESET'], '1')
        # 1 = Quality: Uncharted's velocity mode may render 1280x720 for a 1920x1080 output.
        self.assertEqual(env['BB_SR_VELOCITY'], '1')
        self.assertEqual(env['BB_FRAME_GEN'], 'off')

    def test_every_scaled_preset_enables_the_velocity_mode_render_scale(self):
        for preset in ('1', '2', '3', '4'):
            env = self.environment(anti_aliasing='dlss', aa_preset=preset)
            self.assertEqual(env['BB_UPSCALE_PRESET'], preset)
            self.assertEqual(env['BB_SR_VELOCITY'], '1', preset)

    def test_native_aa_stays_dlaa(self):
        env = self.environment(anti_aliasing='dlss', aa_preset='0')
        self.assertEqual(env['BB_UPSCALE_PRESET'], '0')
        # 0: keep the game's own render size - DLSS then only anti-aliases (DLAA).
        self.assertEqual(env['BB_SR_VELOCITY'], '0')

    def test_frame_generation_is_never_implied_by_super_resolution(self):
        env = self.environment(anti_aliasing='dlss', aa_preset='3')
        self.assertEqual(env['BB_FRAME_GEN'], 'off')
        env = self.environment(anti_aliasing='dlss', aa_preset='3', frame_generation='dlss',
                               frame_gen_multiplier='2')
        self.assertEqual(env['BB_FRAME_GEN'], 'dlss')
        self.assertEqual(env['BB_FRAME_GEN_MULTIPLIER'], '2')

    def test_dlss_label_does_not_claim_dlaa_only(self):
        labels = dict(launcher.ANTI_ALIASING)
        self.assertEqual(labels['dlss'], 'NVIDIA DLSS (GeForce RTX)')
        self.assertNotIn('DLAA', labels['dlss'])
        presets = dict(launcher.AA_PRESETS)
        self.assertIn('Native AA', presets['0'])
        self.assertIn('Quality', presets['1'])


if __name__ == '__main__':
    unittest.main()
