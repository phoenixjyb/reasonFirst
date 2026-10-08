"""Read-only HTTP-launch review boundaries and nonsecret policy tests."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import launch_review as launch
from gitlab_agent.upgrade import pairing, runtime


def saved_policy(**env):
    return plistlib.dumps({"Label": "com.reasonfirst.v4-mcp", "EnvironmentVariables": env})


class StaticLaunchReviewTests(unittest.TestCase):
    def test_source_profiles_are_pinned_to_checked_in_code(self):
        root = Path(__file__).resolve().parents[1]
        legacy = root / "tools/reasonfirst_v4_0_3"
        for name, blob in launch.KNOWN_LEGACY_BLOBS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    launch._git_blob((legacy / name).read_text(encoding="utf-8").encode("utf-8")),
                    blob,
                )
        for name, blob in launch.KNOWN_TARGET_BLOBS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    launch._git_blob((root / "src/gitlab_agent" / name).read_text(encoding="utf-8").encode("utf-8")),
                    blob,
                )

    def test_full_chat_saved_policy_requires_legacy_control(self):
        value = launch._saved_policy(saved_policy(
            RF_MCP_PORT="8765", RF_MCP_READ_ONLY="false",
            RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="false",
            RF_BRIDGE_CONFIG="/synthetic/not-opened/bridge.yaml",
            PRIVATE_SECRET_FIXTURE="dont-print",
        ))
        self.assertEqual(value["endpoint"]["port"], 8765)
        self.assertEqual(value["mode"], "full-chat")
        self.assertEqual(value["legacy_control"], "required")
        self.assertFalse(value["remote_push"])
        self.assertEqual(value["configuration_reference_presence"]["RF_BRIDGE_CONFIG"], True)
        self.assertNotIn("dont-print", str(value))
        self.assertNotIn("/synthetic/not-opened", str(value))

    def test_readonly_never_exposes_legacy_control_or_push(self):
        value = launch._saved_policy(saved_policy(
            RF_MCP_READ_ONLY="yes", RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH="true"))
        self.assertEqual(value["mode"], "read-only")
        self.assertEqual(value["legacy_control"], "absent")
        self.assertFalse(value["remote_push"])

    def test_absent_policy_is_not_mistaken_for_default_full_chat(self):
        value = launch._saved_policy(saved_policy())
        self.assertEqual(value["mode"], "unknown")
        self.assertEqual(value["legacy_control"], "unknown")
        self.assertEqual(value["endpoint"]["port"], None)
        self.assertTrue(value["inherited_environment_not_inspected"])

    def test_invalid_saved_port_and_policy_fail_without_leaking_values(self):
        for key, value in (("RF_MCP_PORT", "0"), ("RF_MCP_PORT", "65536"),
                           ("RF_MCP_PORT", "-12"), ("RF_MCP_PORT", "not-a-port"),
                           ("RF_MCP_READ_ONLY", "private-secret"),
                           ("RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", "private-secret")):
            with self.subTest(key=key), self.assertRaises(launch.LaunchReviewError) as ctx:
                launch._saved_policy(saved_policy(**{key:value}))
            self.assertNotIn(value, str(ctx.exception))

    def test_duplicate_plist_keys_rejected(self):
        raw = saved_policy(RF_MCP_READ_ONLY="true")
        self.assertTrue(raw.startswith(b"<?xml"))
        raw = raw.replace(b"<key>Label</key>", b"<key>Label</key><string>com.reasonfirst.v4-mcp</string><key>Label</key>", 1)
        with self.assertRaises(launch.LaunchReviewError):
            launch._saved_policy(raw)

    def test_sidecar_is_explicitly_not_source_attested(self):
        with patch.object(launch, "_verify_blob", side_effect=AssertionError("must not read untrusted bootstrap")):
            result = launch._launcher(Path("/test-home"), {
                "layout":"versioned_http_sidecar",
                "program":"/test-home/.local/share/reasonfirst/upgrades/uv-http-v0.5.1-fixture/run_reasonfirst.sh"})
        self.assertFalse(result["fully_verified"])
        self.assertEqual(result["source_profile"], "not_audited_in_this_slice")

    def test_unknown_source_path_is_not_followed(self):
        with patch.object(launch, "_verify_blob", side_effect=AssertionError("must not read")):
            with self.assertRaises(launch.LaunchReviewError):
                launch._launcher(Path("/example"), {
                    "layout":"legacy_staged_http", "program":"/private/unrelated"})
    
    def test_legacy_profile_checks_only_known_four_sources(self):
        examined=[]
        def verify(home, path, expected):
            examined.append((path.name, expected))
            return {"path":str(path), "blob":expected, "bytes":8}
        home=Path("/synthetic")
        with patch.object(launch, "_verify_blob", side_effect=verify):
            report=launch._launcher(home, {
                "layout":"legacy_staged_http",
                "program":str(home / ".local/share/reasonfirst/v4-service/tools/codex_web_bridge/run_reasonfirst.sh")})
        self.assertTrue(report["fully_verified"])
        self.assertEqual(examined, list(launch.KNOWN_LEGACY_BLOBS.items()))

    def test_target_reads_only_manifest_and_two_known_package_sources(self):
        home=Path("/synthetic")
        root=home / ".local/share/reasonfirst/runtimes" / ("a"*64)
        manifest={"observation":{"origins":["lib/python3.12/site-packages/gitlab_agent/bridge_http.py"]}}
        raw=json.dumps(manifest).encode()
        paths=[]
        def fake_read(h,p,*,limit=131072):
            paths.append(p)
            return raw, {"bytes":len(raw), "blob":"ignored"}
        def verify(h,p,expected):
            paths.append(p)
            return {"path":str(p),"blob":expected,"bytes":1}
        pair={"identity":{"runtime":{"runtime_path":str(root),
                   "record_sha256":hashlib.sha256(raw).hexdigest()}}}
        with patch.object(launch,"_read_named",side_effect=fake_read),patch.object(launch,"_verify_blob",side_effect=verify):
            result=launch._target(home,pair)
        self.assertTrue(result["verified_known_source_files"])
        self.assertEqual(len(paths),3)
        self.assertEqual([p.name for p in paths],["runtime.json","bridge_http.py","bridge_mcp.py"])

    def test_unknown_target_profile_is_rejected_before_source_reads(self):
        raw=json.dumps({"observation":{"origins":["/external/bridge_http.py"]}}).encode()
        pair={"identity":{"runtime":{"runtime_path":"/synthetic/runtime",
            "record_sha256":hashlib.sha256(raw).hexdigest()}}}
        with patch.object(launch,"_read_named",return_value=(raw,{"bytes":len(raw)})),              patch.object(launch,"_verify_blob",side_effect=AssertionError("must not read arbitrary target")):
            with self.assertRaisesRegex(launch.LaunchReviewError,"target_http_profile_unknown"):
                launch._target(Path("/synthetic"),pair)

    def test_plan_keeps_all_actual_compatibility_gates_blocked(self):
        identity={"schema_version":1, "scope":"fixture"}
        reviewed={"identity":identity}
        evidence={"launcher":{"fully_verified":True},
                  "saved_policy":{"legacy_control":"required", "remote_push":False,
                      "endpoint":{"port":8765}, "prelaunch_code_override_recorded":False},
                  "target":{"control_route_exposed":False}}
        with patch.object(launch.pairing,"check",return_value=reviewed) as recheck,              patch.object(launch.deployment,"_home",return_value=Path("/fixture")),              patch.object(launch,"_observe",return_value=evidence) as observe:
            report=launch.plan(runtime_id="a"*64,expect_pairing_digest="b"*64)
            validated=launch.check(runtime_id="a"*64,expect_pairing_digest="b"*64,
                                   expect_digest=report["plan_digest"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["launcher_source_verified"])
        self.assertFalse(report["compatibility_verified"])
        self.assertFalse(report["activation_authorized"])
        self.assertFalse(report["ready_for_activation"])
        self.assertEqual(report["proposed_actions"],[])
        self.assertIn("legacy_control_required_but_target_has_no_control",report["blockers"])
        self.assertTrue(validated["review_digest_matches"])
        self.assertEqual(recheck.call_count,4)
        self.assertEqual(observe.call_count,4)

    def test_mismatch_returns_no_partial_identity_or_digest(self):
        evidence={"launcher":{"fully_verified":False},
                  "saved_policy":{"legacy_control":"unknown","remote_push":None,
                        "endpoint":{"port":None},"prelaunch_code_override_recorded":False},
                  "target":{"control_route_exposed":False}}
        with patch.object(launch.pairing,"check",return_value={"identity":{}}),              patch.object(launch.deployment,"_home",return_value=Path("/fixture")),              patch.object(launch,"_observe",return_value=evidence):
            report=launch.run_command("launch-check",runtime_id="a"*64,
                       expect_pairing_digest="b"*64,expect_digest="c"*64)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_code"],"launch_review_changed")
        self.assertNotIn("plan_digest",report)
        self.assertNotIn("identity",report)

    def test_unsupported_platform_propagates_no_storage_access(self):
        with patch.object(launch.pairing,"check",
                          side_effect=pairing.PairingError("unsupported_pairing_platform")),              patch.object(launch.deployment,"_home",side_effect=AssertionError("no HOME")):
            report=launch.run_command("launch-plan",runtime_id="a"*64,expect_pairing_digest="b"*64)
        self.assertEqual(report["error_code"],"pairing_not_verified")

    def test_cli_launch_review_is_lazily_dispatched_and_stays_nonmutating(self):
        payload={"ok":True,"operation":"launch-plan","ready_for_activation":False,
                 "compatibility_verified":False, "mutating":False}
        output=io.StringIO()
        with patch.object(launch,"run_command",return_value=payload) as called, redirect_stdout(output):
            code=runtime.main(["launch-plan","--runtime-id","a"*64,
                "--expect-pairing-digest","b"*64,"--json"])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue()),payload)
        called.assert_called_once()

    def test_cli_does_not_offer_restart_or_force_flags(self):
        for flag in ("--activate","--force","--yes"):
            with self.subTest(flag=flag), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runtime.main(["launch-plan","--runtime-id","a"*64,
                             "--expect-pairing-digest","b"*64,flag])

    def test_docs_distinguish_static_review_from_activation(self):
        root=Path(__file__).resolve().parents[1]
        for name in ("LAUNCH_REVIEW.md","LAUNCH_REVIEW_CN.md"):
            raw=(root/"docs"/name).read_text(encoding="utf-8")
            for word in ("launch-plan","launch-check","/control","v0.5.1",
                         "compatibility_verified","ready_for_activation"):
                self.assertIn(word,raw)


if __name__=="__main__":
    unittest.main()
