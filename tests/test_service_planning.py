from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import builtins
import json
import os
from pathlib import Path
import plistlib
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import reasonfirst_cli
from gitlab_agent.deployment_inventory import LABEL
from gitlab_agent.static_service_files import BoundedRead
from gitlab_agent.service_planning import (
    MAX_STATE_BYTES, Observation, ServiceRole, build_service_plan,
    collect_service_evidence, inspect_setup_state, service_status,
)


class ServicePlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / '.config/reasonfirst/setup.yaml'
        self.secret = 'sk-' + 'fabricated' * 4

    def write_state(self, text):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(text, encoding='utf-8')
        self.state.chmod(0o600)

    def collect(self, system='Darwin'):
        return collect_service_evidence(home=self.home, system_name=system)

    def snapshot(self):
        result = {}
        for path in [self.home, *self.home.rglob('*')]:
            info = path.lstat()
            result[str(path.relative_to(self.home))] = (
                info.st_mode, info.st_uid, info.st_gid, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns,
                path.read_bytes() if stat.S_ISREG(info.st_mode) else None,
            )
        return result

    @contextmanager
    def traps(self):
        attempts = []
        native_open = os.open
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        def guarded_open(path, flags, *args, **kwargs):
            if flags & write_flags:
                attempts.append('os.open(write)')
                raise AssertionError('forbidden side effect: os.open(write)')
            return native_open(path, flags, *args, **kwargs)
        native_builtin_open, native_io_open = builtins.open, io.open
        def guarded_file_open(native, label):
            def open_file(path, mode='r', *args, **kwargs):
                if any(flag in mode for flag in 'wax+'):
                    attempts.append(label + '(write)')
                    raise AssertionError('forbidden side effect: ' + label + '(write)')
                return native(path, mode, *args, **kwargs)
            return open_file
        def trap(target):
            def attempted(*args, **kwargs):
                attempts.append(target)
                raise AssertionError('forbidden side effect: ' + target)
            return attempted
        with ExitStack() as stack:
            stack.enter_context(patch('builtins.open', new=guarded_file_open(native_builtin_open, 'builtins.open')))
            stack.enter_context(patch('io.open', new=guarded_file_open(native_io_open, 'io.open')))
            for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket',
                           'socket.create_connection', 'webbrowser.open',
                           'gitlab_agent.reasonfirst_cli.AgentSettings.load',
                           'gitlab_agent.reasonfirst_cli.build_setup_status',
                           'gitlab_agent.reasonfirst_cli.detect_workers',
                           'gitlab_agent.reasonfirst_cli.load_setup_state',
                           'gitlab_agent.reasonfirst_cli.save_setup_state',
                           'pathlib.Path.mkdir', 'pathlib.Path.chmod',
                           'pathlib.Path.write_text', 'pathlib.Path.write_bytes',
                           'os.mkdir', 'os.chmod', 'os.replace', 'os.rename', 'os.unlink',
                           'os.write', 'os.putenv'):
                _, attribute = target.rsplit('.', 1)
                if not target.startswith('os.') or hasattr(os, attribute):
                    stack.enter_context(patch(target, new=trap(target)))
            for target in ('os.fchmod', 'os.chown'):
                _, attribute = target.rsplit('.', 1)
                if hasattr(os, attribute):
                    stack.enter_context(patch(target, new=trap(target)))
            stack.enter_context(patch('os.open', new=guarded_open))
            yield attempts
        if attempts:
            raise AssertionError('forbidden side effect attempts observed: ' + ', '.join(attempts))

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch('pathlib.Path.home', return_value=self.home), \
             redirect_stdout(output), redirect_stderr(errors):
            code = reasonfirst_cli.main(['service', *args, '--json'])
        self.assertEqual(errors.getvalue(), '')
        return code, json.loads(output.getvalue())

    def test_empty_home_status_and_plans_do_not_create_state(self):
        before = self.snapshot()
        with self.traps():
            for _ in range(2):
                code, status = self.cli('status')
                self.assertEqual(code, 0)
                self.assertIsNone(status['mode'])
                self.assertEqual(status['mode_source'], 'unknown')
                self.assertEqual(status['setup_state']['status'], 'missing' if os.name == 'posix' else 'unsupported')
                self.assertFalse(status['ready'])
                self.assertFalse(status['can_execute'])
                for observation in status['observations'].values():
                    self.assertEqual(observation['status'], 'not_checked')
                    self.assertIsNone(observation['observed_at'])
                code, plan = self.cli('plan', '--autostart', 'login')
                self.assertEqual(code, 1)
                self.assertEqual(plan['plan_status'], 'blocked')
                self.assertFalse(plan['safe_to_apply'])
        self.assertEqual(before, self.snapshot())

    def test_all_supported_commands_are_repeatable_non_mutating_and_redacted(self):
        self.write_state('mode: standard\ntunnel_runtime: ' + self.secret + '\n'
                         'tunnel_id: arbitrary-record\nconfig_file: ' + self.secret + '\n'
                         'last_verified_at: ' + self.secret + '\n')
        before = self.snapshot()
        with self.traps():
            for args in (('status',), ('plan', '--autostart', 'login'), ('repair', '--dry-run')):
                first = self.cli(*args)
                self.assertEqual(first, self.cli(*args))
                code, payload = first
                self.assertNotIn('error', payload)
                self.assertFalse(payload['mutating'])
                self.assertFalse(payload['can_execute'])
                if args[0] == 'status':
                    self.assertEqual(code, 0)
                    self.assertEqual(payload['command'], 'service-status')
                    self.assertIn('observations', payload)
                else:
                    self.assertEqual(payload['plan_status'], 'blocked')
                    self.assertEqual(payload['command'], 'service-plan' if args[0] == 'plan' else 'service-repair')
                    self.assertIn('evidence', payload)
                    self.assertEqual(code, 1)
                    self.assertTrue(payload['blockers'])
                    self.assertEqual(payload['evidence']['command'], 'service-status')
                    self.assertNotIn('error', payload['evidence'])
                    self.assertEqual(payload['evidence']['setup_state']['status'],
                                     'recorded' if os.name == 'posix' else 'unsupported')
                    self.assertEqual(payload['evidence']['mode'], 'standard' if os.name == 'posix' else None)
                    if args[0] == 'repair':
                        self.assertEqual(code, 1)
                        self.assertIs(payload['dry_run'], True)
                self.assertNotIn(self.secret, repr(first))
                self.assertFalse(first[1]['changes']['credentials'])
                self.assertFalse(first[1]['changes']['services'])
                self.assertFalse(first[1]['changes']['configuration'])
        self.assertEqual(before, self.snapshot())

    def test_traps_surface_caught_write_process_and_socket_attempts(self):
        cases = (
            ('pathlib.Path.write_text', lambda: self.home.joinpath('attempted').write_text('x')),
            ('subprocess.run', lambda: __import__('subprocess').run(['definitely-not-executed'])),
            ('socket.socket', lambda: socket.socket()),
        )
        for target, attempt in cases:
            with self.subTest(target=target):
                with patch('gitlab_agent.service_cli.collect_service_evidence', side_effect=lambda: attempt()):
                    with self.assertRaisesRegex(AssertionError, 'forbidden side effect attempts observed'):
                        with self.traps():
                            code, payload = self.cli('status')
                self.assertEqual(code, 1)
                self.assertEqual(payload['error'], 'service_inspection_failed')
                self.assertFalse(self.home.joinpath('attempted').exists())

    def test_traps_ledger_direct_builtin_io_and_os_write_open_attempts(self):
        cases = (
            ('builtins.open', 'builtin-open-attempt',
             lambda path: builtins.open(path, 'w', encoding='utf-8')),
            ('io.open', 'io-open-attempt', lambda path: io.open(path, 'wb')),
            ('os.open', 'os-open-attempt',
             lambda path: os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)),
        )
        for target, filename, open_for_write in cases:
            with self.subTest(target=target):
                path = self.home / filename
                with patch('gitlab_agent.service_cli.collect_service_evidence',
                           side_effect=lambda path=path, open_for_write=open_for_write: open_for_write(path)):
                    with self.assertRaisesRegex(AssertionError, 'forbidden side effect attempts observed'):
                        with self.traps() as attempts:
                            code, payload = self.cli('status')
                self.assertTrue(any(target in attempt for attempt in attempts), attempts)
                self.assertEqual(code, 1)
                self.assertEqual(payload['error'], 'service_inspection_failed')
                self.assertFalse(path.exists())

    def test_trap_allows_reads_and_status_plan_repair_when_optional_os_apis_are_absent(self):
        read_file = self.home / 'read-only-fixture'
        read_file.write_text('readable', encoding='utf-8')
        saved = {name: getattr(os, name) for name in ('fchmod', 'chown') if hasattr(os, name)}
        try:
            for name in saved:
                delattr(os, name)
            with self.traps():
                with builtins.open(read_file, 'r', encoding='utf-8') as handle:
                    self.assertEqual(handle.read(), 'readable')
                with io.open(read_file, 'rb') as handle:
                    self.assertEqual(handle.read(), b'readable')
                self.assertFalse(hasattr(os, 'fchmod'))
                self.assertFalse(hasattr(os, 'chown'))
                for args, expected_code in ((('status',), 0),
                                            (('plan', '--autostart', 'login'), 1),
                                            (('repair', '--dry-run'), 1)):
                    code, payload = self.cli(*args)
                    self.assertEqual(code, expected_code)
                    self.assertNotIn('error', payload)
                    self.assertFalse(hasattr(os, 'fchmod'))
                    self.assertFalse(hasattr(os, 'chown'))
        finally:
            for name, value in saved.items():
                setattr(os, name, value)

    def test_argument_errors_and_unimplemented_operations_are_redacted_before_collection(self):
        with patch('gitlab_agent.service_cli.collect_service_evidence') as collect:
            for args in (('repair',), ('install', self.secret), ('start',), ('stop',), ('disable',),
                         ('plan', '--autostart', 'boot'), ('plan', '--autostart', 'unattended'),
                         ('plan',), ('status', '--json=' + self.secret),
                         ('status', '--unknown', self.secret), (self.secret,)):
                with self.subTest(args=args):
                    collect.reset_mock()
                    code, payload = self.cli(*args)
                    collect.assert_not_called()
                    self.assertEqual(code, 2)
                    self.assertNotIn(self.secret, repr(payload))
                    self.assertFalse(payload['can_execute'])

    def test_parser_and_collection_exception_text_is_not_emitted(self):
        with patch('gitlab_agent.service_cli.collect_service_evidence', side_effect=OSError(self.secret)):
            code, payload = self.cli('status')
        self.assertEqual(code, 1)
        self.assertNotIn(self.secret, repr(payload))

    def test_collection_value_error_is_a_redacted_inspection_failure(self):
        with patch('gitlab_agent.service_cli.collect_service_evidence', side_effect=ValueError(self.secret)):
            code, payload = self.cli('status')
        self.assertEqual(code, 1)
        self.assertEqual(payload['error'], 'service_inspection_failed')
        self.assertNotIn(self.secret, repr(payload))

    def test_planning_value_error_is_a_redacted_inspection_failure(self):
        with patch('gitlab_agent.service_cli.collect_service_evidence', return_value=self.collect()), \
             patch('gitlab_agent.service_cli.build_service_plan', side_effect=ValueError(self.secret)):
            code, payload = self.cli('plan', '--autostart', 'login')
        self.assertEqual(code, 1)
        self.assertEqual(payload['error'], 'service_inspection_failed')
        self.assertNotIn(self.secret, repr(payload))

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_malformed_empty_oversized_ambiguous_and_control_state(self):
        for raw in ('', self.secret, 'version: ' + self.secret,
                    'completed_phases: [' + self.secret + ']', 'mode: standard\nmode: full-chat',
                    'mode: standard\nunknown_key: ' + self.secret,
                    'tunnel_runtime: "hello\\u001bworld"', 'mode: [standard]',
                    'mode: &alias standard\ntunnel_runtime: *alias',
                    'mode: standard\n---\nmode: full-chat',
                    'x' * (MAX_STATE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.write_state(raw)
                state, observation, explicit = inspect_setup_state(self.home)
                self.assertIsNone(state)
                self.assertEqual(observation.status, 'invalid')
                self.assertFalse(explicit)
                self.assertNotIn(self.secret, repr(service_status(self.collect())))

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_setup_missing_is_not_absent_definition(self):
        path = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')
        path.parent.mkdir(parents=True)
        root = self.home / '.local/share/reasonfirst/v4-service'
        path.write_bytes(plistlib.dumps({'Label': LABEL, 'ProgramArguments': [str(
            root / 'tools/codex_web_bridge/run_reasonfirst.sh')], 'WorkingDirectory': str(root)}))
        path.chmod(0o600)
        result = self.collect()
        self.assertEqual(result.setup.status, 'missing')
        self.assertTrue(result.inventory['legacy_evidence'])
        self.assertEqual(result.roles[0].transport.value, 'streamable-http')
        self.assertIsNone(result.inventory['running_service_version'])
        plan = build_service_plan(result)
        self.assertIn('potential_owner_overlap_requires_handover_evidence', plan['blockers'])
        self.assertEqual(plan['preserved_topology'][0]['supervision'], 'keep_bridge_and_tunnel_owners_separate')

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_mode_default_is_not_recorded_deployment_identity(self):
        self.write_state('version: 1\n')
        evidence = self.collect()
        self.assertIsNone(evidence.mode)
        self.assertIn('deployment_mode_unknown', build_service_plan(evidence)['blockers'])

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_recorded_readonly_local_modes_do_not_acquire_bridge_capabilities(self):
        for mode in ('standard', 'cli-only'):
            self.write_state('mode: ' + mode + '\ntunnel_runtime: read-record\ntunnel_id: recorded\n')
            with self.traps():
                code, plan = self.cli('plan', '--autostart', 'login')
            self.assertEqual(code, 1)
            self.assertEqual(plan['mode'], mode)
            self.assertFalse(plan['enable_bridge'])
            self.assertFalse(plan['start_coding_worker'])
            self.assertEqual(plan['preserved_topology'][0]['role'], 'read')
            self.assertEqual(plan['preserved_topology'][0]['transport']['status'], 'unknown')

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_two_records_are_hints_not_two_running_instances(self):
        self.write_state('mode: full-chat\ntunnel_runtime: one\ntunnel_id: one\n'
                         'bridge_runtime: two\nbridge_tunnel_id: two\n')
        evidence = self.collect()
        self.assertEqual([role.role for role in evidence.roles], ['read', 'bridge'])
        plan = build_service_plan(evidence)
        self.assertIn('potential_owner_overlap_requires_handover_evidence', plan['blockers'])
        self.assertEqual(plan['observed_runtime_owner'], 'unobserved')
        self.assertEqual(plan['preserved_topology_scope'], 'independent_evidence_not_runtime_instances')
        self.assertEqual(service_status(evidence)['observations']['process_identity']['status'], 'not_checked')

    def test_pure_planner_preserves_explicit_stdio_upstream_child_owner(self):
        evidence = replace(self.collect(), mode='standard', roles=(ServiceRole(
            'read', Observation('confirmed', 'reviewed_fixture', 'synthetic_only', 'stdio'),
            'recorded_not_verified', 'recorded_not_verified'),))
        with patch('os.open', side_effect=AssertionError('Planner must be pure')), \
             patch.object(Path, 'lstat', side_effect=AssertionError('No metadata in planner')):
            plan = build_service_plan(evidence)
            self.assertEqual(plan, build_service_plan(evidence))
        self.assertEqual(plan['preserved_topology'][0]['mcp_child_owner'], 'upstream_tunnel_runtime')
        self.assertIn('no_extra_mcp_supervisor', plan['preserved_topology'][0]['supervision'])
        self.assertFalse(plan['safe_to_apply'])

    @unittest.skipUnless(os.name == "posix", "POSIX reader; other platforms report unsupported")
    def test_empty_and_unclassified_bridge_definition_remain_visible(self):
        path = self.home / 'Library/LaunchAgents' / (LABEL + '.plist')
        path.parent.mkdir(parents=True)
        for raw, status in ((b'', 'invalid'), (plistlib.dumps({
                'Label': LABEL, 'ProgramArguments': ['unclassified-' + self.secret]}),
                'unsupported_layout')):
            path.write_bytes(raw)
            path.chmod(0o600)
            evidence = self.collect()
            self.assertEqual(evidence.inventory['registration']['status'], status)
            self.assertEqual(evidence.roles[0].role, 'bridge')
            self.assertEqual(evidence.roles[0].transport.status, 'unknown')
            self.assertNotIn(self.secret, repr(service_status(evidence)))
            self.assertFalse(build_service_plan(evidence)['safe_to_apply'])

    def test_recorded_mode_conflict_blocks_adoption(self):
        evidence = replace(self.collect(), mode='cli-only', roles=(ServiceRole(
            'bridge', Observation('recognized', 'fixture', 'static', 'streamable-http')),))
        plan = build_service_plan(evidence)
        self.assertIn('recorded_mode_conflicts_with_bridge_evidence', plan['blockers'])
        self.assertFalse(plan['enable_bridge'])

    def test_unavailable_state_guarantees_remain_explicit(self):
        with patch('gitlab_agent.service_planning.read_bounded_regular',
                   return_value=BoundedRead('unsupported', reason='no_follow_open_unavailable')):
            evidence = self.collect('Windows')
        self.assertEqual(evidence.setup.status, 'unsupported')
        self.assertEqual(evidence.setup.value, {'reader_reason': 'no_follow_open_unavailable'})
        self.assertIsNone(evidence.mode)
        self.assertIn('setup_state_unsupported', build_service_plan(evidence)['blockers'])

    def test_adapter_targets_do_not_claim_native_inspection(self):
        for system, target in (('Linux', 'systemd_user_service'), ('Windows', 'user_logon_scheduled_task'),
                               ('Darwin', 'launchd_user_launchagent'), ('Other', None)):
            evidence = self.collect(system)
            status = service_status(evidence)
            self.assertEqual(status['target']['manager'], target)
            self.assertFalse(status['target']['implemented'])
            self.assertIn(status['target']['native_observation'], ('not_inspected', 'unsupported'))
            self.assertFalse(build_service_plan(evidence)['safe_to_apply'])

    @unittest.skipUnless(os.name == "posix", "Secure descriptor reader requires POSIX; Windows reports unsupported")
    def test_setup_symlink_directory_fifo_and_read_failures(self):
        self.state.parent.mkdir(parents=True)
        try:
            self.state.symlink_to(self.home / 'missing-target')
        except OSError:
            self.skipTest('Host cannot create unprivileged symlinks')
        self.assertEqual(inspect_setup_state(self.home)[1].status, 'unsupported')
        self.state.unlink()
        self.state.mkdir()
        self.assertEqual(inspect_setup_state(self.home)[1].status, 'unsupported')
        self.state.rmdir()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.state)
            native_open = os.open
            def no_fifo_open(path, *args, **kwargs):
                if path == self.state.name:
                    raise AssertionError('No FIFO open')
                return native_open(path, *args, **kwargs)
            with patch('os.open', side_effect=no_fifo_open):
                self.assertEqual(inspect_setup_state(self.home)[1].status, 'unsupported')
            self.state.unlink()
        self.write_state('mode: standard')
        with patch('os.open', side_effect=PermissionError(self.secret)):
            self.assertEqual(inspect_setup_state(self.home)[1].status, 'unreadable')


if __name__ == '__main__':
    unittest.main()
