from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import reasonfirst_cli as cli
from gitlab_agent.upgrade import deployment as d


class DeploymentCLITests(unittest.TestCase):
    def invoke(self, argv, *, result=None, system='Linux'):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(Path, 'home', return_value=Path(tmp)), \
             patch.object(d.platform, 'system', return_value=system), \
             patch.object(cli, '_load_settings_clean', side_effect=AssertionError('No config loading')), \
             patch.object(cli, 'build_setup_status', side_effect=AssertionError('Not onboarding/status')), \
             patch.object(cli, '_run_guided_setup', side_effect=AssertionError('No setup')), \
             patch('subprocess.run', side_effect=AssertionError('No service or worker calls')), \
             redirect_stdout(out), redirect_stderr(err):
            if result is None:
                code = cli.main(argv)
            else:
                with patch.object(d, 'run_command', return_value=result) as call:
                    code = cli.main(argv)
                self.call_args = call.call_args
            self.assertEqual(list(Path(tmp).iterdir()), [])
        return code, out.getvalue(), err.getvalue()

    def test_help_and_subcommands_are_available_without_configuration(self):
        parser = cli._build_parser()
        self.assertIn('deployment', parser.format_help())
        for command in ('status', 'plan', 'adopt'):
            args = ['deployment', command, '--json']
            if command == 'adopt':
                args += ['--expect-digest', 'a'*64, '--yes']
            parsed = parser.parse_args(args)
            self.assertEqual(parsed.deployment_command, command)

    def test_unsupported_platform_json_is_single_object_and_nonzero(self):
        for system in ('Linux', 'Windows'):
            for command in ('plan', 'status', 'adopt'):
                with self.subTest(system=system, command=command):
                    argv = ['deployment', command, '--json']
                    if command == 'adopt':
                        argv += ['--expect-digest', 'a'*64, '--yes']
                    code, out, err = self.invoke(argv, system=system)
                    value = json.loads(out)
                    self.assertEqual(code, 1)
                    self.assertFalse(value['ok'])
                    self.assertEqual(value['error_code'], 'unsupported_platform')
                    self.assertEqual(err, '')

    def test_adopt_requires_digest_and_rejects_service_change_options(self):
        for args in (['deployment', 'adopt', '--yes'],
                     ['deployment', 'status', '--activate'],
                     ['deployment', 'adopt', '--expect-digest', 'a'*64, '--restart']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli._build_parser().parse_args(args)
                self.assertNotEqual(error.exception.code, 0)

    def test_dispatch_preserves_explicit_approval_and_digest(self):
        code, out, err = self.invoke(['deployment', 'adopt', '--expect-digest', 'a'*64, '--yes', '--json'],
                                    result={'ok': True, 'operation': 'deployment-adopt', 'record_written': True})
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)['record_written'])
        self.assertEqual(err, '')
        self.assertEqual(self.call_args.args, ('adopt',))
        self.assertEqual(self.call_args.kwargs, {'expect_digest': 'a'*64, 'approved': True})

    def test_no_yes_never_becomes_implicit_approval(self):
        self.invoke(['deployment', 'adopt', '--expect-digest', 'a'*64, '--json'],
                    result={'ok': False, 'error': 'approval required'})
        self.assertFalse(self.call_args.kwargs['approved'])

    def test_human_plan_describes_nonactivation_and_prints_digest(self):
        code, out, err = self.invoke(['deployment', 'plan'], result={
            'ok': True, 'record_status': 'not_recorded', 'plan_digest': 'a'*64,
            'record_written': False, 'proposed_action': 'record-registration-snapshot'})
        self.assertEqual(code, 0)
        self.assertIn('not service activation', out)
        self.assertIn('a'*64, out)
        self.assertIn('No service changed', out)
        self.assertEqual(err, '')

    def test_human_errors_are_nonzero_and_do_not_claim_completion(self):
        code, out, err = self.invoke(['deployment', 'status'])
        self.assertEqual(code, 1)
        self.assertIn('macOS', err)
        self.assertNotIn('No service changed', out)  # no success footer on errors

    def test_docs_distinguish_registry_from_activation_and_released_features(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('DEPLOYMENTS.md', 'DEPLOYMENTS_CN.md'):
            text = (root / 'docs' / filename).read_text(encoding='utf-8')
            for token in ('deployment plan', 'deployment adopt', '--expect-digest', '--yes',
                          'deployment status', '0.5.1', 'not_inspected', 'setup.yaml'):
                self.assertIn(token, text)
        self.assertIn('DEPLOYMENTS.md', (root / 'docs/INSTALL.md').read_text(encoding='utf-8'))
        self.assertIn('DEPLOYMENTS_CN.md', (root / 'docs/INSTALL_CN.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
