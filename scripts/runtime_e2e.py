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
import shutil
import stat
import subprocess
import sys
import tempfile

from gitlab_agent.upgrade import runtime as r



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
        (prepared_root/'added-for-drift-test').write_text('synthetic drift')
        assert r.status(runtime_id=p['runtime_id'], home=home)['runtime_status'] == 'drifted'
        assert not (home/'.config').exists(), 'Application/onboarding state was fabricated'
        print(json.dumps({'operation':'runtime-native-acceptance', 'ok':True,
            'offline_preparation':True, 'prepared_environment_http_tests':4,
            'repeat_refused':True, 'drift_detected':True, 'incomplete_dependencies_blocked':True, 'activation_tested':False,
            'packages':out['observed']['packages'], 'python':out['observed']['python_version']}, sort_keys=True))
    assert cli_snapshot(cli_root) == before_cli, 'CLI environment changed'
    print('Runtime preparation E2E: OK; CLI unchanged; no service activation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
