from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import reasonfirst_cli
from gitlab_agent.setup_inspection import inspect_configuration
from gitlab_agent.setup_status import build_setup_status


class ExistingInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'operator.env'
        self.state = self.root / 'setup.yaml'
        self.secret = 'fixture-' + 'value-not-for-output'
        self.contents = (
            '# Keep every field, comment, and project\n'
            'GITLAB_BASE_URL=https://gitlab.example.test\n'
            f'GITLAB_TOKEN={self.secret}\n'
            'GITLAB_ALLOWED_PROJECTS=team/z,team/a,team/b\n'
            f'GITLAB_WORKSPACE_ROOT={self.root / "managed"}\n'
            'REASONFIRST_DEFAULT_BACKEND=copilot-cli\n'
            'GITLAB_TRUST_ENV=true\n'
            'GITLAB_GIT_TRUST_ENV=true\n'
            'UNRELATED_SETTING=preserve-me\n'
        ).encode()
        self.config.write_bytes(self.contents)
        self.env = {'HOME': str(self.root), 'USERPROFILE': str(self.root),
                    'GITLAB_AGENT_ENV_FILE': str(self.config)}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env, clear=True).start()
        patch('pathlib.Path.home', return_value=self.root).start()

    def args(self, **changes):
        values = dict(state_file=self.state, mode=None, gitlab_url=None,
                      project=None, ref=None, worker=None, json=True,
                      skip_chatgpt=False, reuse_existing=False, reconfigure=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def run_setup(self, args=None, *, answer='', tty=True):
        calls = []
        def respond(prompt):
            calls.append(prompt)
            return answer
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=tty))
            for name in ('apply_env_updates', '_persist_progress', 'save_setup_state',
                         'preflight_project', 'connect_runtime', 'connect_bridge_runtime',
                         '_guided_tunnel_after_local', '_guided_bridge_after_read'):
                stack.enter_context(patch.object(reasonfirst_cli, name,
                    side_effect=AssertionError('Unexpected side effect: ' + name)))
            secret = stack.enter_context(patch.object(reasonfirst_cli.getpass, 'getpass',
                    side_effect=AssertionError('Unnecessary credential prompt')))
            code, result = reasonfirst_cli._run_guided_setup(
                args or self.args(), input_fn=respond, secret_fn=secret)
        self.assertEqual(self.config.read_bytes(), self.contents)
        self.assertFalse(self.state.exists())
        self.assertNotIn(self.secret, repr(result))
        return code, result, calls

    def test_default_reuse_is_one_confirmation_and_no_onboarding(self):
        code, result, prompts = self.run_setup()
        self.assertEqual(code, 0)
        self.assertTrue(result['configuration_reused'])
        self.assertFalse(result['writes_performed'])
        self.assertFalse(result['ready'])
        self.assertEqual(result['config']['allowed_projects'], ['team/a', 'team/b', 'team/z'])
        self.assertEqual(result['config']['default_backend'], 'copilot-cli')
        self.assertEqual(result['config']['workspace_root'], str(self.root / 'managed'))
        self.assertEqual(len(prompts), 1)
        self.assertIn('Reuse', prompts[0])
        self.assertEqual(os.environ, self.env)

    def test_noninteractive_explicit_reuse_is_read_only(self):
        code, result, prompts = self.run_setup(self.args(reuse_existing=True), tty=False)
        self.assertEqual(code, 0)
        self.assertEqual(prompts, [])
        self.assertTrue(result['configuration_reused'])
        self.assertEqual(result['chatgpt_connection'], 'not_verified')

    def test_declined_or_unrecognized_confirmation_does_not_fall_through(self):
        for answer in ('n', 'no', 'unexpected'):
            with self.subTest(answer=answer):
                code, result, prompts = self.run_setup(answer=answer)
                self.assertEqual(code, 1)
                self.assertTrue(result['cancelled'])
                self.assertFalse(result['writes_performed'])
                self.assertEqual(len(prompts), 1)

    def test_environment_override_reuse_preserves_precedence_without_values(self):
        with patch.dict(os.environ, {'GITLAB_TOKEN': 'override-' + 'hidden',
                                     'REASONFIRST_DEFAULT_BACKEND': 'codex-cli'}):
            code, result, _ = self.run_setup(self.args(reuse_existing=True))
            self.assertEqual(code, 0)
            self.assertEqual(result['config']['default_backend'], 'codex-cli')
            self.assertIn('GITLAB_TOKEN', result['config']['environment_override_names'])
            self.assertNotIn('override-hidden', repr(result))
            self.assertEqual(os.environ['GITLAB_TOKEN'], 'override-hidden')

    def test_explicit_reuse_with_change_flags_is_rejected_before_prompts(self):
        for field, value in (('project', 'team/new'), ('gitlab_url', 'https://other.test'),
                             ('worker', 'auto'), ('ref', 'main'), ('mode', 'full-chat'),
                             ('reconfigure', True)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(RuntimeError, 'reuse-existing'):
                    self.run_setup(self.args(reuse_existing=True, **{field: value}))

    def test_environment_only_reuse_never_creates_higher_precedence_file(self):
        self.config.unlink()
        with patch.dict(os.environ, {'GITLAB_BASE_URL': 'https://gitlab.example.test',
                                     'GITLAB_TOKEN': self.secret,
                                     'GITLAB_ALLOWED_PROJECTS': 'team/a'}):
            settings, report = inspect_configuration()
        self.assertIsNotNone(settings)
        self.assertFalse(report['exists'])
        self.assertEqual(report['status'], 'environment_only')
        self.assertFalse(self.config.exists())

    def test_git_only_config_can_be_reused_without_api_token_prompt(self):
        self.contents = self.contents.replace(b'GITLAB_TOKEN=', b'GITLAB_GIT_TOKEN=')
        self.config.write_bytes(self.contents)
        code, result, _ = self.run_setup()
        self.assertEqual(code, 0)
        self.assertFalse(result['config']['has_api_token'])
        self.assertTrue(result['config']['has_git_credential'])

    def test_incomplete_config_is_preserved_and_missing_fields_are_explicit(self):
        self.contents = b'GITLAB_BASE_URL=https://gitlab.example.test\n'
        self.config.write_bytes(self.contents)
        code, result, _ = self.run_setup()
        self.assertEqual(code, 0)
        self.assertFalse(result['local_control_configured'])
        self.assertIn('gitlab_credential', result['config']['missing_fields'])
        self.assertIn('approved_projects', result['config']['missing_fields'])

    def test_invalid_config_never_turns_into_new_setup_even_with_override(self):
        for content in (b'not an assignment\n', b'GITLAB_BASE_URL="unterminated\n',
                        b'GITLAB_BASE_URL=x\nGITLAB_BASE_URL=y\n', b'\xff',
                        b'GITLAB_BASE_URL=https://gitlab.example.test\nGITLAB_COMMAND_TIMEOUT_SECONDS=oops\n'):
            with self.subTest(content=content):
                self.contents = content
                self.config.write_bytes(content)
                code, result, prompts = self.run_setup(self.args(reconfigure=True))
                self.assertEqual(code, 1)
                self.assertEqual(result['stage'], 'configuration-inspection')
                self.assertEqual(result['config']['status'], 'invalid')
                self.assertFalse(result['writes_performed'])
                self.assertEqual(prompts, [])

    def test_bad_config_errors_do_not_echo_values_or_source_lines(self):
        self.contents = ('GITLAB_BASE_URL=https://gitlab.example.test\n'
                         'REASONFIRST_DEFAULT_BACKEND=' + self.secret + '\n').encode()
        self.config.write_bytes(self.contents)
        _, report = inspect_configuration()
        self.assertFalse(report['valid'])
        self.assertNotIn(self.secret, repr(report))
        self.assertEqual(report['error_code'], 'invalid_configuration')

    def test_duplicate_unknown_keys_also_block_ambiguous_file(self):
        self.config.write_bytes(self.contents + b'UNRELATED_SETTING=second\n')
        _, report = inspect_configuration()
        self.assertEqual(report['status'], 'invalid')

    def test_unreadable_config_is_not_missing(self):
        with patch('gitlab_agent.setup_inspection.read_config_bytes', side_effect=PermissionError):
            _, report = inspect_configuration()
        self.assertEqual(report['status'], 'unreadable')
        self.assertEqual(report['error_code'], 'configuration_unreadable')

    def test_oversized_and_directory_config_fail_closed(self):
        self.config.write_bytes(b'#' * (131072 + 1))
        _, report = inspect_configuration()
        self.assertEqual(report['status'], 'invalid')
        self.config.unlink()
        self.config.mkdir()
        _, report = inspect_configuration()
        self.assertEqual(report['status'], 'unsupported_layout')

    def test_symlink_config_is_reported_not_followed(self):
        target = self.root / 'target'
        target.write_bytes(self.contents)
        self.config.unlink()
        try:
            self.config.symlink_to(target)
        except OSError:
            self.skipTest('Host cannot create an unprivileged symlink')
        _, report = inspect_configuration()
        self.assertEqual(report['status'], 'unsupported_layout')
        self.assertEqual(target.read_bytes(), self.contents)

    def test_loaded_config_does_not_mutate_calling_environment(self):
        before = dict(os.environ)
        settings, report = inspect_configuration()
        self.assertEqual(before, dict(os.environ))
        self.assertTrue(report['valid'])
        self.assertEqual(settings.api_token, self.secret)
        self.assertTrue(settings.api_trust_env)
        self.assertTrue(settings.git_trust_env)

    def test_invalid_setup_metadata_blocks_guided_setup_without_secret_prompt(self):
        self.state.write_text('version: 12345\n', encoding='utf-8')
        out = io.StringIO()
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=True), contextlib.redirect_stdout(out):
            code, result = reasonfirst_cli._run_guided_setup(
                self.args(), input_fn=lambda _: self.fail('Must not prompt'),
                secret_fn=lambda _: self.fail('Must not prompt for credentials'))
        self.assertEqual(code, 1)
        self.assertEqual(result['stage'], 'setup-state-inspection')
        self.assertEqual(self.config.read_bytes(), self.contents)
        self.assertEqual(self.state.read_text(), 'version: 12345\n')

    def test_valid_config_no_setup_state_does_not_propose_duplicate_tunnel(self):
        result = build_setup_status(state_path=self.state,
            which=lambda name: '/synthetic/' + name,
            system_name='Linux', desktop_socket=self.root / 'missing.sock')
        ids = [item['id'] for item in result['plan']['actions']]
        self.assertIn('review-existing-deployment', ids)
        self.assertNotIn('configure-tunnel', ids)
        self.assertNotIn('install-tunnel-client', ids)
        self.assertEqual(result['deployment']['running_service_version'], None)
        self.assertEqual(result['deployment']['live_inspection'], 'not_inspected')
        self.assertFalse(result['readiness']['ready'])

    def test_invalid_config_status_is_not_new_install_advice(self):
        self.config.write_bytes(b'INVALID\n')
        result = build_setup_status(state_path=self.state, which=lambda _: None,
            system_name='Linux', desktop_socket=self.root / 'missing.sock')
        ids = [item['id'] for item in result['plan']['actions']]
        self.assertIn('inspect-config', ids)
        self.assertNotIn('configure-gitlab', ids)
        self.assertNotIn('configure-tunnel', ids)
        self.assertNotIn('install-tunnel-client', ids)

    def test_status_alias_is_nonmutating_and_does_not_call_providers(self):
        with patch.object(reasonfirst_cli, '_run_guided_setup', side_effect=AssertionError), \
             patch('subprocess.run', side_effect=AssertionError('No subprocesses in inventory')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = reasonfirst_cli.main(['status', '--json', '--state-file', str(self.state)])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result['command'], 'status')
        self.assertFalse(result['mutating'])
        self.assertEqual(self.config.read_bytes(), self.contents)

    def test_repair_without_setup_record_does_not_recommend_onboarding(self):
        code, result = reasonfirst_cli._run_setup_repair(self.args())
        self.assertEqual(code, 1)
        self.assertIn('setup --status', result['error'])
        self.assertNotIn("run 'reasonfirst setup' first", result['error'])

    def test_saved_wizard_state_and_all_config_bytes_remain_unchanged(self):
        self.state.write_text('version: 1\nmode: cli-only\ncompleted_phases: [system]\n', encoding='utf-8')
        original = self.state.read_bytes()
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=False):
            code, result = reasonfirst_cli._run_guided_setup(self.args(reuse_existing=True))
        self.assertEqual(code, 0)
        self.assertEqual(result['mode'], 'cli-only')
        self.assertTrue(result['setup_state_recorded'])
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(self.config.read_bytes(), self.contents)

    def test_custom_cwd_file_reuse_needs_no_copy_or_migration(self):
        cwd_config = self.root / '.env'
        cwd_config.write_bytes(self.contents)
        with patch.dict(os.environ, {'HOME': str(self.root), 'USERPROFILE': str(self.root)}, clear=True), \
             patch.object(reasonfirst_cli, 'resolve_env_file', return_value=cwd_config), \
             patch('gitlab_agent.config.resolve_env_file', return_value=cwd_config), \
             patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=False):
            code, result = reasonfirst_cli._run_guided_setup(self.args(reuse_existing=True))
        self.assertEqual(code, 0)
        self.assertEqual(result['config']['source'], 'working_directory_file')
        self.assertEqual(cwd_config.read_bytes(), self.contents)
        self.assertFalse((self.root / '.config/gitlab-agent/.env').exists())

    def test_changed_config_after_confirmation_is_not_accepted(self):
        def change(_):
            self.config.write_bytes(self.contents.replace(b'team/z', b'team/changed'))
            return 'y'
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=True):
            code, result = reasonfirst_cli._run_guided_setup(self.args(), input_fn=change)
        self.assertEqual(code, 1)
        self.assertIn('changed', result['error'])
        self.assertFalse(result['writes_performed'])
        self.assertFalse(self.state.exists())

    def test_existing_reconfigure_still_requires_preflight_and_write_confirmation(self):
        answers = []
        def decline(prompt):
            answers.append(prompt)
            return 'n'
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=True), \
             patch.object(reasonfirst_cli, '_choose_worker', return_value='copilot-cli'), \
             patch.object(reasonfirst_cli, 'preflight_project', return_value={'ok': True, 'resolved_commit_sha': 'a'*40}) as preflight, \
             patch.object(reasonfirst_cli, 'apply_env_updates', side_effect=AssertionError), \
             patch.object(reasonfirst_cli, '_persist_progress', side_effect=AssertionError):
            code, result = reasonfirst_cli._run_guided_setup(
                self.args(reconfigure=True, gitlab_url='https://gitlab.example.test', project='team/new'),
                input_fn=decline, secret_fn=lambda _: '')
        self.assertEqual(code, 1)
        self.assertTrue(result['cancelled'])
        preflight.assert_called_once()
        self.assertEqual(result['allowed_projects_after'], ['team/a', 'team/b', 'team/new', 'team/z'])
        self.assertEqual(self.config.read_bytes(), self.contents)
        self.assertTrue(any('Write this private config' in p for p in answers))

    def test_reconfigure_with_environment_override_is_still_blocked(self):
        with patch.dict(os.environ, {'GITLAB_TOKEN': 'override-hidden'}), \
             patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'exported'):
                reasonfirst_cli._run_guided_setup(self.args(reconfigure=True),
                    input_fn=lambda _: self.fail('No prompt before override check'),
                    secret_fn=lambda _: self.fail('No credential prompt'))
        self.assertEqual(self.config.read_bytes(), self.contents)

    def test_reuse_cli_json_is_one_object_without_tty_or_secret_prompt(self):
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=False), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = reasonfirst_cli.main(['setup', '--reuse-existing', '--json', '--state-file', str(self.state)])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertTrue(result['configuration_reused'])
        self.assertFalse(result['provider_check_performed'])
        self.assertEqual(self.config.read_bytes(), self.contents)

    def test_reuse_missing_config_fails_without_starting_onboarding(self):
        self.config.unlink()
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=False):
            code, result = reasonfirst_cli._run_guided_setup(self.args(reuse_existing=True),
                input_fn=lambda _: self.fail('No onboarding'), secret_fn=lambda _: self.fail('No secret prompt'))
        self.assertEqual(code, 1)
        self.assertEqual(result['config']['status'], 'missing')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.state.exists())

    def test_cli_rejects_mixed_status_repair_reuse_modes(self):
        for flags in (['--status', '--reuse-existing'], ['--repair', '--reconfigure']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(reasonfirst_cli.main(['setup', *flags]), 1)
        self.assertEqual(self.config.read_bytes(), self.contents)

    def test_new_configuration_is_still_a_new_user_path(self):
        self.config.unlink()
        with patch.object(reasonfirst_cli.sys.stdin, 'isatty', return_value=True):
            prompts = []
            class FirstPrompt(Exception):
                pass
            def stop(prompt):
                prompts.append(prompt)
                raise FirstPrompt
            with self.assertRaises(FirstPrompt):
                reasonfirst_cli._run_guided_setup(self.args(), input_fn=stop)
        self.assertEqual(prompts, ['GitLab URL: '])
        self.assertFalse(self.config.exists())

    def test_install_guides_distinguish_development_features_and_upgrade_scope(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('INSTALL.md', 'INSTALL_CN.md'):
            text = (root / 'docs' / filename).read_text(encoding='utf-8')
            for value in ('setup --reuse-existing', 'setup --reconfigure', 'not_inspected',
                          'discovered_legacy', 'setup.yaml', 'v0.5.1', 'sidecar'):
                with self.subTest(filename=filename, value=value):
                    self.assertIn(value, text)
            self.assertNotIn('reasonfirst upgrade apply', text)



if __name__ == '__main__':
    unittest.main()
