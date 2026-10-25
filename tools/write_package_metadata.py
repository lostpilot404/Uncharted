#!/usr/bin/env python3
"""Record exact Windows package provenance and PE architecture; no game-data access."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

BASE = '31198fcf945016cc35c23931c33cb440c4895461'
UPDATES = ['ac2bfbc1602623745d8b291a8ced639d6ede6369',
           '754ed2ab4f1fdd21250c8785d4d4f49517d31292']


def command(*args):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {'command': list(args), 'exit_code': p.returncode, 'output': p.stdout.strip()}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def pe_machine(path):
    with path.open('rb') as f:
        if f.read(2) != b'MZ':
            raise ValueError(f'not a PE executable: {path}')
        f.seek(0x3c)
        offset = struct.unpack('<I', f.read(4))[0]
        f.seek(offset)
        if f.read(4) != b'PE\0\0':
            raise ValueError(f'bad PE signature: {path}')
        machine = struct.unpack('<H', f.read(2))[0]
        if machine != 0x8664:
            raise ValueError(f'expected AMD64, got {machine:#x}: {path}')
        return 'AMD64 (0x8664)'


def main(dest):
    dest = Path(dest)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    for ancestor in [BASE, *UPDATES]:
        subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, sha], check=True)
    files = []
    for path in sorted(dest.rglob('*')):
        if path.is_file() and path.name not in ('BUILD-METADATA.json', 'SHA256SUMS.txt'):
            item = {'path': path.relative_to(dest).as_posix(), 'bytes': path.stat().st_size,
                    'sha256': digest(path)}
            if path.suffix.lower() in ('.exe', '.dll', '.pyd'):
                item['pe_machine'] = pe_machine(path)
            files.append(item)
    result = {
        'source_sha': sha, 'base_sha': BASE, 'upstream_release_commits': UPDATES,
        'build_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'target': 'Windows AMD64', 'configuration': 'RelWithDebInfo; BB_PGO=off; BB_LTO=OFF',
        'packaging': 'PyInstaller one-directory; launcher EXE is NOT standalone',
        'run_url': f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
        'toolchains': [command('clang', '--version'), command('cmake', '--version'),
                       command('ninja', '--version'), command('pacman', '-Q'),
                       command('out/pyenv/Scripts/python.exe', '-m', 'pip', 'freeze')],
        'nvidia_sdk': command('git', '-C', 'out/dlss-sdk', 'rev-parse', 'HEAD'),
        'submodules': command('git', 'submodule', 'status', '--recursive'),
        'source_status_after_build': command('git', 'status', '--porcelain=v1'),
        'validation': {'manual_gameplay': 'NOT_PERFORMED',
                       'gameplay': 'BLOCKED: proprietary game dump and compatible GPU not supplied',
                       'native_gui_launch': 'See separate native-launch-results.json artifact',
                       'localization': 'Known upstream translation-coverage test failures; draft candidate only'},
        'files': files,
    }
    metadata = dest / 'BUILD-METADATA.json'
    metadata.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    checks = files + [{'path': metadata.name, 'sha256': digest(metadata)}]
    (dest / 'SHA256SUMS.txt').write_text(''.join(f"{f['sha256']}  {f['path']}\n" for f in checks), encoding='utf-8')
    print(f'package provenance: source={sha} files={len(files)} AMD64_PE_files={sum("pe_machine" in f for f in files)}')


if __name__ == '__main__':
    main(sys.argv[1])
