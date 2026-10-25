#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launcher for the Windows port of Uncharted: The Nathan Drake Collection (Tkinter).

Chooses the game folder, which of the three games to start, the display and console options,
and runs run.py with them as environment variables. Launcher options live in
%APPDATA%/uncharted-launcher/settings.json.

Frozen with PyInstaller the same Uncharted.exe also runs the game without the window
(`--play`), run.py (`--run`) and the preparation scripts (`--script`), so a packaged port
needs no Python installation.
"""
import ctypes
import json
import os
from pathlib import Path
import queue
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

FROZEN = getattr(sys, 'frozen', False)
PORT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PORT_DIR / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
VERSION = '0.3.9'
import uncharted_diagnostics

DIAGNOSTICS = None
if __name__ == '__main__' and sys.argv[1:2] not in (['--run'], ['--script'], ['--install-update']):
    DIAGNOSTICS = uncharted_diagnostics.Diagnostics(
        Path(os.environ.get('APPDATA', Path.home())) / 'uncharted-launcher' / 'diagnostics',
        PORT_DIR, VERSION)
    import traceback
    _original_exception_hook = sys.excepthook
    def _diagnostic_exception(kind, error, tb):
        DIAGNOSTICS.failure('launcher.exception', error)
        DIAGNOSTICS.event('ERROR', 'launcher.traceback', ''.join(traceback.format_exception(kind, error, tb)))
        _original_exception_hook(kind, error, tb)
        try:
            if sys.argv[1:2] == ['--export-diagnostics']:
                return
            ctypes.windll.user32.MessageBoxW(None, 'Uncharted could not start. Check the diagnostics folder in '
                '%APPDATA%/uncharted-launcher. No report was uploaded.', 'Uncharted', 0x10)
        except (AttributeError, OSError):
            pass
    sys.excepthook = _diagnostic_exception
    def _diagnostic_thread_exception(args):
        DIAGNOSTICS.failure('launcher.worker', args.exc_value)
        DIAGNOSTICS.event('ERROR', 'launcher.traceback', ''.join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    _original_thread_hook = threading.excepthook
    _record_thread_exception = _diagnostic_thread_exception
    def _diagnostic_thread_exception(args):
        _record_thread_exception(args)
        _original_thread_hook(args)
    threading.excepthook = _diagnostic_thread_exception


def diagnostic_event(level, component, message):
    if DIAGNOSTICS:
        DIAGNOSTICS.event(level, component, message)


import trophies  # noqa: E402  (scripts/trophies.py: the games' trophy lists)
import uncharted_i18n

DATA_DIR = Path(os.environ.get('BB_DATA_DIR', PORT_DIR))
CONFIG_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'uncharted-launcher'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
NAME = 'Uncharted'
AUTHOR = 'SuperMedo'
# This build; GitHub release tags are v<VERSION> (their digits are compared).
RELEASES_API = 'https://api.github.com/repos/Supermedo/uncharted_pc/releases/latest'
RELEASES_PAGE = 'https://github.com/Supermedo/uncharted_pc/releases/latest'
UPDATE_DIR = Path(tempfile.gettempdir()) / 'uncharted-update'
# Never copied over an installation by an update (the package does not hold them either).
USER_FILES = ('user', 'out', 'art', 'bbport.ini', 'input.ini', 'last_run.log')
MAX_LOG_LINES = 6000
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

GAMES = [
    ('u1', "Drake's Fortune", 'Uncharted 1 · 2007', 'eboot.bin'),
    ('u2', 'Among Thieves', 'Uncharted 2 · 2009', 'big2-ps4_Shipping.elf'),
    ('u3', "Drake's Deception", 'Uncharted 3 · 2011', 'big3-ps4_Shipping.elf'),
]
# Console language (system parameter 1), as the PS4 numbers them.
LANGUAGES = [('1', 'English (United States)'), ('18', 'English (United Kingdom)'), ('2', 'French'),
             ('22', 'French (Canada)'), ('4', 'German'), ('5', 'Italian'), ('3', 'Spanish'),
             ('20', 'Spanish (Latin America)'), ('7', 'Portuguese'), ('17', 'Portuguese (Brazil)'),
             ('6', 'Dutch'), ('8', 'Russian'), ('16', 'Polish'), ('19', 'Turkish'), ('21', 'Arabic'),
             ('0', 'Japanese'), ('9', 'Korean'), ('11', 'Chinese (Simplified)'), ('10', 'Chinese (Traditional)'),
             ('12', 'Finnish'), ('13', 'Swedish'), ('14', 'Danish'), ('15', 'Norwegian')]
PRESENT_MODES = [('Mailbox', 'Mailbox (low latency, no tearing)'), ('Fifo', 'FIFO (VSync)'),
                 ('FifoRelaxed', 'FIFO Relaxed'), ('Immediate', 'Immediate (tearing)')]
# The display rate the games run at (BB_VBLANK_HZ): they time gameplay with the real clock, and
# the prerecorded cutscenes keep 60 frames per second at every setting.
FRAME_RATES = [('30', '30 FPS'), ('60', '60 FPS (as on PS4)'), ('120', '120 FPS'), ('144', '144 FPS (experimental)'),
               ('165', '165 FPS (experimental)'), ('240', '240 FPS (experimental)')]
# The window (or the screen in fullscreen) shows the game's picture at its size: FSR 1 (EASU) or
# bilinear when the picture is smaller, then RCAS sharpening (BB_RCAS_ATTENUATION, lower is sharper).
WINDOW_SIZES = [('1280x720', '1280 × 720'), ('1600x900', '1600 × 900'),
                ('1920x1080', '1920 × 1080'), ('2560x1440', '2560 × 1440'),
                ('3840x2160', '3840 × 2160 (4K)'), ('2560x1080', '2560 × 1080 (21:9)'),
                ('3440x1440', '3440 × 1440 (21:9)'), ('3840x1600', '3840 × 1600 (21:9)'),
                ('5120x1440', '5120 × 1440 (32:9)')]
# The size the games themselves render and show (BB_GAME_RES: run.py, src/runtime_display.c): a real
# higher resolution, not an enlarged 1080p picture. The height here and the shape below make the
# width; the games keep their memory for it, so the sizes stop where it runs out.
RENDER_HEIGHTS = [('1080', "1080p (the games' own)"), ('1440', '1440p'), ('1620', '1620p'), ('2160', '2160p (4K)')]
ASPECTS = [('16:9', "16:9 (the games' own)"), ('21:9', '21:9 ultrawide'), ('32:9', '32:9 super ultrawide'),
           ('window', "The shape of the window or screen")]
ASPECT_RATIOS = {'16:9': 16 / 9, '21:9': 64 / 27, '32:9': 32 / 9}
# Widths the screens of these shapes really have at each height (21:9 is 64:27 at 1080p and 43:18 at 1440p).
SCREEN_WIDTHS = {('21:9', 1080): 2560, ('21:9', 1440): 3440, ('21:9', 1600): 3840, ('21:9', 1620): 3840,
                 ('21:9', 2160): 5120, ('32:9', 1080): 3840, ('32:9', 1440): 5120, ('32:9', 2160): 7680}
MAX_RENDER_PIXELS = 5_800_000   # what the games' GPU memory holds: 3200x1800 starts, 4K does not (docs/resolution.md)
SPEAKERS = [('auto', 'Automatic (the Windows speaker setup)'), ('2', 'Stereo'), ('6', '5.1 surround'), ('8', '7.1 surround')]
SCALERS = [('fsr1', 'FSR 1 (AMD FidelityFX, sharp)'), ('bilinear', 'Smooth (bilinear)')]
SHARPENING = [('off', 'Off'), ('1000', 'Low'), ('500', 'Medium'), ('250', 'High'), ('0', 'Maximum')]
# Temporal anti-aliasing at the games' 1920x1080 (BB_UPSCALER): it takes the scene before the
# games' post-processing with their own motion vectors, so it replaces their edge smoothing.
ANTI_ALIASING = [('off', "Off (the game's own)"), ('taa', 'TAA'), ('fsr3', 'AMD FSR 3.1'),
                 ('fsr4', 'AMD FSR 4'), ('dlss', 'NVIDIA DLSS (GeForce RTX)')]
FSR411 = ('fsr411', 'AMD FSR 4.1.1')
# The render size the upscaler inputs (BB_UPSCALE_PRESET). '' keeps whatever the game menu
# saved in bbport.ini; the others pin it for this launch. Native AA is DLAA: no upscaling.
# The scales are NVIDIA's/AMD's own ratios: 1920x1080 -> 1280x720 (Quality), 1130x636
# (Balanced), 960x540 (Performance), 640x360 (Ultra Performance).
AA_PRESETS = [('', 'As set in the game menu'),
              ('0', 'Native AA (no upscaling, DLAA)'),
              ('1', 'Quality (1.5×, 1280 × 720)'),
              ('2', 'Balanced (1.7×)'),
              ('3', 'Performance (2.0×, 960 × 540)'),
              ('4', 'Ultra Performance (3.0×)')]
# sr_velocity: Uncharted's camera pins the render size; '' leaves the runtime default (DLSS
# super resolution on, other upscalers at the game's size), '0' forces the game's size.
AA_SR_VELOCITY = {'': '', '0': '0', '1': '1', '2': '1', '3': '1', '4': '1'}
XESS = ('xess', 'Intel XeSS (any GPU)')
AA_SHARPNESS = [('0', 'Off'), ('0.2', 'Low'), ('0.4', 'Medium'), ('0.7', 'High')]
# Frames generated between two of the game's (BB_FRAME_GEN, BB_FRAME_GEN_MULTIPLIER).
FRAME_GENERATION = [('off', 'Off'), ('fsr3', 'AMD FSR 3 Frame Generation (any GPU)'),
                    ('dlss', 'NVIDIA DLSS Frame Generation (GeForce RTX 40/50)')]
BUTTON_PROMPTS = [('auto', 'Automatic (follows the device you used last)'),
                  ('keyboard', 'Keyboard and mouse keys'), ('xbox', 'Xbox buttons'),
                  ('0', "PlayStation buttons (the game's own)")]
FRAME_GEN_MULTIPLIERS = [('2', '2× (one generated frame)'), ('3', '3× (DLSS on GeForce RTX 50)'),
                         ('4', '4× (DLSS on GeForce RTX 50)')]
# Controls (input.ini, read by the game: src/runtime_pad.c). Actions with their PS4 names, the
# default keys ("A|B": two bindings) and the default controller buttons (SDL names).
BUTTON_ACTIONS = [
    ('cross', 'Cross ✕', 'Space', 'a'), ('circle', 'Circle ○', 'C', 'b'),
    ('square', 'Square □', 'F', 'x'), ('triangle', 'Triangle △', 'E', 'y'),
    ('l1', 'L1', 'Q', 'leftshoulder'), ('r1', 'R1', 'G', 'rightshoulder'),
    ('l2', 'L2 (aim)', 'Mouse Right', 'lefttrigger'), ('r2', 'R2 (fire)', 'Mouse Left', 'righttrigger'),
    ('l3', 'L3', 'Left Shift', 'leftstick'), ('r3', 'R3', 'V', 'rightstick'),
    ('options', 'Options', 'Escape|Return', 'start'), ('touchpad', 'Touchpad', 'Tab', 'back'),
    ('up', 'D-pad up', 'Up', 'dpup'), ('down', 'D-pad down', 'Down', 'dpdown'),
    ('left', 'D-pad left', 'Left|Wheel Up', 'dpleft'), ('right', 'D-pad right', 'Right|Wheel Down', 'dpright'),
]
STICK_ACTIONS = [
    ('ls_up', 'Move forward', 'W'), ('ls_down', 'Move back', 'S'),
    ('ls_left', 'Move left', 'A'), ('ls_right', 'Move right', 'D'),
    ('rs_up', 'Look up', 'I'), ('rs_down', 'Look down', 'K'),
    ('rs_left', 'Look left', 'J'), ('rs_right', 'Look right', 'L'),
]
DEFAULT_KEYS = {a: k for a, _t, k, _p in BUTTON_ACTIONS} | {a: k for a, _t, k in STICK_ACTIONS}
DEFAULT_PAD = {a: p for a, _t, _k, p in BUTTON_ACTIONS}
PAD_BUTTONS = [('a', 'A / Cross'), ('b', 'B / Circle'), ('x', 'X / Square'), ('y', 'Y / Triangle'),
               ('leftshoulder', 'LB / L1'), ('rightshoulder', 'RB / R1'),
               ('lefttrigger', 'LT / L2'), ('righttrigger', 'RT / R2'),
               ('leftstick', 'Left stick click / L3'), ('rightstick', 'Right stick click / R3'),
               ('start', 'Start / Options'), ('back', 'Back / Share'), ('touchpad', 'Touchpad click'),
               ('dpup', 'D-pad up'), ('dpdown', 'D-pad down'), ('dpleft', 'D-pad left'),
               ('dpright', 'D-pad right'), ('misc1', 'Share / Mic (misc)'), ('paddle1', 'Paddle 1'),
               ('paddle2', 'Paddle 2'), ('paddle3', 'Paddle 3'), ('paddle4', 'Paddle 4'),
               ('none', 'Not assigned')]
# Tk key names → SDL key names (letters and digits map to themselves, upper case).
TK_KEYS = {'space': 'Space', 'Return': 'Return', 'Escape': 'Escape', 'Tab': 'Tab', 'BackSpace': 'Backspace',
           'Shift_L': 'Left Shift', 'Shift_R': 'Right Shift', 'Control_L': 'Left Ctrl',
           'Control_R': 'Right Ctrl', 'Alt_R': 'Right Alt', 'Caps_Lock': 'CapsLock', 'Up': 'Up',
           'Down': 'Down', 'Left': 'Left', 'Right': 'Right', 'Delete': 'Delete', 'Home': 'Home',
           'End': 'End', 'Prior': 'PageUp', 'Next': 'PageDown', 'minus': '-', 'equal': '=',
           'bracketleft': '[', 'bracketright': ']', 'semicolon': ';', 'apostrophe': "'", 'comma': ',',
           'period': '.', 'slash': '/', 'backslash': '\\', 'grave': '`', 'KP_Enter': 'Keypad Enter',
           'KP_Add': 'Keypad +', 'KP_Subtract': 'Keypad -', 'KP_Multiply': 'Keypad *',
           'KP_Divide': 'Keypad /', 'KP_Decimal': 'Keypad .', 'KP_Delete': 'Keypad .',
           'KP_Insert': 'Keypad 0', 'KP_End': 'Keypad 1', 'KP_Down': 'Keypad 2', 'KP_Next': 'Keypad 3',
           'KP_Left': 'Keypad 4', 'KP_Begin': 'Keypad 5', 'KP_Right': 'Keypad 6', 'KP_Home': 'Keypad 7',
           'KP_Up': 'Keypad 8', 'KP_Prior': 'Keypad 9'}
# Tk on Windows names numpad keys like the main ones ("8", "plus"), only the keycode tells them apart (#224).
WIN_NUMPAD = {**{0x60 + n: f'Keypad {n}' for n in range(10)},
              0x6A: 'Keypad *', 0x6B: 'Keypad +', 0x6D: 'Keypad -', 0x6E: 'Keypad .', 0x6F: 'Keypad /'}
MOUSE_SENS_MIN, MOUSE_SENS_MAX = 0.1, 10.0  # the Controls section slider (the game's default pace is 1)
# Compatibility fixes that cost speed, so they run only where they help unless the player picks.
# key -> (title, hint, {choice: {variable: value}}); 'auto' sets nothing (the game's own default).
FIXES = {
    'fix_u2_gpu_results': (
        'Exact GPU results (Uncharted 2)',
        'Explosions, vehicle damage and the convoy of chapter 21 read results of GPU work back. '
        'Automatic: on in Uncharted 2 only. Costs speed in busy scenes.',
        {'on': {'BB_GPU_MERGE': '0'}, 'off': {'BB_GPU_MERGE': '1'}}),
    'fix_u2_mesh': (
        'Mesh raycaster copies (Uncharted 2)',
        'Fixes "iSubMesh < m_numSubMeshInstances" after checkpoints. Automatic: on in Uncharted 2 only. '
        'In Uncharted 3\'s Cartagena square it cost 94 to 54 FPS.',
        {'on': {'BB_SCALAR_COPY64_SYNC': '2', 'BB_SCALAR_COPY64_DEFER': '1'},
         'off': {'BB_SCALAR_COPY64_SYNC': '0', 'BB_SCALAR_COPY64_DEFER': '0'}}),
    'fix_tiny_readback': (
        'Aim and turret focus (Uncharted 2)',
        'Enemies stay in focus when aiming. Automatic: on in Uncharted 2 only; elsewhere it costs about 13%.',
        {'on': {'BB_TINY_READBACK': '1'}, 'off': {'BB_TINY_READBACK': '0'}}),
    'fix_small_buffers': (
        'Brightness flicker fix',
        'Copies small GPU results back every frame (auto exposure). Automatic: off; it halved the frame '
        'rate in Uncharted 3\'s Cartagena square.',
        {'on': {'BB_READBACK_SMALL_BUFFERS': '1'}, 'off': {'BB_READBACK_SMALL_BUFFERS': '0'}}),
    'fix_vram': (
        'Small video memory mode',
        'Keeps 4-8 GB graphics cards inside their video memory. Automatic: on for cards up to 8 GB.',
        {'on': {'BB_VRAM_LEGACY': '0'}, 'off': {'BB_VRAM_LEGACY': '1'}}),
    'fix_job_sleep': (
        'Idle game workers sleep',
        'Frees CPU cores while the game\'s workers wait. Automatic: on for CPUs with up to 8 threads; '
        'on bigger CPUs it cost about 8%.',
        {'on': {'BB_JOB_SLEEP': '1'}, 'off': {'BB_JOB_SLEEP': '0'}}),
    'fix_thread_prio': (
        'PS4 thread priorities',
        'Game threads get the priorities they have on PS4. Automatic: on.',
        {'on': {'BB_THREAD_PRIO': '1'}, 'off': {'BB_THREAD_PRIO': '0'}}),
}
FIX_CHOICES = [('auto', 'Automatic'), ('on', 'On'), ('off', 'Off')]

# Insert opens the in-game settings menu; Left Alt frees the mouse cursor.
RESERVED_KEYS = {'Insert': 'Insert opens the in-game settings menu.',
                 'Alt_L': 'Left Alt frees the mouse cursor while held.'}

APP_DEFAULTS = {'game_dir': '', 'game_dir_u1': '', 'game_dir_u2': '', 'game_dir_u3': '', 'user_dir': '', 'start': 'u1', 'language': '1', 'ui_language': '', 'player_name': '',
                'fullscreen': False, 'present_mode': 'Mailbox', 'frame_rate': '60', 'show_fps': False,
                'window_size': '1920x1080', 'render_height': '1080', 'aspect': '16:9', 'speakers': 'auto', 'scaler': 'fsr1', 'sharpening': '500',
                'anti_aliasing': 'off', 'aa_preset': '', 'aa_sharpness': '0.2', 'frame_generation': 'off',
                'frame_gen_multiplier': '2', 'extra_env': '', 'close_on_play': False,
                'mouse_look': True, 'mouse_sensitivity': '1.0', 'mouse_invert_y': False,
                'pad_invert_y': False, 'art_banner': '', 'art_u1': '', 'art_u2': '', 'art_u3': '', 'steam_art': '', 'art_crops': {}, 'shortcut_name': 'Uncharted: The Nathan Drake Collection', 'shortcut_icon': '', 'trophy_game': 'u1', 'pad_vibration': True, 'pad_vibration_strength': '100', 'button_prompts': 'auto', 'key_bindings': {}, 'pad_bindings': {}, 'check_updates': True,
                **{key: 'auto' for key in FIXES},
                **{f'{game}_infinite_{cheat}': False for game, _t, _s, _e in GAMES for cheat in ('health', 'ammo')}}


UI_LANGUAGE = 'en'


def _(text):
    """Translate launcher text, keeping English as the fallback."""
    return uncharted_i18n.translate(UI_LANGUAGE, text)


def allowed_setting_values():
    """Every enumerated setting and the values `game_environment` knows how to handle."""
    values = {
        'start': [key for key, *_ in GAMES],
        'trophy_game': [key for key, *_ in GAMES],
        'language': [value for value, _ in LANGUAGES],
        'ui_language': [code for code, _ in uncharted_i18n.LANGUAGES],
        'present_mode': [value for value, _ in PRESENT_MODES],
        'frame_rate': [value for value, _ in FRAME_RATES],
        'window_size': [value for value, _ in WINDOW_SIZES],
        'render_height': [value for value, _ in RENDER_HEIGHTS],
        'aspect': [value for value, _ in ASPECTS],
        'speakers': [value for value, _ in SPEAKERS],
        'scaler': [value for value, _ in SCALERS],
        'sharpening': [value for value, _ in SHARPENING],
        # FSR 4.1.1 and XeSS are offered only when their runtime assets are present, but a
        # setting saved by a package that had them stays valid on a package without them.
        'anti_aliasing': [value for value, _ in [*ANTI_ALIASING, FSR411, XESS]],
        'aa_preset': [value for value, _ in AA_PRESETS],
        'aa_sharpness': [value for value, _ in AA_SHARPNESS],
        'frame_generation': [value for value, _ in FRAME_GENERATION],
        'frame_gen_multiplier': [value for value, _ in FRAME_GEN_MULTIPLIERS],
        'button_prompts': [value for value, _ in BUTTON_PROMPTS],
    }
    for key in FIXES:
        values[key] = [value for value, _ in FIX_CHOICES]
    return values


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ('1', 'true', 'yes', 'on'):
        return True
    if isinstance(value, str) and value.strip().lower() in ('0', 'false', 'no', 'off', ''):
        return False
    return default


def normalize_settings(settings):
    """Coerce saved or on-screen values into what the runtime understands.

    Returns ``(settings, problems)``. Unknown keys are kept so an older launcher can still read
    a newer settings file, and every replacement is reported instead of being silently ignored.
    """
    result = dict(settings)
    problems = []
    for key, default in APP_DEFAULTS.items():
        if isinstance(default, bool) and key in result:
            result[key] = _as_bool(result[key], default)
    for key, values in allowed_setting_values().items():
        if key in result and str(result[key]) not in values:
            result[key] = APP_DEFAULTS.get(key, values[0])
            problems.append(_('Invalid value for {}.').format(key))
    for key, low, high, default in (('mouse_sensitivity', MOUSE_SENS_MIN, MOUSE_SENS_MAX, '1.0'),
                                    ('pad_vibration_strength', 0, 100, '100')):
        try:
            value = float(result.get(key, default))
        except (TypeError, ValueError):
            value = None
        if value is None or not low <= value <= high:
            result[key] = default
            problems.append(_('Invalid value for {}.').format(key))
    tokens = str(result.get('extra_env') or '').split()
    valid = [token for token in tokens if '=' in token and token.split('=', 1)[0]]
    if len(valid) != len(tokens):
        result['extra_env'] = ' '.join(valid)
        problems.append(_('Environment variables must use NAME=value.'))
    for key in ('key_bindings', 'pad_bindings', 'art_crops'):
        if not isinstance(result.get(key), dict):
            result[key] = {}
    return result, problems


def _run_problem_priority(line):
    if line.startswith(('STOP:', 'GPU crash:')):
        return 3
    if line.startswith(('ERROR', 'Fault:', 'No eboot.bin', 'No big', 'Set BB_GAME_DIR',
                        'bb-probe.exe not found')):
        return 2
    if 'failed' in line and not line.startswith('GPU ['):
        return 1
    return 0


def select_run_problem(current, output):
    """Keep the most useful diagnostic emitted before the game exits."""
    line = output.strip()
    line_priority = _run_problem_priority(line)
    return line if line_priority and line_priority >= _run_problem_priority(current) else current


# ---------------------------------------------------------------------------------------------
# Command line roles of the frozen executable.

def attach_stdio():
    """A windowed executable starts without sys.stdout; inherited pipes or a console still exist."""
    import msvcrt
    for name, std in (('stdout', -11), ('stderr', -12)):
        if getattr(sys, name) is not None:
            continue
        stream = None
        handle = ctypes.windll.kernel32.GetStdHandle(std)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                stream = open(msvcrt.open_osfhandle(handle, os.O_WRONLY), 'w', encoding='utf-8',
                              errors='replace', buffering=1)
            except OSError:
                stream = None
        setattr(sys, name, stream or open(os.devnull, 'w'))


def run_role(argv):
    """--run [args]: run.py; --script <file> [args]: a preparation script. Returns an exit code."""
    attach_stdio()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, errors='replace', **({} if stream.isatty() else {'encoding': 'utf-8'}))
        except (AttributeError, ValueError):
            pass
    if argv[0] == '--script':
        path, sys.argv = argv[1], argv[1:]
    else:
        path = str(PORT_DIR / 'run.py')
        sys.argv = [path, *argv[1:]]
    try:
        runpy.run_path(path, run_name='__main__')
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return 0


def run_command():
    return [sys.executable, '--run'] if FROZEN else [sys.executable, str(PORT_DIR / 'run.py')]


# ---------------------------------------------------------------------------------------------
# Settings.

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        if Path(path) == CONFIG_FILE:
            diagnostic_event('INFO' if isinstance(error, FileNotFoundError) else 'WARNING',
                             'configuration', f'Settings unavailable; defaults used: {type(error).__name__}')
        return default


def load_settings():
    """Defaults merged with a saved file; repairs bad values in memory without rewriting it.

    The on-disk schema has always been a flat object in settings.json. Keeping it means old
    configurations need no migration: unknown keys survive and invalid ones are repaired here.
    """
    global UI_LANGUAGE
    saved = load_json(CONFIG_FILE, {})
    if not isinstance(saved, dict):
        diagnostic_event('WARNING', 'configuration', 'Settings file is not an object; defaults used')
        saved = {}
    # Repair messages must be in the interface language chosen by the file being loaded.
    UI_LANGUAGE = uncharted_i18n.language_code(saved.get('ui_language', '') if isinstance(saved, dict) else '')
    settings, problems = normalize_settings({**APP_DEFAULTS, **saved})
    for problem in dict.fromkeys(problems):
        diagnostic_event('WARNING', 'configuration', f'Saved setting repaired: {problem}')
    return settings, list(dict.fromkeys(problems))


def ini_path():
    return Path(os.environ.get('BB_CONFIG', DATA_DIR / 'bbport.ini'))


def set_ini(key, value):
    """bbport.ini (read by the game; the in-game menu edits it): one key rewritten in place."""
    path = ini_path()
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    out, found = [], False
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#') and line.split('=', 1)[0].strip() == key:
            out.append(f'{key}={value}')
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f'{key}={value}')
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def game_info(folder):
    """{'title', 'id', 'version', 'games': [keys present]} of a game folder, or None."""
    folder = Path(folder or '.')
    if not any((folder / exe).is_file() for _k, _t, _s, exe in GAMES):
        return None
    info = {'title': 'Uncharted', 'id': folder.name, 'version': '?',
            'games': [key for key, _n, _s, exe in GAMES if (folder / exe).is_file()]}
    try:
        from prepare import sfo
        values = sfo((folder / 'sce_sys/param.sfo').read_bytes())
        info.update(title=values.get('TITLE', info['title']).replace('™', '').strip(),
                    id=values.get('TITLE_ID', info['id']), version=values.get('APP_VER', '?'))
    except (OSError, ValueError, ImportError):
        pass
    return info


def game_folder(s, key):
    """Where one game is: its own folder (Settings → Game), else the collection folder."""
    return str(s.get(f'game_dir_{key}') or '').strip() or str(s.get('game_dir') or '').strip()


def game_available(s, key):
    """The game's executable is in its folder (a game dumped on its own may have it as eboot.bin)."""
    folder = game_folder(s, key)
    if not folder:
        return False
    exe = next(e for k, _t, _s, e in GAMES if k == key)
    own = str(s.get(f'game_dir_{key}') or '').strip()
    return (Path(folder) / exe).is_file() or bool(own and (Path(folder) / 'eboot.bin').is_file())


