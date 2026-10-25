# SPDX-License-Identifier: GPL-2.0-or-later
"""Local-only bounded diagnostics. No networking, dumps, saves or directory crawling.

Sanitization is best effort, NOT a guarantee that arbitrary runtime text is private.
Exports are in-memory snapshots for the user to inspect before explicitly saving.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import threading
import uuid
import zipfile

LIMIT = 256 * 1024
EXPORT_LIMIT = 2 * 1024 * 1024
MAX_EVENT = 8192
SAFE_CHOICES = {
    'start': {'u1', 'u2', 'u3'}, 'anti_aliasing': {'off', 'taa', 'fsr3', 'fsr4', 'fsr411', 'dlss', 'xess'},
    'frame_generation': {'off', 'fsr3', 'dlss'}, 'present_mode': {'Mailbox', 'Fifo', 'FifoRelaxed', 'Immediate'},
    'scaler': {'fsr1', 'bilinear'}, 'aspect': {'16:9', '21:9', '32:9', 'window'},
    'aa_preset': {'', 'native', 'quality', 'balanced', 'performance', 'ultra_performance'},
}
SAFE_BOOL = {'fullscreen', 'show_fps', 'mouse_look', 'mouse_invert_y', 'pad_invert_y', 'pad_vibration'}
SAFE_NUMBER = {'frame_rate', 'render_height', 'aa_sharpness', 'frame_gen_multiplier', 'mouse_sensitivity',
               'pad_vibration_strength', 'language'}


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')


def safe_settings(settings):
    out = {}
    for key, value in settings.items():
        if key in SAFE_CHOICES and isinstance(value, str) and value in SAFE_CHOICES[key]:
            out[key] = value
        elif key in SAFE_BOOL and type(value) is bool:
            out[key] = value
        elif key in SAFE_NUMBER and re.fullmatch(r'\d{1,4}(?:\.\d{1,3})?', str(value)):
            out[key] = str(value)
        elif key == 'window_size' and re.fullmatch(r'\d{3,5}x\d{3,5}', str(value)):
            out[key] = value
    return out


class Sanitizer:
    def __init__(self, paths=()):
        self.values = {str(p) for p in (Path.home(), *paths) if p and len(str(p)) > 3}

    def learn(self, settings):
        for key, value in settings.items():
            if isinstance(value, str) and value and key not in safe_settings(settings):
                if len(value) >= 4 or key == 'player_name':
                    self.values.add(value)
                if key == 'extra_env':
                    for match in re.finditer(r'\w+=(\S+)', value):
                        if len(match[1]) >= 4:
                            self.values.add(match[1])

    def __call__(self, text):
        text = str(text)
        for value in sorted(self.values, key=len, reverse=True):
            for spelling in {value, value.replace('\\', '/'), value.replace('/', '\\')}:
                pattern = re.escape(spelling)
                if len(spelling) < 4:
                    pattern = r'(?<!\w)' + pattern + r'(?!\w)'
                text = re.sub(pattern, '<private>', text, flags=re.I)
        text = re.sub(r'(?i)\b(?:https?|ftp|file)://[^\s<>]+', '<url>', text)
        # Quoted and unquoted absolute paths, including spaces. Over-redact rather than
        # leave the tail of a username/library path behind.
        text = re.sub(r'''["'][A-Za-z]:[\\/][^\r\n]*?["']''', '<path>', text)
        text = re.sub(r'''["']/[^\r\n]*?["']''', '<path>', text)
        text = re.sub(r'(?i)\b[A-Z]:[\\/][^\r\n<>]*', '<path>', text)
        text = re.sub(r'\\\\[^\r\n<>]+', '<path>', text)
        text = re.sub(r'(?<![\w<])/(?!/)[^\r\n<>]*', '<path>', text)
        text = re.sub(r'(?i)\b(?:bearer\s+)[\w.+/=-]+', 'Bearer <secret>', text)
        text = re.sub(r'(?i)\b([a-z0-9_-]*(?:password|passwd|token|secret|api[_-]?key|authorization|cookie)|key)\b'
                      r'(\s*[=:]\s*|\s+)(?:"[^"]*"|\x27[^\x27]*\x27|[^\s,;]+)',
                      r'\1=<secret>', text)
        text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '<email>', text)
        text = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<ip>', text)
        return text


class BoundedLog:
    """Flush every write; cap each file and retain at most two size-rotation backups."""
    def __init__(self, path, fail, limit=LIMIT):
        self.path, self.fail, self.limit = Path(path), fail, limit
        self.lock = threading.RLock()

    def write(self, text):
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                data = text.encode('utf-8', 'replace')[:min(MAX_EVENT, self.limit - 32)]
                if len(text.encode('utf-8', 'replace')) > len(data):
                    data += b' [truncated]\n'
                if self.path.is_symlink():
                    raise OSError('refusing a symbolic-link log')
                if self.path.exists() and self.path.stat().st_size + len(data) > self.limit:
                    self.path.with_name(self.path.name + '.2').unlink(missing_ok=True)
                    for old, new in ((self.path.with_name(self.path.name + '.1'),
                                      self.path.with_name(self.path.name + '.2')),
                                     (self.path, self.path.with_name(self.path.name + '.1'))):
                        if old.exists():
                            old.replace(new)
                with self.path.open('ab') as stream:
                    stream.write(data)
                    stream.flush()
                return True
            except (OSError, ValueError) as error:
                self.fail(f'Log write failed: {type(error).__name__}: {error}')
                return False


class Diagnostics:
    def __init__(self, directory, build_dir, version, limit=LIMIT):
        self.directory, self.build_dir = Path(directory), Path(build_dir)
        self.version, self.limit = version, limit
        self.lock = threading.RLock()
        self.redact = Sanitizer((directory, build_dir, os.environ.get('APPDATA', '')))
        self.errors = []
        self.on_error = None
        self.runtime = self.legacy = None
        self.closed = False
        self.session = {'schema': 1, 'session_id': uuid.uuid4().hex, 'started_utc': utc(),
                        'stage': 'launcher.startup', 'clean_shutdown': False, 'runtime': None}
        self.log = BoundedLog(self.directory / 'launcher.log', self.problem, limit)
        with self.lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                current = self.directory / 'session.json'
                if current.exists():
                    previous = self._read(current, self.limit)
                    self._json('previous-session.json', previous)
                    if previous.get('clean_shutdown') is False:
                        self.event('WARNING', 'launcher', 'Previous session has no clean shutdown record; cause unknown')
            except (OSError, ValueError) as error:
                self.problem(f'Diagnostics initialization failed: {error}')
            self._persist()
        self.event('INFO', 'launcher', 'Application started')

    def problem(self, message):
        message = self.redact(message)[:1024]
        with self.lock:
            if message in self.errors:
                return
            self.errors.append(message)
            self.errors = self.errors[-16:]
        try:
            if self.on_error:
                self.on_error(message)
            else:
                import sys
                if sys.stderr:
                    print(message, file=sys.stderr)
        except Exception:
            pass  # diagnostics must not take down the application

    def _read(self, path, limit=EXPORT_LIMIT):
        if path.is_symlink() or path.stat().st_size > limit:
            raise ValueError('invalid or oversized diagnostics file')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('expected a JSON object')
        return value

    def _json(self, name, value):
        target = self.directory / name
        temp = target.with_suffix('.tmp')
        if target.is_symlink() or temp.is_symlink():
            raise OSError('refusing symbolic-link metadata')
        temp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        temp.replace(target)

    def _persist(self):
        try:
            self._json('session.json', self.session)
        except (OSError, ValueError) as error:
            self.problem(f'Session metadata write failed: {error}')

    def event(self, severity, component, message):
        with self.lock:
            self.log.write(f'{utc()} {severity} {component} session={self.session["session_id"]} '
                           f'{self.redact(message)}\n')

    def stage(self, name, message=''):
        with self.lock:
            self.session['stage'] = name
            self._persist()
            self.event('INFO', name, message or name)

    def _rotate_runtime_history(self, directory):
        try:
            if not (directory / 'last_run.log').exists():
                return
            for suffix in ('', '.1', '.2'):
                for base in ('last_run.log', 'previous_run.log'):
                    path = directory / (base + suffix)
                    if path.is_symlink() or (path.exists() and not path.is_file()):
                        raise OSError('refusing non-file runtime history')
            for suffix in ('', '.1', '.2'):
                source = directory / ('last_run.log' + suffix)
                dest = directory / ('previous_run.log' + suffix)
                if source.exists():
                    source.replace(dest)
                else:
                    dest.unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            self.problem(f'Runtime history rotation failed: {error}')

    def begin_run(self, settings, legacy_dir=None):
        with self.lock:
            self.redact.learn(settings)
            if self.session['runtime']:
                self.session['previous_runtime'] = self.session['runtime']
            self.session['runtime'] = {'run_id': uuid.uuid4().hex, 'started_utc': utc(),
                                       'stage': 'preparation', 'settings': safe_settings(settings),
                                       'stop_requested': False, 'exit_code': None}
            self._rotate_runtime_history(self.directory)
            self.runtime = BoundedLog(self.directory / 'last_run.log', self.problem, self.limit)
            self.legacy = None
            try:
                if legacy_dir and Path(legacy_dir).resolve() != self.directory.resolve():
                    legacy_dir = Path(legacy_dir)
                    self.redact.values.add(str(legacy_dir))
                    self._rotate_runtime_history(legacy_dir)
                    self.legacy = BoundedLog(legacy_dir / 'last_run.log', self.problem, self.limit)
            except (OSError, ValueError) as error:
                self.problem(f'Compatibility log unavailable: {error}')
            self.stage('runtime.preparation')
            self.runtime_text('Runtime preparation started\n')  # sink exists BEFORE Popen

    def runtime_started(self, pid):
        with self.lock:
            if self.session['runtime']:
                self.session['runtime'].update(pid=pid, stage='running')
            self.stage('runtime.started', f'Process started pid={pid}')

    def runtime_text(self, text):
        with self.lock:
            if 'Entering original x86-64 code' in text and self.session.get('runtime'):
                self.session['runtime'].update(stage='game_entered', entered_utc=utc())
                self._persist()
            text = self.redact(text)
            run = self.session.get('runtime') or {}
            stamped = f'{utc()} INFO runtime session={self.session["session_id"]} run={run.get("run_id", "none")} {text}'
            for sink in (self.runtime, self.legacy):
                if sink:
                    sink.write(stamped if sink is self.runtime else text)

    def stop_requested(self, requested=True):
        with self.lock:
            if self.session['runtime']:
                self.session['runtime']['stop_requested'] = requested
                self._persist()
            self.event('INFO', 'runtime', f'Stop requested={requested}')

    def runtime_exit(self, code):
        with self.lock:
            if self.session['runtime']:
                self.session['runtime'].update(exit_code=code, exit_code_u32_hex=f'0x{code & 0xffffffff:08X}',
                                               exited_utc=utc(), stage='exited',
                                               expected_shutdown=code == 0 or self.session['runtime']['stop_requested'])
            self.runtime_text(f'Process exited code={code}\n')
            self.stage('runtime.exited', f'Process exited code={code}')
            if code and not (self.session['runtime'] or {}).get('stop_requested'):
                self.event('ERROR', 'runtime', f'Unexpected process exit code={code} (0x{code & 0xffffffff:08X})')

    def failure(self, stage, error):
        with self.lock:
            self.session['stage'] = stage
            self._persist()
            self.event('ERROR', stage, f'{type(error).__name__}: {error}')

    def finish(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                if self.session['runtime'] and self.session['runtime'].get('exit_code') is None:
                    self.session['runtime']['capture_ended_with_launcher'] = True
                self.session.update(clean_shutdown=True, ended_utc=utc())
                self.event('INFO', 'launcher', 'Launcher shutdown; runtime state is recorded separately')
                self._persist()

    def snapshot(self, settings):
        """Return only allowlisted text files; never follow arbitrary user/file paths."""
        with self.lock:
            self.redact.learn(settings)
            files, omitted = {}, []
            def add(name, text, sanitize=True):
                data = (self.redact(text) if sanitize else text).encode('utf-8')
                if len(data) > self.limit:
                    data = b'[older content omitted]\n' + data[-self.limit + 32:]
                if sum(map(len, files.values())) + len(data) > EXPORT_LIMIT - 64 * 1024:
                    omitted.append(name)
                else:
                    files[name] = data
            def add_json(name, value):
                def clean(value):
                    if isinstance(value, str):
                        return self.redact(value)
                    if isinstance(value, dict):
                        return {str(k): clean(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [clean(v) for v in value]
                    return value
                text = json.dumps(clean(value), indent=2)
                if len(text.encode('utf-8')) > self.limit:
                    omitted.append(name)
                else:
                    add(name, text, sanitize=False)
            info = {'schema': 1, 'created_utc': utc(), 'session_id': self.session['session_id'],
                    'app_version': self.version, 'python_version': platform.python_version(),
                    'os': platform.system(), 'os_version': platform.version(),
                    'architecture': platform.machine(), 'pointer_bits': struct.calcsize('P') * 8,
                    'cpu': platform.processor() or 'unavailable', 'gpu': self.session.get('gpu', {'status': 'unavailable'}),
                    'graphics_driver': {'status': 'not collected'}, 'source_sha': None,
                    'source_status': 'build metadata unavailable', 'remote_telemetry': False,
                    'native_crash_dumps': 'not collected or included', 'diagnostics_errors': self.errors}
            try:
                metadata = self._read(self.build_dir / 'BUILD-METADATA.json')
                for key in ('source_sha', 'base_sha'):
                    if re.fullmatch('[0-9a-f]{40}', str(metadata.get(key))):
                        info[key] = metadata[key]
                if info['source_sha']:
                    info['source_status'] = 'packaged BUILD-METADATA.json'
                upstream = metadata.get('upstream_release_commits', [])
                if isinstance(upstream, list) and all(re.fullmatch('[0-9a-f]{40}', str(v)) for v in upstream):
                    info['upstream_release_commits'] = upstream
                stamp = metadata.get('build_utc', '')
                if isinstance(stamp, str) and re.fullmatch(r'[0-9T:.+Z-]{1,48}', stamp):
                    info['build_utc'] = stamp
                if metadata.get('target') == 'Windows AMD64':
                    info['build_target'] = 'Windows AMD64'
                # Never copy full provenance (file paths/tool output) or user configuration.
            except (OSError, ValueError):
                pass
            add_json('diagnostics.json', info)
            add_json('session.json', self.session)
            add_json('settings-redacted.json', safe_settings(settings))
            for name in ('launcher.log', 'launcher.log.1', 'last_run.log', 'last_run.log.1',
                         'previous_run.log', 'previous_run.log.1'):
                path = self.directory / name
                try:
                    if path.is_symlink() or not path.is_file():
                        omitted.append(name)
                        continue
                    with path.open('rb') as stream:
                        stream.seek(0, 2)
                        stream.seek(max(0, stream.tell() - self.limit))
                        add(name, stream.read(self.limit).decode('utf-8', 'replace'))
                except OSError:
                    omitted.append(name)
            try:
                add_json('previous-session.json', self._read(self.directory / 'previous-session.json', self.limit))
            except (OSError, ValueError):
                omitted.append('previous-session.json')
            add('README.txt', 'Local diagnostics only. No upload has occurred.\n'
                'Sanitization is best effort: inspect EVERY file before sharing. Runtime text may contain private data.\n'
                'No saves, game contents, full settings, dumps, environment or command lines are included.\n'
                'Missing history/hardware data is normal on a first run or unavailable system.\n')
            manifest = {'schema': 1, 'omitted_or_unavailable': omitted,
                        'files': [{'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                                  for name, data in files.items()],
                        'notice': 'manifest does not hash itself; preview and save use this same snapshot'}
            files['manifest.json'] = json.dumps(manifest, indent=2).encode('utf-8')
            return files

    @staticmethod
    def export(destination, files):
        # Exclusive creation: never truncate an existing user file; delete only our
        # own partially written archive if ZIP/disk writing fails.
        path = Path(destination)
        if sum(map(len, files.values())) > EXPORT_LIMIT:
            raise ValueError('Diagnostic report exceeds the size limit')
        for name in files:
            if '/' in name or '\\' in name or name.startswith('.'):
                raise ValueError('Unexpected archive member')
        with path.open('xb') as stream:
            try:
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                    for name, data in files.items():
                        archive.writestr(name, data)
            except Exception:
                stream.close()
                path.unlink(missing_ok=True)
                raise
