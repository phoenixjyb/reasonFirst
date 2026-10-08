from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import deployment as d, launch as l, pairing as p, runtime as r
import test_deployment_pairing as fixture

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / 'tools/reasonfirst_v4_0_3'


def copy_legacy(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in l.LEGACY:
        target = folder / name
        target.write_bytes((LEGACY / name).read_bytes())
        target.chmod(0o700 if name.endswith('.sh') else 0o600)


def copy_sidecar(folder, home):
    folder.mkdir(parents=True)
    python = str(home / "uv tool's env/bin/python")
    bootstrap = folder / 'http_bootstrap.py'
    shell = l.SIDECAR_HEADER + 'exec ' + shlex.quote(python) + ' -I -B ' + shlex.quote(str(bootstrap)) + ' "$@"\n'
    (folder / 'run_reasonfirst.sh').write_text(shell)
    (folder / 'run_reasonfirst.sh').chmod(0o700)
    bootstrap.write_bytes((ROOT / 'tests/fixtures/legacy_http_v051/http_bootstrap.py.txt').read_bytes())
    manifest = dict(stage_kind='reasonfirst-v051-legacy-http-uv',
                    source_commit='9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b',
                    uv_python=python, uv_prefix=str(Path(python).parent.parent),
                    package_modules={'gitlab_agent/__init__.py': {'sha256': 'a'*64, 'bytes': 99}},
                    compatibility_files={})
    for name in l.COMPAT:
        target = folder / 'compat' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (LEGACY / name).read_bytes()
        target.write_bytes(raw)
        manifest['compatibility_files'][name] = dict(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    (folder / 'STAGE.json').write_text(json.dumps(manifest))
    return manifest


@unittest.skipUnless(os.name == 'posix', 'No Windows ACL/source-layout claim; portable boundaries run separately')
class LaunchSourceTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.PairingStorageTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.home, self.runtime_id = self.case.home, self.case.runtime_id
        self.folder = self.case.program.parent
        copy_legacy(self.folder)
        pkg = self.case.venv / 'lib/python3.12/site-packages/gitlab_agent'
        pkg.mkdir(parents=True)
        for name in l.TARGET:
            (pkg / name).write_bytes((ROOT / 'src/gitlab_agent' / name).read_bytes())
        value = json.loads(self.case.manifest.read_bytes())
        value['observation']['origins'] = ['lib/python3.12/site-packages/gitlab_agent/bridge_http.py']
        value['tree'] = r._tree(self.case.venv, value['input']['python'])
        value.pop('manifest_digest')
        value['manifest_digest'] = r.digest(value)
        self.case.manifest.write_bytes(r.canonical(value))
        self.data = self.case.data
        self.data['EnvironmentVariables'].update(RF_MCP_PORT='8765', RF_MCP_READ_ONLY='false',
            RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH='false', RF_BRIDGE_CONFIG=str(self.home / '.config/reasonfirst/bridge.yaml'),
            GITLAB_AGENT_ENV_FILE=str(self.home / '.config/gitlab-agent/.env'),
            RF_CODEX_BRIDGE_STATE_DIR=str(self.home / '.local/share/reasonfirst/bridge'))
        self.save_registration()

    def save_registration(self):
        self.case.plist.write_bytes(plistlib.dumps(self.data))
        # Fixture-only new observation; production never replaces a record.
        self.case.record.unlink()
        plan = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=plan['plan_digest'], approved=True)
        self.pair = p.plan(runtime_id=self.runtime_id, home=self.home)

    def call(self, action='launch-plan', **kwargs):
        out = l.run_command(action, runtime_id=self.runtime_id,
                            expect_pairing_digest=self.pair['plan_digest'], home=self.home, **kwargs)
        self.assertNotIn('private-fixture-value', str(out))
        for key in ('mutating', 'commands_executed', 'configuration_contents_read',
                    'workspace_state_read', 'loaded_service_inspected', 'compatibility_verified',
                    'activation_authorized', 'ready_for_activation'):
            self.assertIs(out[key], False)
        return out

    def assert_failed(self, code=None):
        out = self.call()
        self.assertFalse(out['ok'], out)
        self.assertNotIn('identity', out)
        self.assertNotIn('plan_digest', out)
        if code:
            self.assertEqual(out['error_code'], code)
        return out

    def test_full_chat_source_exposes_specific_control_blocker_without_writes(self):
        before = self.case.snapshot()
        out = self.call()
        self.assertTrue(out['ok'], out)
        self.assertTrue(out['launcher_sources_verified'])
        contract = out['identity']['evidence']['saved_contract']
        self.assertEqual(contract['endpoint'], dict(host='127.0.0.1', path='/mcp', port=8765, port_source='saved_registration'))
        self.assertEqual(contract['mode'], 'full-chat')
        self.assertEqual(contract['control_route'], 'present')
        self.assertIn('legacy_control_not_supported_by_target', out['blockers'])
        self.assertFalse(out['static_control_compatible'])
        self.assertEqual(self.case.snapshot(), before)

    def test_readonly_contract_does_not_add_control_or_push(self):
        self.data['EnvironmentVariables'].update(RF_MCP_READ_ONLY='true', RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH='true')
        self.save_registration()
        out = self.call()
        self.assertTrue(out['ok'], out)
        self.assertTrue(out['static_control_compatible'])
        contract = out['identity']['evidence']['saved_contract']
        self.assertIs(contract['remote_push'], False)
        self.assertEqual(contract['control_route'], 'absent')
        self.assertNotIn('legacy_control_not_supported_by_target', out['blockers'])
        self.assertIn('loaded_service_and_effective_environment', out['blockers'])

    def test_unset_fields_are_unknown_not_guessed_defaults(self):
        self.data['EnvironmentVariables'] = {}
        self.save_registration()
        out = self.call()
        self.assertTrue(out['ok'], out)
        contract = out['identity']['evidence']['saved_contract']
        self.assertIsNone(contract['mode'])
        self.assertIsNone(contract['endpoint']['port'])
        self.assertEqual(contract['control_route'], 'unknown')
        self.assertIn('legacy_control_policy_unresolved', out['blockers'])

    def test_configuration_paths_never_cause_config_or_state_reads(self):
        for name in ('.env', 'bridge.yaml', 'state.json', 'auth.json', 'control-token'):
            (self.home / name).write_text('private-fixture-value')
        native = d._read_at
        def guarded(fd, name, **kw):
            self.assertNotIn(name, ('.env', 'bridge.yaml', 'state.json', 'auth.json', 'control-token'))
            return native(fd, name, **kw)
        with patch.object(d, '_read_at', side_effect=guarded):
            self.assertTrue(self.call()['ok'])

    def test_every_measured_legacy_source_is_required(self):
        for name in l.LEGACY:
            with self.subTest(name=name):
                path = self.folder / name
                raw = path.read_bytes()
                path.write_bytes(raw + b'\n# changed source\n')
                self.assert_failed('unknown_launch_source')
                path.write_bytes(raw)

    def test_changed_helper_cannot_be_hidden_by_same_program_path(self):
        first = self.call()
        self.assertTrue(first['ok'])
        path = self.folder / 'set_local_no_proxy.sh'
        path.write_bytes(path.read_bytes() + b'\nexit 0\n')
        out = self.call('launch-check', expect_digest=first['plan_digest'])
        self.assertFalse(out['ok'])

    def test_missing_nonregular_and_linked_sources_are_rejected(self):
        path = self.folder / 'reasonfirst_mcp_server.py'
        raw = path.read_bytes()
        path.unlink()
        self.assert_failed()
        target = self.home / 'source'
        target.write_bytes(raw)
        path.symlink_to(target)
        self.assert_failed()
        path.unlink(); os.link(target, path)
        self.assert_failed()
        path.unlink(); path.mkdir()
        self.assert_failed()

    def test_source_permissions_and_nonexecutable_entry_are_rejected(self):
        entry = self.folder / 'run_reasonfirst.sh'
        entry.chmod(0o600)
        self.assert_failed('launcher_not_executable')
        entry.chmod(0o722)
        self.assert_failed('storage_or_runtime_inspection_failed')

    def test_symlinked_source_parent_is_not_followed(self):
        moved = self.home / 'relocated'
        self.folder.rename(moved)
        self.folder.symlink_to(moved, target_is_directory=True)
        self.assert_failed()

    def test_policy_or_port_typo_is_error_not_false_or_default(self):
        for key, val in (('RF_MCP_READ_ONLY',''), ('RF_MCP_READ_ONLY','private-fixture-value'),
                         ('RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH','unknown'), ('RF_MCP_PORT','0'),
                         ('RF_MCP_PORT','65536'), ('RF_MCP_PORT','-3')):
            with self.subTest(key=key,val=val):
                old = self.data['EnvironmentVariables'][key]
                self.data['EnvironmentVariables'][key] = val
                self.save_registration(); self.assert_failed()
                self.data['EnvironmentVariables'][key] = old

    def test_custom_reference_must_be_absolute_local_and_unambiguous(self):
        for path in ('../private-fixture-value', '/outside/home/config', str(self.home / '..' / 'other'), 'https://example.invalid/config'):
            self.data['EnvironmentVariables']['RF_BRIDGE_CONFIG'] = path
            self.save_registration(); self.assert_failed('unsupported_path_reference')

    def test_environment_values_are_not_reported(self):
        self.data['EnvironmentVariables'].update(BASH_ENV='private-fixture-value', PYTHONPATH='private-fixture-value')
        self.save_registration()
        out = self.call(); self.assertTrue(out['ok'], out)
        self.assertIn('prelaunch_code_override_requires_review', out['blockers'])
        self.assertIn('legacy_python_import_override_requires_review', out['blockers'])

    def test_duplicate_plist_keys_fail_even_if_old_inventory_accepts_them(self):
        raw = self.case.plist.read_bytes().replace(b'<key>KeepAlive</key>', b'<key>KeepAlive</key>')
        # Insert an exact duplicate Label; same resolved snapshot, distinct bytes.
        raw = raw.replace(b'<key>Label</key>', b'<key>Label</key><string>'+d.LABEL.encode()+b'</string>\n<key>Label</key>',1)
        self.case.plist.write_bytes(raw)
        self.case.record.unlink()
        plan = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=plan['plan_digest'], approved=True)
        self.pair = p.plan(runtime_id=self.runtime_id, home=self.home)
        self.assert_failed('duplicate_plist_key')

    def test_combined_launch_digest_is_not_pairing_digest(self):
        out = self.call(); self.assertTrue(out['ok'])
        self.assertNotEqual(out['plan_digest'], self.pair['plan_digest'])
        self.assertTrue(self.call('launch-check', expect_digest=out['plan_digest'])['review_digest_matches'])
        self.assertFalse(self.call('launch-check', expect_digest=self.pair['plan_digest'])['ok'])

    def test_mode_only_source_metadata_change_invalidates_review_digest(self):
        out = self.call()
        self.assertTrue(out['ok'])
        (self.folder / 'reasonfirst_mcp_server.py').chmod(0o644)
        check = self.call('launch-check', expect_digest=out['plan_digest'])
        self.assertFalse(check['ok'])
        self.assertEqual(check['error_code'], 'launch_review_changed')

    def test_repeated_observations_reject_intermediate_source_changes(self):
        observe = l._observe
        count = 0
        def changed(*args):
            nonlocal count
            out = observe(*args); count += 1
            if count == 2:
                out['source']['profile'] = 'changed'
            return out
        with patch.object(l, '_observe', side_effect=changed):
            self.assert_failed('launch_inputs_changed')

    def test_sidecar_known_bootstrap_and_shell_are_recognized_without_running(self):
        folder = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture'
        copy_sidecar(folder, self.home)
        self.data['ProgramArguments'] = [str(folder / 'run_reasonfirst.sh')]
        self.save_registration()
        before = self.case.snapshot()
        out = self.call(); self.assertTrue(out['ok'], out)
        self.assertEqual(out['identity']['evidence']['source']['profile'], 'isolated-http-v051-sidecar')
        self.assertFalse(out['identity']['evidence']['source']['core_runtime_verified'])
        self.assertIn('legacy_control_not_supported_by_target', out['blockers'])
        self.assertEqual(before, self.case.snapshot())

    def test_sidecar_unknown_bootstrap_is_not_imported_or_trusted(self):
        folder = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture'
        copy_sidecar(folder, self.home)
        self.data['ProgramArguments'] = [str(folder / 'run_reasonfirst.sh')]; self.save_registration()
        (folder / 'http_bootstrap.py').write_text('raise RuntimeError("private-fixture-value")')
        self.assert_failed('unknown_launch_source')

    def test_sidecar_shell_injection_is_data_and_is_rejected(self):
        folder = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture'
        copy_sidecar(folder, self.home)
        self.data['ProgramArguments'] = [str(folder / 'run_reasonfirst.sh')]; self.save_registration()
        path = folder / 'run_reasonfirst.sh'
        path.write_text(path.read_text() + 'touch private-fixture-value\n')
        self.assert_failed('unknown_sidecar_launcher')

    def test_sidecar_manifest_cannot_redirect_reads_to_private_files(self):
        folder = self.home / '.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture'
        manifest = copy_sidecar(folder, self.home)
        self.data['ProgramArguments'] = [str(folder / 'run_reasonfirst.sh')]; self.save_registration()
        manifest['package_modules'] = {'../../.env': {'sha256': 'a'*64, 'bytes': 8}}
        (folder / 'STAGE.json').write_text(json.dumps(manifest))
        self.assert_failed('unsupported_sidecar_manifest')

    def test_no_arbitrary_target_version_or_changed_target_profile_accepted(self):
        path = self.case.venv / 'lib/python3.12/site-packages/gitlab_agent/bridge_http.py'
        path.write_bytes(path.read_bytes() + b'\n# changed profile\n')
        # Fixture re-seals its manifest, simulating another legitimately prepared
        # artifact; its version number alone must not grant a capability profile.
        value = json.loads(self.case.manifest.read_bytes())
        value['tree'] = r._tree(self.case.venv, value['input']['python'])
        value.pop('manifest_digest'); value['manifest_digest'] = r.digest(value)
        self.case.manifest.write_bytes(r.canonical(value))
        self.pair = p.plan(runtime_id=self.runtime_id, home=self.home)
        self.assert_failed('target_profile_not_supported')