def mouse_sensitivity(s):
    """The saved sensitivity as the game reads it: a number in the slider's range."""
    try:
        return f"{min(MOUSE_SENS_MAX, max(MOUSE_SENS_MIN, float(s.get('mouse_sensitivity')))):.2f}"
    except (TypeError, ValueError):
        return '1.00'


def vibration_strength(s):
    """Saved vibration percentage, clamped at the launcher/runtime boundary."""
    try:
        return min(100, max(0, int(float(s.get('pad_vibration_strength', 100)))))
    except (TypeError, ValueError):
        return 100


def controller_probe_path():
    """The packaged or development loader, which also provides the lightweight controller probe."""
    configured = os.environ.get('BB_PROBE')
    candidates = ([Path(configured)] if configured else []) + [PORT_DIR / 'bin' / 'bb-probe.exe',
                                                               PORT_DIR / 'out' / 'bb-probe.exe']
    return next((path for path in candidates if path.is_file()), None)


def controller_probe_environment(probe):
    """Development builds find SDL beside MSYS2; packages already keep it beside bb-probe."""
    env = dict(os.environ)
    if not (probe.parent / 'SDL3.dll').is_file():
        clang64 = Path(env.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin'
        if clang64.is_dir():
            env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
    return env


def parse_controller_probe(text):
    """Parse bb-probe's deliberately small key=value controller response."""
    values = {}
    for line in text.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return {'connected': values.get('connected') == '1',
            'name': values.get('name', ''),
            'rumble': values.get('rumble') == '1',
            'tested': values.get('tested') == '1'}


def side_buttons():
    """(Mouse 4, Mouse 5) held right now: the system's XBUTTON1 / XBUTTON2 (virtual keys 5 and 6)."""
    try:
        return tuple(bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) for vk in (5, 6))
    except (AttributeError, OSError):
        return (False, False)


def display_refresh_rate():
    """Refresh rate of the main display in Hz, or None."""
    try:
        class DevMode(ctypes.Structure):
            _fields_ = [('name', ctypes.c_wchar * 32), ('spec', ctypes.c_ushort), ('driver', ctypes.c_ushort),
                        ('size', ctypes.c_ushort), ('extra', ctypes.c_ushort), ('fields', ctypes.c_ulong),
                        ('position', ctypes.c_long * 2), ('orientation', ctypes.c_ulong),
                        ('fixed', ctypes.c_ulong), ('color', ctypes.c_short * 5), ('form', ctypes.c_wchar * 32),
                        ('logpixels', ctypes.c_ushort), ('bits', ctypes.c_ulong), ('width', ctypes.c_ulong),
                        ('height', ctypes.c_ulong), ('flags', ctypes.c_ulong), ('frequency', ctypes.c_ulong),
                        ('rest', ctypes.c_ulong * 8)]
        mode = DevMode()
        mode.size = ctypes.sizeof(DevMode)
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(mode)) and mode.frequency > 1:
            return int(mode.frequency)
    except (AttributeError, OSError):
        pass
    return None


def fsr411_assets():
    """FSR 4.1.1 needs its model assets, built from AMD's DLL (docs/upscaler.md)."""
    return bool(os.environ.get('BB_FSR411_DIR')) or any(
        (folder / 'fsr4_411').is_dir() for folder in (PORT_DIR, DATA_DIR))


def xess_available():
    """Intel XeSS needs Intel's libxess.dll next to bb-probe.exe (packaging/windows/fetch_xess.sh)."""
    override = os.environ.get('BB_XESS_DLL')
    if override:
        return Path(override).is_file()
    return any((PORT_DIR / folder / 'libxess.dll').is_file() for folder in ('bin', 'out'))


def input_ini_path():
    return Path(os.environ.get('BB_INPUT_CONFIG', DATA_DIR / 'input.ini'))


def write_input_ini(s):
    """input.ini: the keyboard, mouse and controller bindings the game reads at start."""
    keys = {**DEFAULT_KEYS, **(s.get('key_bindings') or {})}
    pad = {**DEFAULT_PAD, **(s.get('pad_bindings') or {})}
    lines = ['# Uncharted controls (written by the launcher, Settings → Controls)',
             f"mouse_look={1 if s.get('mouse_look', True) else 0}",
             f"mouse_sensitivity={mouse_sensitivity(s)}",
             f"mouse_invert_y={1 if s.get('mouse_invert_y') else 0}",
             f"pad_invert_y={1 if s.get('pad_invert_y') else 0}",
             f"pad_vibration={1 if s.get('pad_vibration', True) else 0}",
             f"pad_vibration_strength={vibration_strength(s)}"]
    lines += [f'key.{action}={keys.get(action, "")}' for action in DEFAULT_KEYS]
    lines += [f'pad.{action}={pad.get(action, "none")}' for action in DEFAULT_PAD]
    path = input_ini_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def screen_size():
    """(width, height) of the main display in pixels, or None."""
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except (AttributeError, OSError):
        return None


def parse_size(text):
    try:
        width, height = (int(part) for part in str(text).lower().replace('×', 'x').split('x'))
    except ValueError:
        return None
    return (width, height) if width > 0 and height > 0 else None


def game_resolution(s, screen=None):
    """The size the games render at, 'WxH' (even numbers, the pixel count within what their GPU memory holds), or ''
    for their own 1920x1080."""
    height = int(s.get('render_height') or 1080) if str(s.get('render_height') or '1080').isdigit() else 1080
    shape = s.get('aspect') or '16:9'
    if shape == 'window':
        size = screen if s.get('fullscreen') else parse_size(s.get('window_size'))
        size = size or screen or (1920, 1080)
        ratio = size[0] / size[1]
    else:
        ratio = ASPECT_RATIOS.get(shape, 16 / 9)
    ratio = min(max(ratio, 1.0), 4.0)
    for height in sorted({height, 2160, 1800, 1620, 1440, 1260, 1080, 900, 720}, reverse=True):
        if height > int(s.get('render_height') or 1080) if str(s.get('render_height') or '1080').isdigit() else height > 1080:
            continue
        width = SCREEN_WIDTHS.get((shape, height)) or int(round(height * ratio / 2)) * 2
        if width * height <= MAX_RENDER_PIXELS:   # the next smaller height of the same shape
            break
    return '' if (width, height) == (1920, 1080) else f'{width}x{height}'


