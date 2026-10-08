"""Do not turn saved values into verified behavior for an unknown launcher."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import launch_review as launch


class SavedPolicyGuardTests(unittest.TestCase):
    def review(self, *, verified=True, **env):
        raw = plistlib.dumps({
            "Label": launch.deployment.LABEL,
            "EnvironmentVariables": env,
        })
        pair = {"identity": {"deployment": {"registration": {
            "registration_sha256": hashlib.sha256(raw).hexdigest(),
        }}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(launch.pairing, "check", return_value=pair))
            stack.enter_context(patch.object(launch.deployment, "_home", return_value=Path("/fixture")))
            stack.enter_context(patch.object(launch.deployment, "_registration_bytes", return_value=raw))
            stack.enter_context(patch.object(launch, "_launcher", return_value={
                "fully_verified": verified,
                "source_profile": "reviewed-v051-legacy" if verified else "not_audited_in_this_slice",
            }))
            stack.enter_context(patch.object(launch, "_target", return_value={
                "control_route_exposed": False,
            }))
            stack.enter_context(patch.object(Path, "open", side_effect=AssertionError("No application reads")))
            stack.enter_context(patch("subprocess.run", side_effect=AssertionError("No process launch")))
            stack.enter_context(patch("socket.socket", side_effect=AssertionError("No listener")))
            result = launch.plan(runtime_id="a" * 64, expect_pairing_digest="b" * 64)
        self.assertTrue(result["ok"])
        for name in ("compatibility_verified", "activation_authorized", "ready_for_activation", "commands_executed"):
            self.assertIs(result[name], False)
        self.assertEqual(result["proposed_actions"], [])
        return result

    def test_unaudited_sidecar_cannot_inherit_legacy_endpoint_or_control_semantics(self):
        for mode in ("true", "false"):
            with self.subTest(mode=mode):
                result = self.review(verified=False, RF_MCP_READ_ONLY=mode,
                                     RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="false", RF_MCP_PORT="8765")
                policy = result["identity"]["evidence"]["saved_policy"]
                self.assertEqual(policy["legacy_control"], "unknown")
                self.assertEqual(policy["mode"], "unknown")
                self.assertIsNone(policy["remote_push"])
                self.assertIsNone(policy["endpoint"]["host"])
                self.assertIsNone(policy["endpoint"]["path"])
                self.assertIsNone(policy["endpoint"]["port"])
                self.assertFalse(result["saved_control_policy_compatible"])
                self.assertIn("sidecar_bootstrap_and_imports_not_audited", result["blockers"])
                self.assertIn("legacy_control_requirement_unknown", result["blockers"])

    def test_unaudited_saved_settings_remain_visible_as_values_not_behavior(self):
        result = self.review(verified=False, RF_MCP_READ_ONLY="true", RF_MCP_PORT="8765",
                             RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="true", RF_BRIDGE_CONFIG="private-config-path")
        policy = result["identity"]["evidence"]["saved_policy"]
        self.assertEqual(policy["saved_environment_policy"], {
            "read_only": True, "remote_push": True, "port": 8765,
        })
        self.assertEqual(policy["interpretation_basis"], "unverified_launcher_values_only")
        self.assertNotIn("private-config-path", json.dumps(result))

    def test_recorded_prelaunch_override_blocks_positive_control_comparison(self):
        for name in ("BASH_ENV", "ENV", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "PYTHONHOME"):
            with self.subTest(name=name):
                result = self.review(RF_MCP_READ_ONLY="true", RF_MCP_PORT="8765", **{name: "private-override-value"})
                self.assertTrue(result["launcher_source_verified"])
                self.assertFalse(result["saved_control_policy_compatible"])
                self.assertIn("prelaunch_override_requires_review", result["blockers"])
                self.assertNotIn("private-override-value", json.dumps(result))

    def test_target_rejects_push_setting_even_when_old_readonly_surface_ignores_it(self):
        result = self.review(RF_MCP_READ_ONLY="true", RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="true")
        self.assertIs(result["identity"]["evidence"]["saved_policy"]["remote_push"], False)
        self.assertIn("target_rejects_saved_remote_push_setting", result["blockers"])

    def test_known_readonly_saved_comparison_still_does_not_authorize_activation(self):
        result = self.review(RF_MCP_READ_ONLY="true", RF_MCP_PORT="8765",
                             RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="false")
        self.assertTrue(result["saved_control_policy_compatible"])
        self.assertEqual(result["identity"]["evidence"]["saved_policy"]["mode"], "read-only")
        self.assertNotIn("target_rejects_saved_remote_push_setting", result["blockers"])
        self.assertTrue(set(launch.UNRESOLVED).issubset(result["blockers"]))

    def test_legacy_full_chat_control_mismatch_remains_explicit(self):
        result = self.review(RF_MCP_READ_ONLY="false", RF_MCP_PORT="8765",
                             RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="false")
        self.assertFalse(result["saved_control_policy_compatible"])
        self.assertIn("legacy_control_required_but_target_has_no_control", result["blockers"])


if __name__ == "__main__":
    unittest.main()
