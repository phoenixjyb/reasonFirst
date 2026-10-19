from __future__ import annotations

from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import plistlib
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from gitlab_agent import __version__
from gitlab_agent.deployment_inventory import build_deployment_inventory, LABEL, TUNNEL_LABEL, MAX_PLIST_BYTES
from gitlab_agent.static_service_files import BoundedRead
from gitlab_agent.setup_state import SetupState


class DeploymentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Use the canonical temp home here; the reader's explicit-anchor test
        # covers accepted OS aliases before a selected home.
        self.home = Path(self.temp.name).resolve()
        self.plist = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')
        self.root = self.home / '.local/share/reasonfirst/v4-service'
        self.script = self.root / 'tools/codex_web_bridge/run_reasonfirst.sh'
        self.data = {
            'Label': LABEL, 'ProgramArguments': [str(self.script)],
            'WorkingDirectory': str(self.root), 'KeepAlive': True,
            'EnvironmentVariables': {'EXAMPLE_TOKEN': 'not-' + 'for-output'},
        }

    def write_plist(self, **changes):
        data = {**self.data, **changes}
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        self.plist.write_bytes(plistlib.dumps(data))
        self.plist.chmod(0o600)

    @contextmanager
    def posix_registration_metadata(self, *, mode=0o600, owner=1000, current_user=1000):
        """Model macOS metadata, not Windows ACLs, for the selected fixture only.

        Windows chmod(0600) controls the read-only attribute, not POSIX mode bits.
        Keep the actual file kind, size, reads and exceptions. In particular, never
        turn a symlink or directory into a trusted regular registration.
        """
        native_lstat = Path.lstat
        native_stat = os.stat
        native_fstat = os.fstat

        known_paths = (self.plist, self.plist.parent / (TUNNEL_LABEL + '.plist'))
        known_inodes = set()
        for path in known_paths:
            try:
                info = native_lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                known_inodes.add((info.st_dev, info.st_ino))

        def emulate(info):
            if (info.st_dev, info.st_ino) in known_inodes and stat.S_ISREG(info.st_mode):
                values = list(info)
                values[stat.ST_MODE] = stat.S_IFREG | mode
                values[stat.ST_UID] = owner
                return os.stat_result(values)
            return info

        def fixture_lstat(path, *args, **kwargs):
            return emulate(native_lstat(path, *args, **kwargs))

        def fixture_stat(path, *args, **kwargs):
            return emulate(native_stat(path, *args, **kwargs))

        def fixture_fstat(fd):
            return emulate(native_fstat(fd))

        def synthetic_reader(path, *, max_bytes, anchor=None, owner_uid=None,
                             reject_writable_bits=0o022):
            # Parser/metadata fixture only: this is never a Windows no-follow
            # guarantee. Both path and descriptor metadata must pass independently.
            try:
                info = Path(path).lstat()
            except FileNotFoundError:
                return BoundedRead('missing', reason='path_missing')
            current = owner_uid if owner_uid is not None else current_user
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != current or
                    info.st_mode & reject_writable_bits):
                return BoundedRead('unsupported', reason='synthetic_metadata_rejected')
            if info.st_size > max_bytes:
                return BoundedRead('invalid', reason='file_too_large')
            try:
                with Path(path).open('rb') as handle:
                    descriptor = os.fstat(handle.fileno())
                    if (not stat.S_ISREG(descriptor.st_mode) or descriptor.st_uid != current or
                            descriptor.st_mode & reject_writable_bits or
                            (info.st_dev, info.st_ino, info.st_size) !=
                            (descriptor.st_dev, descriptor.st_ino, descriptor.st_size)):
                        return BoundedRead('unsupported', reason='synthetic_metadata_rejected')
                    raw = handle.read(max_bytes + 1)
            except PermissionError:
                return BoundedRead('unreadable', reason='read_failed')
            if len(raw) > max_bytes:
                return BoundedRead('invalid', reason='file_too_large')
            return BoundedRead('ok', data=raw,
                               guarantees=('synthetic_path_and_descriptor_metadata',))

        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, 'lstat', fixture_lstat))
            stack.enter_context(patch('gitlab_agent.static_service_files.os.stat', fixture_stat))
            stack.enter_context(patch('gitlab_agent.static_service_files.os.fstat', fixture_fstat))
            stack.enter_context(patch('gitlab_agent.static_service_files.os.getuid',
                                      return_value=current_user, create=True))
            if os.name == 'nt':
                stack.enter_context(patch('gitlab_agent.deployment_inventory.read_bounded_regular',
                                          side_effect=synthetic_reader))
            yield

    def inventory(self, system='Darwin', state=None, *, simulate_posix=None):
        # Normal Windows inventory does not inspect launchd at all. These tests
        # explicitly select Darwin to exercise the same parser on every runner.
        # Real POSIX hosts retain native ownership/mode checks by default.
        if simulate_posix is None:
            simulate_posix = os.name == 'nt' and system.lower() in {'darwin', 'macos'}
        with ExitStack() as stack:
            stack.enter_context(patch('subprocess.run', side_effect=AssertionError('No process calls')))
            stack.enter_context(patch('socket.socket', side_effect=AssertionError('No socket calls')))
            if simulate_posix:
                stack.enter_context(self.posix_registration_metadata())
            result = build_deployment_inventory(state=state, system_name=system, home=self.home)
        self.assertIsNone(result['running_service_version'])
        self.assertEqual(result['live_inspection'], 'not_inspected')
        self.assertFalse(result['service_manager_changed'])
        self.assertFalse(result['adoption_performed'])
        self.assertNotIn('not-for-output', repr(result))
        return result

    def test_absent_known_registration_is_not_a_complete_service_search(self):
        result = self.inventory()
        self.assertEqual(result['registration']['status'], 'not_found')
        self.assertEqual(result['registration']['health'], 'not_inspected')
        self.assertFalse(result['legacy_evidence'])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_known_legacy_registration_never_claims_live_health_or_version(self):
        self.write_plist()
        before = self.plist.read_bytes()
        result = self.inventory()
        self.assertEqual(result['installed_cli']['version'], __version__)
        self.assertEqual(result['registration']['status'], 'discovered_legacy')
        self.assertEqual(result['registration']['layout'], 'legacy_staged_http')
        self.assertEqual(result['registration']['program'], str(self.script))
        self.assertEqual(result['registration']['transport'], 'streamable-http')
        self.assertEqual(result['registration']['process_status'], 'not_inspected')
        self.assertEqual(result['registration']['running_version'], None)
        self.assertTrue(result['legacy_evidence'])
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertFalse(self.script.exists())  # The script was not executed/read.

    def test_existing_sidecar_layout_is_recognized_but_not_adopted(self):
        script = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture/run_reasonfirst.sh'
        self.write_plist(ProgramArguments=[str(script)])
        result = self.inventory()
        self.assertEqual(result['registration']['layout'], 'versioned_http_sidecar')
        self.assertEqual(result['registration']['status'], 'discovered_legacy')
        self.assertIn('not_live', result['registration']['transport_basis'])

    def test_alternate_home_with_spaces_and_unicode_is_supported(self):
        self.home = self.home / 'operator 空间'
        self.home.mkdir()
        self.plist = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')
        self.root = self.home / '.local/share/reasonfirst/v4-service'
        self.script = self.root / 'tools/codex_web_bridge/run_reasonfirst.sh'
        self.data.update(ProgramArguments=[str(self.script)], WorkingDirectory=str(self.root))
        self.write_plist()
        self.assertEqual(self.inventory()['registration']['layout'], 'legacy_staged_http')

    def test_unknown_arguments_programs_or_cwd_are_not_echoed_or_executed(self):
        for change in (
            {'ProgramArguments': ['/bin/bash', '-c', 'not-for-output']},
            {'ProgramArguments': ['/tmp/not-for-output']},
            {'WorkingDirectory': '/tmp/not-for-output'},
            {'Program': '/tmp/not-for-output'},
            {'ProgramArguments': []}, {'ProgramArguments': 12},
        ):
            with self.subTest(change=change):
                self.write_plist(**change)
                result = self.inventory()
                self.assertEqual(result['registration']['status'], 'unsupported_layout')
                self.assertIsNone(result['registration']['transport'])
                self.assertTrue(result['legacy_evidence'])

    def test_no_broad_tunnel_label_scan(self):
        self.plist.parent.mkdir(parents=True)
        other = self.plist.parent / 'unrelated.tunnel.plist'
        other.write_text('not-for-output', encoding='utf-8')
        self.assertEqual(self.inventory()['registration']['status'], 'not_found')
        self.assertEqual(other.read_text(), 'not-for-output')

    def test_mismatched_label_is_not_adopted(self):
        self.write_plist(Label='unrelated.service')
        self.assertEqual(self.inventory()['registration']['status'], 'unsupported_layout')

    def test_invalid_and_oversized_plists_are_distinct_from_absence(self):
        self.write_plist()
        for data in (b'not-for-output', b'x' * (MAX_PLIST_BYTES + 1)):
            with self.subTest(size=len(data)):
                self.plist.write_bytes(data)
                self.assertEqual(self.inventory()['registration']['status'], 'invalid')

    def test_unsupported_platform_does_not_assume_launchd_or_systemd(self):
        self.write_plist()
        for system in ('Linux', 'Windows', 'Other'):
            with self.subTest(system=system):
                result = self.inventory(system)
                self.assertEqual(result['registration']['status'], 'not_inspected')
                self.assertEqual([item['status'] for item in result['service_definitions']],
                                 ['not_inspected', 'not_inspected'])
                self.assertFalse(result['legacy_evidence'])

    def test_nonmacos_inventory_does_not_read_or_classify_launchd_registration(self):
        self.write_plist()
        with patch('gitlab_agent.deployment_inventory._launch_agent',
                   side_effect=AssertionError('NonmacOS must not use the launchd adapter')), \
             patch.object(Path, 'open', side_effect=AssertionError('Do not read the plist')):
            for system in ('Windows', 'Linux', 'Other'):
                with self.subTest(system=system):
                    self.assertEqual(self.inventory(system)['registration']['status'], 'not_inspected')

    def test_darwin_layout_fixtures_use_explicit_posix_metadata_on_any_host(self):
        self.write_plist()
        result = self.inventory(simulate_posix=True)
        self.assertEqual(result['registration']['status'], 'discovered_legacy')
        self.assertTrue(result['registration']['ownership_verified'])
        # Native filesystem mode bits are not modified by the emulation.
        before = self.plist.stat()
        self.inventory(simulate_posix=True)
        self.assertEqual(self.plist.stat().st_mode, before.st_mode)

    def test_posix_mode_and_owner_guards_still_reject_before_read_on_any_host(self):
        self.write_plist()
        for mode, owner in ((0o620, 1000), (0o602, 1000), (0o600, 1001)):
            with self.subTest(mode=oct(mode), owner=owner), \
                 self.posix_registration_metadata(mode=mode, owner=owner), \
                 patch.object(Path, 'open', side_effect=AssertionError('Unsafe plist must not be read')):
                result = self.inventory(simulate_posix=False)
                self.assertEqual(result['registration']['status'], 'unsupported_layout')
                self.assertFalse(result['registration']['ownership_verified'])

    def test_directory_is_not_reclassified_as_regular_by_posix_fixture(self):
        self.plist.mkdir(parents=True)
        result = self.inventory(simulate_posix=True)
        self.assertEqual(result['registration']['status'], 'unsupported_layout')
        self.assertFalse(result['registration']['ownership_verified'])

    def test_bridge_config_is_an_existence_marker_not_live_readiness(self):
        marker = self.home / '.config/reasonfirst/bridge.yaml'
        marker.parent.mkdir(parents=True)
        marker.write_text('not-for-output', encoding='utf-8')
        # Content deliberately invalid: no parser/controller is run.
        result = self.inventory('Windows')
        self.assertEqual(result['bridge_config_marker'], 'present')
        self.assertTrue(result['legacy_evidence'])
        self.assertEqual(marker.read_text(), 'not-for-output')

    def test_recorded_runtimes_are_not_live_probes(self):
        state = SetupState(tunnel_id='tunnel_' + 'a'*32, tunnel_runtime='read-fixture',
                           completed_phases=('tunnel',))
        result = self.inventory('Linux', state)
        self.assertEqual(result['recorded_runtimes'][0]['status'], 'recorded')
        self.assertEqual(result['recorded_runtimes'][1]['status'], 'not_recorded')
        self.assertEqual(result['recorded_runtimes'][0]['health'], 'not_inspected')

    def test_group_writable_registration_is_not_trusted(self):
        if os.name == 'nt':
            self.skipTest('POSIX mode protection is not a Windows ACL test')
        self.write_plist()
        self.plist.chmod(0o666)
        self.assertEqual(self.inventory()['registration']['status'], 'unsupported_layout')

    def test_symlink_registration_is_not_followed(self):
        self.write_plist()
        target = self.home / 'target.plist'
        self.plist.replace(target)
        try:
            self.plist.symlink_to(target)
        except OSError:
            self.skipTest('Host cannot create an unprivileged symlink')
        self.assertEqual(self.inventory()['registration']['status'], 'unsupported_layout')

    def test_changed_definition_reason_is_preserved_without_adoption(self):
        # Fixed reader outcome remains distinguishable from initial absence.
        with patch('gitlab_agent.deployment_inventory.read_bounded_regular',
                   return_value=BoundedRead('invalid', reason='file_changed')) as reader:
            # This reader is already controlled; metadata emulation would
            # shadow it with a second reader mock on Windows.
            result = self.inventory(simulate_posix=False)
        self.assertEqual(reader.call_args_list, [
            call(self.plist, max_bytes=MAX_PLIST_BYTES, anchor=self.home),
            call(self.plist.parent / (TUNNEL_LABEL + '.plist'),
                 max_bytes=MAX_PLIST_BYTES, anchor=self.home),
        ])
        self.assertEqual(result['registration']['status'], 'invalid')
        self.assertFalse(result['registration']['ownership_verified'])
        for definition in result['service_definitions']:
            self.assertEqual(definition['status'], 'invalid')
            self.assertEqual(definition['reader_reason'], 'file_changed')
            self.assertEqual(definition['runtime_owner'], 'unobserved')

    def test_injected_changed_reader_survives_simulated_windows_fixture_selection(self):
        # Simulate only this helper's platform selection, not native Windows
        # filesystem APIs, ACLs, or the production reader's guarantees.
        with patch(f'{__name__}.os', SimpleNamespace(name='nt')), \
             patch.object(self, 'posix_registration_metadata',
                          side_effect=AssertionError('Injected reader must bypass metadata emulation')) as emulate:
            self.test_changed_definition_reason_is_preserved_without_adoption()
        emulate.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory replacement regression')
    def test_replaced_parent_cannot_supply_detached_definition_evidence(self):
        self.write_plist()
        native_read = os.read
        changed = False

        def reading(fd, count):
            nonlocal changed
            data = native_read(fd, count)
            if data and not changed:
                changed = True
                self.plist.parent.rename(self.home / 'detached')
                self.write_plist()
            return data

        with patch('gitlab_agent.static_service_files.os.read', side_effect=reading):
            result = self.inventory()
        self.assertTrue(changed)
        self.assertEqual(result['registration']['status'], 'invalid')
        self.assertFalse(result['registration']['ownership_verified'])
        self.assertEqual(result['service_definitions'][0]['reader_reason'], 'path_changed')

    def test_unreadable_registration_is_not_absence(self):
        self.write_plist()
        target = (patch.object(Path, 'open', side_effect=PermissionError('synthetic private diagnostic'))
                  if os.name == 'nt' else
                  patch('gitlab_agent.static_service_files.os.read',
                        side_effect=PermissionError('synthetic private diagnostic')))
        with target:
            self.assertEqual(self.inventory()['registration']['status'], 'unreadable')

    def test_binary_plist_supported(self):
        self.write_plist()
        self.plist.write_bytes(plistlib.dumps(self.data, fmt=plistlib.FMT_BINARY))
        self.assertEqual(self.inventory()['registration']['status'], 'discovered_legacy')

    def test_definition_evidence_keeps_known_tunnel_as_separate_role(self):
        self.write_plist()
        tunnel = self.plist.parent / (TUNNEL_LABEL + '.plist')
        tunnel.write_bytes(plistlib.dumps({
            'Label': TUNNEL_LABEL,
            'ProgramArguments': ['/arbitrary/secret-looking-command', '--token', 'hidden'],
            'EnvironmentVariables': {'API_KEY': 'hidden-value'},
        }))
        tunnel.chmod(0o600)
        result = self.inventory()
        roles = {item['role']: item for item in result['service_definitions']}
        self.assertEqual(roles['bridge']['status'], 'discovered_legacy')
        self.assertEqual(roles['tunnel']['status'], 'discovered_legacy')
        self.assertEqual(roles['tunnel']['runtime_owner'], 'unobserved')
        self.assertEqual(roles['bridge']['definition_file_owner'],
                         'verified_static_metadata_and_label_not_runtime')
        self.assertNotIn('hidden', repr(result))
        self.assertNotIn('arbitrary', repr(result))

    def test_tunnel_only_definition_remains_legacy_evidence(self):
        tunnel = self.plist.parent / (TUNNEL_LABEL + '.plist')
        tunnel.parent.mkdir(parents=True, exist_ok=True)
        tunnel.write_bytes(plistlib.dumps({'Label': TUNNEL_LABEL,
                                           'ProgramArguments': ['/redacted']}))
        tunnel.chmod(0o600)
        result = self.inventory()
        self.assertEqual(result['registration']['status'], 'not_found')
        self.assertEqual(result['service_definitions'][0]['status'], 'not_found')
        self.assertEqual(result['service_definitions'][1]['role'], 'tunnel')
        self.assertEqual(result['service_definitions'][1]['status'], 'discovered_legacy')
        self.assertTrue(result['legacy_evidence'])

    def test_duplicate_xml_keys_are_invalid_and_redacted(self):
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        raw = (b'<?xml version="1.0"?><plist version="1.0"><dict>'
               b'<key>Label</key><string>' + LABEL.encode() + b'</string>'
               b'<key>Label</key><string>private-value</string></dict></plist>')
        self.plist.write_bytes(raw)
        self.plist.chmod(0o600)
        result = self.inventory()
        self.assertEqual(result['registration']['status'], 'invalid')
        self.assertNotIn('private-value', repr(result))

    def test_duplicate_binary_keys_are_invalid(self):
        raw = bytearray(plistlib.dumps({'Label': LABEL, 'Other': 'private-value'},
                                       fmt=plistlib.FMT_BINARY, sort_keys=False))
        trailer = raw[-32:]
        offset_size, ref_size = trailer[6], trailer[7]
        object_count = int.from_bytes(trailer[8:16], 'big')
        top = int.from_bytes(trailer[16:24], 'big')
        offset_table = int.from_bytes(trailer[24:32], 'big')
        offsets = [int.from_bytes(raw[offset_table + i * offset_size:
                                       offset_table + (i + 1) * offset_size], 'big')
                   for i in range(object_count)]
        dict_start = offsets[top]
        self.assertEqual(raw[dict_start] >> 4, 13)
        ref_start = dict_start + 1
        first_key = raw[ref_start:ref_start + ref_size]
        raw[ref_start + ref_size:ref_start + 2 * ref_size] = first_key
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        self.plist.write_bytes(raw)
        self.plist.chmod(0o600)
        self.assertEqual(self.inventory()['registration']['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
