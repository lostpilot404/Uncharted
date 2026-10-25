"""Portable checks for the release provenance/architecture verifier."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    'release_metadata', Path(__file__).resolve().parents[1] / 'tools/write_package_metadata.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class ReleaseMetadataTests(unittest.TestCase):
    def test_amd64_and_reject_other_architecture(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample.exe'
            for machine in (0x8664, 0x14c):
                data = bytearray(256)
                data[:2] = b'MZ'
                struct.pack_into('<I', data, 0x3c, 128)
                data[128:132] = b'PE\0\0'
                struct.pack_into('<H', data, 132, machine)
                p.write_bytes(data)
                if machine == 0x8664:
                    self.assertEqual(metadata.pe_machine(p), 'AMD64 (0x8664)')
                else:
                    with self.assertRaises(ValueError):
                        metadata.pe_machine(p)

    def test_non_executable_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'bad.exe'
            p.write_bytes(b'not an executable')
            with self.assertRaises(ValueError):
                metadata.pe_machine(p)

    def test_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'sample'
            p.write_bytes(b'abc')
            self.assertEqual(metadata.digest(p),
                             'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_provenance_command_timeout_is_fatal(self):
        with mock.patch.object(metadata.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired(['git'], 60)) as run, \
                mock.patch.object(metadata, 'progress'):
            with self.assertRaises(subprocess.TimeoutExpired):
                metadata.command('git', '--version')
            self.assertEqual(run.call_args.kwargs['timeout'], 60)

    def test_cmake_provenance_uses_actual_build_cache(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'CMakeCache.txt'
            p.write_text('CMAKE_CACHE_MAJOR_VERSION:INTERNAL=4\n'
                         'CMAKE_CACHE_MINOR_VERSION:INTERNAL=2\n'
                         'CMAKE_CACHE_PATCH_VERSION:INTERNAL=3\n'
                         'CMAKE_COMMAND:INTERNAL=C:/actual build/bin/cmake.exe\n'
                         'CMAKE_GENERATOR:INTERNAL=Ninja\n'
                         'CMAKE_C_COMPILER:FILEPATH=C:/toolchain/clang.exe\n')
            info = metadata.cmake_build_info(p)
            self.assertEqual(info['version'], '4.2.3')
            self.assertEqual(info['executable'], 'C:/actual build/bin/cmake.exe')
            self.assertEqual(info['generator'], 'Ninja')
            p.write_text('CMAKE_CACHE_MAJOR_VERSION:INTERNAL=4\n')
            with self.assertRaises(ValueError):
                metadata.cmake_build_info(p)

    def test_source_status_keeps_tracked_changes_fatal(self):
        with mock.patch.object(metadata, 'command',
                               return_value={'exit_code': 0, 'output': ''}) as command:
            self.assertIn('scope', metadata.source_status())
            args = command.call_args.args
            self.assertIn('--untracked-files=no', args)
            self.assertIn('--ignore-submodules=untracked', args)
            self.assertIn('core.fsmonitor=false', args)
        for result in ({'exit_code': 1, 'output': ''},
                       {'exit_code': 0, 'output': ' M src/runtime_memory.c'}):
            with mock.patch.object(metadata, 'command', return_value=result):
                with self.assertRaises(ValueError):
                    metadata.source_status()
