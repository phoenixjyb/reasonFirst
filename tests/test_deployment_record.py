from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import deployment as d


@unittest.skipUnless(os.name == 'posix', 'Directory-fd ownership tests require POSIX; not Windows ACL coverage')
class DeploymentRecordTests(unittest.TestCase):
    """Real disposable files; Darwin registration fixtures on both POSIX runners.

    No launchd call or installed user deployment is used. Native macOS runs these
    storage tests too, but they are not service startup/recovery acceptance.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.plist = self.home / 'Library/LaunchAgents' / (d.LABEL + '.plist')
        self.root = self.home / '.local/share/reasonfirst/v4-service'
        self.secret = 'fixture-' + 'not-for-output'
        self.data = {
            'Label': d.LABEL,
            'ProgramArguments': [str(self.root / 'tools/codex_web_bridge/run_reasonfirst.sh')],
            'WorkingDirectory': str(self.root),
            'EnvironmentVariables': {'TOKEN': self.secret},
            'KeepAlive': True,
        }
        self.record = self.home / '.config/reasonfirst/deployments' / (d.LABEL + '.json')
        self.write_plist()
        self.blockers = ExitStack()
        self.addCleanup(self.blockers.close)
        self.blockers.enter_context(patch('subprocess.run', side_effect=AssertionError('No command execution')))
        self.blockers.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No command execution')))
        self.blockers.enter_context(patch('socket.socket', side_effect=AssertionError('No network')))

    def write_plist(self, **changes):
        self.plist.parent.mkdir(parents=True, exist_ok=True)
        self.plist.write_bytes(plistlib.dumps({**self.data, **changes}))
        self.plist.chmod(0o600)

    def plan(self):
        result = d.plan_adoption(system_name='Darwin', home=self.home)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertFalse(result['activation_authorized'])
        return result

    def adopt(self, digest=None, approved=True):
        if digest is None:
            digest = self.plan()['plan_digest']
        return d.adopt_deployment(system_name='Darwin', home=self.home,
                                  expect_digest=digest, approved=approved)

    def status(self):
        return d.deployment_status(system_name='Darwin', home=self.home)

    def assert_failure(self, code, callback):
        with self.assertRaises(d.DeploymentError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(self.secret, str(caught.exception))

    def test_plan_is_read_only_and_hash_binds_all_registration_bytes(self):
        before = sorted(str(x.relative_to(self.home)) for x in self.home.rglob('*'))
        raw = self.plist.read_bytes()
        result = self.plan()
        self.assertEqual(result['snapshot']['registration_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['snapshot']['layout'], 'legacy_staged_http')
        self.assertEqual(result['record_status'], 'not_recorded')
        self.assertEqual(result['runtime_identity'], 'not_inspected')
        self.assertEqual(result['configuration_bindings'], 'not_inspected')
        self.assertEqual(before, sorted(str(x.relative_to(self.home)) for x in self.home.rglob('*')))
        self.assertEqual(raw, self.plist.read_bytes())

    def test_absent_record_status_creates_no_directories_or_inspects_service(self):
        with patch.object(d, '_snapshot', side_effect=AssertionError('No need to read a service')):
            result = self.status()
        self.assertEqual(result['record_status'], 'not_recorded')
        self.assertFalse((self.home / '.config').exists())

    def test_explicit_record_round_trip_preserves_configuration_and_does_not_execute_script(self):
        config = self.home / '.config/gitlab-agent/.env'
        config.parent.mkdir(parents=True)
        config.write_text(self.secret)
        setup = self.home / '.config/reasonfirst/setup.yaml'
        setup.parent.mkdir(parents=True)
        setup.write_text('intentionally not valid setup content')
        before = self.plist.read_bytes()
        result = self.adopt()
        self.assertTrue(result['ok'])
        self.assertTrue(result['record_written'])
        self.assertFalse(result['runtime_installed'])
        self.assertFalse(result['service_changed'])
        self.assertFalse(result['setup_state_changed'])
        self.assertEqual(config.read_text(), self.secret)
        self.assertEqual(setup.read_text(), 'intentionally not valid setup content')
        self.assertEqual(self.plist.read_bytes(), before)
        self.assertFalse(self.root.exists())  # referenced script must not be read or invoked
        status = self.status()
        self.assertEqual(status['registration_comparison'], 'matches_record')
        self.assertFalse(status['ready_for_activation'])
        self.assertIsNone(status['record']['runtime_id'])
        self.assertIsNone(status['record']['running_version'])
        self.assertEqual(status['record']['health'], 'not_inspected')
        self.assertNotIn(self.secret, json.dumps(status))
        self.assertEqual(stat.S_IMODE(self.record.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.record.parent.stat().st_mode), 0o700)
        self.assertEqual(self.record.stat().st_nlink, 1)
        self.assertEqual([x.name for x in self.record.parent.iterdir()], [self.record.name])

    def test_recording_does_not_read_application_configs(self):
        native_open = os.open
        native_path_open = Path.open
        def guarded_os_open(path, *args, **kwargs):
            self.assertNotIn(str(path).split('/')[-1], ('.env', 'bridge.yaml', 'setup.yaml'))
            return native_open(path, *args, **kwargs)
        def guarded_path_open(path, *args, **kwargs):
            self.assertEqual(path, self.plist)
            return native_path_open(path, *args, **kwargs)
        with patch.object(os, 'open', side_effect=guarded_os_open), patch.object(Path, 'open', guarded_path_open):
            self.assertTrue(self.adopt()['ok'])

    def test_repeat_is_noop_preserving_exact_record_bytes_and_timestamp(self):
        self.adopt()
        before = self.record.read_bytes(), self.record.stat().st_mtime_ns
        result = self.adopt()
        self.assertTrue(result['already_recorded'])
        self.assertFalse(result['record_written'])
        self.assertEqual(before, (self.record.read_bytes(), self.record.stat().st_mtime_ns))
        self.assertEqual(self.plan()['proposed_action'], 'none')

    def test_approval_and_exact_digest_required_before_writing(self):
        digest = self.plan()['plan_digest']
        for value, approved, code in ((digest, False, 'approval_required'),
                                      (digest, 1, 'approval_required'),
                                      (self.secret, True, 'approval_required'),
                                      (None, True, 'approval_required'),
                                      ('0'*64, True, 'changed_registration')):
            with self.subTest(value=value, approved=approved):
                self.assert_failure(code, lambda: self.adopt(value, approved) if value is not None else
                    d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=None, approved=True))
                self.assertFalse((self.home / '.config').exists())

    def test_environment_or_unrecognized_plist_change_invalidates_approval(self):
        digest = self.plan()['plan_digest']
        self.write_plist(EnvironmentVariables={'TOKEN': self.secret + '-changed'})
        self.assert_failure('changed_registration', lambda: self.adopt(digest))
        self.assertFalse(self.record.exists())
        self.assertNotEqual(digest, self.plan()['plan_digest'])

    def test_source_change_during_snapshot_is_detected(self):
        original = d._launch_agent
        def change(home):
            value = original(home)
            self.write_plist(KeepAlive=False)
            return value
        with patch.object(d, '_launch_agent', side_effect=change):
            self.assert_failure('changed_registration', self.plan)

    def test_sidecar_is_recorded_without_claiming_its_path_version_is_live(self):
        program = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-test/run_reasonfirst.sh'
        self.write_plist(ProgramArguments=[str(program)])
        self.adopt()
        result = self.status()
        self.assertEqual(result['record']['snapshot']['layout'], 'versioned_http_sidecar')
        self.assertIsNone(result['record']['running_version'])
        self.assertFalse(program.exists())

    def test_paths_with_spaces_and_unicode(self):
        self.home = self.home / 'operator 空间'
        self.home.mkdir(mode=0o700)
        self.plist = self.home / 'Library/LaunchAgents' / (d.LABEL + '.plist')
        self.root = self.home / '.local/share/reasonfirst/v4-service'
        self.record = self.home / '.config/reasonfirst/deployments' / (d.LABEL + '.json')
        self.data.update(ProgramArguments=[str(self.root / 'tools/codex_web_bridge/run_reasonfirst.sh')],
                         WorkingDirectory=str(self.root))
        self.write_plist()
        self.assertTrue(self.adopt()['ok'])
        self.assertEqual(self.status()['registration_comparison'], 'matches_record')

    def test_unsupported_layouts_have_no_write_path(self):
        for change in ({'Label': 'unrelated.tunnel'}, {'ProgramArguments': ['/bin/sh', '-c', self.secret]},
                       {'ProgramArguments': [str(self.home / self.secret)]},
                       {'Program': '/bin/sh'}, {'WorkingDirectory': str(self.home / 'unknown')}):
            with self.subTest(change=change):
                self.write_plist(**change)
                self.assert_failure('unsupported_layout', self.plan)
                self.assertFalse(self.record.exists())

    def test_missing_invalid_or_oversized_registration_is_not_adopted(self):
        self.plist.unlink()
        self.assert_failure('unsupported_layout', self.plan)
        for data in (self.secret.encode(), b'x' * (d.MAX_PLIST_BYTES + 1)):
            self.plist.write_bytes(data)
            self.plist.chmod(0o600)
            self.assert_failure('unsupported_layout', self.plan)

    def test_group_or_other_writable_registration_is_rejected(self):
        for mode in (0o620, 0o602, 0o666):
            with self.subTest(mode=mode):
                self.plist.chmod(mode)
                self.assert_failure('unsafe_path', self.plan)

    def test_foreign_file_owner_is_rejected(self):
        with patch.object(d.os, 'getuid', return_value=os.getuid() + 1):
            self.assert_failure('unsafe_path', self.plan)

    def test_registration_symlink_fifo_and_directory_never_block_or_get_followed(self):
        self.plist.unlink()
        target = self.home / 'private'
        target.write_text(self.secret)
        self.plist.symlink_to(target)
        self.assert_failure('unreadable', self.plan)
        self.plist.unlink()
        os.mkfifo(self.plist, 0o600)
        self.assert_failure('unsafe_path', self.plan)
        self.plist.unlink()
        self.plist.mkdir()
        self.assert_failure('unsafe_path', self.plan)
        self.assertEqual(target.read_text(), self.secret)

    def test_hardlinked_registration_is_rejected(self):
        os.link(self.plist, self.home / 'alias')
        self.assert_failure('unsafe_path', self.plan)

    def test_symlinked_registration_parent_is_rejected(self):
        parent = self.plist.parent
        target = self.home / 'elsewhere'
        parent.rename(target)
        parent.symlink_to(target, target_is_directory=True)
        self.assert_failure('unsafe_path', self.plan)

    def test_symlinked_home_is_not_silently_resolved(self):
        alias = self.home / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        self.assert_failure('unsafe_path', lambda: d.plan_adoption(system_name='Darwin', home=alias))

    def test_symlinked_store_parent_is_not_followed(self):
        other = self.home / 'elsewhere'
        other.mkdir(mode=0o700)
        (self.home / '.config').symlink_to(other, target_is_directory=True)
        self.assert_failure('unsafe_path', self.plan)
        self.assertEqual(list(other.iterdir()), [])

    def test_nonprivate_store_is_not_chmodded_or_adopted(self):
        self.record.parent.mkdir(parents=True, mode=0o755)
        self.assert_failure('unsafe_path', self.plan)
        self.assertEqual(stat.S_IMODE(self.record.parent.stat().st_mode), 0o755)

    def test_record_symlink_is_not_read_or_replaced(self):
        self.record.parent.mkdir(parents=True, mode=0o700)
        target = self.home / 'private'
        target.write_text(self.secret)
        self.record.symlink_to(target)
        self.assert_failure('unreadable', self.plan)
        self.assert_failure('unreadable', self.status)
        self.assertEqual(target.read_text(), self.secret)
        self.assertTrue(self.record.is_symlink())

    def test_drift_does_not_overwrite_record_or_claim_unhealthy(self):
        self.adopt()
        before = self.record.read_bytes()
        self.write_plist(KeepAlive=False)
        self.assertEqual(self.status()['registration_comparison'], 'drifted')
        self.assertEqual(self.status()['record']['health'], 'not_inspected')
        self.assert_failure('existing_record_conflict', self.plan)
        self.assert_failure('existing_record_conflict', lambda: self.adopt('0'*64))
        self.assertEqual(self.record.read_bytes(), before)

    def test_missing_registration_retains_record_and_reports_unavailable(self):
        self.adopt()
        self.plist.unlink()
        self.assertEqual(self.status()['registration_comparison'], 'unavailable_or_unsupported')
        self.assertTrue(self.record.exists())

    def test_untrusted_record_shapes_are_rejected_without_echo_or_overwrite(self):
        self.adopt()
        original = json.loads(self.record.read_bytes())
        mutations = ({'schema_version': True}, {'schema_version': 2}, {'extra': self.secret},
                     {'health': 'healthy'}, {'activation_authorized': True}, {'running_version': '0.5.1'},
                     {'runtime_id': 'guessed'}, {'scope': 'different'}, {'plan_digest': '0'*64},
                     {'recorded_at': self.secret}, {'recorded_by_cli_version': self.secret})
        for change in mutations:
            with self.subTest(change=change):
                self.record.write_bytes(d._canonical({**original, **change}))
                before = self.record.read_bytes()
                self.assert_failure('invalid_record', self.status)
                self.assert_failure('invalid_record', self.plan)
                self.assertEqual(self.record.read_bytes(), before)

    def test_duplicate_json_keys_and_oversize_record_are_rejected(self):
        self.adopt()
        original = self.record.read_bytes()
        for value in (b'{"schema_version":1,' + original[1:], b'x' * (d.MAX_RECORD_BYTES + 1), b'[]', b'\xff'):
            with self.subTest(size=len(value)):
                self.record.write_bytes(value)
                self.assert_failure('invalid_record', self.status)

    def test_record_copied_from_another_home_is_rejected(self):
        self.adopt()
        newhome = self.home / 'otherhome'
        record = newhome / '.config/reasonfirst/deployments' / (d.LABEL + '.json')
        record.parent.mkdir(mode=0o700, parents=True)
        record.write_bytes(self.record.read_bytes())
        record.chmod(0o600)
        self.assert_failure('invalid_record', lambda: d.deployment_status(system_name='Darwin', home=newhome))

    def test_forged_record_paths_are_never_opened_even_with_recomputed_digest(self):
        self.adopt()
        value = json.loads(self.record.read_bytes())
        value['snapshot']['program'] = str(self.home / self.secret)
        value['plan_digest'] = d._digest(value['snapshot'])
        self.record.write_bytes(d._canonical(value))
        self.assert_failure('invalid_record', self.status)

    def test_hardlinked_record_or_broad_permissions_are_not_trusted(self):
        self.adopt()
        self.record.chmod(0o644)
        self.assert_failure('unsafe_path', self.status)
        self.record.chmod(0o600)
        alias = self.home / 'alias'
        os.link(self.record, alias)
        self.assert_failure('unsafe_path', self.status)

    def test_source_changes_before_commit_no_record_published_and_temp_removed(self):
        plan = self.plan()
        native_snapshot = d._snapshot
        calls = 0
        def changing(home):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.write_plist(KeepAlive=False)
            return native_snapshot(home)
        with patch.object(d, '_snapshot', side_effect=changing):
            self.assert_failure('changed_registration', lambda: self.adopt(plan['plan_digest']))
        self.assertFalse(self.record.exists())
        self.assertEqual(list(self.record.parent.iterdir()), [])

    def test_source_changes_after_commit_report_written_but_not_accepted(self):
        plan = self.plan()
        native_snapshot = d._snapshot
        calls = 0
        def changing(home):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.plist.unlink()
            return native_snapshot(home)
        with patch.object(d, '_snapshot', side_effect=changing):
            result = self.adopt(plan['plan_digest'])
        self.assertFalse(result['ok'])
        self.assertTrue(result['record_written'])
        self.assertTrue(self.record.exists())
        self.assertFalse(result['service_changed'])

    def test_concurrent_record_is_not_replaced(self):
        plan = self.plan()
        native_link = os.link
        def racing(*args, **kwargs):
            self.record.write_text('concurrent-winner')
            self.record.chmod(0o600)
            return native_link(*args, **kwargs)
        with patch.object(d.os, 'link', side_effect=racing):
            self.assert_failure('existing_record_conflict', lambda: self.adopt(plan['plan_digest']))
        self.assertEqual(self.record.read_text(), 'concurrent-winner')
        self.assertEqual(len(list(self.record.parent.iterdir())), 1)

    def test_precommit_fsync_failure_leaves_no_canonical_record(self):
        plan = self.plan()
        with patch.object(d.os, 'fsync', side_effect=OSError(self.secret)):
            self.assert_failure('write_failed', lambda: self.adopt(plan['plan_digest']))
        self.assertFalse(self.record.exists())
        self.assertEqual(list(self.record.parent.iterdir()), [])

    def test_directory_fsync_failure_preserves_committed_record_and_reports_uncertainty(self):
        plan = self.plan()
        native_fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(self.secret)
            return native_fsync(fd)
        with patch.object(d, '_home', return_value=self.home), \
             patch.object(d.platform, 'system', return_value='Darwin'), \
             patch.object(d.os, 'fsync', side_effect=fail_directory):
            result = d.run_command('adopt', expect_digest=plan['plan_digest'], approved=True)
        self.assertFalse(result['ok'])
        self.assertIsNone(result['record_written'])
        self.assertTrue(result['inspect_status_before_retry'])
        self.assertTrue(self.record.exists())
        self.assertEqual(self.status()['registration_comparison'], 'matches_record')
        self.assertNotIn(self.secret, json.dumps(result))

    def test_failed_exclusive_temp_creation_does_not_unlink_existing_file(self):
        plan = self.plan()
        self.record.parent.mkdir(parents=True, mode=0o700)
        pending = self.record.parent / '.pending-fixed'
        pending.write_text('existing-data')
        with patch.object(d.uuid, 'uuid4') as new_id:
            new_id.return_value.hex = 'fixed'
            self.assert_failure('write_failed', lambda: self.adopt(plan['plan_digest']))
        self.assertEqual(pending.read_text(), 'existing-data')
        self.assertFalse(self.record.exists())

    def test_binary_plist_supported(self):
        self.plist.write_bytes(plistlib.dumps(self.data, fmt=plistlib.FMT_BINARY))
        self.assertTrue(self.adopt()['ok'])


class DeploymentBoundaryTests(unittest.TestCase):
    def test_unsupported_platforms_fail_before_filesystem_read_or_write(self):
        for system in ('Linux', 'Windows', 'Other'):
            with self.subTest(system=system), patch.object(d.os, 'open', side_effect=AssertionError('No filesystem')):
                for operation in (lambda: d.plan_adoption(system_name=system),
                                  lambda: d.deployment_status(system_name=system),
                                  lambda: d.adopt_deployment(system_name=system, expect_digest='0'*64, approved=True)):
                    with self.assertRaises(d.DeploymentError) as error:
                        operation()
                    self.assertEqual(error.exception.code, 'unsupported_platform')

    def test_cli_boundary_does_not_echo_unexpected_private_exception(self):
        with patch.object(d, 'plan_adoption', side_effect=RuntimeError('private-' + 'value')):
            result = d.run_command('plan')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'inspection_failed')
        self.assertNotIn('private-value', json.dumps(result))
        self.assertFalse(result['record_written'])

    def test_cli_boundary_retains_classified_errors(self):
        with patch.object(d.platform, 'system', return_value='Linux'):
            result = d.run_command('adopt', expect_digest='0'*64, approved=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'unsupported_platform')
        self.assertFalse(result['service_changed'])
        self.assertFalse(result['runtime_installed'])


if __name__ == '__main__':
    unittest.main()
