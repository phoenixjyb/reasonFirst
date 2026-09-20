"""Integration with the existing production WorkspaceManager and installed CLI."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_workspace_publication as fixtures
from gitlab_agent.https_migration import build_plan, apply_plan
from gitlab_agent.workspace import WorkspaceManager


class ProductionMigrationTests(unittest.TestCase):
    def test_existing_controller_can_resume_same_state_after_migration(self):
        fixture = fixtures.WorkspacePublicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("GIT_", "GITLAB_", "XDG_"))}
        home = fixture.root / "private-home"
        home.mkdir()
        env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home / "xdg"))
        with patch.dict(os.environ, env, clear=True):
            old, new = "http://gitlab.example.test", "https://gitlab.example.test"
            config_file = home / ".env"
            config_file.write_text(
                f"GITLAB_BASE_URL={old}\nGITLAB_WORKSPACE_ROOT={fixture.settings.workspace_root}\n"
                "GITLAB_ALLOWED_PROJECTS=team/project\nGITLAB_VERIFY_SSL=true\n", encoding="utf-8")
            fixture.commit("unpublished")
            fixture.manager.write_file(fixture.workspace_id, "pending.txt", "do not discard\n")
            fixture.git("--git-dir", str(fixture.repo), "remote", "set-url", "origin", old + "/team/project.git")
            state = fixture.manager.get_state(fixture.workspace_id)
            state.merge_request_url = old + "/team/project/-/merge_requests/123"
            fixture.manager._save_state(state)
            before = fixture.manager.status(fixture.workspace_id)
            apply_plan(build_plan(config_file=config_file, old_url=old, new_url=new), workers_stopped=True)
            settings = replace(fixture.settings, gitlab_base_url=new)
            manager = WorkspaceManager(settings)
            after = manager.status(fixture.workspace_id)
            for key in ("workspace_id", "head", "branch", "base_sha", "dirty", "status_porcelain"):
                self.assertEqual(before[key], after[key])
            self.assertEqual(after["merge_request_url"], new + "/team/project/-/merge_requests/123")
            self.assertEqual(manager.clone_url("team/project"),
                             fixture.git("--git-dir", str(fixture.repo), "remote", "get-url", "origin"))
            self.assertIn("do not discard", manager.read_file(fixture.workspace_id, "pending.txt")["content"])
            read = manager.read_remote_text_file("team/project", "README.md", ref=state.base_sha, refresh_remote=False)
            self.assertTrue(read["exists"])

    def test_packaged_maintenance_entry_point_is_available(self):
        path = shutil.which("actual-coder-migrate-https")
        self.assertIsNotNone(path)
        result = subprocess.run([path, "--help"], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--plan-digest", result.stdout)
        self.assertIn("--workers-stopped", result.stdout)


if __name__ == "__main__":
    unittest.main()
