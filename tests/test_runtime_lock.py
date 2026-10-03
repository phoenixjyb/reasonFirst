from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from gitlab_agent.upgrade import runtime as r
import test_runtime_preparation as fixture


class RawWheelPathTests(unittest.TestCase):
    def test_raw_member_normalization_cannot_hide_unsafe_paths(self):
        # ZipInfo normalizes Windows separators and truncates NUL on input.
        # Preserve the literal member spelling in both ZIP headers for this test.
        for member in ('a\\b', 'safe\x00hidden'):
            stream = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(fixture.wheel())) as source, zipfile.ZipFile(stream, 'w') as target:
                for entry in source.infolist():
                    target.writestr(entry, source.read(entry))
                entry = zipfile.ZipInfo('placeholder')
                entry.filename = entry.orig_filename = member
                target.writestr(entry, b'x')
            raw = stream.getvalue()
            self.assertIn(member.encode(), raw)
            # Model the reader's Windows separator normalization on every host,
            # without performing filesystem operations or changing os.name.
            with self.subTest(member=member), patch.object(zipfile.os, 'sep', '\\'):
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    item = archive.infolist()[-1]
                    self.assertEqual(item.orig_filename, member)
                    self.assertNotEqual(item.filename, member)
                with self.assertRaisesRegex(r.RuntimeErrorCode, 'unsafe_wheel_path'):
                    r.wheel_metadata('test-1-py3-none-any.whl', raw)

    def test_fixture_preserves_raw_member_spelling_on_windows_and_posix(self):
        for member in ('a\\b', 'safe\x00hidden', '../escape', '/absolute'):
            for separator in ('/', '\\'):
                with self.subTest(member=member, separator=separator), patch.object(zipfile.os, 'sep', separator):
                    raw = fixture.wheel(extra={member: 'x'})
                    self.assertIn(member.encode(), raw)
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        self.assertEqual(archive.infolist()[-1].orig_filename, member)


