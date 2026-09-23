from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gitlab_agent.worker_policy import default_worker_policy
import reasonfirst_codex_bridge.controller as controller


class FakeApp:
    def __init__(self, event_handler=None, backend_name="standalone-local", **kwargs):
        self.backend_name = backend_name
        self.event_handler = event_handler
        self.name = ""
        self.goal = ""
        self.metadata = {}
        self.policy_evidence = {}


    @classmethod
    def global_config_local(cls, *, event_handler=None, server_request_handler=None, approval_request_handler=None):
        return cls(event_handler=event_handler, server_request_handler=server_request_handler, approval_request_handler=approval_request_handler, backend_name="global-config-local")

    @classmethod
    def desktop_preferred(cls, *, event_handler=None, server_request_handler=None, approval_request_handler=None, required=False):
        return cls(event_handler=event_handler, server_request_handler=server_request_handler, approval_request_handler=approval_request_handler, backend_name="desktop-managed-test")

    @classmethod
    def remote_ssh(cls, host, *, remote_codex="codex", event_handler=None, server_request_handler=None, approval_request_handler=None, connect_timeout=8):
        return cls(event_handler=event_handler, server_request_handler=server_request_handler, approval_request_handler=approval_request_handler, backend_name=f"ssh:{host}")

    def admin_requirements(self):
        return {}

    def start_thread(self, *, cwd, policy=None, dynamic_tools=None, sandbox_mode=None):
        assert policy is not None
        assert policy.model == "gpt-5.6-sol"
        self.policy_evidence = {
            "satisfied": True,
            "verification_scope": "fake",
            "requested": policy.to_dict(),
            "resolved": {
                "model": policy.model,
                "reasoning_effort": policy.reasoning_effort,
            },
            "runtime_violations": [],
        }
        return "thr_test"

    def start_turn(self, *, thread_id, cwd, prompt, policy=None, network_access=None, sandbox_mode=None):
        assert policy is not None
        assert policy.reasoning_effort == "high"
        assert thread_id == "thr_test"
        assert "implement reviewed plan" in prompt
        return "turn_test"

    def set_thread_name(self, thread_id, name):
        self.name = name

    def set_thread_goal(self, thread_id, objective):
        self.goal = objective
        return {"goal": {"objective": objective}}

    def update_thread_metadata(self, thread_id, **kwargs):
        self.metadata = kwargs
        return {"thread": {"id": thread_id, **kwargs}}

    def list_threads(self, **kwargs):
        return {"data": [{"id": "thr_test", "name": self.name}]}

    def resume_thread(self, thread_id, *, policy=None):
        assert policy is not None
        self.policy_evidence = {
            "status": "satisfied",
            "satisfied": True,
            "verification_scope": "fake-resume",
            "requested": policy.to_dict(),
            "resolved": {
                "model": policy.model,
                "reasoning_effort": policy.reasoning_effort,
            },
            "runtime_violations": [],
        }
        return dict(self.policy_evidence)

    def read_thread(self, thread_id, include_turns=False):
        return {"thread": {"id": thread_id, "name": self.name, "status": {"type": "idle"}}}

    def worker_policy_evidence(self, thread_id):
        return dict(self.policy_evidence)

    def close(self):
        return None


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "workspace-root"
        worktree = root / "worktrees" / "abc123def456"
        (worktree / "src" / "perception").mkdir(parents=True)
        (worktree / "src" / "perception" / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (worktree / "reports").mkdir()
        (worktree / "reports" / "metrics.json").write_text(json.dumps({"latency_ms": 12.3, "accuracy": 0.91}), encoding="utf-8")
        state = Path(tmp) / "bridge-state"
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(state)

        original_app = controller.AppServerClient
        original_run = controller._run_json
        controller.AppServerClient = FakeApp

        def fake_run(argv, **kwargs):
            args = list(argv)
            if args[-1] == "config":
                return {
                    "workspace_root": str(root),
                    "api_token_set": False,
                    "git_token_set": True,
                    "gitlab_base_url": "https://gitlab.example.com",
                }
            if "project-config" in args:
                return {"project": "group/project", "found": False, "valid": True}
            if "start" in args:
                return {
                    "workspace": {"workspace_id": "abc123def456", "project": "group/project"},
                    "worktree_path": str(worktree),
                    "agent_prompt": "unused until start_codex",
                }
            if "status" in args:
                return {
                    "workspace_id": "abc123def456",
                    "project": "group/project",
                    "worktree_path": str(worktree),
                    "dirty": False,
                    "branch": "chatgpt/optimize-abc123",
                    "head": "abc123",
                    "base_sha": "base",
                }
            if "files" in args:
                return {"workspace_id": "abc123def456", "path": ".", "items": [{"path": "src/perception/a.py", "type": "file", "size": 14}]}
            if "read" in args:
                return {"workspace_id": "abc123def456", "path": "src/perception/a.py", "content": "one\ntwo\nthree\n"}
            if "diff" in args:
                return {"workspace_id": "abc123def456", "base_sha": "base", "truncated": False, "original_bytes": 10, "diff": "diff --git a/a b/a\n+new\n"}
            if "resume" in args:
                return {"agent_prompt": "implement reviewed plan"}
            raise AssertionError(args)

        controller._run_json = fake_run
        try:
            ctrl = controller.BridgeController()
            ctrl._codex_policy=lambda: default_worker_policy("codex")
            routed = ctrl.dispatch_request(
                gitlab_url="https://gitlab.example.com/group/project",
                module="src/perception",
                request="Analyze and optimize latency",
            )
            assert routed["auto_routed"] is True
            assert routed["project"] == "group/project"
            assert routed["focus"] == "src/perception"
            wid = routed["workspace_id"]
            read = ctrl.read(workspace_id=wid, path="src/perception/a.py", start_line=2, end_line=3)
            assert read["content"] == "2: two\n3: three"
            diff = ctrl.diff(workspace_id=wid)
            assert "+new" in diff["diff"]
            artifacts = ctrl.artifacts(workspace_id=wid, path="reports", changed_only=False)
            metric = next(item for item in artifacts["items"] if item["path"] == "reports/metrics.json")
            assert "latency_ms" in metric["content"]
            started = ctrl.start_codex(workspace_id=wid, goal="implement reviewed plan")
            assert started["thread_id"] == "thr_test"
            assert started["turn_id"] == "turn_test"
            assert started["thread_name"].startswith("[ReasonFirst] group/project")
            app = next(iter(ctrl._apps.values()))
            assert app.goal == "implement reviewed plan"
            assert app.metadata["branch"] == "chatgpt/optimize-abc123"
            bundle = ctrl.review_bundle(thread_id="thr_test", artifact_path="reports")
            assert bundle["artifacts"]["items"]
            ctrl.close()
        finally:
            controller.AppServerClient = original_app
            controller._run_json = original_run

    print("controller v3 local closed-loop flow: OK")


if __name__ == "__main__":
    main()
