from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import runtime as r


class RuntimeFixtureTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX fixture hardlink regression')
    def test_cli_cache_hardlinks_are_observed_without_weakening_runtime_guard(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/runtime_e2e.py'
        spec = importlib.util.spec_from_file_location('runtime_e2e_fixture', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli = root / 'cli'; cli.mkdir()
            cached = root / 'cache'; cached.write_bytes(b'fixture')
            os.link(cached, cli / 'installed')
            before = mod.cli_snapshot(cli)
            self.assertEqual((cli / 'installed').stat().st_nlink, 2)
            self.assertEqual(before, mod.cli_snapshot(cli))
            with self.assertRaises(r.storage.DeploymentError):
                r._tree(cli, {'fingerprint': r.executable_fingerprint(sys._base_executable)})
            cached.write_bytes(b'changed')
            self.assertNotEqual(before, mod.cli_snapshot(cli))
            self.assertEqual((cli / 'installed').stat().st_nlink, 2)

    @unittest.skipUnless(os.name == 'posix', 'POSIX preparation failure diagnostics')
    def test_failure_retains_safe_stage_without_private_exception_text(self):
        import test_runtime_preparation as fixture
        case = fixture.RuntimeStorageTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        p = case.plan()
        with patch.object(r, 'probe_python', side_effect=ValueError('sensitive-value')):
            result = r.prepare(expect_digest=p['plan_digest'], approved=True, **case.kwargs)
        self.assertFalse(result['ok'])
        self.assertTrue(result['created'])
        self.assertEqual(result['failure_stage'], 'probe_python')
        self.assertNotIn('sensitive-value', str(result))


if __name__ == '__main__':
    unittest.main()
