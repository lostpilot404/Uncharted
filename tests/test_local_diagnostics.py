"""Failure-driven local diagnostics tests; no GPU, game, telemetry or native-crash claims."""
import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'launcher'))
import uncharted_diagnostics as diagnostics


class LocalDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logs = self.root / 'logs'
        self.build = self.root / 'build'
        self.build.mkdir()
        self.diag = diagnostics.Diagnostics(self.logs, self.build, '0.3.9')

    def read(self, name):
        return json.loads((self.logs / name).read_text())

    def test_earliest_startup_and_normal_shutdown(self):
        self.assertIn('Application started', (self.logs / 'launcher.log').read_text())
        self.assertFalse(self.read('session.json')['clean_shutdown'])
        self.diag.finish()
        self.assertTrue(self.read('session.json')['clean_shutdown'])
        self.assertIn('session=', (self.logs / 'launcher.log').read_text())

    def test_unclean_previous_session_retained_without_claiming_crash(self):
        old = self.diag.session['session_id']
        next_run = diagnostics.Diagnostics(self.logs, self.build, '0.3.9')
        self.assertNotEqual(next_run.session['session_id'], old)
        self.assertEqual(self.read('previous-session.json')['session_id'], old)
        text = (self.logs / 'launcher.log').read_text()
        self.assertIn('cause unknown', text)

    def test_corrupt_session_does_not_block_startup(self):
        (self.logs / 'session.json').write_text('{bad json')
        second = diagnostics.Diagnostics(self.logs, self.build, '0.3.9')
        self.assertTrue(second.errors)
        self.assertEqual(self.read('session.json')['session_id'], second.session['session_id'])

    def test_runtime_sink_exists_before_spawn_and_previous_run_retained(self):
        self.diag.begin_run({'start': 'u1'})
        first = self.diag.session['runtime']['run_id']
        self.assertTrue((self.logs / 'last_run.log').is_file())
        self.diag.runtime_started(123)
        self.diag.runtime_text('Entering original x86-64 code\n')
        self.diag.runtime_exit(-1073741819)
        first_text = (self.logs / 'last_run.log').read_text()
        self.assertIn(first, first_text)
        self.assertEqual(self.read('session.json')['runtime']['exit_code_u32_hex'], '0xC0000005')
        self.assertFalse(self.read('session.json')['runtime']['expected_shutdown'])
        self.diag.begin_run({'start': 'u2'})
        self.assertEqual((self.logs / 'previous_run.log').read_text(), first_text)
        self.assertNotEqual(self.diag.session['runtime']['run_id'], first)
        self.diag.stop_requested()
        self.diag.runtime_exit(1)
        self.assertTrue(self.read('session.json')['runtime']['expected_shutdown'])

    def test_failed_preparation_is_persistent(self):
        self.diag.begin_run({'start': 'u1'})
        self.diag.failure('runtime.spawn', OSError('dependency missing'))
        self.assertEqual(self.read('session.json')['stage'], 'runtime.spawn')
        self.assertIn('dependency missing', (self.logs / 'launcher.log').read_text())

    def test_rotation_caps_file_sizes_and_history_count(self):
        log = diagnostics.BoundedLog(self.logs / 'bounded.log', self.fail, limit=256)
        for _ in range(20):
            self.assertTrue(log.write('x' * 10000))
        members = list(self.logs.glob('bounded.log*'))
        self.assertEqual(len(members), 3)
        self.assertTrue(all(p.stat().st_size <= 256 for p in members))

    def test_permission_and_midstream_errors_are_visible_nonfatal_and_deduplicated(self):
        messages = []
        self.diag.on_error = messages.append
        with mock.patch.object(Path, 'open', side_effect=PermissionError('denied')):
            self.diag.event('INFO', 'test', 'still running')
            self.diag.event('INFO', 'test', 'still running')
        self.assertEqual(len(messages), 1)
        self.diag.begin_run({'start': 'u1'})
        with mock.patch.object(Path, 'open', side_effect=OSError('disk full')):
            self.diag.runtime_text('output\n')
        self.diag.runtime_exit(7)
        self.assertEqual(self.read('session.json')['runtime']['exit_code'], 7)

    def test_unavailable_log_directory_does_not_abort_application(self):
        blocked = self.root / 'file-not-directory'
        blocked.write_text('do not change')
        with mock.patch('sys.stderr', io.StringIO()):
            diag = diagnostics.Diagnostics(blocked, self.build, '0.3.9')
            diag.begin_run({'start': 'u1'})
            diag.runtime_text('hello\n')
            diag.runtime_exit(9)
        self.assertTrue(diag.errors)
        self.assertEqual(blocked.read_text(), 'do not change')

    def test_allowlisted_settings_not_full_configuration(self):
        settings = {'start': 'u2', 'fullscreen': True, 'window_size': '1920x1080',
                    'frame_rate': '60', 'extra_env': 'API_TOKEN=secret', 'game_dir': '/private/game',
                    'user_dir': '/private/save', 'player_name': 'Somebody', 'key_bindings': {'A': 'B'},
                    'anti_aliasing': '/private/invalid-setting'}
        safe = diagnostics.safe_settings(settings)
        self.assertEqual(set(safe), {'start', 'fullscreen', 'window_size', 'frame_rate'})

    def test_privacy_export_manifest_hashes_and_snapshot_stability(self):
        secret = 'NEVER_SHARE_THIS_TOKEN'
        settings = {'start': 'u1', 'game_dir': r'C:\Users\Alice Example\My Games',
                    'user_dir': '/home/alice/saves', 'player_name': 'AlicePrivate',
                    'extra_env': 'API_TOKEN=' + secret}
        sha = 'a' * 40
        (self.build / 'BUILD-METADATA.json').write_text(json.dumps(
            {'source_sha': sha, 'files': [{'path': '/private/build'}], 'secret': secret}))
        self.diag.begin_run(settings)
        self.diag.runtime_text('C:\\Users\\Alice Example\\My Games\\game.elf ' + secret + '\n')
        self.diag.event('ERROR', 'test', 'password="private space secret" https://u:p@example.com/?token=x '
                        'Bearer sensitiveAuth abc@example.com 192.168.1.3')
        for filename in ('save.bin', 'game.elf', 'secret.dmp', 'settings.json'):
            (self.logs / filename).write_text(secret)
        files = self.diag.snapshot(settings)
        self.diag.event('INFO', 'test', 'after snapshot')
        destination = self.root / 'report.zip'
        self.diag.export(destination, files)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), set(files))
            for name, data in files.items():
                self.assertEqual(archive.read(name), data)
                if name.endswith('.json'):
                    json.loads(data)
            all_text = '\n'.join(data.decode() for data in files.values())
            for private in (secret, 'Alice Example', 'AlicePrivate', '/home/alice', '/private/build',
                            'private space secret', 'sensitiveAuth', 'example.com', '192.168.1.3'):
                self.assertNotIn(private, all_text)
            self.assertNotIn('after snapshot', all_text)
            self.assertEqual(json.loads(files['diagnostics.json'])['source_sha'], sha)
            manifest = json.loads(files['manifest.json'])
            for entry in manifest['files']:
                self.assertEqual(len(files[entry['name']]), entry['bytes'])
                self.assertEqual(hashlib.sha256(files[entry['name']]).hexdigest(), entry['sha256'])

    def test_report_cap_and_oversized_json_are_explicit(self):
        for name in ('launcher.log', 'launcher.log.1', 'last_run.log', 'previous_run.log'):
            (self.logs / name).write_text('x' * (diagnostics.LIMIT * 3))
        (self.logs / 'previous-session.json').write_text(json.dumps({'large': 'x' * diagnostics.LIMIT}))
        files = self.diag.snapshot({})
        self.assertLessEqual(sum(map(len, files.values())), diagnostics.EXPORT_LIMIT)
        self.assertIn('previous-session.json', json.loads(files['manifest.json'])['omitted_or_unavailable'])
        self.assertNotIn('previous-session.json', files)
        with self.assertRaises(ValueError):
            self.diag.export(self.root / 'too-big.zip', {'large': b'x' * (diagnostics.EXPORT_LIMIT + 1)})
        self.assertFalse((self.root / 'too-big.zip').exists())

    def test_no_overwrite_path_traversal_or_partial_report_left_behind(self):
        destination = self.root / 'existing.zip'
        destination.write_bytes(b'existing user file')
        with self.assertRaises(FileExistsError):
            self.diag.export(destination, self.diag.snapshot({}))
        self.assertEqual(destination.read_bytes(), b'existing user file')
        with self.assertRaises(ValueError):
            self.diag.export(self.root / 'bad.zip', {'../save': b'bad'})
        partial = self.root / 'partial.zip'
        with mock.patch.object(zipfile.ZipFile, 'writestr', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.diag.export(partial, self.diag.snapshot({}))
        self.assertFalse(partial.exists())

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symbolic links unavailable')
    def test_export_does_not_follow_symlink_logs(self):
        private = self.root / 'private'
        private.write_text('secret game contents')
        link = self.logs / 'last_run.log'
        try:
            link.symlink_to(private)
        except OSError:
            self.skipTest('OS does not permit creating symbolic links')
        files = self.diag.snapshot({})
        self.assertNotIn('last_run.log', files)
        self.assertIn('last_run.log', json.loads(files['manifest.json'])['omitted_or_unavailable'])

    def test_diagnostics_code_has_no_network_operations(self):
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')):
            diag = diagnostics.Diagnostics(self.root / 'offline', self.build, '0.3.9')
            diag.export(self.root / 'offline.zip', diag.snapshot({}))

    def test_new_ui_strings_have_real_translations_with_placeholders(self):
        import uncharted_i18n
        current = ast.parse((ROOT / 'launcher/uncharted_launcher.py').read_text())
        texts = {n.args[0].value for n in ast.walk(current) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_' and n.args
                 and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
        from uncharted_i18n_extra import _DIAGNOSTIC_TEXTS
        self.assertTrue(set(_DIAGNOSTIC_TEXTS) <= texts)
        for language in ('es', 'pt', 'fr', 'de', 'it', 'ja'):
            for text in _DIAGNOSTIC_TEXTS:
                translated = uncharted_i18n.translate(language, text)
                self.assertNotEqual(text, translated)
                self.assertEqual(text.count('{}'), translated.count('{}'))

    def test_real_early_import_failure_retains_local_evidence(self):
        launcher = ROOT / 'launcher/uncharted_launcher.py'
        code = ('import sys,runpy; path=sys.argv[1]; '
                'sys.argv=[path,"--export-diagnostics",sys.argv[2]]; '
                'sys.modules["trophies"]=None; runpy.run_path(path,run_name="__main__")')
        result = subprocess.run([sys.executable, '-c', code, str(launcher), str(self.root / 'unused.zip')],
                                env={**os.environ, 'APPDATA': str(self.root / 'profile')},
                                capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        directory = self.root / 'profile/uncharted-launcher/diagnostics'
        self.assertIn('ModuleNotFoundError', (directory / 'launcher.log').read_text())
        self.assertFalse(json.loads((directory / 'session.json').read_text())['clean_shutdown'])

    def test_real_product_cli_creates_inspectable_offline_zip(self):
        target = self.root / 'actual-cli-report.zip'
        result = subprocess.run([sys.executable, str(ROOT / 'launcher/uncharted_launcher.py'),
                                 '--export-diagnostics', str(target)],
                                env={**os.environ, 'APPDATA': str(self.root / 'cli-profile')},
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        with zipfile.ZipFile(target) as archive:
            self.assertFalse(json.loads(archive.read('diagnostics.json'))['remote_telemetry'])
            self.assertIn('manifest.json', archive.namelist())
            self.assertIn('Application started', archive.read('launcher.log').decode())

    def launcher_module(self):
        spec = importlib.util.spec_from_file_location('diagnostics_launcher_test',
                                                      ROOT / 'launcher/uncharted_launcher.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        return launcher

    def test_real_capture_path_records_nonzero_exit_even_when_disk_fails(self):
        launcher = self.launcher_module()
        window = launcher.Launcher.__new__(launcher.Launcher)
        import queue
        window.output = queue.Queue(maxsize=2)
        process = mock.Mock(pid=123, stdout=io.BytesIO(b'first\nsecond\nthird\n'))
        process.wait.return_value = 7
        self.diag.begin_run({'start': 'u1'})
        messages = []
        self.diag.on_error = messages.append
        with mock.patch.object(launcher, 'DIAGNOSTICS', self.diag), \
                mock.patch.object(Path, 'open', side_effect=OSError('disk full')):
            window.read_output(process)
        self.assertEqual(window.output.qsize(), 2)
        self.assertEqual(window.output.get_nowait(), 'third\n')
        self.assertEqual(window.output.get_nowait(), (7,))
        self.assertTrue(messages)
        self.assertEqual(self.diag.session['runtime']['exit_code'], 7)
        process.wait.assert_called_once()

    def test_actual_gui_spawn_failure_is_recorded_before_process_launch(self):
        launcher = self.launcher_module()
        window = launcher.Launcher.__new__(launcher.Launcher)
        window.process = None
        window.app = {'start': 'u1', 'user_dir': str(self.root / 'legacy')}
        window.collect = mock.Mock()
        window.set_log = mock.Mock()
        window.append = mock.Mock()
        with mock.patch.object(launcher, 'DIAGNOSTICS', self.diag), \
                mock.patch.object(launcher, 'game_folder', return_value=str(self.root)), \
                mock.patch.object(launcher, 'game_available', return_value=True), \
                mock.patch.object(launcher, 'game_environment', return_value={}), \
                mock.patch.object(launcher.subprocess, 'Popen', side_effect=FileNotFoundError('runtime missing')):
            window.play()
        self.assertTrue((self.logs / 'last_run.log').exists())
        self.assertEqual(self.read('session.json')['stage'], 'runtime.spawn')
        self.assertIn('runtime missing', (self.logs / 'launcher.log').read_text())
        self.assertIsNone(window.process)

    def test_legacy_alias_rotates_and_active_runtime_is_not_claimed_clean(self):
        legacy = self.root / 'legacy'
        self.diag.begin_run({'start': 'u1'}, legacy)
        self.diag.runtime_text('first run evidence\n')
        self.diag.runtime_exit(0)
        old = (legacy / 'last_run.log').read_text()
        self.diag.begin_run({'start': 'u2'}, legacy)
        self.assertEqual((legacy / 'previous_run.log').read_text(), old)
        self.assertNotIn('first run evidence', (legacy / 'last_run.log').read_text())
        self.diag.finish()
        state = self.read('session.json')
        self.assertTrue(state['clean_shutdown'])  # launcher only
        self.assertTrue(state['runtime']['capture_ended_with_launcher'])
        self.assertIsNone(state['runtime']['exit_code'])
        self.assertEqual(state['previous_runtime']['exit_code'], 0)
