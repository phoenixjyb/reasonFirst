"""Tests for opt-in native manager inspection; no real service query is performed."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

from gitlab_agent import native_service_observation as native
from gitlab_agent import service_cli


def result(stdout: str, code: int = 0, **kwargs) -> dict[str, object]:
    return {
        "stdout": stdout, "returncode": code, "timed_out": False,
        "output_complete": True, "stdout_truncated": False,
        "stderr_truncated": False, **kwargs,
    }


class NativeServiceObservationTests(unittest.TestCase):
    def test_linux_observes_only_known_units_and_never_adopts(self):
        calls = []
        def query(argv, **options):
            calls.append((argv, options))
            return result("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n")
        report = native.observe_native_services(system_name="Linux", run=query)
        self.assertEqual(len(calls), 2)
        self.assertEqual([x[0][-1] for x in calls],
                         ["reasonfirst-v4-mcp.service", "reasonfirst-v4-tunnel.service"])
        self.assertTrue(all(x[0][0:2] == ["systemctl", "--user"] for x in calls))
        self.assertTrue(all(x[1]["timeout"] == 3 and x[1]["max_output_bytes"] == 8192
                            for x in calls))
        self.assertFalse(report["owner_adopted"])
        for service in report["services"]:
            self.assertEqual(service["registration"], "registered")
            self.assertEqual(service["manager_activity"], "active")
            self.assertEqual(service["login_setting"], "enabled")
            self.assertEqual(service["runtime_owner"], "not_verified")
            self.assertEqual(service["mcp_readiness"], "not_checked")
            self.assertEqual(service["login_acceptance"], "not_verified")

    def test_missing_unit_is_only_a_fixed_identifier_negative(self):
        report = native.observe_native_services(
            system_name="Linux",
            run=lambda *args, **kw: result("LoadState=not-found\nActiveState=inactive\nUnitFileState=\n"))
        # Empty / malformed UnitFileState is NOT proof of absence.
        self.assertTrue(all(x["status"] == "unknown" for x in report["services"]))
        report = native.observe_native_services(
            system_name="Linux",
            run=lambda *args, **kw: result("LoadState=not-found\nActiveState=inactive\nUnitFileState=disabled\n"))
        self.assertTrue(all(x["registration"] == "not_found_at_known_identifier"
                            for x in report["services"]))

    def test_windows_queries_fixed_tasks_without_shell_or_script_interpolation(self):
        commands = []
        def query(argv, **kw):
            commands.append(argv)
            return result("State=Ready\nEnabled=True\nLogonTrigger=True\n")
        report = native.observe_native_services(system_name="Windows", run=query)
        self.assertEqual(len(commands), 2)
        for cmd in commands:
            self.assertEqual(cmd[:3], ["powershell.exe", "-NoProfile", "-NonInteractive"])
            self.assertIn("Get-ScheduledTask", cmd[-1])
            self.assertNotIn("Start-ScheduledTask", cmd[-1])
            self.assertNotIn("Register-ScheduledTask", cmd[-1])
        self.assertNotEqual(commands[0][-1], commands[1][-1])
        for service in report["services"]:
            self.assertEqual(service["registration"], "registered")
            self.assertEqual(service["manager_activity"], "not_running")
            self.assertEqual(service["login_setting"], "enabled_logon_trigger")
            self.assertEqual(service["runtime_owner"], "not_verified")

    @patch.object(native.os, "getuid", return_value=456, create=True)
    def test_darwin_queries_gui_domain_and_does_not_infer_owner(self, uid):
        commands = []
        def query(argv, **kwargs):
            commands.append(argv)
            return result("system = something\nstate = running\npid = 1234\n")
        report = native.observe_native_services(system_name="Darwin", run=query)
        self.assertEqual(commands, [
            ["launchctl", "print", "gui/456/com.reasonfirst.v4-mcp"],
            ["launchctl", "print", "gui/456/com.reasonfirst.v4-tunnel"],
        ])
        self.assertTrue(all(x["registration"] == "registered" for x in report["services"]))
        self.assertTrue(all(x["manager_activity"] == "running" for x in report["services"]))
        self.assertTrue(all(x["process_identity"] == "not_verified" for x in report["services"]))

    def test_failed_truncated_timed_out_and_malformed_queries_are_unknown(self):
        examples = [
            result("secret-stuff", code=1),
            result("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", timed_out=True),
            result("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", stdout_truncated=True),
            result("LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", output_complete=False),
            result("LoadState=loaded\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n"),
        ]
        for item in examples:
            with self.subTest(item=item):
                report = native.observe_native_services(system_name="Linux",
                                                       run=lambda *a, **kw: item)
                self.assertTrue(all(s["registration"] == "unknown" for s in report["services"]))
                self.assertNotIn("secret-stuff", str(report))

    def test_native_query_exceptions_are_redacted(self):
        def fail(*a, **kw):
            raise OSError("my-private-token-should-never-appear")
        report = native.observe_native_services(system_name="Linux", run=fail)
        self.assertTrue(all(s["status"] == "unavailable" for s in report["services"]))
        self.assertNotIn("my-private-token", str(report))

    def test_unknown_platform_or_cross_host_cannot_start_manager(self):
        self.assertEqual(native.observe_native_services(system_name="FreeBSD",
                           run=lambda *a, **kw: self.fail("must not query"))["status"], "unsupported")
        other = "Windows" if native.platform.system().lower() != "windows" else "Linux"
        with patch.object(native, "run_bounded_command", side_effect=AssertionError("must not query")):
            report = native.observe_native_services(system_name=other)
        self.assertEqual(report["status"], "unsupported")

    def test_default_service_cli_does_not_query_native_manager(self):
        with patch("gitlab_agent.service_cli.collect_service_evidence") as collect, \
             patch("gitlab_agent.native_service_observation.observe_native_services",
                   side_effect=AssertionError("must not query")):
            from gitlab_agent.service_planning import ServiceEvidence, Observation
            collect.return_value = ServiceEvidence(
                "Other", {"installed_cli": {}, "registration": {"status": "not_inspected"}},
                Observation("missing", "test", "static"), None, tuple(),
                Observation("unknown", "test", "static"),
                Observation("unknown", "test", "static"))
            sink = io.StringIO()
            with redirect_stdout(sink):
                status = service_cli.main(["status", "--json"])
            self.assertEqual(status, 0)
            payload = json.loads(sink.getvalue())
            self.assertNotIn("native_services", payload)

    def test_explicit_cli_flag_is_still_read_only(self):
        with patch("gitlab_agent.service_cli.collect_service_evidence") as collect, \
             patch("gitlab_agent.native_service_observation.observe_native_services",
                   return_value={"scope": "known_identifiers_only_no_host_scan",
                                 "services": [], "status": "unsupported",
                                 "mutating": False}) as query:
            from gitlab_agent.service_planning import ServiceEvidence, Observation
            collect.return_value = ServiceEvidence(
                "Other", {"installed_cli": {}, "registration": {"status": "not_inspected"}},
                Observation("missing", "test", "static"), None, tuple(),
                Observation("unknown", "test", "static"),
                Observation("unknown", "test", "static"))
            sink = io.StringIO()
            with redirect_stdout(sink):
                status = service_cli.main(["status", "--native", "--json"])
            self.assertEqual(status, 0)
            self.assertEqual(query.call_count, 1)
            payload = json.loads(sink.getvalue())
            self.assertFalse(payload["can_execute"])
            self.assertFalse(payload["mutating"])
            self.assertEqual(payload["native_services"]["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
