import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from uncharted_hw import parse_gpu_memory, low_vram_warning, RECOMMENDED_VRAM_MB


class ParseGpuMemoryTests(unittest.TestCase):
    def test_name_and_mib_are_parsed(self):
        name, mib = parse_gpu_memory(
            'GPU: NVIDIA GeForce RTX 2050/PCIe/SSE2, 4096 MiB of video memory\n')
        self.assertEqual(name, 'NVIDIA GeForce RTX 2050/PCIe/SSE2')
        self.assertEqual(mib, 4096)

    def test_commas_in_the_device_name_stay_in_the_name(self):
        name, mib = parse_gpu_memory(
            'GPU: Contoso Turbo, Turbo Edition, 8192 MiB of video memory\n')
        self.assertEqual(name, 'Contoso Turbo, Turbo Edition')
        self.assertEqual(mib, 8192)

    def test_output_of_other_modes_is_not_a_memory_line(self):
        # --live-resolution's line has no "of video memory" suffix and must not parse as one.
        self.assertEqual(
            parse_gpu_memory('GPU: NVIDIA GeForce RTX 2050, discrete, 4096 MiB: '
                             'live resolution changes off (startup resolution patch)\n'),
            (None, None))

    def test_missing_or_unparsable_output_is_unknown(self):
        self.assertEqual(parse_gpu_memory(''), (None, None))
        self.assertEqual(parse_gpu_memory(None), (None, None))
        self.assertEqual(parse_gpu_memory('GPU: something else entirely\n'), (None, None))


class LowVramWarningTests(unittest.TestCase):
    def test_small_card_with_anti_aliasing_or_frame_generation_warns(self):
        for aa, fg in (('taa', 'off'), ('off', 'fsr3'), ('dlss', 'dlss')):
            with self.subTest(aa=aa, fg=fg):
                warning = low_vram_warning(4096, aa, fg)
                self.assertIsNotNone(warning)
                self.assertIn('4096', warning)
                self.assertIn('memory', warning)

    def test_small_card_with_both_off_is_quiet(self):
        # The README's advice is already followed: no nagging.
        self.assertIsNone(low_vram_warning(4096, 'off', 'off'))

    def test_at_or_above_the_recommendation_is_quiet(self):
        self.assertIsNone(low_vram_warning(RECOMMENDED_VRAM_MB, 'taa', 'fsr3'))
        self.assertIsNone(low_vram_warning(8192, 'taa', 'fsr3'))

    def test_unknown_vram_is_quiet(self):
        # No probe result (no Vulkan, tool missing): never invent a hardware limit.
        self.assertIsNone(low_vram_warning(None, 'taa', 'fsr3'))


if __name__ == '__main__':
    unittest.main()