@unittest.skipUnless(os.name == 'posix', 'POSIX private runtime lock; not Windows ACL coverage')
class RuntimeLockTests(unittest.TestCase):
    def case(self):
        case = fixture.RuntimeStorageTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def simulate(self, case, *, initial=None):
        plan = case.plan()
        root = Path(plan['runtime_path'])
        identity = dict(executable=sys._base_executable, implementation='cpython',
                        version=list(sys.version_info[:3]), fingerprint=plan['input']['python']['fingerprint'])
        observed = dict(packages={r.PACKAGE: '0.5.1'}, version='0.5.1', python_version=identity['version'],
                        prefix=str(root / 'venv'), origins=['lib/fixture'], mcp_version='synthetic',
                        http_signature_accepted=True, controller_instantiated=False, server_started=False)
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if 'venv' in argv:
                (root / 'venv/bin').mkdir(parents=True)
                (root / 'venv/bin/python').symlink_to(Path(sys._base_executable).resolve())
                if initial is not None:
                    initial(root / 'venv/.lock')
            if 'install' in argv:
                lock = root / 'venv/.lock'
                info = lock.lstat()
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(lock.read_bytes(), b'')
            return json.dumps(observed).encode() if '-c' in argv else b''
        with patch.object(r, 'probe_python', return_value=identity), patch.object(r, '_run', side_effect=run):
            result = r.prepare(expect_digest=plan['plan_digest'], approved=True, **case.kwargs)
        return result, root, calls

    def test_prepare_creates_private_empty_lock_before_install(self):
        result, root, calls = self.simulate(self.case())
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['prepared'])
        self.assertFalse(result['ready_for_activation'])
        self.assertEqual(len(calls), 4)
        record = json.loads((root / 'runtime.json').read_bytes())
        self.assertIn(['.lock', 'file', 0o600, hashlib.sha256(b'').hexdigest()], record['tree'])

    def test_preexisting_lock_is_never_replaced_or_repaired(self):
        for kind in ('file', 'symlink', 'directory'):
            with self.subTest(kind=kind):
                case = self.case()
                target = case.root / 'original'
                target.write_bytes(b'preserve')
                before = {}
                def create(path):
                    if kind == 'file':
                        path.write_bytes(b'keep existing lock')
                        path.chmod(0o666)
                    elif kind == 'symlink':
                        path.symlink_to(target)
                    else:
                        path.mkdir()
                    before['stat'] = path.lstat()
                result, root, calls = self.simulate(case, initial=create)
                self.assertFalse(result['ok'])
                self.assertEqual(result['failure_stage'], 'prepare_package_lock')
                self.assertTrue(result['created'])
                self.assertEqual(len(calls), 1)  # uv venv only; no install or probe
                path = root / 'venv/.lock'
                self.assertEqual(path.lstat(), before['stat'])
                self.assertEqual(target.read_bytes(), b'preserve')
                self.assertFalse((root / 'runtime.json').exists())

    def test_lock_is_not_exempt_from_strict_runtime_drift_checks(self):
        result, root, _ = self.simulate(self.case())
        self.assertTrue(result['ok'], result)
        lock = root / 'venv/.lock'
        lock.chmod(0o666)
        case_home = root.parents[4]
        status = r.status(runtime_id=root.name, home=case_home)
        self.assertFalse(status['prepared'])
        self.assertEqual(status['runtime_status'], 'drifted')
        self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o666)  # inspection did not repair it

    def test_real_uv_preserves_precreated_private_lock_during_offline_install(self):
        uv = shutil.which('uv')
        self.assertIsNotNone(uv, 'uv must be installed by the native regression harness')
        with tempfile.TemporaryDirectory(prefix='rf-private-lock-') as temp:
            root = Path(temp).resolve()
            env = r.child_env(root)
            cmd = [uv, '--no-config', '--offline', '--no-cache']
            venv = root / 'venv'
            r._run(cmd + ['venv', '--no-project', '--no-python-downloads', '--python', sys._base_executable, str(venv)], cwd=root, env=env)
            with r.storage._directory(root, ('venv',)) as fd:
                r._write_new(fd, '.lock', b'')
            initial = (venv / '.lock').stat()
            contents = {'demo.py': b'# synthetic offline package\n',
                        'demo-1.dist-info/METADATA': b'Metadata-Version: 2.1\nName: demo\nVersion: 1\n',
                        'demo-1.dist-info/WHEEL': b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'}
            table = io.StringIO()
            writer = csv.writer(table)
            for name, data in contents.items():
                sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
                writer.writerow([name, 'sha256=' + sha, len(data)])
            writer.writerow(['demo-1.dist-info/RECORD', '', ''])
            contents['demo-1.dist-info/RECORD'] = table.getvalue().encode()
            archive = root / 'demo-1-py3-none-any.whl'
            with zipfile.ZipFile(archive, 'w') as wheel:
                for name, data in contents.items():
                    wheel.writestr(name, data)
            req = root / 'requirements.txt'
            req.write_text('demo==1 --hash=sha256:' + hashlib.sha256(archive.read_bytes()).hexdigest() + '\n', encoding='ascii')
            r._run(cmd + ['pip', 'install', '--python', str(venv / 'bin/python'), '--no-python-downloads',
                         '--no-index', '--find-links', str(root), '--only-binary', ':all:', '--require-hashes',
                         '--link-mode', 'copy', '-r', str(req)], cwd=root, env=env)
            r._run(cmd + ['pip', 'check', '--python', str(venv / 'bin/python')], cwd=root, env=env)
            after = (venv / '.lock').stat()
            self.assertEqual((after.st_dev, after.st_ino), (initial.st_dev, initial.st_ino))
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o600)
            self.assertEqual(after.st_nlink, 1)
            rows = r._tree(venv, {'fingerprint': r.executable_fingerprint(sys._base_executable)})
            self.assertIn(['.lock', 'file', 0o600, hashlib.sha256(b'').hexdigest()], rows)
            # A foreign hard link is still rejected; no lock-name exemption.
            os.link(venv / '.lock', root / 'extra-link')
            with self.assertRaises(r.storage.DeploymentError):
                r._tree(venv, {'fingerprint': r.executable_fingerprint(sys._base_executable)})


if __name__ == '__main__':
    unittest.main()
