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
        submodules = {'exit_code': 0, 'output': 'a' * 40 + ' gpu/dependency (v1)'}
        clean = {'exit_code': 0, 'output': ''}
        dirty = {'exit_code': 1, 'output': 'M\tsrc/runtime_memory.c'}
        with mock.patch.object(metadata, 'command', return_value=clean) as command:
            result = metadata.source_status(submodules)
            self.assertEqual(result['submodules'][0]['path'], 'gpu/dependency')
            self.assertEqual(command.call_count, 2)
            self.assertIn('--ignore-submodules=dirty', command.call_args_list[0].args)
            self.assertIn('--exit-code', command.call_args_list[0].args)
            self.assertEqual(command.call_args_list[1].args[:3], ('git', '-C', 'gpu/dependency'))
        for results in ([dirty], [clean, dirty]):
            with mock.patch.object(metadata, 'command', side_effect=results):
                with self.assertRaises(ValueError):
                    metadata.source_status(submodules)
        for output in ('-' + 'a' * 40 + ' gpu/dependency',
                       '+' + 'a' * 40 + ' gpu/dependency'):
            with mock.patch.object(metadata, 'command', return_value=clean):
                with self.assertRaises(ValueError):
                    metadata.source_status({'exit_code': 0, 'output': output})

    def test_exact_build_patch_verification_rejects_unrelated_edits(self):
        with tempfile.TemporaryDirectory() as d:
            patch = Path(d) / 'build.patch'
            patch.write_text('fixture patch')
            clean = {'exit_code': 0, 'output': '', 'stderr': ''}
            with mock.patch.object(metadata, 'command', return_value=clean) as command:
                result = metadata.patched_submodule_status('dependency', [patch])
                self.assertEqual(result['expected_patches'][0]['sha256'], metadata.digest(patch))
                calls = command.call_args_list
                self.assertEqual(calls[0].args[3:], ('read-tree', 'HEAD'))
                self.assertEqual(calls[1].args[3:5], ('apply', '--cached'))
                self.assertIn('GIT_INDEX_FILE', calls[0].kwargs['env'])
                self.assertNotIn('HEAD', calls[-1].args[3:])
                self.assertIn('--exit-code', calls[-1].args)
            dirty = {'exit_code': 1, 'output': 'M\tunrelated.c', 'stderr': ''}
            with mock.patch.object(metadata, 'command', side_effect=[clean, clean, clean, dirty]):
                with self.assertRaises(ValueError):
                    metadata.patched_submodule_status('dependency', [patch])