def game_environment(s, start=None):
    env = dict(os.environ)
    env['BB_INPUT_CONFIG'] = str(write_input_ini(s))
    start = start or s['start']
    # The collection folder; with none, the folder of the game started. A game with a folder of its own
    # (Settings → Game) is taken from it, also when another game of the collection starts it.
    env['BB_GAME_DIR'] = s['game_dir'] or game_folder(s, start)
    for key, _t, _s, _e in GAMES:
        if str(s.get(f'game_dir_{key}') or '').strip():
            env[f'BB_GAME_DIR_{key.upper()}'] = game_folder(s, key)
    env['BB_EXE'] = start
    if s['user_dir']:
        env['BB_USER_DIR'] = s['user_dir']
    env['BB_LANGUAGE'] = s['language']
    if str(s['player_name']).strip():
        env['BB_USER_NAME'] = str(s['player_name']).strip()
    env['BB_FULLSCREEN'] = '1' if s['fullscreen'] else '0'
    env['BB_PRESENT_MODE'] = s['present_mode']
    env['BB_VBLANK_HZ'] = str(s.get('frame_rate') or '60')
    env['BB_WINDOW_SIZE'] = str(s.get('window_size') or '1920x1080')
    if str(s.get('speakers') or 'auto') != 'auto':
        env['BB_SPEAKERS'] = str(s['speakers'])
    resolution = game_resolution(s, screen_size())
    if resolution:
        env['BB_GAME_RES'] = resolution
    window = parse_size(s.get('window_size'))
    if not s.get('fullscreen') and resolution and window and s.get('aspect', '16:9') != '16:9':
        shape = parse_size(resolution)
        # A window of another shape than the picture would show bars: keep its height, take the picture's width.
        env['BB_WINDOW_SIZE'] = f'{round(window[1] * shape[0] / shape[1] / 2) * 2}x{window[1]}'
    env['BB_FSR1'] = '1' if s.get('scaler', 'fsr1') == 'fsr1' else '0'
    sharpening = str(s.get('sharpening') or 'off')
    env['BB_RCAS'] = '0' if sharpening == 'off' else '1'
    if sharpening != 'off':
        env['BB_RCAS_ATTENUATION'] = sharpening
    env['BB_UPSCALER'] = str(s.get('anti_aliasing') or 'off')
    env['BB_FSR_SHARPNESS'] = str(s.get('aa_sharpness') or '0')
    # '' leaves the preset saved by the in-game menu alone; a choice pins render resolution
    # for this launch (BB_UPSCALE_PRESET) and, for a scaled preset, lets Uncharted's
    # velocity-mode camera use it (BB_SR_VELOCITY).
    preset = str(s.get('aa_preset') or '')
    if preset in AA_SR_VELOCITY:
        if preset:
            env['BB_UPSCALE_PRESET'] = preset
        if AA_SR_VELOCITY[preset]:
            env['BB_SR_VELOCITY'] = AA_SR_VELOCITY[preset]
    env['BB_FRAME_GEN'] = str(s.get('frame_generation') or 'off')
    env['BB_FRAME_GEN_MULTIPLIER'] = str(s.get('frame_gen_multiplier') or '2')
    for key, (_title, _hint, choices) in FIXES.items():
        env.update(choices.get(str(s.get(key) or 'auto'), {}))
    env['BB_BUTTON_PROMPTS'] = str(s.get('button_prompts') or 'auto')
    env['BB_CHEATS'] = ','.join(f'{game}:{cheat}' for game, _t, _s, _e in GAMES for cheat in ('health', 'ammo')
                                if s.get(f'{game}_infinite_{cheat}'))
    for item in str(s['extra_env']).split():
        if '=' in item:
            key, value = item.split('=', 1)
            env[key] = value
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'  # open() and the pipes must not fall back to the ANSI code page (GBK, ...)
    return env


# ---------------------------------------------------------------------------------------------
# Surfaces: near-black base, raised panels, Uncharted gold accent.

BG, PANEL, SURFACE, CARD, LINE = '#08090a', '#0c0d0f', '#0f1012', '#17181b', '#26282c'
TEXT, MUTED, GOLD, GOLD_HI, GOLD_DIM = '#f3efe6', '#8e8a82', '#e7c36a', '#f3d78a', '#a88942'
CARD_HI = '#202226'
COLORS = {'surface': SURFACE, 'card': CARD, 'card_hi': CARD_HI, 'line': LINE, 'text': TEXT, 'muted': MUTED,
          'gold': GOLD, 'gold_hi': GOLD_HI, 'track': '#2c2e33', 'knob': '#9a958b', 'knob_on': '#f7f1e2',
          'button': '#25272b', 'button_hi': '#30333a'}
# Two destinations. Sidebar icons: Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets on 10.
NAV_ITEMS = (('play', 'Play'), ('settings', 'Settings'))
NAV_ICONS = {'play': '\ue768', 'settings': '\ue713'}
# Trophy grades as the console colours them (the platinum cool white-blue, the rest metal).
TROPHY_COLORS = {'P': '#9fd0f5', 'G': GOLD, 'S': '#cfd3da', 'B': '#cf8548'}


