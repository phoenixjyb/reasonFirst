from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import compatibility as c, pairing as p, runtime as r, deployment as d
import test_deployment_pairing as fixture

ROOT = Path(__file__).resolve().parents[1]


class AssessmentBoundaryTests(unittest.TestCase):
    def test_unsupported_platform_before_home_or_files(self):
        for platform in ('Linux', 'Windows'):
            with self.subTest(platform=platform), patch.object(p.platform, 'system', return_value=platform), \
                 patch.object(d, '_home', side_effect=AssertionError('No home discovery')):
                result = c.run_command('deployment-assess', runtime_id='a'*64)
                self.assertEqual(result['error_code'], 'unsupported_pairing_platform')
                self.assertNotIn('assessment_digest', result)

    def test_cli_dispatch_returns_one_json_and_nonzero_on_failure(self):
        for action in ('deployment-assess', 'deployment-assess-check'):
            argv = [action, '--runtime-id', 'a'*64, '--json']
            if action.endswith('-check'):
                argv.extend(['--expect-digest', 'b'*64])
            stdout = io.StringIO()
            with patch.object(p, '_supported', side_effect=p.PairingError('unsupported_pairing_platform')), redirect_stdout(stdout):
                self.assertEqual(r.main(argv), 1)
            self.assertFalse(json.loads(stdout.getvalue())['ok'])

    def test_check_requires_digest_and_rejects_execution_flags(self):
        for extra in (['--yes'], ['--force'], ['--activate'], ['--token', 'secret-value']):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                r.main(['deployment-assess', '--runtime-id', 'a'*64, *extra])
            self.assertNotIn('secret-value', stderr.getvalue())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            r.main(['deployment-assess-check', '--runtime-id', 'a'*64])

    def test_help_does_not_inspect_or_start_a_controller(self):
        with patch.object(c, 'assess', side_effect=AssertionError), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
            r.main(['deployment-assess', '--help'])
        self.assertEqual(exc.exception.code, 0)

    def test_unexpected_exceptions_never_echo_private_contents_or_partial_evidence(self):
        with patch.object(p, '_supported', return_value=('Darwin', 'test')), \
             patch.object(c, 'assess', side_effect=RuntimeError('private-config-value')):
            result = c.run_command('deployment-assess', runtime_id='a'*64)
        self.assertFalse(result['ok'])
        self.assertNotIn('private-config-value', json.dumps(result))
        self.assertNotIn('evidence', result)
        self.assertNotIn('assessment_digest', result)

    def test_invalid_digest_rejected_before_inspection(self):
        with patch.object(p, '_supported', return_value=('Darwin', 'test')), \
             patch.object(c, 'assess', side_effect=AssertionError):
            for value in (None, 'a'*63, '../secret', True):
                result = c.run_command('deployment-assess-check', runtime_id='a'*64, expect_digest=value)
                self.assertEqual(result['error_code'], 'invalid_assessment_digest')

    def test_pinned_profiles_match_retained_sources_not_self_reported_versions(self):
        for name, expected in c.LAUNCH_FILES.items():
            self.assertEqual(hashlib.sha256((ROOT/'tools/reasonfirst_v4_0_3'/name).read_bytes()).hexdigest(), expected)
        for name, expected in c.IMPLEMENTATIONS.items():
            self.assertEqual(hashlib.sha256((ROOT/'src/gitlab_agent/bridge_preview'/name).read_bytes()).hexdigest(), expected)
        for name, expected in c.ALIASES.items():
            self.assertEqual(hashlib.sha256((ROOT/'tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge'/name).read_bytes()).hexdigest(), expected)
        for name, expected in c.TARGET_FILES.items():
            self.assertEqual(hashlib.sha256((ROOT/'src/gitlab_agent'/name).read_bytes()).hexdigest(), expected)

    def test_bilingual_docs_are_explicit_about_unsolved_live_and_sidecar_scope(self):
        for file in ('DEPLOYMENT_COMPATIBILITY.md', 'DEPLOYMENT_COMPATIBILITY_CN.md'):
            text = (ROOT/'docs'/file).read_text(encoding='utf-8')
            for token in ('v0.5.1', 'deployment-assess', 'deployment-assess-check', '/control',
                          'compatibility_verified', 'unsupported_launcher_profile', 'not a signature'):
                self.assertIn(token, text)


