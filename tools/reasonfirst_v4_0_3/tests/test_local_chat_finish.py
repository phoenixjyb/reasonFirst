from __future__ import annotations

import os
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reasonfirst_codex_bridge.controller as controller


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(Path(tmp) / "state")
        ctrl = controller.BridgeController()
        ctrl._state["sessions"]["thr_local"] = {
            "workspace_id": "abc123def456",
            "local_finish_preview": {
                "digest": "a" * 64,
                "message": "docs: add chat-only practice note",
                "allow_protected": False,
                "allow_secret_match": False,
                "ok": True,
            },
        }
        ctrl._state["workspaces"]["abc123def456"] = {
            "workspace_id": "abc123def456",
            "kind": "local",
        }

        original_load = controller.AgentSettings.load
        original_manager = controller.WorkspaceManager
        original_runner = controller.CommandRunner
        original_build = controller.build_finish_plan
        original_execute = controller.execute_finish

        calls: list[str] = []
        fake_settings = object()
        fake_manager = object()
        fake_runner = object()
        digest = "a" * 64

        class FakeSettings:
            @staticmethod
            def load():
                calls.append("settings")
                return fake_settings

        def fake_manager_ctor(settings):
            assert settings is fake_settings
            calls.append("manager")
            return fake_manager

        def fake_runner_ctor(settings, manager):
            assert settings is fake_settings
            assert manager is fake_manager
            calls.append("runner")
            return fake_runner

        def fake_build(**kwargs):
            assert kwargs["settings"] is fake_settings
            assert kwargs["manager"] is fake_manager
            assert kwargs["runner"] is fake_runner
            assert kwargs["workspace_id"] == "abc123def456"
            assert kwargs["commit_message"] == "docs: add chat-only practice note"
            calls.append("plan")
            return {
                "ok": True,
                "snapshot": {"digest": digest},
                "plan": {"commit_required": True},
            }

        def fake_execute(**kwargs):
            assert kwargs["manager"] is fake_manager
            assert kwargs["workspace_id"] == "abc123def456"
            assert kwargs["plan"]["snapshot"]["digest"] == digest
            calls.append("execute")
            return {
                "workspace_id": "abc123def456",
                "push": {"merge_request_url": "https://gitlab.example.test/group/project/-/merge_requests/7"},
            }

        controller.AgentSettings.load = FakeSettings.load
        controller.WorkspaceManager = fake_manager_ctor
        controller.CommandRunner = fake_runner_ctor
        controller.build_finish_plan = fake_build
        controller.execute_finish = fake_execute

        try:
            result = ctrl.finish(
                thread_id="thr_local",
                message="docs: add chat-only practice note",
                snapshot_digest=digest,
            )
            assert result["ok"] is True
            assert result["published"] is True
            assert result["reviewed_snapshot_digest"] == digest
            assert calls == ["settings", "manager", "runner", "plan", "execute"]

            calls.clear()
            try:
                ctrl.finish(
                    thread_id="thr_local",
                    message="docs: add chat-only practice note",
                    snapshot_digest="b" * 64,
                )
            except controller.BridgeError as exc:
                assert "does not match the latest reviewed finish preview" in str(exc)
            else:
                raise AssertionError("stale snapshot must fail closed")
            assert "execute" not in calls

            calls.clear()
            ctrl._state["sessions"]["thr_local"]["local_finish_preview"]["digest"] = "b" * 64
            try:
                ctrl.finish(
                    thread_id="thr_local",
                    message="docs: add chat-only practice note",
                    snapshot_digest="b" * 64,
                )
            except controller.BridgeError as exc:
                assert "changed after ChatGPT reviewed" in str(exc)
            else:
                raise AssertionError("workspace change after preview must fail closed")
            assert "execute" not in calls
            ctrl._state["sessions"]["thr_local"]["local_finish_preview"]["digest"] = digest

            def blocked_build(**kwargs):
                calls.append("blocked-plan")
                return {
                    "ok": False,
                    "snapshot": {"digest": digest},
                    "blockers": ["validation failed"],
                    "plan": {},
                }

            controller.build_finish_plan = blocked_build
            calls.clear()
            blocked = ctrl.finish(
                thread_id="thr_local",
                message="docs: add chat-only practice note",
                snapshot_digest=digest,
            )
            assert blocked["ok"] is False
            assert blocked["published"] is False
            assert blocked["reason"] == "finish_plan_blocked"
            assert "execute" not in calls

            try:
                ctrl.finish(
                    thread_id="thr_local",
                    message="docs: add chat-only practice note",
                    snapshot_digest=digest,
                    allow_protected=True,
                )
            except controller.BridgeError as exc:
                assert "Protected-path override differs" in str(exc)
            else:
                raise AssertionError("unreviewed protected override must fail closed")

            try:
                ctrl.finish(
                    thread_id="thr_local",
                    message="docs: add chat-only practice note",
                    snapshot_digest="not-a-digest",
                )
            except controller.BridgeError as exc:
                assert "64-character snapshot_digest" in str(exc)
            else:
                raise AssertionError("invalid digest must fail closed")
        finally:
            controller.AgentSettings.load = original_load
            controller.WorkspaceManager = original_manager
            controller.CommandRunner = original_runner
            controller.build_finish_plan = original_build
            controller.execute_finish = original_execute
            ctrl.close()

    print("chat-only local finish snapshot gate: OK")


if __name__ == "__main__":
    main()
