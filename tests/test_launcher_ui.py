"""Headless regressions for the Play | Settings launcher, its persistence and support actions.

A fake Tk toolkit (fake_tk.py) exercises the real build/navigation/save code. These tests cover
structure and wiring, not pixels: they do not claim that the GUI renders or is usable.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from paths import ROOT

sys.path.insert(0, str(ROOT / 'launcher'))
import fake_tk


def load_launcher(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'launcher' / 'uncharted_launcher.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoThread:
    """Construction must not start the update/GPU/log worker threads in a test."""

    def __init__(self, *_args, **_kwargs):
        pass

    def start(self):
        pass


class LauncherUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = load_launcher('uncharted_launcher_ui_test')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_dir = Path(self.temp.name)
        self.config = self.root_dir / 'settings.json'
        self.launcher_window = None
        for target, attribute, replacement in (
                (self.launcher, 'CONFIG_DIR', self.root_dir),
                (self.launcher, 'CONFIG_FILE', self.config),
                (self.launcher, 'set_ini', mock.Mock()),
                (self.launcher.threading, 'Thread', NoThread),
                (self.launcher.Launcher, 'load_fonts', fake_tk.fonts),
                (self.launcher, 'GameJob', mock.Mock())):
            patcher = mock.patch.object(target, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(sys.modules, {'uncharted_widgets': fake_tk.widget_module()})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'BB_INPUT_CONFIG': str(self.root_dir / 'input.ini')})
        env.start()
        self.addCleanup(env.stop)

    def window(self, saved=None):
        if saved is not None:
            self.config.write_text(json.dumps(saved), encoding='utf-8')
        tk, ttk = fake_tk.toolkit()
        filedialog = types.SimpleNamespace(askdirectory=mock.Mock(return_value=''),
                                           askopenfilename=mock.Mock(return_value=''),
                                           asksaveasfilename=mock.Mock(return_value=''))
        messagebox = mock.Mock()
        root = fake_tk.Widget()
        launcher_window = self.launcher.Launcher(root, tk, ttk, filedialog, messagebox)
        self.launcher_window = launcher_window
        return launcher_window

    # ---- information architecture ----------------------------------------------------
    def test_only_play_and_settings_are_destinations(self):
        window = self.window()
        self.assertEqual(list(window.nav), ['play', 'settings'])
        self.assertEqual(set(window.pages), {'play', 'settings'})
        self.assertEqual(window.current_page, 'play')

    def test_settings_has_one_page_of_practical_sections(self):
        window = self.window()
        self.assertEqual(set(window.section_anchors),
                         {'game', 'display', 'graphics', 'audio', 'controls', 'fixes',
                          'cheats', 'advanced', 'support'})
        window.show('settings')
        self.assertEqual(window.current_page, 'settings')
        window.jump_to_section('support')
        self.assertIn('view', window.scrollers['settings'][0].options)

    def test_removed_pages_redirect_to_settings(self):
        window = self.window()
        for old_page in ('game', 'display', 'controls', 'fixes', 'trophies', 'cheats',
                         'advanced', 'log'):
            with self.subTest(old_page=old_page):
                window.show(old_page)
                self.assertEqual(window.current_page, 'settings')
        window.show_support()
        self.assertEqual(window.current_page, 'settings')

    def test_play_has_one_launch_action_and_a_single_stop_action(self):
        window = self.window()
        self.assertEqual(window.play_button.command, window.play)
        self.assertEqual(window.stop_button.command, window.stop)
        self.assertEqual(window.stop_button.options.get('state'), 'disabled')

    def test_support_actions_are_inside_settings_not_play(self):
        window = self.window()
        self.assertEqual(set(window.support_actions), {'export', 'logs', 'report', 'copy'})
        self.assertEqual(window.support_actions['export'], window.export_diagnostics)
        self.assertEqual(window.support_actions['logs'], window.open_logs_folder)
        self.assertEqual(window.support_actions['report'], window.report_problem)
        self.assertEqual(window.support_actions['copy'], window.copy_log)
        self.assertIs(window.support_actions['export'].__self__, window)

    # ---- game selection and readiness -------------------------------------------------
    def test_game_selection_changes_the_selected_content_and_readiness(self):
        window = self.window()
        window.var('start').set('u2')
        self.assertEqual(window.play_title.options['text'], self.launcher._('Among Thieves'))
        self.assertIn('Uncharted 2', window.play_detail.options['text'])
        self.assertIn(self.launcher_text('Set the game folder in Settings.'),
                      window.play_hint.options['text'] + window.play_detail.options['text'])

    def test_missing_file_guidance_is_shown_on_play(self):
        window = self.window()
        self.assertIn(self.launcher._('Set the game folder in Settings.'), window.ready.options['text'])
        window.play()
        window.messagebox.showerror.assert_called_once()
        self.assertEqual(window.current_page, 'settings')
        self.assertIsNone(window.process)

    def test_ready_folder_reports_the_selected_game(self):
        game = self.root_dir / 'collection'
        game.mkdir()
        for executable in ('eboot.bin', 'big2-ps4_Shipping.elf', 'big3-ps4_Shipping.elf'):
            (game / executable).write_bytes(b'stub')
        window = self.window()
        window.var('game_dir').set(str(game))
        self.assertEqual(window.ready.options['text'],
                             self.launcher._('Ready: {}').format("Drake's Fortune"))
        self.assertIn(str(game), window.play_hint.options['text'])

    # ---- configuration loading, saving, migration -------------------------------------
    def test_defaults_and_legacy_keys_survive_a_save(self):
        legacy = {'frame_rate': '120', 'ui_language': 'de', 'a_future_key': 'kept'}
        window = self.window(saved=legacy)
        self.assertEqual(window.app['frame_rate'], '120')
        self.assertEqual(window.app['a_future_key'], 'kept')
        self.assertFalse(window.app['fullscreen'])
        self.assertTrue(window.save_settings())
        on_disk = json.loads(self.config.read_text(encoding='utf-8-sig'))
        self.assertEqual(on_disk['frame_rate'], '120')
        self.assertEqual(on_disk['a_future_key'], 'kept')
        self.assertIn('anti_aliasing', on_disk)
        again = self.window()
        self.assertEqual(again.app['frame_rate'], '120')
        self.assertEqual(again.app['a_future_key'], 'kept')

    def test_malformed_and_bom_configs_fall_back_to_defaults(self):
        self.config.write_text('{not json', encoding='utf-8')
        window = self.window()
        self.assertEqual(window.app['start'], 'u1')
        self.assertEqual(window.app['frame_rate'], '60')
        self.config.write_text('\ufeff{"frame_rate": "144"}', encoding='utf-8')
        again = self.window()
        self.assertEqual(again.app['frame_rate'], '144')

    def test_invalid_saved_values_are_repaired_and_reported(self):
        legacy = {'start': 'u9', 'frame_rate': 'nonsense', 'mouse_sensitivity': 'abc',
                  'extra_env': 'BB_OK=1 broken BB_ALSO=2', 'fullscreen': 'false',
                  'key_bindings': 'not a dict'}
        window = self.window(saved=legacy)
        self.assertTrue(window.settings_problems)  # the repair is reported once at load
        self.assertTrue(window.save_settings())    # and the corrected values persist cleanly
        self.assertEqual(window.app['start'], 'u1')
        self.assertEqual(window.app['frame_rate'], '60')
        self.assertEqual(window.app['mouse_sensitivity'], '1.0')
        self.assertEqual(window.app['extra_env'], 'BB_OK=1 BB_ALSO=2')
        self.assertIs(window.app['fullscreen'], False)
        self.assertEqual(window.app['key_bindings'], {})
        on_disk = json.loads(self.config.read_text(encoding='utf-8-sig'))
        self.assertEqual(on_disk['start'], 'u1')
        self.assertEqual(on_disk['extra_env'], 'BB_OK=1 BB_ALSO=2')

    def test_normalize_settings_is_deterministic_and_idempotent(self):
        settings = dict(self.launcher.APP_DEFAULTS)
        settings.update({'start': 'nope', 'render_height': '9999', 'pad_vibration_strength': '500'})
        once, problems = self.launcher.normalize_settings(settings)
        twice, again = self.launcher.normalize_settings(once)
        self.assertEqual(once, twice)
        self.assertTrue(problems)
        self.assertEqual(again, [])  # repeating the repair is safe and reports nothing new
        self.assertEqual(once['start'], 'u1')
        self.assertEqual(once['pad_vibration_strength'], '100')
        self.assertTrue(problems)

    # ---- launch flow ------------------------------------------------------------------
    def test_play_validates_the_folder_and_starts_the_saved_settings(self):
        game = self.root_dir / 'collection'
        game.mkdir()
        (game / 'eboot.bin').write_bytes(b'stub')
        window = self.window()
        window.var('game_dir').set(str(game))
        window.var('start').set('u1')
        window.var('frame_rate').set('120')
        process = mock.Mock(pid=4711, stdout=mock.Mock())
        with mock.patch.object(self.launcher.subprocess, 'Popen', return_value=process) as popen:
            window.play()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.kwargs['env']['BB_VBLANK_HZ'], '120')
        self.assertEqual(popen.call_args.kwargs['env']['BB_GAME_DIR'], str(game))
        self.assertEqual(popen.call_args.kwargs['env']['BB_EXE'], 'u1')
        self.assertEqual(window.play_button.options.get('state'), 'disabled')
        self.assertEqual(window.stop_button.options.get('state'), 'normal')
        self.assertTrue((self.root_dir / 'input.ini').is_file())

    # ---- diagnostics ------------------------------------------------------------------
    def test_log_copy_is_redacted_and_log_folder_is_reachable(self):
        window = self.window()
        fake_diagnostics = mock.Mock()
        fake_diagnostics.directory = self.root_dir / 'logs'
        fake_diagnostics.redact.side_effect = lambda text: text.replace('SECRET', '[redacted]')
        with mock.patch.object(self.launcher, 'DIAGNOSTICS', fake_diagnostics), \
                mock.patch.object(window, 'open_path') as open_path:
            window.log.options['contents'] = 'path=SECRET'
            window.copy_log()
            window.open_logs_folder()
        self.assertIn('[redacted]', window.root.clipboard)
        self.assertNotIn('SECRET', window.root.clipboard)
        open_path.assert_called_once_with(fake_diagnostics.directory)

    def test_export_previews_the_exact_snapshot_before_saving(self):
        window = self.window()
        snapshot = {'manifest.json': b'{"session": 1}', 'launcher.log': b'local text'}
        fake_diagnostics = mock.Mock()
        fake_diagnostics.snapshot.return_value = snapshot
        buttons = []
        real_button = window.ttk.Button

        def record_button(*args, **kwargs):
            widget = real_button(*args, **kwargs)
            buttons.append((kwargs.get('text'), widget))
            return widget
        window.ttk.Button = record_button
        destination = str(self.root_dir / 'diagnostics.zip')
        window.filedialog.asksaveasfilename = mock.Mock(return_value=destination)
        with mock.patch.object(self.launcher, 'DIAGNOSTICS', fake_diagnostics):
            window.export_diagnostics()
            save = next(command for text, widget in buttons
                        if text == self.launcher._('Save ZIP') for command in [widget.options['command']])
            save()
        fake_diagnostics.export.assert_called_once_with(destination, snapshot)
        window.messagebox.showerror.assert_not_called()

    def test_export_without_diagnostics_reports_an_error(self):
        window = self.window()
        window.export_diagnostics()
        window.messagebox.showerror.assert_called_once()

    # Small helper so the readiness assertion reads in terms of UI text, not implementation.
    def launcher_text(self, english):
        return self.launcher._(english)


if __name__ == '__main__':
    unittest.main()
