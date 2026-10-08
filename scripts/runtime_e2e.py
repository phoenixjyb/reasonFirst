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

from gitlab_agent.upgrade import deployment as d, launch, pairing, runtime as r



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


def execute(argv, cwd, *, env=None):
    proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=240, check=False)
    if proc.returncode:
        raise RuntimeError('Disposable runtime-E2E command failed: ' + str(proc.returncode))
    return proc


def launch_acceptance(home, runtime_id, python, source, pair_digest):
    """Read actual known source copies, never run a legacy helper in the fixture."""
    folder = home / '.local/share/reasonfirst/v4-service/tools/codex_web_bridge'
    folder.mkdir(parents=True)
    for name in launch.LEGACY:
        path = folder / name
        path.write_bytes((source / 'tools/reasonfirst_v4_0_3' / name).read_bytes())
        path.chmod(0o700 if name.endswith('.sh') else 0o600)
    def review(action, expected=None):
        argv = [str(python), '-I', '-B', '-m', 'gitlab_agent.upgrade.runtime',
                action, '--runtime-id', runtime_id, '--expect-pairing-digest', pair_digest, '--json']
        if expected is not None:
            argv += ['--expect-digest', expected]
        proc = subprocess.run(argv, cwd=home, env=r.child_env(home), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=False)
        out = json.loads(proc.stdout)
        assert proc.returncode == (0 if out['ok'] else 1), out
        assert out['compatibility_verified'] is False and out['ready_for_activation'] is False
        assert out['commands_executed'] is False and out['configuration_contents_read'] is False
        return out
    before = cli_snapshot(home)
    out = review('launch-plan')
    assert out['ok'] and out['launcher_sources_verified'], out
    assert out['identity']['evidence']['saved_contract']['mode'] == 'full-chat'
    assert out['identity']['evidence']['saved_contract']['control_route'] == 'present'
    assert 'legacy_control_not_supported_by_target' in out['blockers']
    checked = review('launch-check', out['plan_digest'])
    assert checked['ok'] and checked['review_digest_matches'], checked
    assert cli_snapshot(home) == before, 'Launch inspection changed fixture files'
    helper = folder / 'set_local_no_proxy.sh'
    raw = helper.read_bytes()
    helper.write_bytes(raw + b'\n# synthetic source drift\n')
    drifted = review('launch-check', out['plan_digest'])
    assert not drifted['ok'] and drifted['error_code'] == 'unknown_launch_source', drifted
    helper.write_bytes(raw)
    print(json.dumps({'operation': 'launch-review-native-acceptance', 'ok': True,
        'prepared_runtime_cli': True, 'legacy_control_blocked': True,
        'source_drift_refused': True, 'legacy_source_executed': False,
        'application_configuration_read': False, 'activation_tested': False}, sort_keys=True))


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
            'EnvironmentVariables': {'RF_MCP_PORT':'8765', 'RF_MCP_READ_ONLY':'false',
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
