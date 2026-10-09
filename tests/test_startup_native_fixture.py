"""Fixture-report boundaries only; native installed-artifact evidence is separate."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


def _script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name + "_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StartupNativeFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = _script("startup_e2e")
        cls.harness = _script("runtime_e2e")

    def _execute(self, payload, *, exit_code=0):
        result = subprocess.CompletedProcess(["synthetic-fixture"], exit_code, payload,
                                              b"do-not-reflect-fixture-stderr")
        stream = io.StringIO()
        with patch.object(self.harness, "execute", return_value=result), patch("sys.stdout", stream):
            report = self.harness.execute_startup_fixture(["synthetic-fixture"], Path.cwd())
        self.assertNotIn("do-not-reflect", stream.getvalue())
        return report

    def test_unsupported_native_fixture_exercises_adapter_before_io(self):
        report = self.fixture._report("unsupported")
        with patch.object(self.fixture.startup_managed.platform, "system", return_value="Windows"):
            self.fixture._verify_unsupported(report)
        self.assertTrue(report["unsupported_before_side_effects"])
        self.assertTrue(self._execute(json.dumps(report).encode())["ok"])
        self.assertFalse(report["child_import_origins_verified"])
        self.assertFalse(report["runtime_unchanged"])

    def test_report_cannot_emit_unknown_raw_fields_or_codes(self):
        for extra in ({"raw": "do-not-reflect-wire"}, {"error_code": "do-not-reflect-error"}):
            report = self.fixture._report("clean-wheel")
            report.update(extra)
            with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
                self._execute(json.dumps(report).encode(), exit_code=1)
        with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
            self._execute(b"do-not-reflect-oversized" * 1000)

    def test_success_requires_real_evidence_and_confirmed_cleanup(self):
        report = self.fixture._report("clean-wheel")
        report.update(ok=True, stage="complete")
        with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
            self._execute(json.dumps(report).encode())
        report = self.fixture._report("unsupported")
        report.update(ok=True, stage="complete", unsupported_before_side_effects=True,
                      child_import_origins_verified=True)
        with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
            self._execute(json.dumps(report).encode())

    def test_classified_failure_is_printed_without_child_stderr(self):
        report = self.fixture._report("prepared-runtime")
        report.update(stage="read-only", error_code="probe_cleanup_unconfirmed")
        result = subprocess.CompletedProcess(["synthetic-fixture"], 1, json.dumps(report).encode(),
                                              b"do-not-reflect-private-stderr")
        stream = io.StringIO()
        with patch.object(self.harness, "execute", return_value=result), patch("sys.stdout", stream):
            with self.assertRaisesRegex(RuntimeError, "fixture failed; see classified report"):
                self.harness.execute_startup_fixture(["synthetic-fixture"], Path.cwd())
        self.assertEqual(json.loads(stream.getvalue()), report)
        self.assertNotIn("do-not-reflect", stream.getvalue())

    def test_success_requires_both_catalog_helper_cleanup_observations(self):
        report = {
            "operation": "disposable-managed-startup", "ok": True,
            "error_code": None, "cleanup_error_code": None,
            **{name: False for name in self.fixture._FALSE_FLAGS},
            **{name: True for name in self.fixture._SUCCESS_FLAGS},
            "codec_result": {"fresh_reply_claims_match": True, "pid_claim_matches": True,
                             **{name: False for name in self.fixture._FALSE_FLAGS}},
        }
        self.fixture._check_probe(report)
        for name in ("catalog_helper_reaped", "catalog_helper_cleanup_confirmed"):
            with self.subTest(evidence=name), self.assertRaisesRegex(
                    self.fixture.FixtureFailure, "unexpected_probe_result"):
                self.fixture._check_probe(dict(report, **{name: False}))


if __name__ == "__main__":
    unittest.main()