@unittest.skipUnless(os.name == 'posix', 'Known macOS source profile on POSIX fixtures; not Windows ACL support')
class AssessmentStorageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PairingStorageTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.home, self.rid = self.f.home, self.f.runtime_id
        self.tools = self.home / '.local/share/reasonfirst/v4-service/tools/codex_web_bridge'
        self.tools.mkdir(parents=True)
        for name in c.LAUNCH_FILES:
            shutil.copyfile(ROOT/'tools/reasonfirst_v4_0_3'/name, self.tools/name)
        (self.tools/'reasonfirst_codex_bridge').mkdir()
        for name in c.IMPLEMENTATIONS:
            shutil.copyfile(ROOT/'src/gitlab_agent/bridge_preview'/name,
                            self.tools/'reasonfirst_codex_bridge'/name)
        # Populate actual code bytes in the synthetic recorded runtime. Its
        # preparation execution remains mocked; native E2E tests use a real venv.
        self.site = self.f.venv/'lib/python3.12/site-packages/gitlab_agent'
        for name in c.TARGET_FILES:
            file = self.site/name
            file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/'src/gitlab_agent'/name, file)
        self.refresh_manifest()
        self.cfg = self.home/'.config/reasonfirst/bridge.yaml'
        self.cfg.write_text('version: 4\ndefaults: {target: local}\ntargets: {local: {type: local}}\n', encoding='utf-8')
        self.cfg.chmod(0o600)

    def refresh_manifest(self):
        doc = json.loads(self.f.manifest.read_bytes())
        doc['tree'] = r._tree(self.f.venv, doc['input']['python'])
        doc.pop('manifest_digest')
        doc['manifest_digest'] = r.digest(doc)
        self.f.manifest.write_bytes(r.canonical(doc))

    def saved_env(self, updates):
        # Deliberately establish a new independently reviewed fixture record;
        # production never replaces one. Test code removes only its own fixture.
        data = dict(self.f.data)
        data['EnvironmentVariables'] = updates
        self.f.plist.write_bytes(plistlib.dumps(data))
        self.f.record.unlink()
        plan = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=plan['plan_digest'], approved=True)

    def call(self, action='deployment-assess', **kwargs):
        before = self.f.snapshot()
        env = dict(os.environ)
        result = c.run_command(action, runtime_id=self.rid, home=self.home, **kwargs)
        self.assertEqual(before, self.f.snapshot())
        self.assertEqual(env, dict(os.environ))
        for key in ('compatibility_verified', 'ready_for_activation', 'activation_authorized',
                    'mutating', 'commands_executed', 'credential_store_files_read', 'workspace_state_read', 'live_service_verified'):
            self.assertIs(result[key], False)
        self.assertEqual(result['proposed_actions'], [])
        self.assertNotIn('private-fixture-value', json.dumps(result))
        return result

    def reject(self, code):
        result = self.call()
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error_code'], code)
        self.assertNotIn('evidence', result)
        self.assertNotIn('assessment_digest', result)

    def test_default_full_chat_reports_real_control_mismatch_not_ready(self):
        result = self.call()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['assessment_status'], 'blocked_by_declared_policy')
        self.assertIn('legacy_control_route_not_supported_by_target', result['blockers'])
        self.assertFalse(result['declared_surface_match'])
        self.assertTrue(result['evidence']['saved_policy']['legacy_control_route_exposed'])
        self.assertEqual(result['evidence']['saved_policy']['policy_origins']['RF_MCP_READ_ONLY'], 'source_default_if_not_inherited')
        self.assertEqual(result['unresolved_requirements'], list(c.UNRESOLVED))

    def test_explicit_readonly_can_match_saved_surface_but_not_live_compatibility(self):
        self.saved_env({'RF_MCP_READ_ONLY':'true', 'RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH':'false', 'RF_MCP_PORT':'9123'})
        result = self.call()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['declared_surface_match'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['assessment_status'], 'requires_live_compatibility_checks')
        self.assertEqual(result['evidence']['saved_policy']['endpoint']['port'], 9123)
        self.assertFalse(result['evidence']['saved_policy']['legacy_control_route_exposed'])
        self.assertTrue(result['evidence']['saved_policy']['launcher_sets_manager_no_proxy'])

    def test_remote_push_optin_is_never_silently_dropped_even_in_readonly(self):
        self.saved_env({'RF_MCP_READ_ONLY':'true','RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH':'true'})
        result = self.call()
        self.assertIn('experimental_remote_push_environment_rejected_by_target', result['blockers'])
        self.assertFalse(result['evidence']['saved_policy']['remote_push_tool_exposed'])

    def test_matching_assessment_digest_recheck(self):
        result = self.call()
        check = self.call('deployment-assess-check', expect_digest=result['assessment_digest'])
        self.assertTrue(check['ok'], check)
        self.assertTrue(check['review_digest_matches'])
        self.assertEqual(check['assessment_digest'], r.digest(check['evidence']))

    def test_pairing_digest_is_not_an_assessment_digest(self):
        pair = p.plan(runtime_id=self.rid, home=self.home)
        result = self.call('deployment-assess-check', expect_digest=pair['plan_digest'])
        self.assertEqual(result['error_code'], 'assessment_changed')

    def test_config_whitespace_change_invalidates_review(self):
        before = self.call()
        with self.cfg.open('a') as out:
            out.write('\n# a reviewed config changed\n')
        result = self.call('deployment-assess-check', expect_digest=before['assessment_digest'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'assessment_changed')

    def test_all_named_launch_sources_are_pinned_and_never_executed(self):
        for name in c.LAUNCH_FILES:
            path = self.tools/name
            raw = path.read_bytes()
            path.write_bytes(raw+b'\n')
            self.reject('unrecognized_launch_source')
            path.write_bytes(raw)

    def test_missing_source_stops_without_partial_policy(self):
        (self.tools/'run_mcp_server.sh').unlink()
        self.reject('launch_source_missing')

    def test_controller_and_config_aliases_resolve_to_checked_staged_code(self):
        root = self.home/'.local/share/reasonfirst/v4-service'
        for name in c.ALIASES:
            shutil.copyfile(ROOT/'tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge'/name, self.tools/'reasonfirst_codex_bridge'/name)
            dest = root/'src/gitlab_agent/bridge_preview'/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/'src/gitlab_agent/bridge_preview'/name, dest)
        self.assertTrue(self.call()['ok'])
        (root/'src/gitlab_agent/bridge_preview/controller.py').write_text('arbitrary source')
        self.reject('unrecognized_launch_source')

    def test_sidecar_is_explicitly_unsupported_not_approximated(self):
        program = self.home/'.local/share/reasonfirst/upgrades/uv-http-v0.5.1-test/run_reasonfirst.sh'
        self.f.data['ProgramArguments'] = [str(program)]
        self.saved_env({})
        self.reject('unsupported_launcher_profile')
        self.assertFalse(program.exists())

    def test_invalid_boolean_and_endpoint_are_not_defaults(self):
        for env, code in (({'RF_MCP_READ_ONLY':'typo'}, 'invalid_saved_policy'),
                          ({'RF_MCP_READ_ONLY':''}, 'invalid_saved_policy'),
                          ({'RF_MCP_PORT':'0'}, 'invalid_saved_endpoint'),
                          ({'RF_MCP_PORT':'65536'}, 'invalid_saved_endpoint'),
                          ({'RF_MCP_PORT':'private-fixture-value'}, 'invalid_saved_endpoint')):
            self.saved_env(env)
            self.reject(code)

    def test_saved_code_injection_overrides_block(self):
        for name in ('BASH_ENV', 'PYTHONPATH', 'PYTHONHOME', 'DYLD_INSERT_LIBRARIES'):
            self.saved_env({name:'private-fixture-value'})
            self.reject('unsupported_saved_code_override')

    def test_saved_home_cannot_redirect_config_reads(self):
        self.saved_env({'HOME':'/different/operator'})
        self.reject('saved_home_conflict')

    def test_custom_config_reference_and_v3_shape_are_preserved(self):
        custom = self.home/'custom policy.yml'
        custom.write_text('version: 3\ncontrol: {repo: private-fixture-value}\n', encoding='utf-8')
        custom.chmod(0o600)
        self.saved_env({'RF_BRIDGE_CONFIG':str(custom), 'RF_CODEX_BRIDGE_STATE_DIR':'~/unchanged-state'})
        result = self.call()
        self.assertEqual(result['evidence']['bridge_config']['schema_version'], 3)
        self.assertTrue(result['evidence']['bridge_config']['legacy_control_config_present'])
        self.assertEqual(result['evidence']['saved_policy']['references']['bridge_state'], str(self.home/'unchanged-state'))
        self.assertFalse((self.home/'unchanged-state').exists())

    def test_credential_state_files_and_unrelated_scripts_are_not_read(self):
        watched = []
        read = c._read
        def guarded(home, path, **kwargs):
            watched.append(str(path))
            self.assertNotIn(path.name, ('.env', 'auth.json', 'config.toml', 'control-token', 'state.json', 'setup.yaml'))
            return read(home, path, **kwargs)
        with patch.object(c, '_read', side_effect=guarded):
            self.assertTrue(self.call()['ok'])
        self.assertTrue(watched)

    def test_sensitive_or_outside_config_references_are_not_followed(self):
        for value, code in (('/etc/bridge.yaml', 'unsupported_reference_path'),
                            ('~/../bridge.yaml', 'unsupported_reference_path'),
                            ('~/auth.json', 'unsupported_bridge_config_path'),
                            ('~/.env', 'unsupported_bridge_config_path')):
            self.saved_env({'RF_BRIDGE_CONFIG':value})
            self.reject(code)

    def test_missing_config_is_not_created_or_claimed_valid(self):
        self.cfg.unlink()
        result = self.call()
        self.assertTrue(result['ok'])
        self.assertEqual(result['evidence']['bridge_config']['schema_status'], 'absent_legacy_defaults_not_validated')
        self.assertFalse(self.cfg.exists())

    def test_invalid_duplicate_aliased_or_oversized_yaml_stops_without_values(self):
        for text in ('version: 4\nversion: 3\n', 'version: 9\n', 'version: true\n',
                     'version: 4\ndefaults: []\n', 'version: 4\nx: private-fixture-value\n',
                     'version: 4\ndefaults: &d {}\ntargets: *d\n',
                     '!!python/object/apply:os.system [private-fixture-value]', '[]', 'version: ['):
            self.cfg.write_text(text, encoding='utf-8')
            self.reject('unsupported_bridge_config')
        self.cfg.write_bytes(b'x'*(c.MAX_CONFIG+1))
        self.reject('deployment_or_storage_inspection_failed')

    def test_unsafe_sources_symlinks_and_config_permissions_are_rejected(self):
        src = self.tools/'run_reasonfirst.sh'
        src.chmod(0o666)
        self.reject('deployment_or_storage_inspection_failed')
        src.chmod(0o644)
        other = self.home/'outside.yaml';other.write_text('version: 4\n')
        self.cfg.unlink();self.cfg.symlink_to(other)
        self.reject('deployment_or_storage_inspection_failed')
        self.cfg.unlink();self.cfg.write_text('version: 4\n');self.cfg.chmod(0o644)
        self.reject('deployment_or_storage_inspection_failed')

    def test_target_unchanged_version_with_different_http_code_is_unsupported(self):
        (self.site/'bridge_http.py').write_text('different source, same reported version')
        self.refresh_manifest()
        self.reject('unrecognized_target_http_profile')

    def test_runtime_drift_refused_by_existing_validator(self):
        (self.site/'bridge_http.py').write_text('modified after preparation')
        self.reject('runtime_not_prepared')

    def test_config_changed_during_read_returns_no_partial_assessment(self):
        call = c._config
        count = 0
        def changing(home, path):
            nonlocal count
            count += 1
            if count == 2:
                with self.cfg.open('a') as out:
                    out.write('\n')
            return call(home, path)
        with patch.object(c, '_config', side_effect=changing):
            result = c.run_command('deployment-assess', runtime_id=self.rid, home=self.home)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'changed_during_assessment')
        self.assertNotIn('assessment_digest', result)

    def test_custom_yaml_collection_tags_are_rejected_without_execution(self):
        for text in ('!arbitrary {version: 4}', 'version: 4\ncontrol: !tag {}\n',
                     'version: 4\ntargets: {local: !tag []}\n', 'version: !!int "3.0"'):
            self.cfg.write_text(text, encoding='utf-8')
            self.reject('unsupported_bridge_config')

    def test_duplicate_saved_plist_keys_are_not_interpreted(self):
        raw = self.f.plist.read_bytes()
        self.f.plist.write_bytes(raw.replace(b'<key>Label</key>', b'<key>EnvironmentVariables</key><dict/><key>Label</key>'))
        self.f.record.unlink()
        adoption = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=adoption['plan_digest'], approved=True)
        self.reject('duplicate_saved_plist_key')

    def test_binary_plist_uses_same_strict_policy_reader(self):
        self.f.plist.write_bytes(plistlib.dumps(self.f.data, fmt=plistlib.FMT_BINARY))
        self.f.record.unlink()
        adoption = d.plan_adoption(system_name='Darwin', home=self.home)
        d.adopt_deployment(system_name='Darwin', home=self.home, expect_digest=adoption['plan_digest'], approved=True)
        self.assertTrue(self.call()['ok'])

    def test_plist_drift_is_rejected_before_source_policy(self):
        raw = self.f.plist.read_bytes()
        self.f.plist.write_bytes(raw.replace(b'<true/>', b'<false/>') + b'\n')
        self.reject('registration_drifted')


if __name__ == '__main__':
    unittest.main()
