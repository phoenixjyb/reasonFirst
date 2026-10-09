"""Fixture-report boundaries only; native installed-artifact evidence is separate."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
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
        try:
            with patch.object(self.harness, "execute", return_value=result), patch("sys.stdout", stream):
                return self.harness.execute_startup_fixture(["synthetic-fixture"], Path.cwd())
        finally:
            self.assertNotIn("do-not-reflect", stream.getvalue())

    def _emit_probe(self, probe, *, expected_error=None, stage="read-only"):
        """Exercise the real main/report path with fixture orchestration replaced."""
        def verify(_args, report):
            report["stage"] = stage
            self.fixture._check_probe(probe, expected_error=expected_error)
            report.update(ok=True, stage="complete", supervisor_import_origins_verified=True,
                          child_import_origins_verified=True, read_only_catalog_verified=True,
                          full_chat_catalog_verified=True, occupied_listener_preserved=True,
                          decoy_listener_preserved=True, attempt_cleanup_confirmed=True,
                          runtime_unchanged=True)

        argv = ["startup_e2e.py", "verify", "--route", "clean-wheel"]
        for option in ("runtime-id", "runtime-home", "prepared-root", "supervisor-root",
                       "source-root", "fixture-root"):
            argv.extend(("--" + option, "unused-synthetic-argument"))
        stream = io.StringIO()
        with patch.object(self.fixture, "_verify", side_effect=verify), \
                patch.object(self.fixture.sys, "argv", argv), patch("sys.stdout", stream):
            exit_code = self.fixture.main()
        self.assertNotIn("do-not-reflect", stream.getvalue())
        return exit_code, json.loads(stream.getvalue())

    def _assert_classified_output(self, report):
        result = subprocess.CompletedProcess(["synthetic-fixture"], 1, json.dumps(report).encode(),
                                              b"do-not-reflect-private-stderr")
        stream = io.StringIO()
        with patch.object(self.harness, "execute", return_value=result), patch("sys.stdout", stream):
            with self.assertRaisesRegex(RuntimeError, "fixture failed; see classified report"):
                self.harness.execute_startup_fixture(["synthetic-fixture"], Path.cwd())
        self.assertEqual(json.loads(stream.getvalue()), report)
        self.assertNotIn("do-not-reflect", stream.getvalue())

    def test_unsupported_native_fixture_exercises_adapter_before_io(self):
        report = self.fixture._report("unsupported")
        with patch.object(self.fixture.startup_managed.platform, "system", return_value="Windows"):
            self.fixture._verify_unsupported(report)
        self.assertTrue(report["unsupported_before_side_effects"])
        self.assertTrue(self._execute(json.dumps(report).encode())["ok"])
        self.assertFalse(report["child_import_origins_verified"])
        self.assertFalse(report["runtime_unchanged"])
        self.assertIsNone(report["probe_error_code"])
        self.assertIsNone(report["probe_cleanup_error_code"])

    def test_report_cannot_emit_unknown_raw_fields_or_codes(self):
        for extra in ({"raw": "do-not-reflect-wire"}, {"error_code": "do-not-reflect-error"},
                      {"probe_error_code": "do-not-reflect-error"},
                      {"probe_cleanup_error_code": "do-not-reflect-cleanup"}):
            report = self.fixture._report("clean-wheel")
            report["error_code"] = "fixture_failed"
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
        self._assert_classified_output(report)

    def test_listener_query_failure_survives_probe_fixture_and_outer_report(self):
        managed = self.fixture.startup_managed
        with tempfile.TemporaryDirectory(prefix="rf-query-diagnostic-") as temp:
            selected = SimpleNamespace(runtime_id="a" * 64, home=Path(temp),
                                       manifest_digest="b" * 64, interpreter_digest="c" * 64)
            launch = self.fixture._launch(self.fixture._free_port())
            # Exercise the real bind/query/failure/cleanup path; inject only the
            # query error and unrelated runtime selection/platform prerequisites.
            with patch.object(managed, "_supported"), \
                    patch.object(managed.platform, "system", return_value="Linux"), \
                    patch.object(managed, "_supervisor_sdk_supported"), \
                    patch.object(managed, "_select_runtime", return_value=selected), \
                    patch.object(managed.socket.socket, "getsockopt",
                                 side_effect=OSError("do-not-reflect-query-error")) as query, \
                    patch.object(managed.subprocess, "Popen") as spawn:
                probe = managed.probe_disposable(runtime_id=selected.runtime_id,
                                                  home=selected.home, launch=launch)
            query.assert_called_once_with(managed.socket.SOL_SOCKET, managed.socket.SO_ACCEPTCONN)
            spawn.assert_not_called()
        self.assertEqual(probe["error_code"], "listener_observation_failed")
        self.assertTrue(probe["cleanup_confirmed"])
        exit_code, report = self._emit_probe(probe)
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["error_code"], "unexpected_probe_result")
        self.assertEqual(report["probe_error_code"], "listener_observation_failed")
        self.assertIsNone(report["probe_cleanup_error_code"])
        self._assert_classified_output(report)

    def test_probe_cleanup_failure_preserves_primary_error_independently(self):
        probe = self.fixture.startup_managed._base_report()
        probe.update(error_code="startup_timeout", cleanup_error_code="fd_cleanup_failed",
                     cleanup_confirmed=False)
        exit_code, report = self._emit_probe(probe)
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["error_code"], "probe_cleanup_unconfirmed")
        self.assertEqual(report["probe_error_code"], "startup_timeout")
        self.assertEqual(report["probe_cleanup_error_code"], "fd_cleanup_failed")
        self.assertIsNone(report["cleanup_error_code"])
        self._assert_classified_output(report)

    def test_unknown_probe_values_are_replaced_without_invoking_or_reflecting_them(self):
        class Untrusted:
            def __str__(self):
                raise AssertionError("untrusted value formatted")

            __repr__ = __str__

            def __hash__(self):
                raise AssertionError("untrusted value hashed")

        class StringSubclass(str):
            pass

        values = ("do-not-reflect-unknown", {"do-not-reflect": "private"},
                  ["do-not-reflect"], b"do-not-reflect", True, 42,
                  StringSubclass("startup_timeout"), Untrusted())
        for index, value in enumerate(values):
            with self.subTest(case=index):
                probe = self.fixture.startup_managed._base_report()
                probe.update(error_code=value, cleanup_error_code=value,
                             private_claim="do-not-reflect-private-claim")
                exit_code, report = self._emit_probe(probe)
                self.assertEqual(exit_code, 1)
                self.assertEqual(report["probe_error_code"], "probe_failed")
                self.assertEqual(report["probe_cleanup_error_code"], "probe_failed")
                self._assert_classified_output(report)

    def test_outer_report_requires_present_nullable_enum_diagnostics(self):
        for key in ("probe_error_code", "probe_cleanup_error_code"):
            for value in (True, 1, [], {}, "do-not-reflect-unknown"):
                with self.subTest(key=key, value_type=type(value).__name__):
                    report = self.fixture._report("clean-wheel")
                    report.update(error_code="fixture_failed", **{key: value})
                    with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
                        self._execute(json.dumps(report).encode(), exit_code=1)
            report = self.fixture._report("clean-wheel")
            report["error_code"] = "fixture_failed"
            del report[key]
            with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
                self._execute(json.dumps(report).encode(), exit_code=1)

    def test_expected_listener_failures_leave_success_diagnostics_null(self):
        probe = self.fixture.startup_managed._base_report()
        probe.update(error_code="listener_bind_failed", cleanup_confirmed=True)
        for stage in ("occupied_listener", "decoy_listener"):
            with self.subTest(stage=stage):
                exit_code, report = self._emit_probe(probe, expected_error="listener_bind_failed", stage=stage)
                self.assertEqual(exit_code, 0)
                self.assertIsNone(report["probe_error_code"])
                self.assertIsNone(report["probe_cleanup_error_code"])
                self.assertTrue(self._execute(json.dumps(report).encode())["ok"])
                for key in ("probe_error_code", "probe_cleanup_error_code"):
                    forged = dict(report, **{key: "listener_observation_failed"})
                    with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
                        self._execute(json.dumps(forged).encode())

    def test_report_limit_accepts_8192_bytes_and_rejects_one_more(self):
        report = self.fixture._report("unsupported")
        report.update(ok=True, stage="complete", unsupported_before_side_effects=True)
        payload = json.dumps(report).encode().ljust(8192, b" ")
        self.assertTrue(self._execute(payload)["ok"])
        with self.assertRaisesRegex(RuntimeError, "report invalid; child output withheld"):
            self._execute(payload + b" ")

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
