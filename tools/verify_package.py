#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check that a built uncharted-windows.zip is structurally complete.

Used by .github/workflows/package.yml after packaging/windows/package.sh, and runnable by hand:

    python3 tools/verify_package.py dist/uncharted-windows.zip

It checks presence, not behaviour: no Windows machine in CI can start the game or DLSS.
Exits non-zero with a ::error line for the missing pieces.
"""
import sys
import os
import zipfile

# Everything the package must contain for a player to install it and enable DLSS Super
# Resolution without extra downloads (the game itself is never redistributed).
REQUIRED = [
    'Uncharted.exe',                              # the launcher (PyInstaller-frozen)
    'Play Uncharted.exe',                         # starts the game with the launcher's settings
    'bin/bb-probe.exe',                           # the runtime
    'bin/bb-gpu-capabilities.exe',                # the GPU check run.sh uses
    'bin/bbport_dlss.dll',                        # MIT bridge, this project
    'bin/nvngx_dlss.dll',                         # NVIDIA DLSS Super Resolution runtime
    'licenses/NVIDIA-DLSS-LICENSE.txt',           # its redistribution terms
    'licenses/bbport_dlss-LICENSE.txt',           # the bridge's MIT licence
    'run.py',                                     # preparation and start-up
    'README.txt',                                 # install, DLSS, troubleshooting
    'scripts/prepare.py',
]
# Directories that must never be shipped: player data and generated files.
FORBIDDEN_PREFIXES = ['out/', 'user/']


def main(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        count = len(archive.infolist())
    roots = {name.split('/')[0] for name in names}
    if roots != {'uncharted-windows'}:
        print(f'::error title=package::unexpected archive roots {sorted(roots)}')
        return 1
    missing = [item for item in REQUIRED if f'uncharted-windows/{item}' not in names]
    leaked = [name for name in names
              if any(name.startswith(f'uncharted-windows/{prefix}') for prefix in FORBIDDEN_PREFIXES)]
    dlls = sorted({name.rsplit('/', 1)[-1] for name in names if name.endswith('.dll')})
    size = os.path.getsize(path)
    print(f'package: {count} entries, {size} bytes, {len(dlls)} DLLs')
    print('dlls: ' + ', '.join(dlls))
    if 'nvngx_dlssg.dll' in dlls:
        print('note: nvngx_dlssg.dll (DLSS Frame Generation) is present but its settings '
              'default to off')
    if missing:
        print('::error title=package::missing ' + ', '.join(missing))
        return 1
    if leaked:
        print('::error title=package::generated or player data in the package: '
              + ', '.join(sorted(leaked)[:5]))
        return 1
    # The runtime and the bridge must sit next to each other: Dlss::Get() looks for both DLLs
    # in the executable's directory.
    print('package structure OK')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
