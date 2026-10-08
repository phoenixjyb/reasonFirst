#!/usr/bin/env python3
"""CI-only native API fixture, never a maintainer's ReasonFirst job.

Loads exactly one random-label /bin/sleep job in a clean CI user's context, then
unloads that same job. This tests the adapter/comparator using the prepared Python;
it is not an end-to-end ReasonFirst activation or the public command's full path.
Only a classified result is emitted, and success is emitted after cleanup.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

from gitlab_agent.upgrade import loaded_service as loaded


class FixtureError(RuntimeError):
    """Only locally assigned fixed codes may be included in the test report."""


NATIVE_ERRORS = frozenset({
    'unsupported_loaded_service_platform', 'user_domain_required',
    'invalid_job_label', 'native_query_timeout', 'native_output_limit',
    'native_query_failed', 'native_api_unavailable',
    'job_unavailable_or_query_failed', 'native_job_unserializable',
    'invalid_native_job',
})


def _query(label, root):
    try:
        return loaded._query_job(label, root)
    except loaded.LoadedServiceError as exc:
        code = str(exc)
        raise FixtureError(code if code in NATIVE_ERRORS else 'native_query_failed') from None


def native_shape(job, saved):
    """CI-fixture-only shape/occurrence counts, never native keys or values.

    Search the complete returned dictionary rather than guessing alternate field
    names. Only fixed field names, type tags, counts and completion are emitted.
    The compared values come from this synthetic fixture, not real user config.
    A bounded/incomplete walk is explicitly not evidence of absence.
    """
    names = ('Program', 'ProgramArguments', 'WorkingDirectory', 'EnvironmentVariables')
    counts = {'working_directory_key': 0, 'environment_variables_key': 0,
              'fixture_cwd_value': 0, 'fixture_environment_value': 0}
    def tag(value):
        if isinstance(value, dict): return 'dictionary'
        if isinstance(value, list): return 'array'
        if isinstance(value, str): return 'string'
        if type(value) is bool: return 'boolean'
        if type(value) is int: return 'integer'
        if type(value) is float: return 'real'
        if isinstance(value, bytes): return 'data'
        return 'other'
    shape = {
        'scope': 'synthetic-job-response-shape-only',
        'selected_top_level_types': {name: tag(job[name]) if name in job else 'absent' for name in names},
        'top_level_entries': len(job), 'visited_nodes': 0, 'max_depth_seen': 0,
        'occurrences': counts, 'traversal_complete': True,
    }
    cwd = saved['WorkingDirectory']
    env = saved['EnvironmentVariables']['RF_SYNTHETIC_FIXTURE']
    stack = [(job, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 16 or shape['visited_nodes'] >= 4096:
            shape['traversal_complete'] = False
            break
        shape['visited_nodes'] += 1
        shape['max_depth_seen'] = max(shape['max_depth_seen'], depth)
        if isinstance(value, dict):
            # Input bytes are bounded by the production adapter. Also cap queued
            # work before expanding a large container in a synthetic/unit test.
            if len(value) * 2 + len(stack) + shape['visited_nodes'] > 4096:
                shape['traversal_complete'] = False
                break
            for key, item in value.items():
                counts['working_directory_key'] += int(key == 'WorkingDirectory')
                counts['environment_variables_key'] += int(key == 'EnvironmentVariables')
                stack.extend(((key, depth + 1), (item, depth + 1)))
        elif isinstance(value, list):
            if len(value) + len(stack) + shape['visited_nodes'] > 4096:
                shape['traversal_complete'] = False
                break
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            counts['fixture_cwd_value'] += int(value == cwd)
            counts['fixture_environment_value'] += int(value == env)
    return shape


def _run_fixture(report):
    if (sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.getuid() == 0 or os.getuid() != os.geteuid()):
        raise FixtureError('fixture_context_required')
    label = 'com.reasonfirst.fixture.loaded-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='rf-native-job-') as td:
        root = Path(td).resolve()
        env = {'HOME': str(root), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL':'C'}
        def command(*args):
            return subprocess.run(['/bin/launchctl', *args], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=root, env=env, timeout=12, check=False)
        # A collision would be unexpected: never remove or reuse a preexisting job.
        report['stage'] = 'collision_check'
        if command('list',label).returncode == 0:
            raise FixtureError('fixture_label_collision')
        cwd = root / "initial 空间"
        cwd.mkdir()
        changed_cwd = root / 'changed'
        changed_cwd.mkdir()
        plist = root / (label+'.plist')
        original = {'Label': label, 'ProgramArguments': ['/bin/sleep','90'],
                    'WorkingDirectory': str(cwd), 'RunAtLoad': True,
                    'EnvironmentVariables': {'RF_SYNTHETIC_FIXTURE': 'private-fixture'}}
        original_raw = plistlib.dumps(original)
        plist.write_bytes(original_raw); plist.chmod(0o600)
        try:
            report['stage'] = 'load'
            report['load_attempted'] = True
            if command('load',str(plist)).returncode:
                raise FixtureError('fixture_load_failed')
            report['stage'] = 'initial_query'
            deadline = time.monotonic()+10
            job = None
            while time.monotonic()<deadline:
                job = _query(label,root)  # Missing native API is failure, not skip.
                report['actual_loaded_job_queried'] = True
                if job.get('PID'):
                    break
                time.sleep(0.1)
            if not job or not job.get('PID'):
                raise FixtureError('fixture_pid_unobserved')
            report['native_shape'] = native_shape(job, original)
            report['stage'] = 'initial_comparison'
            initial = loaded._compare(original,job)
            report['selected_fields'] = initial['selected_fields']
            report['field_coverage'] = initial['field_coverage']
            report['selected_launch_fields_match'] = initial['selected_launch_fields_match']
            report['activation_blockers'] = loaded._observation_blockers(initial)
            # Approved narrower contract: program/argv must be observed and
            # match. Optional native cwd/environment stay unknown if omitted;
            # when returned they MUST match, never be ignored or filled in.
            if (initial['selected_fields']['Program'] != 'matches'
                    or initial['selected_fields']['ProgramArguments'] != 'matches'
                    or not initial['reported_fields_match']
                    or initial['selected_launch_fields_match'] != all(
                        v == 'matches' for v in initial['selected_fields'].values())
                    or loaded.STARTUP_BLOCKER not in report['activation_blockers']
                    or (initial['field_coverage'] != 'complete' and
                        'loaded_launch_fields_differ_or_not_reported' not in report['activation_blockers'])):
                raise FixtureError('native_fields_incomplete_or_different')
            # Change a field the API actually returns. Reading the new saved
            # file as though it were loaded state must fail this negative test.
            changed = {**original, 'ProgramArguments': ['/bin/sleep', '91'],
                       'WorkingDirectory': str(changed_cwd),
                       'EnvironmentVariables': {'RF_SYNTHETIC_FIXTURE':'changed'}}
            changed_raw=plistlib.dumps(changed)
            plist.write_bytes(changed_raw)
            report['stage'] = 'drift_query'
            second=_query(label,root)
            if second.get('PID')!=job['PID']:
                raise FixtureError('fixture_pid_changed')
            report['stage'] = 'drift_comparison'
            difference=loaded._compare(changed,second)
            report['drift_fields'] = difference['selected_fields']
            expected = {'Program': 'matches', 'ProgramArguments': 'differs'}
            expected.update({name: 'not_reported' if initial['selected_fields'][name] == 'not_reported'
                             else 'differs' for name in ('WorkingDirectory', 'EnvironmentVariables')})
            if (loaded._projection(job) != loaded._projection(second)
                    or difference['selected_fields'] != expected
                    or difference['selected_launch_fields_match']
                    or difference['reported_fields_match']):
                raise FixtureError('saved_loaded_drift_not_detected')
            if plist.read_bytes()!=changed_raw:
                raise FixtureError('saved_registration_changed')
            report['saved_file_drift_detected'] = True
        finally:
            if report['load_attempted']:
                # Keep the original failure as well as a failed cleanup outcome.
                # Do not emit success before this block completes.
                report['cleanup_attempted'] = True
                try:
                    plist.write_bytes(original_raw)
                    cleanup=command('unload',str(plist))
                    absent=command('list',label).returncode != 0
                    report['cleanup_confirmed'] = cleanup.returncode == 0 and absent
                    if not report['cleanup_confirmed']:
                        report['cleanup_error_code'] = 'fixture_cleanup_not_confirmed'
                except Exception:
                    report['cleanup_error_code'] = 'fixture_cleanup_failed'
                if report['cleanup_error_code'] and sys.exc_info()[0] is None:
                    report['stage'] = 'cleanup'
                    raise FixtureError(report['cleanup_error_code'])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--ci-fixture', action='store_true', required=True)
    parser.parse_args(argv)
    report = {
        'operation': 'loaded-job-native-fixture', 'ok': False,
        'observation_contract': loaded.OBSERVATION_CONTRACT,
        'field_coverage': 'not_observed', 'selected_launch_fields_match': False,
        'activation_blockers': [loaded.STARTUP_BLOCKER],
        'managed_startup_confirmation_verified': False,
        'activation_authorized': False, 'ready_for_activation': False,
        'stage': 'eligibility', 'error_code': None,
        'selected_fields': {}, 'drift_fields': {},
        'load_attempted': False, 'cleanup_attempted': False,
        'cleanup_confirmed': False, 'cleanup_error_code': None,
        'actual_loaded_job_queried': False, 'saved_file_drift_detected': False,
        'production_label_used': False, 'reasonfirst_server_started': False,
        'public_cli_full_path_tested': False, 'activation_tested': False,
    }
    try:
        _run_fixture(report)
    except FixtureError as exc:
        report['error_code'] = str(exc)
    except KeyboardInterrupt:
        report['error_code'] = 'fixture_interrupted'
    except Exception:
        # No raw OS/framework/launchctl error or arbitrary data reaches logs.
        report['error_code'] = 'fixture_failed'
    else:
        report['ok'] = True
        report['stage'] = 'complete'
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