class Launcher:
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        global UI_LANGUAGE
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = root
        self.app, self.settings_problems = load_settings()
        if DIAGNOSTICS:
            DIAGNOSTICS.redact.learn(self.app)
            DIAGNOSTICS.stage('configuration.loaded')
        UI_LANGUAGE = uncharted_i18n.language_code(self.app.get('ui_language', ''))
        self.configure_art()
        self.vars = {}
        self.process = self.job = None
        self.entered, self.last_problem, self.stopping = False, '', False
        self.output = queue.Queue(maxsize=4096)
        self.ui_calls = queue.Queue()
        self.info, self.available = None, {}
        self.gpu_vram_mb = None  # bb-gpu-capabilities --memory; see uncharted_hw (README 4 GB note)
        self.update_box = None
        self.save_note = None
        self.trophy_dialog = None
        self.section_anchors, self.current_card = {}, None
        root.title(f'{NAME} — The Nathan Drake Collection for Windows · by {AUTHOR}')
        root.configure(bg=BG)
        self.dpi = root.winfo_fpixels('1i') / 96.0
        root.geometry(f'{self.px(1180)}x{self.px(860)}')
        root.minsize(self.px(1040), self.px(760))
        self.set_icon()
        self.load_fonts()
        self.style()
        self.build()
        self.show('play')
        if self.settings_problems:
            root.after(200, lambda: self.messagebox.showwarning(NAME, '\n'.join(self.settings_problems)))
        root.protocol('WM_DELETE_WINDOW', self.close)
        if DIAGNOSTICS:
            DIAGNOSTICS.on_error = lambda text: self.ui_calls.put(
                lambda: self.messagebox.showwarning(NAME, _('Local diagnostics could not be written: {}').format(text)))
            for text in DIAGNOSTICS.errors[:1]:
                DIAGNOSTICS.on_error(text)
            DIAGNOSTICS.stage('launcher.ready')
        root.report_callback_exception = self.callback_exception
        root.after(100, self.drain_output)
        root.after(150, self.detect_controller)
        root.after(1500, self.poll_trophies)
        if self.app.get('check_updates', True):
            threading.Thread(target=self.check_update, daemon=True).start()
        threading.Thread(target=self.detect_gpu, daemon=True).start()

    def px(self, size):
        return int(size * self.dpi)

    def set_icon(self):
        try:
            self.root.iconbitmap(default=str(PORT_DIR / 'launcher' / 'uncharted.ico'))
        except self.tk.TclError:
            pass

    def load_fonts(self):
        """Cinzel for headings (Uncharted-style capitals); Segoe UI for body text."""
        from uncharted_fonts import font_path

        self.brand_family = 'Cinzel'
        for name in ('Cinzel-Regular.ttf', 'Cinzel-SemiBold.ttf', 'Cinzel-Bold.ttf'):
            path = font_path(name)
            if path:
                try:
                    ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)
                except (AttributeError, OSError):
                    pass
        if not font_path('Cinzel-Bold.ttf'):
            self.brand_family = 'Georgia'
        self.font_brand_xl = (self.brand_family, 24, 'bold')
        self.font_brand_lg = (self.brand_family, 18, 'bold')
        self.font_brand_md = (self.brand_family, 13, 'bold')
        self.font_brand_sm = (self.brand_family, 10)
        self.font_ui = ('Segoe UI', 10)
        self.font_ui_sm = ('Segoe UI', 9)
        self.font_ui_md = ('Segoe UI Semibold', 10)
        self.font_button = ('Segoe UI Semibold', 10)
        self.font_play = ('Segoe UI Semibold', 13)
        self.font_title = ('Segoe UI Semibold', 22)
        self.font_card = ('Segoe UI Semibold', 9)
        import tkinter.font as tkfont
        families = set(tkfont.families(self.root))
        icons = next((f for f in ('Segoe Fluent Icons', 'Segoe MDL2 Assets') if f in families), None)
        self.font_icon = (icons, 13) if icons else None
        self.font_icon_sm = (icons, 11) if icons else None

    # ---- look --------------------------------------------------------------------------------
    def style(self):
        s = self.ttk.Style(self.root)
        s.theme_use('clam')
        base = self.font_ui
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', GOLD)
        self.root.option_add('*TCombobox*Listbox.font', base)
        s.configure('.', background=SURFACE, foreground=TEXT, fieldbackground=CARD, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=CARD, focuscolor=GOLD, font=base)
        s.configure('TFrame', background=SURFACE)
        s.configure('Shell.TFrame', background=SURFACE)
        s.configure('TLabel', background=CARD, foreground=TEXT)
        s.configure('Muted.TLabel', background=CARD, foreground=MUTED, font=('Segoe UI', 9))
        s.configure('Section.TLabel', background=SURFACE, foreground=GOLD_DIM,
                    font=(self.brand_family, 10, 'bold'))
        s.configure('TCombobox', arrowcolor=GOLD, foreground=TEXT, padding=(8, 5), bordercolor=CARD_HI,
                    lightcolor=CARD_HI, darkcolor=CARD_HI, background=CARD_HI, arrowsize=14)
        s.map('TCombobox', fieldbackground=[('readonly', CARD_HI)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', CARD_HI)], selectforeground=[('readonly', TEXT)],
              bordercolor=[('focus', GOLD_DIM)], lightcolor=[('focus', GOLD_DIM)], background=[('active', '#2a2c31')])
        s.configure('TEntry', foreground=TEXT, insertcolor=TEXT, padding=(8, 5), fieldbackground=CARD_HI,
                    bordercolor=CARD_HI, lightcolor=CARD_HI, darkcolor=CARD_HI)
        s.map('TEntry', bordercolor=[('focus', GOLD_DIM)], lightcolor=[('focus', GOLD_DIM)])
        s.configure('TButton', background='#221f1a', foreground=TEXT, padding=(14, 7), borderwidth=0)
        s.map('TButton', background=[('active', '#322c24'), ('disabled', PANEL)], foreground=[('disabled', MUTED)])
        s.configure('Vertical.TScrollbar', background='#2a2c31', arrowcolor=MUTED, troughcolor=SURFACE,
                    bordercolor=SURFACE, lightcolor='#2a2c31', darkcolor='#2a2c31', gripcount=0, arrowsize=12)
        s.map('Vertical.TScrollbar', background=[('active', '#3a3d44')])

    # ---- widget helpers ----------------------------------------------------------------------
    def var(self, key):
        if key not in self.vars:
            value = self.app.get(key, APP_DEFAULTS[key])
            v = self.tk.BooleanVar(value=bool(value)) if isinstance(APP_DEFAULTS[key], bool) \
                else self.tk.StringVar(value=str(value))
            self.vars[key] = v
        return self.vars[key]

    def choice(self, parent, key, options, width=36, on_change=None):
        var = self.var(key)
        values = [v for v, _t in options]
        box = self.ttk.Combobox(parent, values=[_(t) for _v, t in options], state='readonly', width=width)
        if var.get() not in values:
            var.set(values[0])

        def show(*_args):
            box.current(values.index(var.get()) if var.get() in values else 0)
        show()
        def selected(_event):
            var.set(values[box.current()])
            if on_change:
                on_change()
        box.bind('<<ComboboxSelected>>', selected)
        var.trace_add('write', show)
        return box

    def next_row(self, parent):
        return parent.grid_size()[1]

    def target(self, parent):
        """Where a page's rows go: its current card (one is opened when rows come before a section)."""
        if parent in self.page_cards:
            if self.page_cards[parent] is None:
                self.new_card(parent, None)
            self.page_cards[parent].refit()
            return self.page_cards[parent].inner
        return parent

    def new_card(self, page, title):
        from uncharted_widgets import Card
        card = Card(self.tk, page, COLORS, self.px, title, self.font_card)
        card.canvas.grid(row=self.next_row(page), column=0, columnspan=2, sticky='ew', pady=(0, self.px(14)))
        self.page_cards[page] = card
        self.current_card = card
        return card

    def toggle(self, parent, variable, background=CARD):
        from uncharted_widgets import Toggle
        return Toggle(self.tk, parent, variable, COLORS, self.px, background)

    def pill(self, parent, text, command, kind='secondary', background=CARD, padding=(16, 8), icon=None):
        from uncharted_widgets import PillButton
        return PillButton(self.tk, parent, text, command, COLORS, self.px,
                          self.font_play if kind == 'primary' and icon else self.font_button, kind,
                          background, padding, icon, self.font_icon_sm)

    def row(self, parent, title, widget, hint=None):
        parent = self.target(parent)
        r = self.next_row(parent)
        self.ttk.Label(parent, text=title).grid(row=r, column=0, sticky='nw', padx=(0, 24), pady=(9, 0))
        widget.grid(row=r, column=1, sticky='w', pady=(5, 0), in_=parent)
        widget.lift()
        if hint:
            self.ttk.Label(parent, text=hint, style='Muted.TLabel', wraplength=self.px(560), justify='left').grid(
                row=r + 1, column=1, sticky='w', pady=(3, 4))
        return widget

    def check(self, parent, key, title, hint=None):
        parent = self.target(parent)
        r = self.next_row(parent)
        line = self.tk.Frame(parent, bg=CARD)
        line.grid(row=r, column=0, columnspan=2, sticky='w', pady=(7, 0))
        self.toggle(line, self.var(key)).pack(side='left')
        label = self.tk.Label(line, text=title, bg=CARD, fg=TEXT, font=self.font_ui, cursor='hand2')
        label.pack(side='left', padx=(self.px(12), 0))
        label.bind('<Button-1>', lambda _e: self.var(key).set(not self.var(key).get()))
        if hint:
            self.ttk.Label(parent, text=hint, style='Muted.TLabel', wraplength=self.px(640), justify='left').grid(
                row=r + 1, column=0, columnspan=2, sticky='w', padx=(self.px(52), 0), pady=(2, 2))

    def note(self, parent, text, top=12):
        parent = self.target(parent)
        self.ttk.Label(parent, text=text, style='Muted.TLabel', wraplength=self.px(680), justify='left').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 0))

    def section(self, parent, title, top=18):
        if parent in self.page_cards:
            return self.new_card(parent, title).inner
        self.ttk.Label(parent, text=title, style='Section.TLabel').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 2))
        return parent

    def folder(self, parent, key, title, prompt, hint=None, on_change=None):
        var = self.var(key)
        holder = self.tk.Frame(self.target(parent), bg=CARD)
        self.ttk.Entry(holder, textvariable=var, width=54).pack(side='left')

        def browse():
            chosen = self.filedialog.askdirectory(title=prompt, initialdir=var.get() or str(PORT_DIR))
            if chosen:
                var.set(str(Path(chosen)))
        self.pill(holder, _('Browse…'), browse, padding=(14, 6)).pack(side='left', padx=(8, 0))
        self.pill(holder, _('Open'), lambda: self.open_path(var.get(), key), kind='ghost',
                  padding=(12, 6)).pack(side='left', padx=(4, 0))
        if on_change:
            var.trace_add('write', lambda *_a: on_change())
        return self.row(parent, title, holder, hint)

    def image_path(self, title, current=''):
        """Choose and validate a local image."""
        chosen = self.filedialog.askopenfilename(
            title=title, initialdir=str(Path(current).parent) if current else str(PORT_DIR),
            filetypes=[(_('Image files'), '*.png *.jpg *.jpeg *.webp *.ico'), (_('All files'), '*.*')])
        if not chosen:
            return ''
        try:
            from PIL import Image
            with Image.open(chosen) as image:
                image.verify()
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
            return ''
        return str(Path(chosen))

    def enable_image_drop(self, widget, callback):
        """Accept Explorer file drops when tkinterdnd2 is bundled; clicking always remains available."""
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', lambda event: callback(self.root.tk.splitlist(event.data)[0]))
        except (ImportError, self.tk.TclError, AttributeError, IndexError):
            pass

    def edit_art(self, key, title, size, crop_key, initial_path=None, initial_crop=None):
        """Preview, position and save one local image without copying it into the project."""
        from PIL import Image, ImageTk
        import uncharted_art

        window = self.tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        path = self.tk.StringVar(value=self.app.get(key, '') if initial_path is None else initial_path)
        saved = (self.app.setdefault('art_crops', {}).get(crop_key, {})
                 if initial_crop is None else initial_crop)
        result = {'value': None}
        zoom = self.tk.DoubleVar(value=float(saved.get('zoom', 1.0)))
        focus_x = self.tk.DoubleVar(value=float(saved.get('x', 0.5)))
        focus_y = self.tk.DoubleVar(value=float(saved.get('y', 0.5)))
        preview_w = self.px(560)
        preview_h = max(self.px(180), round(preview_w * size[1] / size[0]))
        if preview_h > self.px(390):
            preview_h = self.px(390)
            preview_w = round(preview_h * size[0] / size[1])
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=title, bg=PANEL, fg=TEXT, font=self.font_title).pack(anchor='w')
        self.tk.Label(shell, text=_('Drag an image here or click Choose image.'), bg=PANEL, fg=MUTED,
                      font=self.font_ui).pack(anchor='w', pady=(2, self.px(12)))
        canvas = self.tk.Canvas(shell, width=preview_w, height=preview_h, bg=BG, highlightthickness=2,
                                highlightbackground=GOLD_DIM, bd=0, cursor='hand2')
        canvas.pack()
        preview = {'photo': None}

        def redraw(*_args):
            canvas.delete('all')
            try:
                with Image.open(path.get()) as source:
                    crop = {'zoom': zoom.get(), 'x': focus_x.get(), 'y': focus_y.get()}
                    image = uncharted_art.crop_image(source.convert('RGB'), (preview_w, preview_h), crop)
                preview['photo'] = ImageTk.PhotoImage(image)
                canvas.create_image(0, 0, image=preview['photo'], anchor='nw')
            except (OSError, ValueError, self.tk.TclError):
                preview['photo'] = None
                canvas.create_text(preview_w // 2, preview_h // 2, text=_('Choose image'), fill=MUTED,
                                   font=self.font_ui_md)
            canvas.create_rectangle(1, 1, preview_w - 2, preview_h - 2, outline=GOLD, width=2)

        def choose(candidate=''):
            chosen = candidate or self.image_path(title, path.get())
            if chosen:
                try:
                    with Image.open(chosen) as image:
                        image.verify()
                except (OSError, ValueError) as error:
                    self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                    return
                path.set(str(Path(chosen)))
                redraw()

        controls = self.tk.Frame(shell, bg=PANEL)
        controls.pack(fill='x', pady=(self.px(14), 0))
        for row, (label, variable, start, end, step) in enumerate((
                (_('Zoom'), zoom, 1.0, 3.0, 0.01), (_('Horizontal position'), focus_x, 0.0, 1.0, 0.01),
                (_('Vertical position'), focus_y, 0.0, 1.0, 0.01))):
            self.tk.Label(controls, text=label, bg=PANEL, fg=TEXT, font=self.font_ui,
                          width=20, anchor='w').grid(row=row, column=0, sticky='w', pady=2)
            scale = self.tk.Scale(controls, variable=variable, from_=start, to=end, resolution=step,
                                  orient='horizontal', showvalue=False, length=self.px(380), bg=PANEL,
                                  fg=TEXT, troughcolor=CARD_HI, activebackground=GOLD, highlightthickness=0,
                                  command=redraw)
            scale.grid(row=row, column=1, sticky='ew', pady=2)
        buttons = self.tk.Frame(shell, bg=PANEL)
        buttons.pack(fill='x', pady=(self.px(16), 0))
        self.pill(buttons, _('Choose image'), choose, background=PANEL).pack(side='left')

        def restore():
            path.set('')
            zoom.set(1.0)
            focus_x.set(0.5)
            focus_y.set(0.5)
            redraw()

        self.pill(buttons, _('Restore'), restore, kind='ghost', background=PANEL).pack(side='left', padx=(6, 0))
        self.pill(buttons, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def save():
            result['value'] = (path.get(), {'zoom': zoom.get(), 'x': focus_x.get(), 'y': focus_y.get()})
            window.destroy()

        self.pill(buttons, _('Save changes'), save, kind='primary', background=PANEL).pack(side='right', padx=(0, 6))
        canvas.bind('<Button-1>', lambda _event: choose())
        self.enable_image_drop(canvas, choose)
        for variable in (path, zoom, focus_x, focus_y):
            variable.trace_add('write', redraw)
        redraw()
        window.bind('<Escape>', lambda _event: window.destroy())
        window.wait_window()
        return result['value']

    def page(self, name, title, subtitle, action=None):
        """One scrollable destination; `action` adds a header button and a short result label."""
        tk = self.tk
        outer = tk.Frame(self.content, bg=SURFACE)
        shell = tk.Frame(outer, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(18), pady=(self.px(14), 0))
        head = tk.Frame(shell, bg=SURFACE)
        head.pack(fill='x', padx=self.px(26), pady=(self.px(18), self.px(14)))
        tk.Label(head, text=title, bg=SURFACE, fg=TEXT, font=self.font_title).pack(anchor='w')
        if subtitle:
            tk.Label(head, text=subtitle, bg=SURFACE, fg=MUTED, font=self.font_ui).pack(anchor='w', pady=(2, 0))
        if action:
            label, command = action
            self.save_note = tk.Label(head, text='', bg=SURFACE, fg=GOLD, font=self.font_ui_sm)
            self.save_note.pack(side='right', padx=(0, 14))
            self.pill(head, label, command, kind='primary', background=SURFACE,
                      padding=(18, 7)).pack(side='right')
        body = tk.Frame(shell, bg=SURFACE)
        body.pack(fill='both', expand=True)
        canvas = tk.Canvas(body, bg=SURFACE, highlightthickness=0, bd=0)
        bar = self.ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        inner = self.ttk.Frame(canvas, style='Shell.TFrame', padding=(26, 0, 26, 12))
        inner.columnconfigure(1, weight=1)
        self.page_cards[inner] = None
        window = canvas.create_window(0, 0, window=inner, anchor='nw')

        def fit(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), inner.winfo_reqheight()))
            if inner.winfo_reqheight() > canvas.winfo_height():
                bar.pack(side='right', fill='y')
            else:
                bar.pack_forget()
                canvas.yview_moveto(0)
        inner.bind('<Configure>', fit)
        canvas.bind('<Configure>', fit)

        self.scrollers[name] = (canvas, bar)
        self.page_inners[name] = inner
        self.pages[name] = outer
        return inner

    def on_wheel(self, event):
        """The mouse wheel scrolls the page under the pointer (also over its buttons and lists;
        lists do not change their choice); while a control is being bound it is the binding."""
        if getattr(self, 'capturing', None):
            return self.captured_wheel(event)
        scroller = self.scrollers.get(self.current_page)
        if scroller:
            canvas, bar = scroller
            x, y = self.root.winfo_pointerxy()
            inside = (canvas.winfo_rootx() <= x < canvas.winfo_rootx() + canvas.winfo_width() and
                      canvas.winfo_rooty() <= y < canvas.winfo_rooty() + canvas.winfo_height())
            if inside and bar.winfo_ismapped():
                canvas.yview_scroll(-1 * (event.delta // 120), 'units')
        return 'break'

    # ---- layout ------------------------------------------------------------------------------
    def build(self):
        tk = self.tk
        side_w = self.px(214)
        side = tk.Frame(self.root, bg=BG, width=side_w)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        tk.Label(side, text='UNCHARTED', bg=BG, fg=GOLD, font=self.font_brand_lg,
                 anchor='w').pack(anchor='w', padx=18, pady=(26, 0))
        tk.Label(side, text=_('version {}').format(VERSION), bg=BG, fg=MUTED,
                 font=self.font_ui_sm, anchor='w').pack(anchor='w', padx=18, pady=(2, 0))
        tk.Frame(side, bg=LINE, height=1).pack(fill='x', padx=18, pady=(18, 14))
        self.nav, self.nav_pills, self.nav_icons, self.nav_marks, self.current_page = {}, {}, {}, {}, None
        for name, title in NAV_ITEMS:
            row = tk.Frame(side, bg=BG, cursor='hand2')
            row.pack(fill='x', padx=(0, 12), pady=2)
            mark = tk.Frame(row, bg=BG, width=self.px(3))
            mark.pack(side='left', fill='y')
            pill = tk.Frame(row, bg=BG, cursor='hand2')
            pill.pack(side='left', fill='x', expand=True, padx=(self.px(9), 0))
            widgets = [row, pill]
            if self.font_icon:
                icon = tk.Label(pill, text=NAV_ICONS[name], bg=BG, fg=MUTED, font=self.font_icon,
                                padx=self.px(12), pady=self.px(9), cursor='hand2')
                icon.pack(side='left')
                self.nav_icons[name] = icon
                widgets.append(icon)
            item = tk.Label(pill, text=_(title), bg=BG, fg=TEXT, anchor='w', font=self.font_ui_md,
                            padx=self.px(2 if self.font_icon else 14), pady=self.px(9), cursor='hand2')
            item.pack(side='left', fill='x', expand=True)
            widgets.append(item)
            for widget in widgets:
                widget.bind('<Button-1>', lambda _e, n=name: self.show(n))
                widget.bind('<Enter>', lambda _e, n=name: self._nav_hover(n, True))
                widget.bind('<Leave>', lambda _e, n=name: self._nav_hover(n, False))
            self.nav[name], self.nav_pills[name], self.nav_marks[name] = item, pill, mark
        tk.Frame(side, bg=BG).pack(fill='both', expand=True)
        self.side = side
        self.side_note = tk.Label(side, text='', bg=BG, fg=MUTED, font=self.font_ui_sm)
        self.side_note.pack(anchor='w', padx=18, pady=(0, 18))

        tk.Frame(self.root, bg=LINE, width=1).pack(side='left', fill='y')
        right = tk.Frame(self.root, bg=SURFACE)
        right.pack(side='left', fill='both', expand=True)
        dock = tk.Frame(right, bg=PANEL, height=self.px(52))
        dock.pack(fill='x', side='bottom')
        dock.pack_propagate(False)
        tk.Frame(dock, bg=LINE, height=1).pack(fill='x')
        self.status = tk.Label(dock, text='', bg=PANEL, fg=MUTED, font=self.font_ui, anchor='w',
                               justify='left', wraplength=self.px(760))
        self.status.pack(side='left', padx=24, pady=10, fill='x', expand=True)
        self.content = tk.Frame(right, bg=SURFACE)
        self.content.pack(fill='both', expand=True)

        self.pages, self.scrollers, self.page_inners, self.page_cards = {}, {}, {}, {}
        self.section_anchors, self.current_card = {}, None
        self.root.bind_all('<MouseWheel>', self.on_wheel)
        self.root.bind_class('TCombobox', '<MouseWheel>', self.on_wheel)
        self.build_play()
        self.build_settings()
        self.refresh()

    def _nav_style(self, name, active=False, hover=False):
        if active:
            bg, fg, icon = '#17181b', TEXT, GOLD
        elif hover:
            bg, fg, icon = '#111214', TEXT, TEXT
        else:
            bg, fg, icon = BG, MUTED, MUTED
        self.nav_pills[name].configure(bg=bg)
        self.nav[name].configure(bg=bg, fg=fg)
        if name in self.nav_icons:
            self.nav_icons[name].configure(bg=bg, fg=icon)
        self.nav_marks[name].configure(bg=GOLD if active else BG)

    def _nav_hover(self, name, inside):
        if name == self.current_page:
            return
        self._nav_style(name, hover=inside)

    def show(self, name):
        """Show a destination; names of the former pages all lead to Settings."""
        name = name if name in ('play', 'settings') else 'settings'
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill='both', expand=True)
        self.current_page = name
        for nav_name in self.nav:
            self._nav_style(nav_name, active=(nav_name == name))
        if name == 'play':
            self.refresh()

    def show_support(self):
        """Reveal the runtime log after a failed launch, without a third destination."""
        self.show('settings')
        self.jump_to_section('support')

    def jump_to_section(self, key):
        canvas = self.scrollers.get('settings', (None,))[0]
        widget = self.section_anchors.get(key)
        if canvas is None or widget is None:
            return
        self.root.update_idletasks()
        total = self.page_inners['settings'].winfo_reqheight()
        if total > 0:
            canvas.yview_moveto(max(0.0, min(1.0, widget.winfo_y() / total)))

    def begin_section(self, f, key, title, top=18):
        self.section(f, title, top)
        if self.current_card:
            self.section_anchors[key] = self.current_card.canvas

    def subsection(self, parent, title, top=14):
        """A lighter heading inside a settings section (not a new card or destination)."""
        parent = self.target(parent)
        self.ttk.Label(parent, text=title, style='Section.TLabel').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 2))

    def build_play(self):
        """The default destination: choose the content, see its state, launch it once."""
        tk = self.tk
        page = tk.Frame(self.content, bg=SURFACE)
        self.pages['play'] = page
        shell = tk.Frame(page, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(34), pady=(self.px(26), self.px(16)))
        tk.Label(shell, text=_('Play'), bg=SURFACE, fg=TEXT, font=self.font_title).pack(anchor='w')
        self.play_title = tk.Label(shell, text='', bg=SURFACE, fg=GOLD_HI,
                                   font=self.font_brand_xl, anchor='w', justify='left')
        self.play_title.pack(anchor='w', pady=(self.px(20), 0))
        self.play_detail = tk.Label(shell, text='', bg=SURFACE, fg=MUTED, font=self.font_ui,
                                    anchor='w', justify='left')
        self.play_detail.pack(anchor='w', pady=(4, self.px(24)))

        form = tk.Frame(shell, bg=SURFACE)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        tk.Label(form, text=_('Game'), bg=SURFACE, fg=TEXT,
                 font=self.font_ui_md).grid(row=0, column=0, sticky='w', pady=(0, 10))
        self.game_choice = self.choice(form, 'start', [(key, title) for key, title, _s, _e in GAMES], 44)
        self.game_choice.grid(row=0, column=1, sticky='w', pady=(0, 10))
        tk.Label(form, text=_('Game folder'), bg=SURFACE, fg=TEXT,
                 font=self.font_ui_md).grid(row=1, column=0, sticky='w', pady=(0, 4))
        location = tk.Frame(form, bg=SURFACE)
        location.grid(row=1, column=1, sticky='ew', pady=(0, 4))
        self.ttk.Entry(location, textvariable=self.var('game_dir'), width=58).pack(
            side='left', fill='x', expand=True)

        def browse():
            chosen = self.filedialog.askdirectory(title=_('Choose the game folder with eboot.bin.'),
                                                  initialdir=self.var('game_dir').get() or str(PORT_DIR))
            if chosen:
                self.var('game_dir').set(str(Path(chosen)))
        self.pill(location, _('Browse…'), browse, background=SURFACE,
                  padding=(14, 6)).pack(side='left', padx=(8, 0))
        self.pill(location, _('Open'), lambda: self.open_path(self.var('game_dir').get(), 'game_dir'),
                  kind='ghost', background=SURFACE, padding=(12, 6)).pack(side='left', padx=(4, 0))

        self.ready = tk.Label(shell, text='', bg=SURFACE, fg=GOLD, font=self.font_ui_md,
                              wraplength=self.px(760), justify='left', anchor='w')
        self.ready.pack(fill='x', pady=(self.px(22), self.px(4)))
        self.play_hint = tk.Label(shell, text='', bg=SURFACE, fg=MUTED, font=self.font_ui_sm,
                                  wraplength=self.px(760), justify='left', anchor='w')
        self.play_hint.pack(fill='x')

        actions = tk.Frame(shell, bg=SURFACE)
        actions.pack(anchor='w', pady=(self.px(32), 0))
        self.play_button = self.pill(actions, _('Play'), self.play, kind='primary',
                                     background=SURFACE, padding=(56, 13),
                                     icon='\ue768' if self.font_icon else None)
        self.play_button.pack(side='left')
        self.stop_button = self.pill(actions, _('Stop'), self.stop, background=SURFACE,
                                     padding=(28, 13))
        self.stop_button.configure(state='disabled')
        self.stop_button.pack(side='left', padx=(12, 0))
        self.var('start').trace_add('write', lambda *_a: self.refresh())
        self.var('game_dir').trace_add('write', lambda *_a: self.refresh())

    # ---- settings ----------------------------------------------------------------------------
    def build_settings(self):
        f = self.page('settings', _('Settings'), '',
                      action=(_('Save settings'), self.save_settings))
        index = self.tk.Frame(f, bg=SURFACE)
        index.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(0, self.px(8)))
        sections = (('game', _('Game')), ('display', _('Display')), ('graphics', _('Graphics')),
                    ('audio', _('Audio')), ('controls', _('Controls')), ('fixes', _('Fixes')),
                    ('cheats', _('Cheats')), ('advanced', _('Advanced')), ('support', _('Support')))
        for key, label in sections:
            self.pill(index, label, lambda k=key: self.jump_to_section(k), kind='ghost',
                      background=SURFACE, padding=(11, 5)).pack(side='left', padx=(0, 5), pady=(0, 4))
        self.build_content_section(f)
        self.build_display_section(f)
        self.build_graphics_section(f)
        self.build_audio_section(f)
        self.build_controls_section(f)
        self.build_fixes_section(f)
        self.build_cheats_section(f)
        self.build_advanced_section(f)
        self.build_support_section(f)

    def build_content_section(self, f):
        self.begin_section(f, 'game', _('Game'))
        self.folder(f, 'game_dir', _('Game folder'), _('The folder with eboot.bin'),
                    _('The folder with eboot.bin, big2-ps4_Shipping.elf and big3-ps4_Shipping.elf. '
                      'Leave it empty if every game has a folder of its own, below.'),
                    on_change=self.refresh)
        self.folder(f, 'user_dir', _('Saves folder'), _('Where saves and the shader cache are kept'),
                    _('Empty: the user folder next to the launcher.'))
        self.note(f, _('Only when the games are not in one collection folder (each dumped on its own). A game with '
                       'a folder here is taken from it; the others from the collection folder above.'), top=8)
        for key, _title, subtitle, _exe in GAMES:
            self.folder(f, f'game_dir_{key}', _(subtitle.split(' · ')[0]), _('The folder of this game'),
                        _('Empty: the collection folder.'), on_change=self.refresh)
        self.row(f, _('Language'), self.choice(f, 'language', LANGUAGES, 30),
                 _('The language the PS4 would be set to; the games use it for text and speech.'))
        name = self.ttk.Entry(f, textvariable=self.var('player_name'), width=30)
        self.row(f, _('Player name'), name, _('Shown where the game uses the PSN name.'))
        holder = self.tk.Frame(self.target(f), bg=CARD)
        holder.grid(row=self.next_row(self.target(f)), column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.pill(holder, _('Trophies'), self.open_trophies).pack(side='left')

    def build_display_section(self, f):
        self.begin_section(f, 'display', _('Display'))
        self.check(f, 'fullscreen', _('Fullscreen'))
        self.row(f, _('Presentation'), self.choice(f, 'present_mode', PRESENT_MODES, 34),
                 _('Mailbox shows each frame as soon as it is ready without tearing; FIFO waits for the display.'))
        hint = _('Gameplay keeps its normal speed at every setting; the cutscene videos always play at '
                 '60 FPS. Above your display\'s refresh rate use Mailbox or Immediate.')
        refresh = display_refresh_rate()
        if refresh:
            hint += ' ' + _('Your display runs at {} Hz.').format(refresh)
        self.row(f, _('Frame rate'), self.choice(f, 'frame_rate', FRAME_RATES, 34), hint)
        self.row(f, _('Render resolution'), self.choice(f, 'render_height', RENDER_HEIGHTS, 34),
                 _('The size the games draw at, so a 1440p or 4K screen gets a sharper picture, not an enlarged '
                   '1080p one. It needs more graphics memory and a faster GPU; the games\' GPU memory limits the '
                   'largest size.'))
        self.row(f, _('Aspect ratio'), self.choice(f, 'aspect', ASPECTS, 34),
                 _('Ultrawide screens: the games draw a wider view (more at the sides) instead of stretching the '
                   '16:9 picture. "The shape of the window or screen" follows the window size below, or the '
                   'screen in fullscreen.'))
        self.row(f, _('Window size'), self.choice(f, 'window_size', WINDOW_SIZES, 34),
                 _('The window the picture is shown in; fullscreen uses the screen. A picture of another size is '
                   'scaled to fit (FSR 1 below when it is enlarged).'))
        self.row(f, _('Upscaling'), self.choice(f, 'scaler', SCALERS, 34),
                 _('FSR 1 keeps edges sharp when the image is enlarged; bilinear is softer.'))
        self.row(f, _('Sharpening'), self.choice(f, 'sharpening', SHARPENING, 34),
                 _('AMD RCAS after FSR 1 (only while the image is enlarged).'))
        self.check(f, 'show_fps', _('Show the FPS counter'))

    def build_graphics_section(self, f):
        self.begin_section(f, 'graphics', _('Graphics'))
        modes = ANTI_ALIASING + ([FSR411] if fsr411_assets() else []) + ([XESS] if xess_available() else [])
        self.row(f, _('Anti-aliasing'), self.choice(f, 'anti_aliasing', modes, 34),
                 _('Temporal reconstruction with the games\' own motion vectors: smoother edges and '
                   'less shimmering than the games\' own. Native AA keeps 1920 × 1080; a preset '
                   'below renders smaller and reconstructs 1920 × 1080. DLSS needs a GeForce RTX '
                   '(Super Resolution works on RTX 20 series and newer); FSR 4 runs best on a '
                   'Radeon RX 7000/9000 or a GeForce RTX 40/50. Intel XeSS runs on any GPU.'))
        self.row(f, _('Render size'), self.choice(f, 'aa_preset', AA_PRESETS, 34),
                 _('What the upscaler renders and then reconstructs to 1920 × 1080. Quality is '
                   '1280 × 720: DLSS Super Resolution turns that into a 1920 × 1080 image, which is '
                   'real upscaling, unlike Native AA. Frame generation stays off unless you choose '
                   'it below.'))
        self.row(f, _('Anti-aliasing sharpness'), self.choice(f, 'aa_sharpness', AA_SHARPNESS, 34))
        hint = _('Frames made between two of the game\'s, for smoother motion: 60 FPS shows as 120. '
                 'Use Mailbox or Immediate presentation and a display of at least that refresh rate. '
                 'Adds a little input latency; menus and cutscene videos are not generated. Use it '
                 'with the 60 or 120 FPS frame rate: above about 165 FPS it pauses.')
        refresh = display_refresh_rate()
        if refresh:
            hint += ' ' + _('Your display runs at {} Hz.').format(refresh)
        self.row(f, _('Frame generation'), self.choice(f, 'frame_generation', FRAME_GENERATION, 34), hint)
        self.row(f, _('Multiplier'), self.choice(f, 'frame_gen_multiplier', FRAME_GEN_MULTIPLIERS, 34),
                 _('FSR 3 makes one frame (2×). DLSS makes up to three on a GeForce RTX 50 (4×), '
                   'one on an RTX 40.'))
        parent = self.target(f)
        r = self.next_row(parent)
        self.vram_warning = self.tk.Label(parent, text='', bg=CARD, fg='#d9a441',
                                          font=self.font_ui_sm, wraplength=self.px(560), justify='left')
        self.vram_warning.grid(row=r, column=0, columnspan=2, sticky='w', pady=(8, 2))
        self.var('anti_aliasing').trace_add('write', lambda *_: self.refresh_vram_warning())
        self.var('frame_generation').trace_add('write', lambda *_: self.refresh_vram_warning())
        self.refresh_vram_warning()

    def build_audio_section(self, f):
        self.begin_section(f, 'audio', _('Audio'))
        self.row(f, _('Speakers'), self.choice(f, 'speakers', SPEAKERS, 34),
                 _('What the games are told the speakers are, which sets the choices in their Audio Output '
                   'menu (stereo, 5.1, 7.1). Automatic follows the Windows speaker configuration of the '
                   'default playback device; set it to 5.1 or 7.1 when the games only offer stereo.'))

    def build_controls_section(self, f):
        self.begin_section(f, 'controls', _('Controls'))
        self.note(f, _('Keyboard, mouse and controller. They work together; changes apply when the game starts.'),
                  top=2)
        self.key_buttons = {}
        self.capturing = None
        self.check(f, 'mouse_look', _('Look around with the mouse'),
                   _('The cursor stays in the game window; hold Left Alt (or switch windows) to free it. '
                     'Insert opens the in-game settings menu.'))
        from uncharted_widgets import Slider
        self.row(f, _('Mouse sensitivity'),
                 Slider(self.tk, self.target(f), self.var('mouse_sensitivity'), MOUSE_SENS_MIN, MOUSE_SENS_MAX, 0.05,
                        COLORS, self.px, self.font_ui_md, width=300, log=True, entry=True),
                 _('How far the camera turns for a mouse movement; 1.00 is the default. Drag the slider or '
                   'type a number (0.1 to 10) for a faster turn.'))
        self.check(f, 'mouse_invert_y', _('Invert the mouse vertically'))

        self.subsection(f, _('Button pictures'))
        self.row(f, _('Show in the game'), self.choice(f, 'button_prompts', BUTTON_PROMPTS, 40),
                 _('The pictures of buttons in menus and hints. Automatic shows your keys while you use '
                   'the keyboard or mouse, Xbox buttons after an Xbox (or other) controller, and the '
                   'PlayStation buttons after a PlayStation controller.'))

        self.subsection(f, _('Keyboard and mouse buttons'))
        self.note(f, _('Click a box, then press a key or a mouse button (the wheel counts too). '
                       '✕ removes a binding. Each action can have two.'), top=2)
        card = self.target(f)
        table = self.tk.Frame(card, bg=CARD)
        table.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(10, 0))
        rows = [(a, t) for a, t, _k, _p in BUTTON_ACTIONS] + [(a, t) for a, t, _k in STICK_ACTIONS]
        for r, (action, title) in enumerate(rows):
            self.ttk.Label(table, text=_(title)).grid(row=r, column=0, sticky='w', padx=(0, 24), pady=3)
            for slot in (0, 1):
                button = self.tk.Button(table, width=16, relief='flat', bg=CARD_HI, fg=TEXT,
                                        activebackground='#2a2c31', activeforeground=TEXT,
                                        font=self.font_ui, cursor='hand2', bd=0, padx=8, pady=4,
                                        command=lambda a=action, i=slot: self.capture_key(a, i))
                button.grid(row=r, column=1 + slot * 2, sticky='w', pady=3)
                clear = self.tk.Label(table, text='\ue711' if self.font_icon else '✕', bg=CARD, fg=MUTED,
                                      font=self.font_icon_sm or self.font_ui, cursor='hand2', padx=8)
                clear.grid(row=r, column=2 + slot * 2, sticky='w', padx=(2, 10))
                clear.bind('<Button-1>', lambda _e, a=action, i=slot: self.set_key(a, i, ''))
                clear.bind('<Enter>', lambda _e, w=clear: w.configure(fg=TEXT))
                clear.bind('<Leave>', lambda _e, w=clear: w.configure(fg=MUTED))
                self.key_buttons[(action, slot)] = button
        self.show_keys()
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.pill(holder, _('Default keys'), self.reset_keys).pack(side='left')

        self.subsection(f, _('Controller'))
        self.note(f, _('Which controller button gives each PS4 button (Xbox / PlayStation names).'), top=2)
        self.pad_vars = {}
        for action, title, _k, _p in BUTTON_ACTIONS:
            var = self.tk.StringVar(value=(self.app.get('pad_bindings') or {}).get(action, DEFAULT_PAD[action]))
            self.pad_vars[action] = var
            values = [v for v, _t in PAD_BUTTONS]
            box = self.ttk.Combobox(f, values=[_(t) for _v, t in PAD_BUTTONS], state='readonly', width=26)
            box.current(values.index(var.get()) if var.get() in values else values.index('none'))
            box.bind('<<ComboboxSelected>>', lambda _e, b=box, v=var: v.set(values[b.current()]))
            var.trace_add('write', lambda *_a, b=box, v=var: b.current(values.index(v.get())))
            self.row(f, _(title), box)
        self.check(f, 'pad_invert_y', _('Invert the right stick vertically'))
        card = self.target(f)
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.pill(holder, _('Default controller buttons'), self.reset_pad).pack(side='left')

        self.subsection(f, _('Vibration'))
        self.controller_name = self.ttk.Label(self.target(f), text=_('Detecting…'))
        self.row(f, _('Controller'), self.controller_name)
        self.controller_status = self.ttk.Label(self.target(f), text=_('Checking vibration support…'),
                                                style='Muted.TLabel')
        self.row(f, _('Status'), self.controller_status)
        self.check(f, 'pad_vibration', _('Controller vibration'),
                   _('The setting applies the next time the game starts.'))
        self.row(f, _('Vibration intensity'),
                 Slider(self.tk, self.target(f), self.var('pad_vibration_strength'), 0, 100, 5,
                        COLORS, self.px, self.font_ui_md, fmt='{:.0f}', width=300, suffix='%'))
        card = self.target(f)
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.controller_refresh_button = self.pill(holder, _('Detect controller'), self.detect_controller)
        self.controller_refresh_button.pack(side='left')
        self.controller_test_button = self.pill(holder, _('Test vibration'),
                                                lambda: self.detect_controller(test=True))
        self.controller_test_button.pack(side='left', padx=(8, 0))
        self.controller_test_button.configure(state='disabled')

    def key_list(self, action):
        value = (self.app.get('key_bindings') or {}).get(action, DEFAULT_KEYS[action])
        parts = (value.split('|') + ['', ''])[:2]
        return [p.strip() for p in parts]

    def show_keys(self):
        for (action, slot), button in self.key_buttons.items():
            if self.capturing == (action, slot):
                button.configure(text=_('Press a key…'), bg=GOLD, fg='#141008')
            else:
                name = self.key_list(action)[slot]
                button.configure(text=name or '—', bg=CARD_HI, fg=TEXT if name else MUTED)

    def set_key(self, action, slot, name):
        keys = self.key_list(action)
        keys[slot] = name
        if name:  # one binding per key: taken from the action that had it
            bindings = dict(self.app.get('key_bindings') or {})
            for other in DEFAULT_KEYS:
                if other == action:
                    continue
                others = self.key_list(other)
                if name in others:
                    others = ['' if k == name else k for k in others]
                    bindings[other] = '|'.join(others).strip('|') if any(others) else ''
            self.app['key_bindings'] = bindings
        if not keys[0] and keys[1]:
            keys = [keys[1], '']
        bindings = dict(self.app.get('key_bindings') or {})
        bindings[action] = '|'.join(k for k in keys if k)
        self.app['key_bindings'] = bindings
        self.capturing = None
        self.release_capture()
        self.show_keys()

    def capture_key(self, action, slot):
        self.release_capture()
        self.capturing = (action, slot)
        self.show_keys()
        # The click that opened the capture must not be taken as the binding.
        self.root.after(200, self.arm_capture)

    def arm_capture(self):
        if not self.capturing:
            return
        self.root.bind_all('<KeyPress>', self.captured_key)
        self.root.bind_all('<ButtonPress>', self.captured_button)
        self.poll_side_buttons(side_buttons())

    def poll_side_buttons(self, held):
        """Tk on Windows never reports the side buttons (XBUTTON1/2: no <Button-4/5> event), so they
        are read from the system while a binding is being captured. `held`: their state a moment ago."""
        if not self.capturing:
            return
        down = side_buttons()
        if self.root.focus_displayof():  # only while the launcher is the active window
            for index, name in enumerate(('Mouse 4', 'Mouse 5')):
                if down[index] and not held[index]:
                    self.set_key(*self.capturing, name)
                    return
        self.side_poll = self.root.after(15, self.poll_side_buttons, down)

    def release_capture(self):
        for sequence in ('<KeyPress>', '<ButtonPress>'):
            self.root.unbind_all(sequence)
        if getattr(self, 'side_poll', None):
            self.root.after_cancel(self.side_poll)
            self.side_poll = None

    def captured_key(self, event):
        sym = event.keysym
        if sym in RESERVED_KEYS:
            self.status.configure(text=_(RESERVED_KEYS[sym]), fg=GOLD)
            return 'break'
        name = WIN_NUMPAD.get(event.keycode) if sys.platform == 'win32' else None
        name = name or TK_KEYS.get(sym)
        if not name and len(sym) == 1 and sym.isalnum():
            name = sym.upper()
        if not name and sym.startswith('F') and sym[1:].isdigit():
            name = sym
        if not name and sym.startswith('KP_') and sym[3:].isdigit():
            name = f'Keypad {sym[3:]}'
        if name:
            self.set_key(*self.capturing, name)
        return 'break'

    def captured_button(self, event):
        # 4 and 5 as other toolkits number the side buttons; 8 and 9 as X11 does
        name = {1: 'Mouse Left', 2: 'Mouse Middle', 3: 'Mouse Right', 4: 'Mouse 4', 5: 'Mouse 5',
                8: 'Mouse 4', 9: 'Mouse 5'}.get(event.num)
        if name:
            self.set_key(*self.capturing, name)
        return 'break'

    def captured_wheel(self, event):
        self.set_key(*self.capturing, 'Wheel Up' if event.delta > 0 else 'Wheel Down')
        return 'break'

    def reset_keys(self):
        self.app['key_bindings'] = {}
        self.capturing = None
        self.release_capture()
        self.show_keys()

    def reset_pad(self):
        for action, var in self.pad_vars.items():
            var.set(DEFAULT_PAD[action])

    def build_fixes_section(self, f):
        self.begin_section(f, 'fixes', _('Fixes'))
        self.note(f, _('Fixes that cost speed. Automatic turns each one on only where it '
                       'helps; change one only when you see its problem.'), top=2)
        for key, (title, hint, _choices) in FIXES.items():
            self.row(f, _(title), self.choice(f, key, FIX_CHOICES, 16), _(hint))

    def build_cheats_section(self, f):
        self.begin_section(f, 'cheats', _('Cheats'))
        self.note(f, _('Each cheat only affects its own game; changes apply when the game starts.'), top=2)
        card = self.target(f)
        for game, title, subtitle, _exe in GAMES:
            self.ttk.Label(card, text=f'{title} · {subtitle}').grid(
                row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(10, 0))
            self.check(f, f'{game}_infinite_health', _('Infinite Health'))
            self.check(f, f'{game}_infinite_ammo', _('Infinite Ammo'))

    def build_advanced_section(self, f):
        self.begin_section(f, 'advanced', _('Advanced'))
        self.note(f, _('Maintenance and options for testing.'), top=2)
        self.row(f, _('Interface language'),
                 self.choice(f, 'ui_language', uncharted_i18n.LANGUAGES, 24, self.change_ui_language))
        self.check(f, 'close_on_play', _('Close the launcher when the game starts'))
        extra = self.ttk.Entry(f, textvariable=self.var('extra_env'), width=60)
        self.row(f, _('Environment'), extra, _('Extra variables for the game, NAME=value separated by spaces.'))
        self.check(f, 'check_updates', _('Look for a new version when the launcher starts'))
        card = self.target(f)
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(12, 0))
        self.pill(holder, _('Clear shader cache'), self.clear_cache).pack(side='left')
        self.pill(holder, _('Open the saves folder'),
                  lambda: self.open_path(self.var('user_dir').get(), 'user_dir')).pack(side='left', padx=(8, 0))
        self.pill(holder, _('Check for updates'),
                  lambda: threading.Thread(target=self.check_update, args=(True,), daemon=True).start()
                  ).pack(side='left', padx=(8, 0))
        self.pill(holder, _('Releases page'), lambda: webbrowser.open(RELEASES_PAGE)).pack(
            side='left', padx=(8, 0))
        holder = self.tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.pill(holder, _('Add to Steam'), self.add_to_steam).pack(side='left')
        self.pill(holder, _('Desktop shortcut'), self.shortcut).pack(side='left', padx=(8, 0))
        self.note(f, _('Compiled shaders are kept so the game does not stutter again. Clear the cache only '
                       'when the picture is broken after an update.'), top=10)

    # ---- trophies (secondary action, not a destination) ---------------------------------------
    def open_trophies(self):
        tk = self.tk
        if getattr(self, 'trophy_dialog', None) is not None:
            self.trophy_dialog.lift()
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(_('Trophies'))
        dialog.configure(bg=SURFACE)
        dialog.geometry(f'{self.px(920)}x{self.px(700)}')
        dialog.transient(self.root)
        self.trophy_dialog = dialog
        self.trophy_game = self.app.get('trophy_game') if self.app.get('trophy_game') in [
            key for key, *_ in GAMES] else GAMES[0][0]
        self.trophy_filter = 'all'
        self.trophy_icons, self.trophy_state, self.trophy_info = {}, None, {}
        shell = tk.Frame(dialog, bg=SURFACE)
        shell.pack(fill='both', expand=True, padx=self.px(18), pady=self.px(14))
        canvas = tk.Canvas(shell, bg=SURFACE, highlightthickness=0, bd=0)
        bar = self.ttk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        body = self.ttk.Frame(canvas, style='Shell.TFrame', padding=(8, 0, 8, 12))
        body.columnconfigure(1, weight=1)
        window = canvas.create_window(0, 0, window=body, anchor='nw')

        def fit(_event=None):
            canvas.itemconfigure(window, width=canvas.winfo_width())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), body.winfo_reqheight()))
        body.bind('<Configure>', fit)
        canvas.bind('<Configure>', fit)

        selector = tk.Frame(body, bg=SURFACE)
        selector.grid(row=0, column=0, columnspan=2, sticky='we', pady=(0, self.px(10)))
        self.trophy_tabs = {}
        for key, title, _sub, _exe in GAMES:
            button = self.pill(selector, title, lambda k=key: self.set_trophy_game(k), background=SURFACE,
                               padding=(16, 7))
            button.pack(side='left', padx=(0, 8))
            self.trophy_tabs[key] = button
        self.trophy_filters = {}
        for name, title in (('locked', _('Locked')), ('earned', _('Earned')), ('all', _('All'))):
            button = self.pill(selector, title, lambda n=name: self.set_trophy_filter(n),
                               background=SURFACE, padding=(14, 7))
            button.pack(side='right', padx=(8, 0))
            self.trophy_filters[name] = button
        self.trophy_summary = tk.Label(body, text='', bg=SURFACE, fg=MUTED, font=self.font_ui,
                                       anchor='w', justify='left')
        self.trophy_summary.grid(row=1, column=0, columnspan=2, sticky='we', pady=(0, self.px(10)))
        self.trophy_body = tk.Frame(body, bg=SURFACE)
        self.trophy_body.grid(row=2, column=0, columnspan=2, sticky='we')
        dialog.protocol('WM_DELETE_WINDOW', self.close_trophies)
        self.render_trophies()

    def close_trophies(self):
        dialog = getattr(self, 'trophy_dialog', None)
        self.trophy_dialog = None
        if dialog is not None:
            dialog.destroy()

    def trophy_user_dir(self):
        return Path(self.var('user_dir').get().strip() or DATA_DIR / 'user')

    def trophy_info_for(self, key):
        """The trophy list of one game (texts in the console language), made from its trophy pack in
        the game folder when it is not there yet; None without a pack."""
        s = self.current()
        folder = game_folder(s, key)
        if not folder:
            return None
        stem = next(exe for k, _t, _s, exe in GAMES if k == key)
        out = DATA_DIR / 'out' / Path(stem).stem.split('-')[0] / 'trophy'
        own = bool(str(s.get(f'game_dir_{key}') or '').strip())
        language = str(s.get('language') or '1')
        try:
            trophies.prepare(folder, key, out, int(language) if language.isdigit() else 1, own)
        except Exception as error:  # a broken pack must not break the launcher
            diagnostic_event('WARNING', 'trophies', f'Trophy pack unavailable: {error}')
            return None
        info = trophies.read_cache(out)
        if info:
            info['folder'] = out
        return info

    def trophy_icon(self, info, number, size, earned):
        """The trophy's icon from the cache folder (dimmed while it is locked)."""
        name = (info['npcommid'], number, size, earned)
        if name not in self.trophy_icons:
            image = None
            try:
                from PIL import Image, ImageEnhance, ImageTk
                with Image.open(info['folder'] / f'TROP{number:03d}.PNG') as source:
                    icon = source.convert('RGBA').resize((size, size), Image.LANCZOS)
                if not earned:
                    icon = ImageEnhance.Brightness(ImageEnhance.Color(icon).enhance(0.15)).enhance(0.55)
                image = ImageTk.PhotoImage(icon)
            except (ImportError, OSError):
                pass
            self.trophy_icons[name] = image
        return self.trophy_icons[name]

    @staticmethod
    def mark_pill(button, on):
        kind = 'primary' if on else 'secondary'
        if button.kind != kind:
            button.kind = kind
            button.draw()

    def set_trophy_game(self, key):
        self.trophy_game, self.trophy_state = key, None
        self.app['trophy_game'] = key
        self.render_trophies()

    def set_trophy_filter(self, name):
        self.trophy_filter, self.trophy_state = name, None
        self.render_trophies()

    def render_trophies(self, force=False):
        """Redraws the list of the chosen game when what it shows changed."""
        key = self.trophy_game
        info = self.trophy_info.get(key)
        if info is None or force:
            info = self.trophy_info[key] = self.trophy_info_for(key) or False
        unlocked = trophies.read_unlocks(self.trophy_user_dir(), info['npcommid']) if info else {}
        state = (key, self.trophy_filter, tuple(sorted(unlocked.items())), bool(info))
        for k, button in self.trophy_tabs.items():
            self.mark_pill(button, k == key)
        for name, button in self.trophy_filters.items():
            self.mark_pill(button, name == self.trophy_filter)
        if state == self.trophy_state:
            return
        self.trophy_state = state
        tk = self.tk
        for child in self.trophy_body.winfo_children():
            child.destroy()
        if not info:
            self.trophy_summary.configure(text='')
            self.note_in(self.trophy_body, _('No trophy list for this game. Set the game folder in Settings.'))
            return
        progress = trophies.progress(info, unlocked)
        grades = '   '.join(f"{_(trophies.GRADE_NAMES[g])} {progress['earned'][g]}/{progress['total'][g]}"
                            for g in 'PGSB' if progress['total'][g])
        self.trophy_summary.configure(
            text=f"{info['title']}\n{progress['count']} of {len(info['trophies'])} earned · {progress['percent']}%"
                 f'   ·   {grades}')
        shown = 0
        size = self.px(52)
        for number, grade, hidden, name, detail in info['trophies']:
            when = unlocked.get(number)
            if (self.trophy_filter == 'earned') != (when is not None) and self.trophy_filter != 'all':
                continue
            shown += 1
            if hidden and when is None:
                name, detail = _('Hidden trophy'), _('Keep playing to find out how to earn it.')
            card = tk.Frame(self.trophy_body, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            card.pack(fill='x', pady=(0, 6))
            icon = self.trophy_icon(info, number, size, when is not None)
            if icon:
                tk.Label(card, image=icon, bg=CARD).pack(side='left', padx=(10, 12), pady=8)
            side = tk.Frame(card, bg=CARD)
            side.pack(side='right', padx=(10, 16))
            color = TROPHY_COLORS.get(grade, MUTED) if when is not None else MUTED
            tk.Label(side, text=_(trophies.GRADE_NAMES.get(grade, 'Bronze')), bg=CARD, fg=color,
                     font=('Segoe UI Semibold', 9)).pack(anchor='e')
            tk.Label(side, text=time.strftime('%d %b %Y', time.localtime(when)) if when is not None else _('Locked'),
                     bg=CARD, fg=MUTED, font=self.font_ui_sm).pack(anchor='e', pady=(2, 0))
            middle = tk.Frame(card, bg=CARD)
            middle.pack(side='left', fill='x', expand=True, pady=8)
            tk.Label(middle, text=name, bg=CARD, fg=TEXT if when is not None else MUTED, anchor='w',
                     font=self.font_ui_md).pack(anchor='w')
            tk.Label(middle, text=detail, bg=CARD, fg=MUTED, font=self.font_ui_sm, justify='left', anchor='w',
                     wraplength=self.px(520)).pack(anchor='w', pady=(1, 0))
        if not shown:
            self.note_in(self.trophy_body, _('Nothing here yet.') if self.trophy_filter == 'earned'
                         else _('Every trophy is earned.'))

    def note_in(self, parent, text):
        self.tk.Label(parent, text=text, bg=SURFACE, fg=MUTED, font=self.font_ui, anchor='w', justify='left',
                      wraplength=self.px(640)).pack(anchor='w', pady=(8, 0))

    def poll_trophies(self):
        """Every 1.5 s while the trophy window is open: the game writes an unlock as it happens."""
        if getattr(self, 'trophy_dialog', None) is not None:
            try:
                self.render_trophies()
            except Exception as error:
                diagnostic_event('WARNING', 'trophies', f'Trophy refresh failed: {error}')
        self.root.after(1500, self.poll_trophies)

    # ---- support and diagnostics --------------------------------------------------------------
    def build_support_section(self, f):
        """Log, privacy review and export stay available, away from the Play flow."""
        tk = self.tk
        self.begin_section(f, 'support', _('Support'))
        card = self.target(f)
        holder = tk.Frame(card, bg=CARD)
        holder.grid(row=self.next_row(card), column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.support_actions = {}
        for key, label, action in (('export', _('Export diagnostics'), self.export_diagnostics),
                                   ('logs', _('Open logs folder'), self.open_logs_folder),
                                   ('report', _('Report a problem'), self.report_problem),
                                   ('copy', _('Copy'), self.copy_log)):
            self.support_actions[key] = action
            self.pill(holder, label, action).pack(side='left', padx=(0, 8))
        body = tk.Frame(card, bg=CARD)
        body.grid(row=self.next_row(card), column=0, columnspan=2, sticky='we', pady=(10, 0))
        self.log = tk.Text(body, bg=CARD, fg='#d9d4ca', insertbackground=TEXT, font=('Cascadia Mono', 9),
                           wrap='none', relief='flat', state='disabled', height=10,
                           highlightbackground=LINE, highlightthickness=1,
                           padx=self.px(10), pady=self.px(8))
        scroll = self.ttk.Scrollbar(body, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)

    def current(self):
        """The settings as they stand on screen (not yet saved)."""
        return {**self.app, **{key: v.get() for key, v in self.vars.items()}}

    def detect_gpu(self):
        """The card's video memory from bb-gpu-capabilities (runs beside the game's own probe)."""
        from uncharted_hw import parse_gpu_memory
        exe = PORT_DIR / 'bin' / 'bb-gpu-capabilities.exe'
        if not exe.is_file():
            exe = PORT_DIR / 'out' / 'bb-gpu-capabilities.exe'
        env = dict(os.environ)
        if not (exe.parent / 'SDL3.dll').is_file():
            clang64 = Path(os.environ.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin'
            if clang64.is_dir():
                env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
        try:
            result = subprocess.run([str(exe), '--memory'], capture_output=True, text=True,
                                    timeout=30, env=env, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as error:
            diagnostic_event('WARNING', 'graphics', f'GPU probe unavailable: {error}')
            return
        gpu_name, self.gpu_vram_mb = parse_gpu_memory(result.stderr)
        if DIAGNOSTICS:
            with DIAGNOSTICS.lock:
                DIAGNOSTICS.session['gpu'] = {'model': DIAGNOSTICS.redact(gpu_name or 'unavailable'),
                                             'vram_mib': self.gpu_vram_mb}
                DIAGNOSTICS._persist()
            diagnostic_event('INFO', 'graphics', 'GPU memory probe completed')
        self.ui_calls.put(self.refresh_vram_warning)

    def refresh_vram_warning(self):
        """README advice next to Anti-aliasing and Frame generation on small cards (uncharted_hw)."""
        from uncharted_hw import low_vram_warning
        if not getattr(self, 'vram_warning', None):
            return
        text = low_vram_warning(self.gpu_vram_mb, self.var('anti_aliasing').get(),
                                self.var('frame_generation').get())
        self.vram_warning.configure(text=text or '')

    def refresh(self):
        """Selected game, its location, readiness and the two launch buttons."""
        s = self.current()
        keys = [key for key, *_ in GAMES]
        start = s['start'] if s['start'] in keys else keys[0]
        title, subtitle = next((t, sub) for k, t, sub, _e in GAMES if k == start)
        self.info = game_info(game_folder(s, start))
        unset = not any(game_folder(s, k) for k in keys)
        self.available = {key: unset or game_available(s, key) for key in keys}
        folder = game_folder(s, start)
        ready = bool(self.info) and self.available[start]
        self.play_title.configure(text=_(title))
        if self.info:
            detail = f"{self.info['title']}  ·  {self.info['id']}  ·  {_('version')} {self.info['version']}"
        else:
            detail = _('Set the game folder in Settings.')
        self.play_detail.configure(text=f'{_(subtitle)}\n{detail}')
        if not self.info:
            status = _('Set the game folder in Settings.')
            hint = _('Choose the game folder with eboot.bin.')
        elif not ready:
            status = _('This game is not in the chosen folder.')
            hint = _('Choose the game folder with eboot.bin.')
        else:
            status = _('Ready: {}').format(title)
            missing = [t for k, t, _s, _e in GAMES if not self.available[k]]
            hint = (_('Not in this folder: ') + ', '.join(missing)) if missing else folder
        self.ready.configure(text=status, fg=GOLD if ready else '#e07a4f')
        self.play_hint.configure(text=hint)
        if not self.process:
            self.status.configure(text=status if ready else _('No game folder yet.'), fg=MUTED)
        self.refresh_vram_warning()

    # ---- actions -----------------------------------------------------------------------------
    def change_ui_language(self):
        """Save and rebuild every label as soon as the interface language changes."""
        global UI_LANGUAGE
        page = self.current_page or 'play'
        self.close_trophies()
        self.collect()
        UI_LANGUAGE = uncharted_i18n.language_code(self.app['ui_language'])
        for child in self.root.winfo_children():
            child.destroy()
        self.vars = {}
        self.build()
        self.show(page)
        if self.process:
            self.play_button.configure(state='disabled')
            self.stop_button.configure(state='normal')

    def configure_art(self):
        import uncharted_art
        uncharted_art.set_custom_art({
            'collection_banner.jpg': self.app.get('art_banner', ''),
            'u1.jpg': self.app.get('art_u1', ''),
            'u2.jpg': self.app.get('art_u2', ''),
            'u3.jpg': self.app.get('art_u3', ''),
        }, {
            'collection_banner.jpg': self.app.get('art_crops', {}).get('banner', {}),
            'u1.jpg': self.app.get('art_crops', {}).get('u1', {}),
            'u2.jpg': self.app.get('art_crops', {}).get('u2', {}),
            'u3.jpg': self.app.get('art_crops', {}).get('u3', {}),
        })

    def collect(self):
        """Write the on-screen settings, normalizing anything invalid. Returns the problems found."""
        for key, value in self.vars.items():
            self.app[key] = value.get()
        if getattr(self, 'pad_vars', None):
            self.app['pad_bindings'] = {action: var.get() for action, var in self.pad_vars.items()}
        normalized, problems = normalize_settings(self.app)
        self.app.update(normalized)
        for key, value in normalized.items():
            if key in self.vars and str(self.vars[key].get()) != str(value):
                self.vars[key].set(value)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(self.app, indent=2) + '\n', encoding='utf-8')
        try:
            set_ini('show_fps', '1' if self.app['show_fps'] else '0')
        except OSError:
            pass
        return problems

    def save_settings(self):
        problems = self.collect()
        if self.save_note is not None:
            self.save_note.configure(text='' if problems else _('Settings saved.'),
                                     fg=MUTED if problems else GOLD)
        if problems:
            self.messagebox.showwarning(NAME, '\n'.join(dict.fromkeys(problems)))
            return False
        diagnostic_event('INFO', 'configuration', 'Settings saved')
        return True

    def detect_controller(self, test=False):
        """Detect or briefly rumble the first SDL gamepad without starting the game renderer."""
        if self.process:
            self.controller_status.configure(text=_('Unavailable while the game is running.'))
            return
        probe = controller_probe_path()
        if not probe:
            self.controller_name.configure(text=_('Not available'))
            self.controller_status.configure(text=_('bb-probe.exe was not found.'))
            return
        self.controller_refresh_button.configure(state='disabled')
        self.controller_test_button.configure(state='disabled')
        self.controller_status.configure(text=_('Testing…') if test else _('Detecting…'))
        strength = vibration_strength(self.current())

        def work():
            command = [str(probe), '--controller-test', str(strength)] if test \
                else [str(probe), '--controller-info']
            try:
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                        errors='replace', timeout=5, env=controller_probe_environment(probe),
                                        creationflags=NO_WINDOW)
                if result.returncode:
                    raise OSError(result.stderr.strip() or _('controller probe failed'))
                info, error = parse_controller_probe(result.stdout), ''
                diagnostic_event('INFO', 'controller', 'Controller probe completed')
            except (OSError, subprocess.TimeoutExpired) as failure:
                info, error = None, str(failure)
                diagnostic_event('WARNING', 'controller', f'Controller probe failed: {failure}')
            self.ui_calls.put(lambda: self.show_controller(info, error, test))
        threading.Thread(target=work, daemon=True).start()

    def show_controller(self, info, error, test):
        if self.process:
            self.controller_refresh_button.configure(state='disabled')
            self.controller_test_button.configure(state='disabled')
            self.controller_status.configure(text=_('Unavailable while the game is running.'))
            return
        self.controller_refresh_button.configure(state='normal')
        if error:
            self.controller_name.configure(text=_('Not available'))
            self.controller_status.configure(text=_('Could not detect the controller: {}').format(error[:120]))
            return
        if not info['connected']:
            self.controller_name.configure(text=_('No controller detected'))
            self.controller_status.configure(text=_('Connect a controller, then detect again.'))
            return
        self.controller_name.configure(text=info['name'] or _('Game controller'))
        self.controller_test_button.configure(state='normal' if info['rumble'] else 'disabled')
        if not info['rumble']:
            status = _('Connected · Vibration not supported')
        elif test:
            status = _('Connected · Vibration test completed') if info['tested'] \
                else _('Connected · Vibration test failed')
        else:
            status = _('Connected · Vibration supported')
        self.controller_status.configure(text=status)

    def play(self):
        if self.process:
            return
        self.collect()
        if DIAGNOSTICS:
            DIAGNOSTICS.begin_run(self.app, Path(self.app.get('user_dir') or DATA_DIR / 'user'))
        folder = game_folder(self.app, self.app['start'])
        if not folder or not Path(folder).is_dir():
            diagnostic_event('ERROR', 'runtime.validation', 'Game folder unavailable')
            self.messagebox.showerror(NAME, _('Choose the game folder with eboot.bin.'))
            self.show('settings')
            self.jump_to_section('game')
            return
        if not game_available(self.app, self.app['start']):
            diagnostic_event('ERROR', 'runtime.validation', 'Selected game unavailable')
            self.messagebox.showerror(NAME, _('This game is not in the chosen folder.'))
            return
        self.set_log('')
        try:
            env = game_environment(self.app)
        except OSError as error:
            if DIAGNOSTICS:
                DIAGNOSTICS.failure('runtime.configuration', error)
            self.messagebox.showerror(
                NAME, _('Could not save the input configuration: {}').format(error))
            return
        try:
            self.process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=env,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except OSError as error:
            if DIAGNOSTICS:
                DIAGNOSTICS.failure('runtime.spawn', error)
            self.append(_('Could not start: {}').format(error) + '\n')
            self.process = None
            return
        if DIAGNOSTICS:
            DIAGNOSTICS.runtime_started(self.process.pid)
        self.job = GameJob(self.process)
        self.entered, self.last_problem, self.stopping = False, '', False
        threading.Thread(target=self.read_output, args=(self.process,), daemon=True).start()
        self.play_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.controller_refresh_button.configure(state='disabled')
        self.controller_test_button.configure(state='disabled')
        self.status.configure(text=_('Preparing the game; it opens in its own window…'), fg=GOLD)
        if self.app.get('close_on_play'):
            self.root.after(5000, self.root.destroy)

    def read_output(self, process):
        """Helper thread: the game's output to the Settings log and to <saves>/last_run.log."""
        fallback = None
        if not DIAGNOSTICS:
            fallback = uncharted_diagnostics.BoundedLog(
                Path(self.app.get('user_dir') or DATA_DIR / 'user') / 'last_run.log',
                lambda text: self.output.put(text + '\n'))
        try:
            for raw in iter(lambda: process.stdout.readline(8192), b''):
                text = raw.decode('utf-8', errors='replace')
                self.queue_output(text)
                if DIAGNOSTICS:
                    DIAGNOSTICS.runtime_text(text)
                else:
                    fallback.write(text)
        except OSError as error:
            if DIAGNOSTICS:
                DIAGNOSTICS.failure('runtime.capture', error)
        finally:
            code = process.wait()
            if DIAGNOSTICS:
                DIAGNOSTICS.runtime_exit(code)
            self.queue_output((code,))

    def queue_output(self, item):
        while True:
            try:
                self.output.put_nowait(item)
                return
            except queue.Full:
                try:
                    self.output.get_nowait()
                except queue.Empty:
                    pass
                if not getattr(self, 'display_log_truncated', False):
                    self.display_log_truncated = True
                    diagnostic_event('WARNING', 'launcher.log',
                                     'Display queue full; older display lines omitted. Persistent capture continues.')

    def drain_output(self):
        while not self.ui_calls.empty():
            self.ui_calls.get_nowait()()
        try:
            for _i in range(500):
                item = self.output.get_nowait()
                if isinstance(item, tuple):
                    code = item[0]
                    self.append(_('\n— the game exited (code {}) —\n').format(code))
                    self.process = None
                    if self.job:
                        self.job.close()
                    self.play_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.refresh()
                    self.detect_controller()
                    # It failed to start or crashed (not Stop, not closing the window): say why.
                    if not self.stopping and (code != 0 or not self.entered):
                        reason = self.last_problem or _('see the log in Settings')
                        self.status.configure(text=_('The game stopped: {}').format(reason[:110]),
                                              fg='#e07a4f')
                        self.show_support()
                else:
                    stripped = item.strip()
                    self.last_problem = select_run_problem(self.last_problem, stripped)
                    if 'Entering original x86-64 code' in item:
                        self.entered = True
                        self.status.configure(text=_('The game is running.'), fg=GOLD)
                    elif item.startswith('Switching to '):
                        self.status.configure(text=_('Starting the next game of the collection…'), fg=GOLD)
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_output)

    def stop(self):
        if not self.process:
            return
        self.stop_button.configure(state='disabled')
        if DIAGNOSTICS:
            DIAGNOSTICS.stop_requested()
        try:
            if not self.job or not self.job.terminate():
                terminate_process_tree(self.process)
        except (OSError, subprocess.SubprocessError) as error:
            if DIAGNOSTICS:
                DIAGNOSTICS.stop_requested(False)
                DIAGNOSTICS.failure('runtime.stop', error)
            self.stop_button.configure(state='normal')
            self.messagebox.showerror(NAME, _('Could not stop the game: {}').format(error))
            return
        self.stopping = True
        self.status.configure(text=_('Stopping the game…'), fg=GOLD)

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def copy_log(self):
        self.root.clipboard_clear()
        text = self.log.get('1.0', 'end')
        self.root.clipboard_append(DIAGNOSTICS.redact(text) if DIAGNOSTICS else text)

    def callback_exception(self, kind, error, tb):
        import traceback
        if DIAGNOSTICS:
            DIAGNOSTICS.failure('launcher.callback', error)
            DIAGNOSTICS.event('ERROR', 'launcher.traceback', ''.join(traceback.format_exception(kind, error, tb)))
        self.messagebox.showerror(NAME, _('Launcher error: {}').format(str(error)[:500]))

    def open_logs_folder(self):
        if DIAGNOSTICS:
            self.open_path(DIAGNOSTICS.directory)

    def report_problem(self):
        if self.messagebox.askyesno(NAME, _('Open the issue form in your browser? No diagnostics will be uploaded.')):
            webbrowser.open('https://github.com/lostpilot404/Uncharted/issues/new?template=bug_report.yml')

    def export_diagnostics(self):
        if not DIAGNOSTICS:
            self.messagebox.showerror(NAME, _('Diagnostics are unavailable.'))
            return
        try:
            files = DIAGNOSTICS.snapshot(self.current())
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('Could not export diagnostics: {}').format(error))
            return
        # Preview exactly the bytes that will be saved, not a freshly recollected report.
        dialog = self.tk.Toplevel(self.root)
        dialog.title(_('Diagnostics preview'))
        dialog.geometry('900x650')
        self.tk.Label(dialog, text=_('Review every file before sharing. Sanitization is best effort. No upload occurs.'),
                      wraplength=850).pack(padx=10, pady=10)
        selected = self.tk.StringVar(dialog, value='manifest.json')
        chooser = self.ttk.Combobox(dialog, values=sorted(files), textvariable=selected, state='readonly')
        chooser.pack(fill='x', padx=10)
        viewport = self.tk.Frame(dialog)
        viewport.pack(fill='both', expand=True, padx=10, pady=10)
        viewport.rowconfigure(0, weight=1)
        viewport.columnconfigure(0, weight=1)
        preview = self.tk.Text(viewport, wrap='none')
        vertical = self.ttk.Scrollbar(viewport, orient='vertical', command=preview.yview)
        horizontal = self.ttk.Scrollbar(viewport, orient='horizontal', command=preview.xview)
        preview.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        preview.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        def show(_event=None):
            preview.configure(state='normal')
            preview.delete('1.0', 'end')
            preview.insert('1.0', files[selected.get()].decode('utf-8', 'replace'))
            preview.configure(state='disabled')
        chooser.bind('<<ComboboxSelected>>', show)
        show()
        def save():
            destination = self.filedialog.asksaveasfilename(parent=dialog, defaultextension='.zip',
                initialfile='uncharted-diagnostics.zip', filetypes=[('ZIP', '*.zip')])
            if not destination:
                return
            try:
                DIAGNOSTICS.export(destination, files)
            except (OSError, ValueError, zipfile.BadZipFile) as error:
                self.messagebox.showerror(NAME, _('Could not export diagnostics: {}').format(error))
                return
            dialog.destroy()
        self.ttk.Button(dialog, text=_('Save ZIP'), command=save).pack(side='left', padx=10, pady=10)
        self.ttk.Button(dialog, text=_('Cancel'), command=dialog.destroy).pack(side='left', padx=10, pady=10)

    def open_path(self, path, key=None):
        if not path:
            path = DATA_DIR / 'user' if key == 'user_dir' else DATA_DIR
        path = Path(path or DATA_DIR)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    # ---- updates -------------------------------------------------------------------------------
    def check_update(self, manual=False):
        """Helper thread: asks GitHub for the newest release and offers it when it is newer."""
        try:
            version, url, page = latest_release()
        except (OSError, ValueError, KeyError) as failure:
            if manual:
                self.ui_calls.put(lambda error=failure: self.messagebox.showerror(
                    NAME, _('Could not check for updates: {}').format(error)))
            return

        def show():
            if version_tuple(version) > version_tuple(VERSION):
                self.offer_update(version, url, page)
            elif manual:
                self.messagebox.showinfo(NAME, _('You have the latest version ({}).').format(VERSION))
        self.ui_calls.put(show)

    def offer_update(self, version, url, page):
        if self.update_box:
            return
        tk = self.tk
        text = _('Version {} is available.').format(version)
        box = tk.Frame(self.side, bg=CARD, highlightthickness=1, highlightbackground=GOLD_DIM)
        self.update_label = tk.Label(box, text=text, bg=CARD, fg=GOLD, font=self.font_ui_md,
                                     wraplength=self.px(190), justify='left')
        self.update_label.pack(anchor='w', padx=12, pady=(10, 8))
        buttons = tk.Frame(box, bg=CARD)
        buttons.pack(anchor='w', padx=12, pady=(0, 12))
        self.update_button = self.pill(buttons, _('Update'), lambda: self.install_update(version, url, page),
                                       kind='primary', padding=(14, 6))
        self.update_button.pack(side='left')
        self.pill(buttons, _("What's new"), lambda: webbrowser.open(page), padding=(12, 6)).pack(
            side='left', padx=(6, 0))
        box.pack(fill='x', padx=18, pady=(0, 14), before=self.side_note)
        self.update_box = box
        if not self.process:
            self.status.configure(text=text, fg=GOLD)

    def install_update(self, version, url, page):
        if self.process:
            self.messagebox.showinfo(NAME, _('Close the game before updating.'))
            return
        if not FROZEN or not url:  # a source tree updates with git
            webbrowser.open(page)
            return
        if not self.messagebox.askyesno(NAME, _(
                'Install version {} now? The launcher closes, installs it and opens again. Saves and settings '
                'are kept.').format(version)):
            return
        self.update_button.configure(state='disabled')

        def progress(percent):
            self.update_label.configure(text=_('Downloading version {}… {}%').format(version, percent))

        def work():
            try:
                shutil.rmtree(UPDATE_DIR, ignore_errors=True)
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                archive = UPDATE_DIR / 'update.zip'
                request = urllib.request.Request(url, headers={'User-Agent': 'uncharted-launcher'})
                with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
                    total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        percent = done * 100 // total if total else 0
                        if percent != shown:
                            shown = percent
                            self.ui_calls.put(lambda p=percent: progress(p))
                with zipfile.ZipFile(archive) as package:
                    package.extractall(UPDATE_DIR / 'new')
                archive.unlink()
                new = next((p.parent for p in (UPDATE_DIR / 'new').rglob('Uncharted.exe')), None)
                if not new:
                    raise OSError('Uncharted.exe is missing from the download')
                # The new launcher copies itself over this installation once this one has closed.
                subprocess.Popen([str(new / 'Uncharted.exe'), '--install-update', str(PORT_DIR), str(os.getpid())],
                                 cwd=str(new), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                self.ui_calls.put(self.close)
            except (OSError, zipfile.BadZipFile) as failure:
                def failed(error=failure):
                    self.update_button.configure(state='normal')
                    self.update_label.configure(text=_('Version {} is available.').format(version))
                    self.messagebox.showerror(NAME, _('Update failed: {}').format(error))
                self.ui_calls.put(failed)
        threading.Thread(target=work, daemon=True).start()

    def clear_cache(self):
        if self.process:
            return
        cache = Path(self.app['user_dir'] or DATA_DIR / 'user') / 'cache'
        try:
            shutil.rmtree(cache)
        except FileNotFoundError:
            pass
        except OSError as error:
            self.messagebox.showerror(NAME, _('Could not clear the shader cache: {}').format(error))
            return
        self.messagebox.showinfo(NAME, _('Shader cache cleared. The next start stutters for a while as it is rebuilt.'))

    def shortcut(self):
        """Configure the collection shortcut where the action happens."""
        from PIL import Image, ImageTk

        window = self.tk.Toplevel(self.root)
        window.title(_('Desktop shortcut'))
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=_('Desktop shortcut'), bg=PANEL, fg=TEXT,
                      font=self.font_title).grid(row=0, column=0, columnspan=2, sticky='w')
        self.tk.Label(shell, text=_('Shortcut name'), bg=PANEL, fg=TEXT,
                      font=self.font_ui).grid(row=1, column=0, sticky='w', pady=(18, 4))
        name = self.tk.StringVar(value=self.app.get('shortcut_name', APP_DEFAULTS['shortcut_name']))
        self.ttk.Entry(shell, textvariable=name, width=48).grid(row=2, column=0, columnspan=2, sticky='ew')
        self.tk.Label(shell, text=_('Shortcut icon'), bg=PANEL, fg=TEXT,
                      font=self.font_ui).grid(row=3, column=0, sticky='w', pady=(16, 4))
        icon = self.tk.StringVar(value=self.app.get('shortcut_icon', ''))
        preview = self.tk.Canvas(shell, width=self.px(112), height=self.px(112), bg=BG,
                                 highlightthickness=2, highlightbackground=GOLD_DIM, cursor='hand2')
        preview.grid(row=4, column=0, sticky='w')
        photo = {'image': None}

        def draw_icon(*_args):
            preview.delete('all')
            try:
                with Image.open(icon.get()) as source:
                    image = source.convert('RGBA')
                    image.thumbnail((self.px(96), self.px(96)), Image.LANCZOS)
                photo['image'] = ImageTk.PhotoImage(image)
                preview.create_image(self.px(56), self.px(56), image=photo['image'])
            except (OSError, ValueError, self.tk.TclError):
                photo['image'] = None
                preview.create_text(self.px(56), self.px(56), text=_('Original icon'), fill=MUTED,
                                    font=self.font_ui_sm, width=self.px(86))

        def choose_icon(candidate=''):
            chosen = candidate or self.image_path(_('Shortcut icon'), icon.get())
            if chosen:
                try:
                    with Image.open(chosen) as image:
                        image.verify()
                except (OSError, ValueError) as error:
                    self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                    return
                icon.set(chosen)

        actions = self.tk.Frame(shell, bg=PANEL)
        actions.grid(row=4, column=1, sticky='w', padx=(16, 0))
        self.pill(actions, _('Choose image'), choose_icon, background=PANEL).pack(anchor='w')
        self.pill(actions, _('Restore'), lambda: icon.set(''), kind='ghost', background=PANEL).pack(
            anchor='w', pady=(6, 0))
        buttons = self.tk.Frame(shell, bg=PANEL)
        buttons.grid(row=5, column=0, columnspan=2, sticky='ew', pady=(18, 0))
        self.pill(buttons, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def create():
            self.app['shortcut_name'] = name.get().strip() or APP_DEFAULTS['shortcut_name']
            self.app['shortcut_icon'] = icon.get()
            self.collect()
            window.destroy()
            self.create_shortcut()

        self.pill(buttons, _('Create shortcut'), create, kind='primary', background=PANEL).pack(
            side='right', padx=(0, 6))
        preview.bind('<Button-1>', lambda _event: choose_icon())
        self.enable_image_drop(preview, choose_icon)
        icon.trace_add('write', draw_icon)
        draw_icon()
        window.bind('<Escape>', lambda _event: window.destroy())

    def create_shortcut(self):
        """Create the configured .lnk on the desktop."""
        if FROZEN:
            target, arguments, icon = sys.executable, '', f'{sys.executable},0'
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            target = str(pythonw if pythonw.exists() else sys.executable)
            arguments, icon = Path(__file__).resolve(), PORT_DIR / 'launcher' / 'uncharted.ico'
        custom_icon = self.app.get('shortcut_icon', '')
        if custom_icon:
            try:
                from PIL import Image
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                icon = CONFIG_DIR / 'custom-shortcut.ico'
                with Image.open(custom_icon) as image:
                    image.convert('RGBA').save(icon, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            except OSError as error:
                self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                return
        name = re.sub(r'[<>:"/\\|?*]', '', self.app.get('shortcut_name', '')).strip() or 'Uncharted'
        ps = lambda value: "'" + str(value).replace("'", "''") + "'"
        script = ('$desktop=[Environment]::GetFolderPath("Desktop");'
                  f'$s=(New-Object -ComObject WScript.Shell).CreateShortcut($desktop+{ps(chr(92) + name + ".lnk")});'
                  f'$s.TargetPath={ps(target)};$s.WorkingDirectory={ps(PORT_DIR)};$s.IconLocation={ps(icon)};'
                  + (f'$s.Arguments={ps(chr(34) + str(arguments) + chr(34))};' if arguments else '') + '$s.Save()')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                                creationflags=NO_WINDOW)
        if result.returncode == 0:
            self.messagebox.showinfo(NAME, _('Shortcut created on the desktop.'))
        else:
            self.messagebox.showerror(NAME, result.stderr.decode(errors='replace')[:400])

    def add_to_steam(self):
        """Preview the collection art before writing the non-Steam shortcut."""
        from PIL import Image, ImageTk
        import uncharted_art

        window = self.tk.Toplevel(self.root)
        window.title(_('Add to Steam'))
        window.configure(bg=PANEL)
        window.transient(self.root)
        window.grab_set()
        window.resizable(False, False)
        shell = self.tk.Frame(window, bg=PANEL)
        shell.pack(padx=self.px(24), pady=self.px(22))
        self.tk.Label(shell, text=_('Add collection to Steam'), bg=PANEL, fg=TEXT,
                      font=self.font_title).grid(row=0, column=0, columnspan=3, sticky='w')
        self.tk.Label(shell, text=_('Use one image and adjust each Steam format separately.'), bg=PANEL,
                      fg=MUTED, font=self.font_ui).grid(row=1, column=0, columnspan=3, sticky='w', pady=(2, 14))
        formats = [('portrait', _('Portrait'), (600, 900), (150, 225)),
                   ('header', _('Header'), (920, 430), (230, 108)),
                   ('hero', _('Hero'), (1920, 620), (300, 97))]
        previews = {}
        photos = {}
        selected = {'path': self.app.get('steam_art', ''),
                    'crops': {name: dict(self.app.get('art_crops', {}).get(f'steam_{name}', {}))
                              for name, _label, _size, _preview in formats}}

        def redraw():
            for column, (name, label, size, preview_size) in enumerate(formats):
                canvas = previews[name]
                canvas.delete('all')
                try:
                    with Image.open(selected['path']) as source:
                        image = uncharted_art.crop_image(
                            source.convert('RGB'), preview_size, selected['crops'][name])
                    photos[name] = ImageTk.PhotoImage(image)
                    canvas.create_image(0, 0, image=photos[name], anchor='nw')
                except (OSError, ValueError, self.tk.TclError):
                    canvas.create_text(preview_size[0] // 2, preview_size[1] // 2,
                                       text=_('Choose image'), fill=MUTED, font=self.font_ui_sm)
                canvas.create_rectangle(1, 1, preview_size[0] - 2, preview_size[1] - 2,
                                        outline=GOLD_DIM, width=2)

        def choose(candidate=''):
            chosen = candidate or self.image_path(_('Collection image'), selected['path'])
            if chosen:
                try:
                    with Image.open(chosen) as image:
                        image.verify()
                except (OSError, ValueError) as error:
                    self.messagebox.showerror(NAME, _('This image could not be opened: {}').format(error))
                    return
                selected['path'] = chosen
                redraw()

        def adjust(name, label, size):
            result = self.edit_art('steam_art', label, size, f'steam_{name}',
                                   initial_path=selected['path'], initial_crop=selected['crops'][name])
            if result:
                selected['path'], selected['crops'][name] = result
                redraw()

        for column, (name, label, size, preview_size) in enumerate(formats):
            cell = self.tk.Frame(shell, bg=PANEL)
            cell.grid(row=2, column=column, sticky='n', padx=(0 if column == 0 else 16, 0))
            self.tk.Label(cell, text=label, bg=PANEL, fg=TEXT, font=self.font_ui_md).pack(anchor='w')
            canvas = self.tk.Canvas(cell, width=preview_size[0], height=preview_size[1], bg=BG,
                                    highlightthickness=0, cursor='hand2')
            canvas.pack(pady=(6, 8))
            previews[name] = canvas
            canvas.bind('<Button-1>', lambda _e, n=name, l=label, s=size: adjust(n, l, s))
            self.enable_image_drop(canvas, choose)
            self.pill(cell, _('Adjust crop'), lambda n=name, l=label, s=size: adjust(n, l, s),
                      background=PANEL, padding=(12, 6)).pack(anchor='w')
        footer = self.tk.Frame(shell, bg=PANEL)
        footer.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(18, 0))
        self.pill(footer, _('Choose image'), choose, background=PANEL).pack(side='left')
        self.pill(footer, _('Cancel'), window.destroy, kind='ghost', background=PANEL).pack(side='right')

        def add():
            self.app['steam_art'] = selected['path']
            self.app.setdefault('art_crops', {}).update(
                {f'steam_{name}': crop for name, crop in selected['crops'].items()})
            self.collect()
            window.destroy()
            self.perform_add_to_steam()

        self.pill(footer, _('Add to Steam'), add, kind='primary', background=PANEL).pack(side='right', padx=(0, 6))
        redraw()
        window.bind('<Escape>', lambda _event: window.destroy())

    def perform_add_to_steam(self):
        """Write the collection shortcut and its user-selected artwork to Steam."""
        import bbport_steam
        steam = bbport_steam.steam_folder()
        configs = bbport_steam.user_configs(steam) if steam else []
        if not configs:
            self.messagebox.showerror(NAME, _('Steam was not found on this PC (or nobody has signed in to it yet).'))
            return
        if FROZEN:
            exe, options = Path(sys.executable), '--play'
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            exe, options = (pythonw if pythonw.exists() else Path(sys.executable)), f'"{Path(__file__).resolve()}" --play'
        self.collect()
        restart = False
        if bbport_steam.steam_running():
            if not self.messagebox.askyesno(NAME, _('Steam has to close while the game is added; it starts '
                                                    'again afterwards. Close Steam now?')):
                return
            subprocess.Popen([str(steam / 'steam.exe'), '-shutdown'], creationflags=NO_WINDOW)
            for _i in range(60):
                time.sleep(0.5)
                if not bbport_steam.steam_running():
                    break
            else:
                self.messagebox.showerror(NAME, _('Steam did not close. Close it yourself and try again.'))
                return
            time.sleep(1)
            restart = True
        title = 'Uncharted: The Nathan Drake Collection'
        try:
            for config in configs:
                bbport_steam.add_shortcut(config / 'shortcuts.vdf', title, str(exe), str(PORT_DIR), str(exe), options)
                # Library pictures (cover, header, hero) from the box art of the player's game folder.
                try:
                    import uncharted_art
                    uncharted_art.steam_artwork(
                        game_folder(self.app, 'u1') or game_folder(self.app, 'u2'), config / 'grid',
                        bbport_steam.shortcut_id(f'"{exe}"', title), self.app.get('steam_art', ''), {
                            name: self.app.get('art_crops', {}).get(f'steam_{name}', {})
                            for name in ('portrait', 'header', 'hero')})
                except Exception:  # pictures are a nicety: the shortcut works without them
                    pass
        except (OSError, ValueError) as error:
            self.messagebox.showerror(NAME, _('Could not add the game to Steam: {}').format(error))
            return
        if restart:
            os.startfile('steam://open/games')
        self.messagebox.showinfo(NAME, _('The collection is in your Steam library. It starts with the settings '
                                         'saved in this launcher.'))

    def close(self):
        try:
            self.collect()
        except Exception as error:  # never keep the window open over a settings problem
            if DIAGNOSTICS:
                DIAGNOSTICS.failure('configuration.save', error)
        self.root.destroy()