class PortableLaunchTests(unittest.TestCase):
    def test_unsupported_host_before_any_source_inspection(self):
        for system in ('Linux', 'Windows', 'Other'):
            with patch.object(p.platform, 'system', return_value=system), patch.object(l, '_observe', side_effect=AssertionError):
                out = l.run_command('launch-plan', runtime_id='a'*64, expect_pairing_digest='b'*64)
                self.assertFalse(out['ok'])
                self.assertEqual(out['error_code'], 'pairing_not_verified')

    def test_lazy_cli_and_safe_arguments(self):
        for action in ('launch-plan', 'launch-check'):
            args = [action, '--runtime-id', 'a'*64, '--expect-pairing-digest', 'b'*64]
            if action == 'launch-check':
                args += ['--expect-digest','c'*64]
            with patch.object(p.platform, 'system', return_value='Linux'), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(r.main(args), 1)
            self.assertEqual(json.loads(out.getvalue())['error_code'], 'pairing_not_verified')
            with redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                r.main(args + ['--activate', 'private-value'])
            self.assertNotIn('private-value', err.getvalue())

    def test_pairing_approval_is_required_and_not_synthesized(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            r.main(['launch-plan','--runtime-id','a'*64])
        with patch.object(p, 'check', side_effect=AssertionError):
            out = l.run_command('launch-check', runtime_id='a'*64, expect_pairing_digest='b'*64, expect_digest='bad')
        self.assertEqual(out['error_code'], 'invalid_launch_digest')

    def test_unexpected_errors_have_no_private_values_or_partial_evidence(self):
        with patch.object(l, 'plan', side_effect=ValueError('private-value')):
            out = l.run_command('launch-plan', runtime_id='a'*64, expect_pairing_digest='b'*64)
        self.assertEqual(out['error_code'], 'launch_inspection_failed')
        self.assertNotIn('private-value', str(out))
        self.assertNotIn('identity', out)
        self.assertNotIn('plan_digest', out)

    def test_known_profiles_match_reviewed_reference_bytes(self):
        # Source checkouts can normalize LF to CRLF on Windows. This test verifies
        # repository reference text; production hashes actual bytes unchanged.
        for name, sig in l.LEGACY.items():
            raw = (LEGACY / name).read_text(encoding='utf-8').encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sig)
        raw = (ROOT / 'tests/fixtures/legacy_http_v051/http_bootstrap.py.txt').read_text(encoding='utf-8').encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), l.BOOTSTRAP_SHA256)
        for name,sig in l.TARGET.items():
            raw=(ROOT/'src/gitlab_agent'/name).read_text(encoding='utf-8').encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sig)

    def test_documentation_distinguishes_saved_contract_from_live_compatibility(self):
        for name in ('LAUNCH_REVIEW.md','LAUNCH_REVIEW_CN.md'):
            text=(ROOT/'docs'/name).read_text(encoding='utf-8')
            for token in ('launch-plan', 'launch-check', '--expect-pairing-digest', '/control',
                          'compatibility_verified', 'v0.5.1', 'not_inspected'):
                self.assertIn(token,text)


if __name__ == '__main__':
    unittest.main()
