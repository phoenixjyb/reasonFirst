from __future__ import annotations

import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import __version__
from gitlab_agent.deployment_inventory import build_deployment_inventory, LABEL, MAX_PLIST_BYTES
from gitlab_agent.setup_state import SetupState


class DeploymentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
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

    def inventory(self, system='Darwin', state=None):
        with patch('subprocess.run', side_effect=AssertionError('No process calls')), \
             patch('socket.socket', side_effect=AssertionError('No socket calls')):
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
                self.assertFalse(result['legacy_evidence'])

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

    def test_unreadable_registration_is_not_absence(self):
        self.write_plist()
        with patch.object(Path, 'open', side_effect=PermissionError):
            self.assertEqual(self.inventory()['registration']['status'], 'unreadable')

    def test_binary_plist_supported(self):
        self.write_plist()
        self.plist.write_bytes(plistlib.dumps(self.data, fmt=plistlib.FMT_BINARY))
        self.assertEqual(self.inventory()['registration']['status'], 'discovered_legacy')


if __name__ == '__main__':
    unittest.main()