class GameJob:
    """A Windows job holding run.py and everything it starts (the game, the next game of the
    collection, an in-game restart), so Stop ends all of them."""

    def __init__(self, process):
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if self.handle:
            assigned = self.kernel32.AssignProcessToJobObject(
                ctypes.c_void_p(self.handle), ctypes.c_void_p(int(process._handle)))
            if not assigned:
                self.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
                self.handle = None

    def terminate(self):
        return bool(self.handle and self.kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), 1))

    def close(self):
        if self.handle:
            self.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def terminate_process_tree(process):
    """Stop a launcher process and every child when its Windows job is unavailable."""
    if process.poll() is not None:
        return
    taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
    result = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=10)
    if result.returncode and process.poll() is None:
        detail = result.stdout.decode(errors='replace').strip()
        raise OSError(detail or _('taskkill exited with code {}').format(result.returncode))


def version_tuple(text):
    return tuple(int(number) for number in re.findall(r'\d+', text or ''))


def latest_release():
    """(version, zip URL, page URL) of the newest GitHub release."""
    request = urllib.request.Request(RELEASES_API, headers={'Accept': 'application/vnd.github+json',
                                                            'User-Agent': 'uncharted-launcher'})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    version = '.'.join(re.findall(r'\d+', release['tag_name']))
    url = next((asset['browser_download_url'] for asset in release.get('assets', [])
                if asset.get('name', '').lower().endswith('.zip')), None)
    return version, url, release.get('html_url') or RELEASES_PAGE


