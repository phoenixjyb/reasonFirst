from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.evidence import build_evidence_pack
from gitlab_agent.workspace import WorkspaceManager


def run(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return proc.stdout.strip()


class EvidencePackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        seed = root / "seed"
        remote = root / "remote.git"
        seed.mkdir()
        run("git", "init", "-b", "main", cwd=seed)
        run("git", "config", "user.name", "Test User", cwd=seed)
        run("git", "config", "user.email", "test@example.com", cwd=seed)
        (seed / "README.md").write_text("seed\n", encoding="utf-8")
        run("git", "add", "README.md", cwd=seed)
        run("git", "commit", "-m", "Initial commit", cwd=seed)
        run("git", "clone", "--bare", str(seed), str(remote))

        self.remote = remote
        self.settings = AgentSettings(
            config_file=root / ".env",
            gitlab_base_url="https://gitlab.example.invalid",
            api_token="",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "agent",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python"},
            command_timeout_seconds=30,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name="Agent Test",
            git_author_email="agent@example.com",
        )
        self.manager = WorkspaceManager(
            self.settings,
            url_resolver=lambda project: str(self.remote),
        )
        created = self.manager.create_workspace(
            "team/project",
            task_slug="evidence",
        )
        self.workspace_id = str(created["workspace_id"])
        self.manager.ensure_task_spec(
            self.workspace_id,
            task_slug="evidence",
            goal="Review the implementation",
            requested_backend="auto",
            acceptance_criteria=["Evidence remains bounded and redacted"],
            non_goals=["Do not publish"],
        )
        self.manager.record_attempt(
            self.workspace_id,
            source="start",
            goal="Review the implementation",
            requested_backend="auto",
            selected_backend="codex-cli",
            ci_context_included=False,
            worker_policy={
                "backend": "codex",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_evidence_pack_redacts_recursive_task_diff_and_ci_content(self) -> None:
        fake_pat = "glpat-" + ("z" * 26)
        self.manager.write_file(
            self.workspace_id,
            "README.md",
            f"seed\ncredential={fake_pat}\n",
        )

        ci_feedback = {
            "found": True,
            "stale_for_workspace": True,
            "pipeline": {
                "status": "failed",
                "sha": "other-head",
            },
            "repair_context": f"SECRET_TOKEN={fake_pat}",
        }
        pack = build_evidence_pack(
            settings=self.settings,
            manager=self.manager,
            workspace_id=self.workspace_id,
            ci_feedback=ci_feedback,
        )

        rendered = str(pack)
        self.assertNotIn(fake_pat, rendered)
        self.assertIn("[REDACTED", rendered)
        self.assertFalse(pack["complete_for_human_review"])
        self.assertTrue(any("stale" in item.lower() for item in pack["warnings"]))
        self.assertEqual(pack["task"]["attempt_count"], 1)
        self.assertEqual(
            pack["task"]["task_spec"]["acceptance_criteria"],
            ["Evidence remains bounded and redacted"],
        )
        self.assertTrue(pack["redactions"])

    def test_truncated_diff_marks_evidence_incomplete(self) -> None:
        tiny_settings = replace(self.settings, max_output_bytes=200)
        manager = WorkspaceManager(
            tiny_settings,
            url_resolver=lambda project: str(self.remote),
        )
        created = manager.create_workspace("team/project", task_slug="truncate")
        workspace_id = str(created["workspace_id"])
        manager.ensure_task_spec(
            workspace_id,
            task_slug="truncate",
            goal="Generate bounded evidence",
            requested_backend="auto",
        )
        manager.write_file(
            workspace_id,
            "large.txt",
            "\n".join(f"line-{index}-" + ("x" * 40) for index in range(40)) + "\n",
        )

        pack = build_evidence_pack(
            settings=tiny_settings,
            manager=manager,
            workspace_id=workspace_id,
        )
        self.assertTrue(pack["review"]["diff"]["truncated"])
        self.assertFalse(pack["complete_for_human_review"])
        self.assertTrue(
            any("truncated" in item.lower() for item in pack["warnings"])
        )

    def test_requested_missing_or_running_ci_is_explicitly_incomplete(self) -> None:
        missing = build_evidence_pack(
            settings=self.settings,
            manager=self.manager,
            workspace_id=self.workspace_id,
            ci_feedback={
                "found": False,
                "stale_for_workspace": False,
                "pipeline": None,
            },
        )
        self.assertFalse(missing["complete_for_human_review"])
        self.assertTrue(
            any("no pipeline" in item.lower() for item in missing["warnings"])
        )

        running = build_evidence_pack(
            settings=self.settings,
            manager=self.manager,
            workspace_id=self.workspace_id,
            ci_feedback={
                "found": True,
                "stale_for_workspace": False,
                "pipeline": {"status": "running"},
            },
        )
        self.assertFalse(running["complete_for_human_review"])
        self.assertTrue(
            any("incomplete" in item.lower() for item in running["warnings"])
        )

    def test_evidence_pack_is_read_only_for_task_history(self) -> None:
        before = self.manager.task_context(self.workspace_id)
        first = build_evidence_pack(
            settings=self.settings,
            manager=self.manager,
            workspace_id=self.workspace_id,
        )
        second = build_evidence_pack(
            settings=self.settings,
            manager=self.manager,
            workspace_id=self.workspace_id,
        )
        after = self.manager.task_context(self.workspace_id)

        self.assertEqual(before, after)
        self.assertEqual(
            first["workspace"]["head"],
            second["workspace"]["head"],
        )


if __name__ == "__main__":
    unittest.main()
