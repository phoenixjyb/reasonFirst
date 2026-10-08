"""Regression-test CI diagnostic handling, not native launchd acceptance."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location('fixture_report_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load_script('loaded_service_e2e')
harness = load_script('runtime_e2e')
SECRET = 'private-' + 'diagnostic-value'
FIELDS = ('Program', 'ProgramArguments', 'WorkingDirectory', 'EnvironmentVariables')


def success_report():
    return {
        'operation': 'loaded-job-native-fixture', 'ok': True, 'stage': 'complete',
        'error_code': None, 'selected_fields': {k: 'matches' for k in FIELDS},
        'drift_fields': {k: 'matches' if k == 'Program' else 'differs' for k in FIELDS},
        'observation_contract': 'partial-selected-fields-v1', 'field_coverage': 'complete',
        'selected_launch_fields_match': True,
        'activation_blockers': ['managed_startup_confirmation_not_verified'],
        'managed_startup_confirmation_verified': False,
        'activation_authorized': False, 'ready_for_activation': False,
        'load_attempted': True, 'cleanup_attempted': True, 'cleanup_confirmed': True,
        'cleanup_error_code': None, 'actual_loaded_job_queried': True,
        'saved_file_drift_detected': True, 'production_label_used': False,
        'reasonfirst_server_started': False, 'public_cli_full_path_tested': False,
        'activation_tested': False,
    }


def failure_report():
    value = success_report()
    value.update(ok=False, stage='initial_query', error_code='native_api_unavailable',
                 selected_fields={}, drift_fields={}, actual_loaded_job_queried=False,
                 saved_file_drift_detected=False, field_coverage='not_observed',
                 selected_launch_fields_match=False)
    return value


class FixtureReportingTests(unittest.TestCase):
    def invoke(self, *, native_error=None, missing=None, cleanup_failure=False,
               collision=False, load_failure=False, drift_failure=False,
               mismatch=None, changed_native=None, retained_optional_drift=None):
        calls = []
        original = None
        query_count = 0
        output = io.StringIO()

        def command(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[0], '/bin/launchctl')
            self.assertEqual(set(kwargs['env']), {'HOME', 'PATH', 'LC_ALL'})
            self.assertNotIn('com.reasonfirst.v4-mcp', repr(argv))
            if argv[1] in ('load', 'unload'):
                data = plistlib.loads(Path(argv[2]).read_bytes())
                self.assertTrue(data['Label'].startswith('com.reasonfirst.fixture.loaded-'))
                self.assertEqual(data['ProgramArguments'], ['/bin/sleep', '90'])
                self.assertNotIn('-w', argv)
                # No success must already have been emitted before cleanup.
                if argv[1] == 'unload':
                    self.assertEqual(output.getvalue(), '')
            code = 1 if argv[1] == 'list' else 0
            if collision and len(calls) == 1:
                code = 0
            if cleanup_failure and argv[1] == 'unload':
                code = 1
            if load_failure and argv[1] == 'load':
                code = 1
            return subprocess.CompletedProcess(argv, code, SECRET.encode(), SECRET.encode())

        def query(label, home):
            nonlocal original, query_count
            query_count += 1
            if native_error:
                raise native_error
            if original is None:
                original = plistlib.loads((home / (label + '.plist')).read_bytes())
                original['PID'] = 12345
            value = copy.deepcopy(original)
            if missing:
                for key in ((missing,) if isinstance(missing, str) else missing):
                    value.pop(key, None)
            if mismatch:
                value[mismatch] = {'OTHER': SECRET} if mismatch == 'EnvironmentVariables' else '/other'
            if changed_native and query_count > 1:
                value[changed_native] = 54321 if changed_native == 'PID' else SECRET
            if drift_failure and query_count > 1:
                value = plistlib.loads((home / (label + '.plist')).read_bytes())
                value['PID'] = 12345
            if retained_optional_drift and query_count > 1:
                # Simulate incorrectly filling a previously missing loaded field
                # from the new saved registration; the fixture must reject this.
                value[retained_optional_drift] = plistlib.loads(
                    (home / (label + '.plist')).read_bytes())[retained_optional_drift]
            return value

        with ExitStack() as stack:
            stack.enter_context(patch.object(fixture.sys, 'platform', 'darwin'))
            stack.enter_context(patch.object(fixture.os, 'getuid', return_value=501, create=True))
            stack.enter_context(patch.object(fixture.os, 'geteuid', return_value=501, create=True))
            stack.enter_context(patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}))
            stack.enter_context(patch.object(fixture.subprocess, 'run', side_effect=command))
            stack.enter_context(patch.object(fixture.loaded, '_query_job', side_effect=query))
            stack.enter_context(redirect_stdout(output))
            code = fixture.main(['--ci-fixture'])
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertNotIn(SECRET, output.getvalue())
        report = harness.loaded_fixture_report(output.getvalue().encode())
        self.assertEqual(report['ok'], code == 0)
        return report, calls

    def test_success_requires_drift_acceptance_and_confirmed_cleanup_before_output(self):
        report, calls = self.invoke()
        self.assertTrue(report['ok'])
        self.assertEqual(report['stage'], 'complete')
        self.assertTrue(report['cleanup_confirmed'])
        self.assertTrue(report['saved_file_drift_detected'])
        self.assertEqual([c[1] for c in calls], ['list', 'load', 'unload', 'list'])

    def test_native_failure_is_classified_and_cleanup_still_runs(self):
        report, _ = self.invoke(native_error=fixture.loaded.LoadedServiceError('native_api_unavailable'))
        self.assertFalse(report['ok'])
        self.assertEqual(report['stage'], 'initial_query')
        self.assertEqual(report['error_code'], 'native_api_unavailable')
        self.assertTrue(report['cleanup_confirmed'])

    def test_unreported_config_is_partial_success_not_full_comparison_or_activation(self):
        for key in ('WorkingDirectory', 'EnvironmentVariables'):
            with self.subTest(key=key):
                report, _ = self.invoke(missing=key)
                self.assertTrue(report['ok'])  # Revised observation-only contract.
                self.assertEqual(report['observation_contract'], 'partial-selected-fields-v1')
                self.assertIsNone(report['error_code'])
                self.assertEqual(report['selected_fields'][key], 'not_reported')
                self.assertEqual(report['drift_fields'][key], 'not_reported')
                self.assertEqual(report['field_coverage'], 'partial')
                self.assertFalse(report['selected_launch_fields_match'])  # Original gate stays false.
                self.assertIn('loaded_launch_fields_differ_or_not_reported', report['activation_blockers'])
                self.assertIn('managed_startup_confirmation_not_verified', report['activation_blockers'])
                self.assertFalse(report['activation_authorized'])
                self.assertFalse(report['ready_for_activation'])
                self.assertTrue(report['cleanup_confirmed'])

    def test_failed_cleanup_cannot_follow_a_success_json(self):
        report, _ = self.invoke(cleanup_failure=True)
        self.assertFalse(report['ok'])
        self.assertFalse(report['cleanup_confirmed'])
        self.assertEqual(report['stage'], 'cleanup')
        self.assertEqual(report['error_code'], 'fixture_cleanup_not_confirmed')

    def test_cleanup_error_preserves_original_failure_and_both_outcomes(self):
        report, _ = self.invoke(cleanup_failure=True,
                               native_error=fixture.loaded.LoadedServiceError('invalid_native_job'))
        self.assertEqual(report['error_code'], 'invalid_native_job')
        self.assertEqual(report['cleanup_error_code'], 'fixture_cleanup_not_confirmed')
        self.assertEqual(report['stage'], 'initial_query')

    def test_collision_never_loads_unloads_or_queries_the_existing_job(self):
        report, calls = self.invoke(collision=True)
        self.assertEqual(report['error_code'], 'fixture_label_collision')
        self.assertFalse(report['load_attempted'])
        self.assertFalse(report['cleanup_attempted'])
        self.assertEqual([c[1] for c in calls], ['list'])

    def test_failed_load_does_not_claim_native_observation(self):
        report, _ = self.invoke(load_failure=True)
        self.assertEqual(report['error_code'], 'fixture_load_failed')
        self.assertFalse(report['actual_loaded_job_queried'])
        self.assertTrue(report['cleanup_attempted'])

    def test_drift_acceptance_is_not_relaxed(self):
        report, _ = self.invoke(drift_failure=True)
        self.assertFalse(report['ok'])
        self.assertEqual(report['error_code'], 'saved_loaded_drift_not_detected')
        self.assertFalse(report['saved_file_drift_detected'])

    def test_private_exception_message_never_reaches_report(self):
        for exc in (RuntimeError(SECRET), fixture.loaded.LoadedServiceError(SECRET)):
            with self.subTest(kind=type(exc).__name__):
                report, _ = self.invoke(native_error=exc)
                self.assertFalse(report['ok'])
                self.assertIn(report['error_code'], {'fixture_failed', 'native_query_failed'})

    def test_nonci_context_is_rejected_before_process_or_native_calls(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(fixture.subprocess, 'run', side_effect=AssertionError('No process')), \
             patch.object(fixture.loaded, '_query_job', side_effect=AssertionError('No query')), \
             redirect_stdout(output):
            self.assertEqual(fixture.main(['--ci-fixture']), 1)
        report = harness.loaded_fixture_report(output.getvalue().encode())
        self.assertEqual(report['error_code'], 'fixture_context_required')
        self.assertFalse(report['load_attempted'])


class HarnessReportingTests(unittest.TestCase):
    def test_valid_classified_success_and_failure_are_retained(self):
        for report in (success_report(), failure_report()):
            self.assertEqual(harness.loaded_fixture_report(json.dumps(report).encode()), report)

    def test_unknown_fields_and_raw_values_cannot_escape_schema(self):
        for key, value in (('extra', SECRET), ('stage', SECRET), ('error_code', SECRET),
                           ('cleanup_error_code', SECRET), ('ok', 1), ('activation_tested', True),
                           ('selected_fields', {k: SECRET for k in FIELDS})):
            with self.subTest(key=key):
                raw = json.dumps({**failure_report(), key: value}).encode()
                with self.assertRaises(RuntimeError) as error:
                    harness.loaded_fixture_report(raw)
                self.assertNotIn(SECRET, str(error.exception))

    def test_oversize_duplicate_keys_malformed_and_wrong_shapes_are_rejected(self):
        for raw in (b'x'*8193, b'[]', b'null', b'\xff',
                    b'{"ok":false,' + json.dumps(failure_report()).encode()[1:]):
            with self.subTest(raw_size=len(raw)), self.assertRaises(RuntimeError):
                harness.loaded_fixture_report(raw)

    def test_success_without_cleanup_or_native_assertions_is_rejected(self):
        for key in ('cleanup_confirmed', 'cleanup_attempted', 'saved_file_drift_detected',
                    'actual_loaded_job_queried', 'load_attempted'):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                harness.loaded_fixture_report(json.dumps({**success_report(), key: False}).encode())
        with self.assertRaises(RuntimeError):
            harness.loaded_fixture_report(json.dumps({**success_report(), 'selected_fields': {}}).encode())

    def test_parent_preserves_native_failure_even_with_zero_outer_exit_and_discards_stderr(self):
        for code in (0, 1):
            output = io.StringIO()
            proc = subprocess.CompletedProcess(['fixture'], code, json.dumps(failure_report()).encode(), SECRET.encode())
            with patch.object(harness.subprocess, 'run', return_value=proc), redirect_stdout(output):
                with self.assertRaises(RuntimeError) as error:
                    harness.execute_loaded_fixture(['fixture'], Path('.'), env={})
            self.assertEqual(json.loads(output.getvalue())['error_code'], 'native_api_unavailable')
            self.assertNotIn(SECRET, str(error.exception) + output.getvalue())

    def test_nonzero_exit_cannot_emit_success_report(self):
        output = io.StringIO()
        proc = subprocess.CompletedProcess(['fixture'], 1, json.dumps(success_report()).encode(), SECRET.encode())
        with patch.object(harness.subprocess, 'run', return_value=proc), redirect_stdout(output):
            with self.assertRaises(RuntimeError):
                harness.execute_loaded_fixture(['fixture'], Path('.'), env={})
        self.assertEqual(output.getvalue(), '')

    def test_parent_success_uses_the_supplied_prepared_python_and_exact_command(self):
        argv = ['/fixture/prepared/bin/python', '-I', '-B', 'fixture.py', '--ci-fixture']
        proc = subprocess.CompletedProcess(argv, 0, json.dumps(success_report()).encode(), b'')
        with patch.object(harness.subprocess, 'run', return_value=proc) as call, redirect_stdout(io.StringIO()):
            self.assertIs(harness.execute_loaded_fixture(argv, Path('/fixture'), env={'GITHUB_ACTIONS': 'true'}), proc)
        self.assertEqual(call.call_args.args, (argv,))
        self.assertEqual(call.call_args.kwargs['env'], {'GITHUB_ACTIONS': 'true'})
        self.assertEqual(call.call_args.kwargs['timeout'], 240)

    def test_launch_and_timeout_errors_never_echo_raw_arguments_or_output(self):
        for exc in (OSError(SECRET), subprocess.TimeoutExpired([SECRET], 240, output=SECRET)):
            with patch.object(harness.subprocess, 'run', side_effect=exc):
                with self.assertRaises(RuntimeError) as error:
                    harness.execute_loaded_fixture(['fixture'], Path('.'), env={})
            self.assertNotIn(SECRET, str(error.exception))


if __name__ == '__main__':
    unittest.main()