def install_update(target, wait_pid):
    """--install-update TARGET PID, run by the downloaded version from its temporary folder:
    waits for the old launcher to close, copies this version over TARGET (never the saves,
    settings or art) and starts it."""
    target = Path(target)
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, int(wait_pid))  # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, 60000)
        kernel.CloseHandle(handle)
    ignore = shutil.ignore_patterns(*USER_FILES)
    for attempt in range(30):
        try:
            shutil.copytree(PORT_DIR, target, dirs_exist_ok=True, ignore=ignore)
            break
        except OSError:  # a file still in use: the old launcher is closing
            time.sleep(1)
    else:
        ctypes.windll.user32.MessageBoxW(None, _('Could not install the update. Download it from the '
                                                 'releases page.'), NAME, 0x10)
        webbrowser.open(RELEASES_PAGE)
        return 1
    subprocess.Popen([str(target / 'Uncharted.exe')], cwd=str(target))
    return 0


def play_without_window(settings):
    """Saved-settings launch with the same bounded capture as the GUI; no console required."""
    attach_stdio()
    if DIAGNOSTICS:
        DIAGNOSTICS.begin_run(settings, Path(settings.get('user_dir') or DATA_DIR / 'user'))
        write = DIAGNOSTICS.runtime_text
    else:
        def warn(text):
            if sys.stderr:
                sys.stderr.write(text + '\n')
        fallback = uncharted_diagnostics.BoundedLog(
            Path(settings.get('user_dir') or DATA_DIR / 'user') / 'last_run.log', warn)
        write = fallback.write
    try:
        env = game_environment(settings)
    except OSError as error:
        text = _('Could not save the input configuration: {}').format(error) + '\n'
        write(text)
        if DIAGNOSTICS:
            DIAGNOSTICS.failure('runtime.configuration', error)
        try:
            sys.stderr.write(text)
        except (AttributeError, OSError, ValueError):
            pass
        return 1
    try:
        process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    except OSError as error:
        write(_('Could not start: {}').format(error) + '\n')
        if DIAGNOSTICS:
            DIAGNOSTICS.failure('runtime.spawn', error)
        return 1
    if DIAGNOSTICS:
        DIAGNOSTICS.runtime_started(process.pid)
    try:
        for raw in iter(lambda: process.stdout.readline(8192), b''):
            text = raw.decode('utf-8', errors='replace')
            write(text)
            try:
                sys.stdout.write(text)
            except (AttributeError, OSError, ValueError):
                pass
    except OSError as error:
        if DIAGNOSTICS:
            DIAGNOSTICS.failure('runtime.capture', error)
    code = process.wait()
    if DIAGNOSTICS:
        DIAGNOSTICS.runtime_exit(code)
    return code


def main():
    args = sys.argv[1:]
    if args and args[0] in ('--run', '--script'):
        sys.exit(run_role(args))
    if args[:1] == ['--install-update'] and len(args) == 3:
        sys.exit(install_update(args[1], args[2]))
    if FROZEN and UPDATE_DIR not in PORT_DIR.parents:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)  # what a finished update left behind
    settings, _ = load_settings()
    if args[:1] == ['--export-diagnostics'] and len(args) == 2:
        try:
            if not DIAGNOSTICS:
                raise OSError('Diagnostics unavailable')
            DIAGNOSTICS.export(args[1], DIAGNOSTICS.snapshot(settings))
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            if DIAGNOSTICS:
                DIAGNOSTICS.failure('diagnostics.export', error)
            sys.exit(1)
        return
    if '--play' in args and game_available(settings, settings['start']):
        sys.exit(play_without_window(settings))
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except (ImportError, tk.TclError):
        root = tk.Tk()
    Launcher(root, tk, ttk, filedialog, messagebox)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        if DIAGNOSTICS:
            DIAGNOSTICS.finish()
        raise
    else:
        if DIAGNOSTICS:
            DIAGNOSTICS.finish()
