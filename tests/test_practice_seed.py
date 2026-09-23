from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.practice_seed import (
    PRACTICE_KIT_FILES,
    build_practice_seed_plan,
    execute_practice_seed,
)
from gitlab_agent.workspace import WorkspaceManager


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout.strip()


class PracticeSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config_file = self.root / ".env"
        config_file.write_text("GITLAB_BASE_URL=https://gitlab.example.test\n", encoding="utf-8")
        if os.name != "nt":
            config_file.chmod(0o600)

        self.settings = AgentSettings(
            config_file=config_file,
            gitlab_base_url="https://gitlab.example.test",
            api_token="read-token",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="write-token",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/reasonfirst-practice"},
            require_write_allowlist=True,
            workspace_root=self.root / "manager",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python3"},
            command_timeout_seconds=60,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name="ReasonFirst Test",
            git_author_email="reasonfirst@example.test",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _remote(self, files: dict[str, str]) -> tuple[Path, str]:
        source = self.root / f"source-{len(list(self.root.glob('source-*')))}"
        remote = self.root / f"remote-{len(list(self.root.glob('remote-*')))}.git"
        source.mkdir()
        _git("init", "-b", "main", cwd=source)
        _git("config", "user.name", "ReasonFirst Test", cwd=source)
        _git("config", "user.email", "reasonfirst@example.test", cwd=source)
        for relative, content in files.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git("add", "-A", cwd=source)
        _git("commit", "-m", "seed starter", cwd=source)
        _git("clone", "--bare", str(source), str(remote))
        return remote, remote.resolve().as_uri()

    def _empty_remote(self) -> tuple[Path, str]:
        remote = self.root / f"empty-remote-{len(list(self.root.glob('empty-remote-*')))}.git"
        _git("init", "--bare", str(remote))
        return remote, remote.resolve().as_uri()

    def _manager(self, remote_url: str) -> WorkspaceManager:
        return WorkspaceManager(
            self.settings,
            url_resolver=lambda project: remote_url,
        )

    def test_empty_project_can_be_planned_and_seeded(self) -> None:
        remote, remote_url = self._empty_remote()
        manager = self._manager(remote_url)

        plan = build_practice_seed_plan(
            settings=self.settings,
            manager=manager,
            project="team/reasonfirst-practice",
            ref="main",
        )
        self.assertTrue(plan["ok"])
        self.assertIsNone(plan["base_sha"])
        self.assertEqual(plan["existing_paths"], [])

        result = execute_practice_seed(
            settings=self.settings,
            manager=manager,
            project="team/reasonfirst-practice",
            ref="main",
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["previous_base_sha"])
        self.assertEqual(result["seed_commit"], result["remote_commit_sha"])
        remote_head = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        self.assertEqual(remote_head, result["seed_commit"])

    def test_readme_only_project_can_be_planned_and_seeded(self) -> None:
        remote, remote_url = self._remote(
            {"README.md": "# Empty GitLab starter\n"}
        )
        manager = self._manager(remote_url)

        plan = build_practice_seed_plan(
            settings=self.settings,
            manager=manager,
            project="team/reasonfirst-practice",
            ref="main",
        )

        self.assertTrue(plan["ok"])
        self.assertTrue(plan["seedable"])
        self.assertFalse(plan["already_seeded"])
        self.assertEqual(plan["existing_paths"], ["README.md"])
        self.assertTrue(plan["baseline_validation"]["passed"])
        self.assertFalse(plan["write_performed"])

        result = execute_practice_seed(
            settings=self.settings,
            manager=manager,
            project="team/reasonfirst-practice",
            ref="main",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["write_performed"])
        self.assertTrue(result["baseline_validation"]["passed"])
        self.assertEqual(result["seed_commit"], result["remote_commit_sha"])
        self.assertIn(".actualcoder.yaml", result["changed_paths"])
        self.assertIn("EXERCISE.md", result["changed_paths"])

        remote_head = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        self.assertEqual(remote_head, result["seed_commit"])
        contract = _git(
            "--git-dir",
            str(remote),
            "show",
            "refs/heads/main:.actualcoder.yaml",
        )
        self.assertIn("name: unit-tests", contract)
        exercise = _git(
            "--git-dir",
            str(remote),
            "show",
            "refs/heads/main:EXERCISE.md",
        )
        self.assertIn("Stage 1: strict summary", exercise)

        second_plan = build_practice_seed_plan(
            settings=self.settings,
            manager=manager,
            project="team/reasonfirst-practice",
            ref="main",
        )
        self.assertFalse(second_plan["ok"])
        self.assertTrue(second_plan["already_seeded"])

    def test_nontrivial_project_is_refused(self) -> None:
        _, remote_url = self._remote(
            {
                "README.md": "# Existing project\n",
                "app.py": "print('real app')\n",
            }
        )
        plan = build_practice_seed_plan(
            settings=self.settings,
            manager=self._manager(remote_url),
            project="team/reasonfirst-practice",
            ref="main",
        )

        self.assertFalse(plan["ok"])
        self.assertFalse(plan["seedable"])
        self.assertIn("app.py", plan["unexpected_existing_paths"])
        self.assertFalse(plan["write_performed"])

    def test_already_seeded_project_is_refused(self) -> None:
        _, remote_url = self._remote(
            {
                "README.md": "# Practice\n",
                ".actualcoder.yaml": "version: 1\n",
            }
        )
        plan = build_practice_seed_plan(
            settings=self.settings,
            manager=self._manager(remote_url),
            project="team/reasonfirst-practice",
            ref="main",
        )

        self.assertFalse(plan["ok"])
        self.assertTrue(plan["already_seeded"])
        self.assertFalse(plan["seedable"])

    def test_packaged_kit_matches_repository_example_exactly(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        example_root = repository_root / "examples" / "practice-lab"
        packaged_root = repository_root / "src" / "gitlab_agent" / "practice_kit"

        example_files = sorted(
            path.relative_to(example_root).as_posix()
            for path in example_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )
        packaged_files = sorted(
            path.relative_to(packaged_root).as_posix()
            for path in packaged_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
        )

        self.assertEqual(example_files, sorted(PRACTICE_KIT_FILES))
        self.assertEqual(packaged_files, sorted(PRACTICE_KIT_FILES))
        for relative in PRACTICE_KIT_FILES:
            self.assertEqual(
                (packaged_root / relative).read_bytes(),
                (example_root / relative).read_bytes(),
                relative,
            )


if __name__ == "__main__":
    unittest.main()
