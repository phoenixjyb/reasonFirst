#!/usr/bin/env python3
"""Disposable native runtime-staging acceptance, not a maintainer-machine updater.

Only this CI harness acquires dependencies online. Production preparation is
then offline and exact-hashed. Invoked by the existing install_e2e harness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile

from gitlab_agent.upgrade import deployment as d, pairing, runtime as r, launch_review



def cli_snapshot(root):
    """Observe the disposable uv CLI fixture, including legitimate cache hardlinks.

    This is not the production prepared-runtime trust validator. Never chmod,
    unlink, copy, or relink the fixture just to obtain an acceptable snapshot.
    """
    rows = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(base) / name
            info = path.lstat()
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                rows.append([rel, 'link', os.readlink(path)])
            elif stat.S_ISDIR(info.st_mode):
                rows.append([rel, 'dir', stat.S_IMODE(info.st_mode)])
            elif stat.S_ISREG(info.st_mode):
                rows.append([rel, 'file', stat.S_IMODE(info.st_mode), info.st_nlink,
                             hashlib.sha256(path.read_bytes()).hexdigest()])
            else:
                raise RuntimeError('Unexpected disposable CLI fixture entry')
    return sorted(rows)


def execute(argv, cwd, *, env=None, allow_failure=False):
    proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=240, check=False)
    if proc.returncode and not allow_failure:
        raise RuntimeError('Disposable runtime-E2E command failed: ' + str(proc.returncode))
    return proc


def execute_startup_fixture(argv, cwd, *, env=None):
    """Print only the bounded fixture schema, including classified failures."""
    proc = execute(argv, cwd, env=env, allow_failure=True)
    flags = {'ok', 'supervisor_import_origins_verified', 'child_import_origins_verified',
             'read_only_catalog_verified', 'full_chat_catalog_verified',
             'occupied_listener_preserved', 'decoy_listener_preserved',
             'attempt_cleanup_confirmed', 'runtime_unchanged',
             'unsupported_before_side_effects', 'tool_catalog_only',
             'tool_calls_exercised', 'working_service_touched', 'activation_tested'}
    keys = flags | {'operation', 'supervisor_route', 'stage', 'error_code', 'cleanup_error_code'}
    stages = {'context', 'initial_status', 'read-only', 'full-chat', 'occupied_listener',
              'decoy_listener', 'final_status', 'complete'}
    errors = {'fixture_context_required', 'supervisor_origin_mismatch', 'runtime_not_prepared',
              'unexpected_probe_result', 'probe_cleanup_unconfirmed', 'runtime_changed',
              'listener_fixture_failed', 'fixture_cleanup_failed', 'unsupported_boundary_failed',
              'fixture_interrupted', 'fixture_failed'}
    try:
        if len(proc.stdout) > 8192:
            raise ValueError
        report = json.loads(proc.stdout, object_pairs_hook=r.unique)
        if (type(report) is not dict or set(report) != keys
                or report['operation'] != 'managed-startup-native-acceptance'
                or report['supervisor_route'] not in {'clean-wheel', 'prepared-runtime', 'unsupported'}
                or report['stage'] not in stages
                or any(type(report[key]) is not bool for key in flags)
                or any(report[key] is not None and report[key] not in errors
                       for key in ('error_code', 'cleanup_error_code'))
                or report['tool_catalog_only'] is not True
                or any(report[key] for key in ('tool_calls_exercised', 'working_service_touched', 'activation_tested'))):
            raise ValueError
        if report['ok']:
            if (report['stage'] != 'complete' or report['error_code'] is not None
                    or report['cleanup_error_code'] is not None or proc.returncode != 0):
                raise ValueError
            if report['supervisor_route'] == 'unsupported':
                if (report['unsupported_before_side_effects'] is not True
                        or any(report[key] for key in flags - {
                            'ok', 'unsupported_before_side_effects', 'tool_catalog_only'})):
                    raise ValueError
            elif (report['unsupported_before_side_effects'] is not False
                  or not all(report[key] for key in (
                      'supervisor_import_origins_verified', 'child_import_origins_verified',
                      'read_only_catalog_verified', 'full_chat_catalog_verified',
                      'occupied_listener_preserved', 'decoy_listener_preserved',
                      'attempt_cleanup_confirmed', 'runtime_unchanged'))):
                raise ValueError
        elif report['error_code'] is None or proc.returncode == 0:
            raise ValueError
    except Exception:
        raise RuntimeError('Managed startup fixture report invalid; child output withheld.') from None
    print(json.dumps(report, sort_keys=True), flush=True)
    if not report['ok']:
        raise RuntimeError('Managed startup fixture failed; see classified report.')
    return report



def validate_native_shape(shape):
    """Accept only a fixed, bounded CI-fixture shape report; no raw names/values."""
    keys = {'scope', 'selected_top_level_types', 'top_level_entries', 'visited_nodes',
            'max_depth_seen', 'occurrences', 'traversal_complete'}
    fields = {'Program', 'ProgramArguments', 'WorkingDirectory', 'EnvironmentVariables'}
    counters = {'working_directory_key', 'environment_variables_key',
                'fixture_cwd_value', 'fixture_environment_value'}
    tags = {'absent', 'dictionary', 'array', 'string', 'boolean', 'integer', 'real', 'data', 'other'}
    if not isinstance(shape, dict) or set(shape) != keys:
        raise ValueError
    if shape['scope'] != 'synthetic-job-response-shape-only' or type(shape['traversal_complete']) is not bool:
        raise ValueError
    types = shape['selected_top_level_types']
    occurrences = shape['occurrences']
    if not isinstance(types, dict) or set(types) != fields or any(t not in tags for t in types.values()):
        raise ValueError
    if not isinstance(occurrences, dict) or set(occurrences) != counters:
        raise ValueError
    for key, bound in (('top_level_entries', 262144), ('visited_nodes', 4096), ('max_depth_seen', 16)):
        value = shape[key]
        if type(value) is not int or not 0 <= value <= bound:
            raise ValueError
    if shape['visited_nodes'] < 1:
        raise ValueError
    if any(type(v) is not int or not 0 <= v <= 4096 for v in occurrences.values()):
        raise ValueError


def loaded_fixture_report(raw):
    """Whitelist bounded test diagnostics; never print a child's raw streams."""
    stages = {'eligibility', 'collision_check', 'load', 'initial_query',
              'initial_comparison', 'drift_query', 'drift_comparison', 'cleanup', 'complete'}
    codes = {'fixture_context_required', 'fixture_label_collision', 'fixture_load_failed',
             'fixture_pid_unobserved', 'native_fields_incomplete_or_different',
             'fixture_pid_changed', 'saved_loaded_drift_not_detected', 'saved_registration_changed',
             'fixture_cleanup_not_confirmed', 'fixture_cleanup_failed', 'fixture_interrupted',
             'fixture_failed', 'unsupported_loaded_service_platform', 'user_domain_required',
             'invalid_job_label', 'native_query_timeout', 'native_output_limit',
             'native_query_failed', 'native_api_unavailable', 'job_unavailable_or_query_failed',
             'native_job_unserializable', 'invalid_native_job'}
    flags = {'ok', 'load_attempted', 'cleanup_attempted', 'cleanup_confirmed',
             'actual_loaded_job_queried', 'saved_file_drift_detected', 'production_label_used',
             'reasonfirst_server_started', 'public_cli_full_path_tested', 'activation_tested',
             'selected_launch_fields_match', 'managed_startup_confirmation_verified',
             'activation_authorized', 'ready_for_activation'}
    fields = {'Program', 'ProgramArguments', 'WorkingDirectory', 'EnvironmentVariables'}
    expected = flags | {'operation', 'stage', 'error_code', 'cleanup_error_code',
                        'selected_fields', 'drift_fields', 'observation_contract',
                        'field_coverage', 'activation_blockers'}
    try:
        if not isinstance(raw, bytes) or len(raw) > 8192:
            raise ValueError
        report = json.loads(raw, object_pairs_hook=r.unique)
        if not isinstance(report, dict) or set(report) not in (expected, expected | {'native_shape'}):
            raise ValueError
        if 'native_shape' in report:
            validate_native_shape(report['native_shape'])
        if report['operation'] != 'loaded-job-native-fixture' or report['stage'] not in stages:
            raise ValueError
        if any(type(report[key]) is not bool for key in flags):
            raise ValueError
        for key in ('error_code', 'cleanup_error_code'):
            if report[key] is not None and report[key] not in codes:
                raise ValueError
        for key in ('selected_fields', 'drift_fields'):
            value = report[key]
            if not isinstance(value, dict) or (value and set(value) != fields):
                raise ValueError
            if any(item not in {'matches', 'differs', 'not_reported'} for item in value.values()):
                raise ValueError
        if any(report[key] for key in ('production_label_used', 'reasonfirst_server_started',
                                        'public_cli_full_path_tested', 'activation_tested',
                                        'managed_startup_confirmation_verified',
                                        'activation_authorized', 'ready_for_activation')):
            raise ValueError
        if report['observation_contract'] != 'partial-selected-fields-v1':
            raise ValueError
        selected = report['selected_fields']
        missing = [name for name, value in selected.items() if value == 'not_reported']
        coverage = ('not_observed' if not selected else 'none' if len(missing) == 4
                    else 'partial' if missing else 'complete')
        if report['field_coverage'] != coverage:
            raise ValueError
        if report['selected_launch_fields_match'] != (bool(selected) and all(v == 'matches' for v in selected.values())):
            raise ValueError
        blockers = report['activation_blockers']
        permitted = {'managed_startup_confirmation_not_verified',
                     'loaded_launch_fields_differ_or_not_reported', 'no_running_pid_reported'}
        if (not isinstance(blockers, list) or not all(isinstance(v, str) for v in blockers)
                or len(set(blockers)) != len(blockers) or set(blockers) - permitted
                or 'managed_startup_confirmation_not_verified' not in blockers
                or (selected and not report['selected_launch_fields_match']
                    and 'loaded_launch_fields_differ_or_not_reported' not in blockers)):
            raise ValueError
        if report['ok']:
            if (report['stage'] != 'complete' or report['error_code'] is not None
                    or report['cleanup_error_code'] is not None
                    or not all(report[key] for key in ('load_attempted', 'cleanup_attempted',
                        'cleanup_confirmed', 'actual_loaded_job_queried', 'saved_file_drift_detected'))
                    or set(selected) != fields
                    or selected['Program'] != 'matches' or selected['ProgramArguments'] != 'matches'
                    or any(v == 'differs' for v in selected.values())
                    or 'no_running_pid_reported' in blockers
                    or report['drift_fields'] != {
                        'Program': 'matches', 'ProgramArguments': 'differs',
                        **{name: 'not_reported' if selected[name] == 'not_reported' else 'differs'
                           for name in ('WorkingDirectory', 'EnvironmentVariables')}}):
                raise ValueError
        elif report['error_code'] is None:
            raise ValueError
        return report
    except Exception:
        raise RuntimeError('Native fixture report invalid; child output withheld.') from None


