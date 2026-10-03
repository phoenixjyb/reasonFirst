from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import sys
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import deployment as d, pairing as p, runtime as r
import test_runtime_preparation as fixture


class PortablePairingTests(unittest.TestCase):
    def test_unsupported_host_stops_before_home_storage_or_execution(self):
        for system in ('Linux', 'Windows', 'Other'):
            with self.subTest(system=system), patch.object(p.platform, 'system', return_value=system), \
                    patch.object(d, '_home', side_effect=AssertionError), \
                    patch.object(r, 'status', side_effect=AssertionError), \
                    patch('subprocess.run', side_effect=AssertionError):
                for action in ('deployment-plan', 'deployment-check'):
                    out = p.run_command(action, runtime_id='a'*64, expect_digest='b'*64)
                    self.assertEqual(out['error_code'], 'unsupported_pairing_platform')
                    self.assertFalse(out['ok'])
                    self.assertFalse(out['ready_for_activation'])
                    self.assertNotIn('plan_digest', out)

    def test_invalid_identifiers_stop_before_home(self):
        with patch.object(p, '_supported', return_value=('Darwin', 'test')), \
                patch.object(d, '_home', side_effect=AssertionError):
            for value in ('../escape', 'A'*64, 'a'*63, None, True):
                self.assertEqual(p.run_command('deployment-plan', runtime_id=value)['error_code'], 'invalid_runtime_id')
                self.assertEqual(p.run_command('deployment-check', runtime_id='a'*64, expect_digest=value)['error_code'], 'invalid_pairing_digest')

    def test_error_never_echoes_unexpected_exception_or_partial_digest(self):
        with patch.object(p, 'plan', side_effect=ValueError('private-value')):
            out = p.run_command('deployment-plan', runtime_id='a'*64)
        self.assertEqual(out['error_code'], 'inspection_failed')
        self.assertNotIn('private-value', str(out))
        self.assertFalse(out['pairing_verified'])
        self.assertNotIn('identity', out)
        self.assertNotIn('plan_digest', out)

    def test_cli_dispatch_and_nonzero_error_are_single_json(self):
        for action in ('deployment-plan', 'deployment-check'):
            args = [action, '--runtime-id', 'a'*64, '--json']
            if action == 'deployment-check':
                args += ['--expect-digest', 'b'*64]
            stdout = io.StringIO()
            with patch.object(p, '_supported', side_effect=p.PairingError('unsupported_pairing_platform')), redirect_stdout(stdout):
                self.assertEqual(r.main(args), 1)
            self.assertEqual(json.loads(stdout.getvalue())['error_code'], 'unsupported_pairing_platform')

    def test_cli_does_not_accept_activation_approval_or_echo_bad_arguments(self):
        for extra in (['--yes'], ['--force'], ['--serve'], ['--secret', 'private-value']):
            stderr = io.StringIO()
            with redirect_stderr(stderr), patch.object(p, 'plan', side_effect=AssertionError), self.assertRaises(SystemExit):
                r.main(['deployment-plan', '--runtime-id', 'a'*64] + extra)
            self.assertNotIn('private-value', stderr.getvalue())

    def test_cli_check_requires_digest(self):
        with redirect_stderr(io.StringIO()), patch.object(p, 'check', side_effect=AssertionError), self.assertRaises(SystemExit):
            r.main(['deployment-check', '--runtime-id', 'a'*64])

    def test_help_without_configuration_or_controller(self):
        with patch.object(d, '_home', side_effect=AssertionError), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
            r.main(['deployment-plan', '--help'])
        self.assertEqual(result.exception.code, 0)

    def test_bilingual_guides_preserve_support_and_nonactivation_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('DEPLOYMENT_PAIRING.md', 'DEPLOYMENT_PAIRING_CN.md'):
            text = (root / 'docs' / name).read_text(encoding='utf-8')
            for token in ('reasonfirst-runtime deployment-plan', 'reasonfirst-runtime deployment-check',
                          '--expect-digest', 'v0.5.1', 'macOS', 'Linux', 'Windows', '/control',
                          'compatibility_verified', 'ready_for_activation', 'not a signature'):
                self.assertIn(token, text)


