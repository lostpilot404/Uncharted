# SPDX-License-Identifier: GPL-2.0-or-later
"""Hardware limits documented in the README, decided from bb-gpu-capabilities --memory output.

README: a graphics card needs "6 GB of video memory or more"; "4 GB cards (GTX 1650, RX 570) run
out of video memory and crash after a while; on those, keep Anti-aliasing and Frame generation
off." The launcher surfaces that advice next to the settings it applies to. It never changes
settings on its own.
"""
import re

GPU_MEMORY_LINE = re.compile(r'^GPU: (?P<name>.+), (?P<mib>\d+) MiB of video memory$', re.MULTILINE)

# The README's documented recommendation for Anti-aliasing and Frame generation.
RECOMMENDED_VRAM_MB = 6 * 1024


def parse_gpu_memory(stderr):
    """(name, MiB) from the "GPU: ..." line of bb-gpu-capabilities --memory, else (None, None).

    The greedy match keeps commas in device names ("Contoso Turbo, Turbo Edition") intact.
    """
    match = GPU_MEMORY_LINE.search(stderr or '')
    if not match:
        return None, None
    return match.group('name'), int(match.group('mib'))


def low_vram_warning(vram_mb, anti_aliasing, frame_generation):
    """The README's advice when a small card runs Anti-aliasing or Frame generation, else None.

    A visible hint only. Settings stay exactly as the user left them.
    """
    if vram_mb is None or vram_mb >= RECOMMENDED_VRAM_MB:
        return None
    if anti_aliasing == 'off' and frame_generation == 'off':
        return None
    return ('⚠ This card reports {} MiB of video memory. Anti-aliasing and frame generation can '
            'run it out of memory and crash the games (README); keep them off on cards with less '
            'than 6 GB.').format(vram_mb)