def execute_loaded_fixture(argv, cwd, *, env):
    """Preserve classified native-test failure instead of losing it in execute()."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=240, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Native fixture process failed; child output withheld.') from None
    report = loaded_fixture_report(proc.stdout)
    if report['ok'] and proc.returncode != 0:
        raise RuntimeError('Native fixture exit status conflicts with success report; child output withheld.')
    print(json.dumps(report, sort_keys=True), flush=True)
    if proc.returncode != 0 or not report['ok']:
        raise RuntimeError('Native loaded-job fixture failed; see classified report.')
    return proc

def launch_acceptance(home, runtime_id, python, source, pair_digest):
    """Native disposable input files + prepared-runtime CLI; never load launchd."""
    folder = home / ".local/share/reasonfirst/v4-service/tools/codex_web_bridge"
    folder.mkdir(parents=True)
    for name in launch_review.KNOWN_LEGACY_BLOBS:
        item = folder / name
        item.write_bytes((source / "tools/reasonfirst_v4_0_3" / name).read_bytes())
        item.chmod(0o700 if name.endswith(".sh") else 0o600)

    def check(action, expected=None):
        argv = [str(python), "-I", "-B", "-m", "gitlab_agent.upgrade.runtime",
                action, "--runtime-id", runtime_id,
                "--expect-pairing-digest", pair_digest, "--json"]
        if expected:
            argv += ["--expect-digest", expected]
        proc = subprocess.run(argv, cwd=home, env=r.child_env(home),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120, check=False)
        result = json.loads(proc.stdout)
        assert proc.returncode == (0 if result["ok"] else 1), result
        assert result["ready_for_activation"] is False
        assert result["compatibility_verified"] is False
        assert result["commands_executed"] is False
        return result

    before = cli_snapshot(home)
    planned = check("launch-plan")
    assert planned["ok"] and planned["launcher_source_verified"], planned
    assert "legacy_control_required_but_target_has_no_control" in planned["blockers"], planned
    assert planned["identity"]["evidence"]["saved_policy"]["mode"] == "full-chat"
    checked = check("launch-check", planned["plan_digest"])
    assert checked["ok"] and checked["review_digest_matches"]
    assert cli_snapshot(home) == before, "Launch inspection mutated fixture"
    changed = folder / "set_local_no_proxy.sh"
    original = changed.read_bytes()
    changed.write_bytes(original + b"\n# synthetic drift\n")
    refused = check("launch-check", planned["plan_digest"])
    assert refused["ok"] is False and refused["error_code"] == "known_launcher_modified", refused
    changed.write_bytes(original)
    print(json.dumps({"operation":"launch-review-native-acceptance","ok":True,
                      "verified_legacy_sources":4,"verified_target_sources":2,
                      "control_mismatch_blocked":True,"drift_refused":True,
                      "actual_service_started":False,"activation_tested":False}, sort_keys=True))


def pairing_acceptance(home, runtime_id, python, source):
    """Only called with this harness's disposable HOME; no actual LaunchAgent."""
    env = r.child_env(home)
    def inspect(action, *, digest=None, error=None):
        argv = [str(python), '-I', '-B', '-m', 'gitlab_agent.upgrade.runtime',
                action, '--runtime-id', runtime_id, '--json']
        if digest is not None:
            argv += ['--expect-digest', digest]
        proc = subprocess.run(argv, cwd=home, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=120, check=False)
        out = json.loads(proc.stdout)
        assert proc.returncode == (1 if error else 0), out
        assert out['ok'] is (error is None), out
        assert out['ready_for_activation'] is False and out['compatibility_verified'] is False
        if error:
            assert out['error_code'] == error, out
            assert 'plan_digest' not in out
        return out
    if platform.system() != 'Darwin':
        inspect('deployment-plan', error='unsupported_pairing_platform')
        print('Deployment pairing: unsupported native platform rejected; no registry fixture created.')
        return
    # Intentionally create synthetic saved-registration evidence only AFTER the
    # existing runtime harness proved it did not fabricate application state.
    plist = home / 'Library/LaunchAgents' / (d.LABEL + '.plist')
    plist.parent.mkdir(parents=True)
    service = home / '.local/share/reasonfirst/v4-service'
    data = {'Label':d.LABEL,
            'ProgramArguments':[str(service/'tools/codex_web_bridge/run_reasonfirst.sh')],
            'WorkingDirectory':str(service), 'KeepAlive':True,
            'EnvironmentVariables': {'RF_MCP_READ_ONLY':'false', 'RF_MCP_PORT':'8765',
                                     'RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH':'false'}}
    with plist.open('xb') as stream:
        stream.write(plistlib.dumps(data))
    plist.chmod(0o600)
    adoption = d.plan_adoption(system_name='Darwin', home=home)
    recorded = d.adopt_deployment(system_name='Darwin', home=home,
                                 expect_digest=adoption['plan_digest'], approved=True)
    assert recorded['record_written'] is True
    before = cli_snapshot(home)
    plan = inspect('deployment-plan')
    assert plan['pairing_verified'] is True
    assert plan['legacy_control_requirement'] == 'unknown'
    assert plan['proposed_actions'] == []
    check = inspect('deployment-check', digest=plan['plan_digest'])
    assert check['review_digest_matches'] is True
    assert cli_snapshot(home) == before, 'Pairing changed disposable fixture files'
    launch_acceptance(home, runtime_id, python, source, plan['plan_digest'])
    data['KeepAlive'] = False
    plist.write_bytes(plistlib.dumps(data))
    inspect('deployment-check', digest=plan['plan_digest'], error='registration_drifted')
    print(json.dumps({'operation':'deployment-pairing-native-acceptance', 'ok':True,
        'prepared_runtime_cli':True, 'read_only_pairing':True, 'registration_drift_refused':True,
        'live_service_tested':False, 'compatibility_verified':False, 'activation_tested':False}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', required=True)
    parser.add_argument('--source-root', required=True)
    args = parser.parse_args()
    wheel = Path(args.wheel).resolve()
    source = Path(args.source_root).resolve()
    if os.name != 'posix':
        # Do not guess ACLs or inspect a user home on Windows.
        try:
            r.status(runtime_id='a' * 64)
        except r.RuntimeErrorCode as exc:
            assert exc.code == 'unsupported_platform'
        else:
            raise AssertionError('Windows unexpectedly accepted POSIX storage')
        result = pairing.run_command('deployment-plan', runtime_id='a' * 64)
        assert result['error_code'] == 'unsupported_pairing_platform'
        print('Deployment pairing: Windows unsupported boundary verified; no storage inspected.')
        print('Runtime preparation: Windows unsupported boundary verified; no storage inspected.')
        execute_startup_fixture([sys.executable, '-I', '-B',
                                 str(source/'scripts/startup_e2e.py'), 'unsupported'], Path.cwd())
        return 0
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required by the installation harness')
    base = sys._base_executable
    cli_root = Path(sys.prefix).resolve()
    assert cli_root != Path(sys.base_prefix).resolve(), 'Use the clean wheel environment'
    # Byte-for-byte/mode/link snapshot of the already-installed CLI environment.
    before_cli = cli_snapshot(cli_root)
    with tempfile.TemporaryDirectory(prefix='rf-runtime-native-') as temp:
        root = Path(temp).resolve()
        home = root / "home 空间's fixture"
        home.mkdir(mode=0o700)
        wheels = root / 'wheelhouse';wheels.mkdir()
        # Online acquisition is confined to this disposable test fixture. It is
        # not performed by runtime.plan/prepare and uses no maintainer settings.
        env = r.child_env(root)
        execute([base, '-I', '-m', 'pip', '--isolated', 'download', '--disable-pip-version-check',
                 '--only-binary=:all:', '--dest', str(wheels), str(wheel)], root, env=env)
        kwargs = dict(wheelhouse=str(wheels), python=base, uv=uv,
                      package_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), home=home)
        p = r.plan(**kwargs)
        assert not list(home.iterdir()), 'Plan wrote home state'
        out = r.prepare(expect_digest=p['plan_digest'], approved=True, **kwargs)
        assert out['ok'] and out['prepared'], out
        assert out['ready_for_activation'] is False
        prepared_root = Path(out['runtime_path']) / 'venv'
        assert prepared_root != cli_root
        env = r.child_env(root)
        env['RF_HTTP_TEST_WHEEL_ROOT'] = str(prepared_root)
        proc = execute([str(prepared_root/'bin/python'), '-I', '-B',
                        str(source/'tests/test_bridge_http_integration.py')], root, env=env)
        print(proc.stdout.decode('utf-8'))
        print(proc.stderr.decode('utf-8'))
        assert r.status(runtime_id=p['runtime_id'], home=home)['prepared'] is True
        # The same freshly prepared child is exercised by the clean-wheel
        # supervisor and by its own prepared interpreter, never from source.
        for python, route, supervisor_root in (
                (Path(sys.executable), 'clean-wheel', cli_root),
                (prepared_root/'bin/python', 'prepared-runtime', prepared_root)):
            execute_startup_fixture([
                str(python), '-I', '-B', str(source/'scripts/startup_e2e.py'), 'verify',
                '--runtime-id', p['runtime_id'], '--runtime-home', str(home),
                '--prepared-root', str(prepared_root), '--supervisor-root', str(supervisor_root),
                '--source-root', str(source), '--fixture-root', str(root), '--route', route,
            ], root, env=r.child_env(root))
        assert r.status(runtime_id=p['runtime_id'], home=home)['prepared'] is True
        # Idempotent inspection, never idempotent overwrite of an old directory.
        try:
            r.prepare(expect_digest=p['plan_digest'], approved=True, **kwargs)
        except r.RuntimeErrorCode as exc:
            assert exc.code == 'destination_exists_inspect_status'
        else:
            raise AssertionError('An existing runtime was overwritten')
        # A missing transitive dependency must fail offline without importing
        # the target, changing the completed runtime or borrowing CLI packages.
        incomplete = root / 'incomplete-wheelhouse'
        incomplete.mkdir()
        for item in p['input']['wheels']:
            if item['name'] != 'mcp':
                shutil.copyfile(wheels / item['filename'], incomplete / item['filename'])
        failed_kwargs = dict(kwargs, wheelhouse=str(incomplete))
        failed_plan = r.plan(**failed_kwargs)
        failed = r.prepare(expect_digest=failed_plan['plan_digest'], approved=True, **failed_kwargs)
        assert not failed['ok'] and failed['created'], failed
        assert failed['error_code'] == 'command_failed', failed
        assert r.status(runtime_id=failed['runtime_id'], home=home)['runtime_status'] == 'preparation_incomplete'
        assert r.status(runtime_id=p['runtime_id'], home=home)['prepared'] is True
        assert not (home/'.config').exists(), 'Application/onboarding state was fabricated'
        pairing_acceptance(home, p['runtime_id'], prepared_root/'bin/python', source)
        if sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true':
            fixture_env = r.child_env(home)
            fixture_env['GITHUB_ACTIONS'] = 'true'
            execute_loaded_fixture([str(prepared_root/'bin/python'), '-I', '-B',
                               str(source/'scripts/loaded_service_e2e.py'), '--ci-fixture'],
                              home, env=fixture_env)

        (prepared_root/'added-for-drift-test').write_text('synthetic drift')
        assert r.status(runtime_id=p['runtime_id'], home=home)['runtime_status'] == 'drifted'
        print(json.dumps({'operation':'runtime-native-acceptance', 'ok':True,
            'offline_preparation':True, 'prepared_environment_http_tests':4,
            'repeat_refused':True, 'drift_detected':True, 'incomplete_dependencies_blocked':True, 'activation_tested':False,
            'packages':out['observed']['packages'], 'python':out['observed']['python_version']}, sort_keys=True))
    assert cli_snapshot(cli_root) == before_cli, 'CLI environment changed'
    print('Runtime preparation E2E: OK; CLI unchanged; no service activation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
