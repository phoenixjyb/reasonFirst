from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
import zipfile

from gitlab_agent.upgrade import runtime as r


def wheel(name='chatgpt_selfhosted_gitlab_mcp', version='0.5.1', *, metadata='', extra=None):
    contents = {
        f'{name}-{version}.dist-info/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{metadata}\n',
        f'{name}-{version}.dist-info/WHEEL': 'Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        'gitlab_agent/bridge_http.py': '# fixture\n',
        'gitlab_agent/bridge_mcp.py': '# fixture\n',
        'gitlab_agent/bridge_preview/controller.py': '# fixture\n',
    }
    contents.update(extra or {})
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for path, value in contents.items():
            # Preserve unsafe spellings in the archive on every host instead
            # of letting ZipInfo sanitize the fixture before validation sees it.
            info = zipfile.ZipInfo('fixture')
            info.filename = info.orig_filename = path
            z.writestr(info, value)
    return out.getvalue()


class PortableRuntimeTests(unittest.TestCase):
    def test_unsupported_platform_fails_before_filesystem_or_execution(self):
        for system in ('Windows', 'Other'):
            with patch.object(r.platform, 'system', return_value=system), patch.object(r.storage, '_home', side_effect=AssertionError), patch.object(r.subprocess, 'run', side_effect=AssertionError):
                for fn in (lambda: r.plan(wheelhouse='/', python='/', uv='/', package_sha256='a'*64),
                           lambda: r.prepare(expect_digest='a'*64),
                           lambda: r.status(runtime_id='a'*64)):
                    with self.assertRaisesRegex(r.RuntimeErrorCode, 'unsupported_platform'):
                        fn()

    def test_error_does_not_echo_unknown_arguments(self):
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit):
            r.main(['status', '--runtime-id', 'a'*64, '--secret', 'sensitive-value'])
        self.assertNotIn('sensitive-value', err.getvalue())

    def test_main_unknown_exception_is_not_echoed(self):
        out = io.StringIO()
        with patch.object(r, 'status', side_effect=RuntimeError('sensitive-value')), redirect_stdout(out):
            self.assertEqual(r.main(['status', '--runtime-id', 'a'*64, '--json']), 1)
        self.assertNotIn('sensitive-value', out.getvalue())

    def test_no_implicit_approval(self):
        with patch.object(r, 'supported'), patch.object(r, 'plan', side_effect=AssertionError):
            with self.assertRaisesRegex(r.RuntimeErrorCode, 'approval_required'):
                r.prepare(expect_digest='a'*64)

    def test_child_environment_is_allowlisted_and_does_not_mutate_parent(self):
        fixture = {'PATH': 'test-path', 'GITLAB_TOKEN': 'synthetic', 'RF_BRIDGE_CONFIG': '/private',
                   'OPENAI_ADMIN_KEY': 'synthetic', 'PYTHONPATH': '/bad', 'UV_INDEX_URL': '/bad',
                   'https_proxy': '/bad', 'SSH_AUTH_SOCK': '/bad', 'SOME_OTHER_CREDENTIAL': '/bad'}
        with patch.dict(os.environ, fixture, clear=True):
            # Windows normalizes environment keys to uppercase on assignment.
            before = dict(os.environ)
            value = r.child_env(Path('/disposable'))
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(set(value).intersection(before), {'PATH'})
        self.assertEqual(value['UV_PYTHON_DOWNLOADS'], 'never')

    def test_wheel_rejects_traversal_and_duplicate_paths(self):
        for path in ('../escape', '/absolute', 'a\\b', 'C:drive'):
            with self.assertRaises(r.RuntimeErrorCode):
                r.wheel_metadata('test-1-py3-none-any.whl', wheel(extra={path: 'x'}))
        raw = wheel(extra={'GITLAB_AGENT/bridge_http.py': 'case collision'})
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'duplicate_wheel_path'):
            r.wheel_metadata('test-1-py3-none-any.whl', raw)

    def test_wheel_metadata_is_bounded_and_requires_http_package(self):
        raw = wheel(metadata='Requires-Dist: other @ https://example.invalid/package.whl\n')
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'direct_dependency'):
            r.wheel_metadata('test-1-py3-none-any.whl', raw)
        with self.assertRaises(r.RuntimeErrorCode):
            r.wheel_metadata('test-1-py3-none-any.whl', b'not a zip')
        with self.assertRaises(r.RuntimeErrorCode):
            r.wheel_metadata('test-1-py3-none-any.whl', wheel(metadata='Name: second\n'))

    def test_documentation_marks_platforms_and_nonactivation(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('RUNTIME_PREPARATION.md', 'RUNTIME_PREPARATION_CN.md'):
            text = (root / 'docs' / name).read_text(encoding='utf-8')
            for token in ('reasonfirst-runtime plan', 'reasonfirst-runtime prepare', '--expect-digest', '--yes', 'reasonfirst-runtime status',
                          'v0.5.1', 'Windows', '--offline', '/control', 'not a signature'):
                self.assertIn(token, text)


@unittest.skipUnless(os.name == 'posix', 'POSIX private storage; Windows ACL preparation is not implemented')
class RuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rf-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home 空间's"
        self.home.mkdir(mode=0o700)
        self.wheels = self.root / 'wheels'
        self.wheels.mkdir()
        self.name = 'chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl'
        self.raw = wheel()
        (self.wheels / self.name).write_bytes(self.raw)
        self.uv = self.root / 'uv'
        self.uv.write_text('synthetic executable, never run\n')
        self.uv.chmod(0o755)
        self.kwargs = dict(wheelhouse=str(self.wheels), python=sys._base_executable,
            uv=str(self.uv), package_sha256=hashlib.sha256(self.raw).hexdigest(), home=self.home)

    def plan(self):
        return r.plan(**self.kwargs)

    def test_plan_creates_nothing_and_invokes_nothing(self):
        with patch.object(r.subprocess, 'run', side_effect=AssertionError), patch.object(r, 'probe_python', side_effect=AssertionError):
            p = self.plan()
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(p['prepared'])
        self.assertFalse(p['commands_executed'])
        self.assertFalse(p['dependency_compatibility_checked'])
        self.assertEqual(p['plan_digest'], r.digest(p['input']))

    def test_changes_to_any_input_invalidate_plan(self):
        before = self.plan()['plan_digest']
        self.uv.write_text('changed\n')
        self.assertNotEqual(self.plan()['plan_digest'], before)
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'changed_input'):
            r.prepare(expect_digest=before, approved=True, **self.kwargs)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_dependency_wheels_are_bound_too(self):
        before = self.plan()['plan_digest']
        (self.wheels / 'dep-1-py3-none-any.whl').write_bytes(wheel('dep', '1'))
        self.assertNotEqual(before, self.plan()['plan_digest'])

    def test_wrong_package_hash_and_duplicate_distribution_rejected(self):
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'package_hash_mismatch'):
            r.plan(**dict(self.kwargs, package_sha256='b'*64))
        (self.wheels / 'other-1-py3-none-any.whl').write_bytes(self.raw)
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'duplicate_distribution'):
            self.plan()

    def test_nonwheel_and_symlink_inputs_rejected(self):
        p = self.wheels / 'extra.txt'
        p.write_text('not a wheel')
        with self.assertRaises(r.RuntimeErrorCode):
            self.plan()
        p.unlink()
        (self.wheels / self.name).unlink()
        p = self.root / 'outside.whl'
        p.write_bytes(self.raw)
        (self.wheels / self.name).symlink_to(p)
        with self.assertRaises(r.storage.DeploymentError):
            self.plan()

    def test_absent_status_does_not_create_directories(self):
        status = r.status(runtime_id='a'*64, home=self.home)
        self.assertEqual(status['runtime_status'], 'not_found')
        self.assertEqual(list(self.home.iterdir()), [])

    def test_invalid_id_cannot_escape(self):
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'invalid_runtime_id'):
            r.status(runtime_id='../elsewhere', home=self.home)

    def test_failed_preparation_retains_new_directory_without_retry(self):
        p = self.plan()
        with patch.object(r, 'probe_python', side_effect=RuntimeError('private-value')) as probe:
            result = r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertFalse(result['ok'])
        self.assertTrue(result['created'])
        self.assertNotIn('private-value', json.dumps(result))
        probe.assert_called_once()
        status = r.status(runtime_id=p['runtime_id'], home=self.home)
        self.assertEqual(status['runtime_status'], 'preparation_incomplete')
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'destination_exists'):
            r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertEqual((Path(p['runtime_path'])/'intent.json').read_bytes(), r.canonical(p['input']))
        self.assertEqual((self.wheels/self.name).read_bytes(), self.raw)

    def test_private_directory_and_symlinked_store(self):
        p = self.plan()
        with patch.object(r, 'probe_python', side_effect=RuntimeError):
            r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertEqual(Path(p['runtime_path']).stat().st_mode & 0o777, 0o700)
        store = self.home.joinpath(*r.PARTS)
        other = self.root/'other';other.mkdir()
        alias = self.root/'alias-home';alias.symlink_to(self.home)
        with self.assertRaises(r.storage.DeploymentError):
            r.status(runtime_id=p['runtime_id'], home=alias)

    def test_only_python_and_lib64_links_are_permitted(self):
        root = self.root/'tree';root.mkdir()
        (root/'unsafe').symlink_to(self.wheels)
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'unsafe_runtime_link'):
            r._tree(root, self.plan()['input']['python'])

    def test_tree_rejects_symlink_root_and_detects_changes(self):
        root = self.root/'tree';root.mkdir()
        file = root/'value';file.write_text('one')
        initial = r._tree(root, self.plan()['input']['python'])
        file.write_text('two')
        self.assertNotEqual(initial, r._tree(root, self.plan()['input']['python']))
        alias = self.root/'alias';alias.symlink_to(root)
        with self.assertRaises(r.storage.DeploymentError):
            r._tree(alias, self.plan()['input']['python'])

    def test_create_only_write_never_overwrites_existing_record(self):
        with r.storage._directory(self.home, ()) as fd:
            r._write_new(fd, 'one.json', b'first')
            with self.assertRaises(FileExistsError):
                r._write_new(fd, 'one.json', b'second')
        self.assertEqual((self.home/'one.json').read_bytes(), b'first')

    def test_timeout_and_failure_are_classified_without_output(self):
        for error, code in ((subprocess.TimeoutExpired('uv', 1), 'command_timeout'),
                            (OSError('private value'), 'command_launch_failed')):
            with patch.object(r.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(r.RuntimeErrorCode, code):
                    r._run(['never'], cwd=self.root, env={})

    def test_tampered_manifest_and_duplicate_keys_fail_without_execution(self):
        p = self.plan()
        with patch.object(r, 'probe_python', side_effect=RuntimeError):
            r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        record = Path(p['runtime_path'])/'runtime.json'
        record.write_text('{"schema_version":1,"schema_version":2}')
        record.chmod(0o600)
        with patch.object(r.subprocess, 'run', side_effect=AssertionError):
            with self.assertRaisesRegex(r.RuntimeErrorCode, 'duplicate_metadata'):
                r.status(runtime_id=p['runtime_id'], home=self.home)
        record.write_text('{"schema_version":1}')
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'invalid_runtime_record'):
            r.status(runtime_id=p['runtime_id'], home=self.home)

    def test_changed_copied_wheel_stops_before_any_executable(self):
        p = self.plan()
        original = r.storage._read_at
        calls = 0
        def read(fd, name, **kwargs):
            nonlocal calls
            value = original(fd, name, **kwargs)
            if name == self.name:
                calls += 1
                if calls == 2:  # plan snapshot is read first
                    return b'changed while preparing'
            return value
        with patch.object(r.storage, '_read_at', side_effect=read), patch.object(r, 'probe_python') as probe:
            out = r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertEqual(out['error_code'], 'changed_input')
        self.assertTrue(out['created'])
        probe.assert_not_called()
        self.assertFalse((Path(p['runtime_path'])/'runtime.json').exists())

    def test_intent_write_failure_retains_partial_directory_and_refuses_retry(self):
        p = self.plan()
        with patch.object(r, '_write_new', side_effect=OSError('private-value')), patch.object(r, 'probe_python') as probe:
            result = r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertFalse(result['ok'])
        self.assertTrue(result['created'])
        self.assertNotIn('private-value', json.dumps(result))
        self.assertTrue(Path(p['runtime_path']).is_dir())
        probe.assert_not_called()
        with self.assertRaisesRegex(r.RuntimeErrorCode, 'destination_exists'):
            r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)

    def test_existing_nonprivate_runtime_store_is_not_chmodded(self):
        store = self.home.joinpath(*r.PARTS)
        store.mkdir(parents=True, mode=0o755)
        before = store.stat().st_mode
        p = self.plan()
        with self.assertRaises(r.storage.DeploymentError):
            r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertEqual(store.stat().st_mode, before)
        self.assertEqual(list(store.iterdir()), [])

    def test_tool_venv_interpreter_is_not_a_base_identity(self):
        fp = dict(r.executable_fingerprint(sys._base_executable), venv_config_sha256='a'*64)
        with patch.object(r, 'executable_fingerprint', return_value=fp):
            with self.assertRaisesRegex(r.RuntimeErrorCode, 'base_python_required'):
                self.plan()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_wrong_owner_mode_or_hardlink_input_is_not_accepted(self):
        item = self.wheels/self.name
        item.chmod(0o666)
        with self.assertRaises(r.storage.DeploymentError):
            self.plan()
        item.chmod(0o600)
        os.link(item, self.root/'hardlink')
        with self.assertRaises(r.storage.DeploymentError):
            self.plan()

    def test_fake_success_still_requires_matching_manifest_and_detects_drift(self):
        # Package execution is mocked here; real uv/SDK proof is the native
        # install harness, not this state-machine test.
        p = self.plan();root = Path(p['runtime_path'])
        python = dict(executable=sys._base_executable, implementation='cpython', version=list(sys.version_info[:3]),
                      fingerprint=p['input']['python']['fingerprint'])
        observation = dict(packages={r.PACKAGE:'0.5.1'}, version='0.5.1', python_version=python['version'],
                           prefix=str(root/'venv'), origins=['lib/test'], mcp_version='synthetic',
                           http_signature_accepted=True, controller_instantiated=False, server_started=False)
        def run(argv, **kwargs):
            if 'venv' in argv:
                (root/'venv/bin').mkdir(parents=True)
                (root/'venv/bin/python').symlink_to(Path(sys._base_executable).resolve())
            if '-c' in argv:
                return json.dumps(observation).encode()
            return b''
        with patch.object(r, 'probe_python', return_value=python), patch.object(r, '_run', side_effect=run) as calls:
            result = r.prepare(expect_digest=p['plan_digest'], approved=True, **self.kwargs)
        self.assertTrue(result['ok']);self.assertTrue(result['prepared'])
        self.assertFalse(result['ready_for_activation'])
        self.assertEqual(len(calls.call_args_list), 4)
        install = calls.call_args_list[1].args[0]
        for flag in ('--offline','--no-index','--no-cache','--require-hashes','--no-python-downloads'):
            self.assertIn(flag, install)
        self.assertNotIn('--force', install)
        (root/'venv/change').write_text('drift')
        self.assertEqual(r.status(runtime_id=p['runtime_id'], home=self.home)['runtime_status'], 'drifted')
        self.assertFalse((self.home/'.config').exists())


if __name__ == '__main__':
    unittest.main()