@unittest.skipUnless(os.name == 'posix', 'Real POSIX fixtures; not Windows ACL or launchd acceptance')
class PairingStorageTests(unittest.TestCase):
    def setUp(self):
        # Preparation execution is mocked, but the records, tree and validation
        # below are real. Native installed-package evidence comes from CI.
        self.case = fixture.RuntimeStorageTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.home = self.case.home
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(p.platform, 'system', return_value='Darwin'))
        plan = self.case.plan()
        self.runtime_id = plan['runtime_id']
        root = Path(plan['runtime_path'])
        self.venv = root / 'venv'
        self.manifest = root / 'runtime.json'
        python = dict(executable=sys._base_executable, implementation='cpython',
                      version=list(sys.version_info[:3]), fingerprint=plan['input']['python']['fingerprint'])
        observation = dict(packages={r.PACKAGE:'0.5.1'}, version='0.5.1', python_version=python['version'],
                           prefix=str(self.venv), origins=['lib/test'], mcp_version='synthetic',
                           http_signature_accepted=True, controller_instantiated=False, server_started=False)
        def run(argv, **kwargs):
            if 'venv' in argv:
                (self.venv/'bin').mkdir(parents=True)
                (self.venv/'bin/python').symlink_to(Path(sys._base_executable).resolve())
            return json.dumps(observation).encode() if '-c' in argv else b''
        with patch.object(r, 'probe_python', return_value=python), patch.object(r, '_run', side_effect=run):
            result = r.prepare(expect_digest=plan['plan_digest'], approved=True, **self.case.kwargs)
        self.assertTrue(result['prepared'], result)
        self.plist = self.home / 'Library/LaunchAgents' / (d.LABEL + '.plist')
        self.plist.parent.mkdir(parents=True)
        service = self.home / '.local/share/reasonfirst/v4-service'
        self.program = service / 'tools/codex_web_bridge/run_reasonfirst.sh'
        self.data = {'Label':d.LABEL, 'ProgramArguments':[str(self.program)],
                     'WorkingDirectory':str(service), 'EnvironmentVariables':{'TOKEN':'private-fixture-value'}}
        self.plist.write_bytes(plistlib.dumps(self.data)); self.plist.chmod(0o600)
        adoption = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home,
                           expect_digest=adoption['plan_digest'], approved=True)
        self.record = self.home.joinpath(*d._PARTS, d._NAME)
        self.stack.enter_context(patch('subprocess.run', side_effect=AssertionError('No subprocess')))
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No subprocess')))
        self.stack.enter_context(patch('socket.socket', side_effect=AssertionError('No network')))
        self.stack.enter_context(patch.object(r, 'probe_python', side_effect=AssertionError('No interpreter probe')))

    def call(self, action='deployment-plan', **kwargs):
        return p.run_command(action, runtime_id=self.runtime_id, home=self.home, **kwargs)

    def snapshot(self):
        rows = []
        for path in sorted(self.home.rglob('*')):
            st = path.lstat()
            value = ('link', os.readlink(path)) if path.is_symlink() else ('dir', '') if path.is_dir() else ('file', hashlib.sha256(path.read_bytes()).hexdigest())
            rows.append((str(path.relative_to(self.home)), st.st_mode, st.st_ino, st.st_nlink, st.st_mtime_ns, st.st_ctime_ns, value))
        return rows

    def assert_failure(self, code=None):
        before = self.snapshot()
        result = self.call()
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['pairing_verified'])
        self.assertFalse(result['ready_for_activation'])
        self.assertNotIn('plan_digest', result)
        self.assertNotIn('private-fixture-value', str(result))
        if code:
            self.assertEqual(result['error_code'], code)
        self.assertEqual(before, self.snapshot())
        return result

    def test_success_binds_actual_records_without_writes_or_commands(self):
        before = self.snapshot()
        result = self.call()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['pairing_verified'])
        identity = result['identity']
        self.assertEqual(result['plan_digest'], r.digest(identity))
        self.assertEqual(identity['runtime']['record_sha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(identity['deployment']['record_sha256'], hashlib.sha256(self.record.read_bytes()).hexdigest())
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('private-fixture-value', str(result))

    def test_success_does_not_establish_control_mode_configuration_or_health(self):
        result = self.call()
        self.assertTrue(result['ok'], result)
        for key in ('compatibility_verified', 'activation_authorized', 'ready_for_activation', 'commands_executed', 'live_service_verified', 'mutating'):
            self.assertIs(result[key], False)
        self.assertEqual(result['legacy_control_requirement'], 'unknown')
        self.assertEqual(result['compatibility'], 'not_established')
        self.assertEqual(result['proposed_actions'], [])
        self.assertEqual(result['unresolved_requirements'], list(p.UNRESOLVED))
        self.assertIn('legacy_control_requirement', result['unresolved_requirements'])

    def test_success_does_not_read_launcher_application_or_setup_files(self):
        real_open = os.open
        forbidden = {'.env', 'bridge.yaml', 'setup.yaml', 'run_reasonfirst.sh'}
        for path in (self.program, self.home/'.config/gitlab-agent/.env', self.home/'.config/reasonfirst/bridge.yaml', self.home/'.config/reasonfirst/setup.yaml'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private-fixture-value')
        real_path_open = Path.open
        def guarded_open(file, *args, **kwargs):
            if Path(file).name in forbidden:
                raise AssertionError('Forbidden file read')
            return real_open(file, *args, **kwargs)
        def guarded_path_open(path, *args, **kwargs):
            if path.name in forbidden:
                raise AssertionError('Forbidden file read')
            return real_path_open(path, *args, **kwargs)
        with patch('os.open', side_effect=guarded_open), patch.object(Path, 'open', guarded_path_open):
            self.assertTrue(self.call()['ok'])

    def test_repeated_plan_and_check_are_stable(self):
        first = self.call()
        self.assertEqual(first, self.call())
        before = self.snapshot()
        checked = self.call('deployment-check', expect_digest=first['plan_digest'])
        self.assertTrue(checked['ok'], checked)
        self.assertTrue(checked['review_digest_matches'])
        self.assertEqual(before, self.snapshot())

    def test_runtime_or_adoption_digest_is_not_a_pairing_digest(self):
        result = self.call()
        for digest in (self.runtime_id, result['identity']['deployment']['adoption_digest']):
            self.assertEqual(self.call('deployment-check', expect_digest=digest)['error_code'], 'pairing_changed')

    def test_missing_registration_never_prepares_or_adopts(self):
        self.record.unlink()
        with patch.object(r, 'status', side_effect=AssertionError), patch.object(d, 'adopt_deployment', side_effect=AssertionError):
            self.assert_failure('deployment_not_recorded')

    def test_hidden_plist_field_change_is_rejected(self):
        self.data['EnvironmentVariables']['TOKEN'] = 'changed-private-fixture-value'
        self.plist.write_bytes(plistlib.dumps(self.data))
        self.assert_failure('registration_drifted')

    def test_missing_current_plist_is_not_success_or_an_empty_deployment(self):
        self.plist.unlink()
        self.assert_failure('deployment_or_storage_inspection_failed')

    def test_missing_runtime_manifest_is_not_ready(self):
        self.manifest.unlink()
        self.assert_failure('runtime_manifest_missing')

    def test_runtime_file_drift_blocks_pairing(self):
        (self.venv/'new-file').write_text('drift')
        self.assert_failure('runtime_not_prepared')

    def test_runtime_lock_still_has_strict_permission_checks(self):
        (self.venv/'.lock').chmod(0o666)
        self.assert_failure('runtime_not_prepared')

    def test_interpreter_drift_blocks_pairing_without_reprobe(self):
        with patch.object(r, 'executable_fingerprint', return_value={'changed':True}):
            self.assert_failure('runtime_not_prepared')

    def test_raw_registry_bytes_are_bound_even_when_semantics_match(self):
        initial = self.call()['plan_digest']
        self.record.write_bytes(self.record.read_bytes()+b'\n')
        self.assertTrue(self.call()['ok'])
        self.assertEqual(self.call('deployment-check', expect_digest=initial)['error_code'], 'pairing_changed')

    def test_raw_runtime_bytes_are_bound_even_when_semantics_match(self):
        initial = self.call()['plan_digest']
        self.manifest.write_bytes(self.manifest.read_bytes()+b'\n')
        self.assertTrue(self.call()['ok'])
        self.assertEqual(self.call('deployment-check', expect_digest=initial)['error_code'], 'pairing_changed')

    def test_different_host_rejected_before_runtime_tree_read(self):
        record = json.loads(self.manifest.read_bytes())
        for field in ('platform', 'machine'):
            changed = json.loads(json.dumps(record))
            changed['input'][field] = 'wrong'
            self.manifest.write_bytes(r.canonical(changed))
            with patch.object(r, 'status', side_effect=AssertionError):
                self.assert_failure('runtime_host_mismatch')

    def test_duplicate_and_invalid_metadata_are_refused(self):
        for raw in (b'{"input":{},"input":{}}', b'[]', b'not json'):
            self.manifest.write_bytes(raw)
            self.assert_failure()

    def test_metadata_digest_tampering_is_refused(self):
        record = json.loads(self.manifest.read_bytes())
        record['prepared_at'] = 'private-fixture-value'
        self.manifest.write_bytes(r.canonical(record))
        self.assert_failure('runtime_inspection_failed')

    def test_record_symlinks_and_hardlinks_are_not_followed_or_repaired(self):
        for path in (self.record, self.manifest):
            original = path.read_bytes()
            outside = self.case.root / ('outside-' + path.name)
            outside.write_bytes(original); outside.chmod(0o600)
            path.unlink(); path.symlink_to(outside)
            self.assert_failure('deployment_or_storage_inspection_failed')
            path.unlink(); os.link(outside, path)
            self.assert_failure('deployment_or_storage_inspection_failed')
            path.unlink(); outside.unlink()
            path.write_bytes(original); path.chmod(0o600)

    def test_forged_registration_path_not_opened_even_with_recomputed_hash(self):
        record = json.loads(self.record.read_bytes())
        record['snapshot']['registration_path'] = str(self.case.root/'private-fixture-value')
        record['plan_digest'] = d._digest(record['snapshot'])
        self.record.write_bytes(d._canonical(record))
        with patch.object(d, '_snapshot', side_effect=AssertionError):
            self.assert_failure('deployment_or_storage_inspection_failed')

    def test_change_between_runtime_status_and_second_read_is_refused(self):
        original = r.status
        def status(**kwargs):
            result = original(**kwargs)
            self.manifest.write_bytes(self.manifest.read_bytes()+b'\n')
            return result
        with patch.object(r, 'status', side_effect=status):
            out = self.call()
        self.assertFalse(out['ok'])
        self.assertEqual(out['error_code'], 'changed_during_inspection')
        self.assertNotIn('plan_digest', out)

    def test_change_between_pair_observations_is_refused(self):
        original = p._runtime
        calls = 0
        def runtime(*args):
            nonlocal calls
            result = original(*args)
            calls += 1
            if calls == 1:
                self.record.write_bytes(self.record.read_bytes()+b'\n')
            return result
        with patch.object(p, '_runtime', side_effect=runtime):
            out = self.call()
        self.assertEqual(out['error_code'], 'changed_during_inspection')
        self.assertNotIn('identity', out)

    def test_copy_to_another_home_does_not_establish_pairing(self):
        import shutil
        other = self.case.root/'other-home'
        shutil.copytree(self.home, other, symlinks=True)
        out = p.run_command('deployment-plan', runtime_id=self.runtime_id, home=other)
        self.assertFalse(out['ok'])
        self.assertEqual(out['error_code'], 'deployment_or_storage_inspection_failed')

    def test_cli_success_is_static_not_activation(self):
        stdout = io.StringIO()
        with patch.object(Path, 'home', return_value=self.home), redirect_stdout(stdout):
            code = r.main(['deployment-plan', '--runtime-id', self.runtime_id, '--json'])
        out = json.loads(stdout.getvalue())
        self.assertEqual(code, 0, out)
        self.assertTrue(out['pairing_verified'])
        self.assertFalse(out['compatibility_verified'])
        self.assertFalse(out['ready_for_activation'])


if __name__ == '__main__':
    unittest.main()
