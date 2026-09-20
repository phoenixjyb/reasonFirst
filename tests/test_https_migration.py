from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import https_migration as migration


OLD = "http://gitlab.example.test"
NEW = "https://gitlab.example.test"
PROJECT = "team/project"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        # Isolate global Git settings and application environment for every case.
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name).resolve()
        home = self.top / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("GIT_", "GITLAB_", "XDG_"))}
        env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / "xdg")})
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = self.top / "state-root"
        (self.root / "repos").mkdir(parents=True)
        (self.root / "worktrees").mkdir()
        (self.root / "state").mkdir()
        self.config_file = home / ".env"
        self.config_file.write_text(
            f"# keep this comment\nGITLAB_BASE_URL={OLD}\n"
            f"GITLAB_WORKSPACE_ROOT={self.root}\nGITLAB_ALLOWED_PROJECTS={PROJECT}\n"
            "GITLAB_VERIFY_SSL=true\nGITLAB_TOKEN=not-a-real-token\n", encoding="utf-8")
        self.config_file.chmod(0o600)
        self.repo, self.worktree, self.state_path = self.add_workspace(PROJECT, "abc123def456")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                              check=True, timeout=20).stdout.strip()

    def add_workspace(self, project, ws):
        seed = self.top / ("seed-" + ws)
        seed.mkdir()
        self.git("init", "-b", "main", cwd=seed)
        self.git("config", "user.name", "Migration Test", cwd=seed)
        self.git("config", "user.email", "test@example.invalid", cwd=seed)
        (seed / "README.md").write_text("baseline\n", encoding="utf-8")
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-m", "base", cwd=seed)
        base = self.git("rev-parse", "HEAD", cwd=seed)
        repo = self.root / "repos" / migration._repo_name(project)
        self.git("clone", "--bare", str(seed), str(repo))
        worktree = self.root / "worktrees" / ws
        branch = "chatgpt/test-" + ws[:8]
        self.git("--git-dir", str(repo), "worktree", "add", "-b", branch, str(worktree), base)
        self.git("-c", "user.name=Migration Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-m", "unpublished", cwd=worktree)
        head = self.git("rev-parse", "HEAD", cwd=worktree)
        self.git("--git-dir", str(repo), "remote", "set-url", "origin", f"{OLD}/{project}.git")
        state = {"workspace_id": ws, "project": project, "repo_path": str(repo),
                 "worktree_path": str(worktree), "base_ref": "main", "base_sha": base,
                 "branch": branch, "created_at": "2026-09-20T00:00:00Z",
                 "pushed": True, "last_commit": head, "remote_branch": branch,
                 "merge_request_url": f"{OLD}/{project}/-/merge_requests/42",
                 "future_field": {"must": "survive"}}
        path = self.root / "state" / (ws + ".json")
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return repo, worktree, path

    def plan(self):
        return migration.build_plan(config_file=self.config_file, old_url=OLD, new_url=NEW)

    def origin(self, repo=None):
        return self.git("--git-dir", str(repo or self.repo), "remote", "get-url", "origin")

    def snapshot(self):
        return {"env": self.config_file.read_bytes(), "repo": (self.repo / "config").read_bytes(),
                "state": self.state_path.read_bytes(), "head": self.git("rev-parse", "HEAD", cwd=self.worktree),
                "branch": self.git("symbolic-ref", "HEAD", cwd=self.worktree),
                "refs": self.git("--git-dir", str(self.repo), "show-ref"),
                "files": {p.relative_to(self.worktree).as_posix(): p.read_bytes()
                          for p in self.worktree.rglob("*") if p.is_file()}}

    def test_preview_preserves_all_state_and_creates_no_migration_artifacts(self):
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(len(plan.changes), 3)
        self.assertEqual(plan.workspace_count, 1)
        self.assertFalse((self.root / "migrations").exists())
        self.assertFalse((self.root / ".https-migration.lock").exists())

    def test_apply_preserves_unpublished_commit_dirty_files_and_mr_identity(self):
        (self.worktree / "README.md").write_text("pending tracked edit\n")
        (self.worktree / "notes.txt").write_text("pending untracked edit\n")
        before = self.snapshot()
        result = migration.apply_plan(self.plan(), workers_stopped=True)
        after = self.snapshot()
        self.assertTrue(result["applied"])
        for key in ("head", "branch", "refs", "files"):
            self.assertEqual(before[key], after[key], key)
        old_state, new_state = json.loads(before["state"]), json.loads(after["state"])
        self.assertEqual(new_state.pop("merge_request_url"), f"{NEW}/{PROJECT}/-/merge_requests/42")
        old_state.pop("merge_request_url")
        self.assertEqual(old_state, new_state)
        self.assertEqual(self.origin(), f"{NEW}/{PROJECT}.git")
        self.assertIn("GITLAB_TOKEN=not-a-real-token", after["env"].decode())
        self.assertFalse(self.plan().changes)

    def test_second_apply_is_no_op(self):
        migration.apply_plan(self.plan(), workers_stopped=True)
        before = self.snapshot()
        folders = list((self.root / "migrations").iterdir())
        result = migration.apply_plan(self.plan(), workers_stopped=True)
        self.assertTrue(result["no_op"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(folders, list((self.root / "migrations").iterdir()))

    def test_explicit_matching_push_url_is_upgraded(self):
        self.git("--git-dir", str(self.repo), "config", "remote.origin.pushurl", f"{OLD}/{PROJECT}.git")
        migration.apply_plan(self.plan(), workers_stopped=True)
        self.assertEqual(self.git("--git-dir", str(self.repo), "remote", "get-url", "--push", "origin"),
                         f"{NEW}/{PROJECT}.git")

    def test_same_project_multiple_worktrees_share_one_updated_cache(self):
        ws2 = "012345abcdef"
        worktree = self.root / "worktrees" / ws2
        self.git("--git-dir", str(self.repo), "worktree", "add", "-b", "chatgpt/second", str(worktree), "main")
        state = json.loads(self.state_path.read_bytes())
        state.update(workspace_id=ws2, worktree_path=str(worktree), branch="chatgpt/second")
        (self.root / "state" / (ws2 + ".json")).write_text(json.dumps(state))
        plan = self.plan()
        self.assertEqual(plan.workspace_count, 2)
        self.assertEqual(sum(c.kind == "git_config" for c in plan.changes), 1)
        migration.apply_plan(plan, workers_stopped=True)
        self.assertFalse(self.plan().changes)

    def test_cache_without_workspace_is_migrated(self):
        self.state_path.unlink()
        plan = self.plan()
        self.assertEqual(plan.workspace_count, 0)
        migration.apply_plan(plan, workers_stopped=True)
        self.assertEqual(self.origin(), f"{NEW}/{PROJECT}.git")

    def test_partial_existing_https_state_is_converged_forward(self):
        self.git("--git-dir", str(self.repo), "remote", "set-url", "origin", f"{NEW}/{PROJECT}.git")
        plan = self.plan()
        self.assertEqual(len(plan.changes), 2)
        migration.apply_plan(plan, workers_stopped=True)
        self.assertFalse(self.plan().changes)

    def test_null_mr_link_stays_null(self):
        state = json.loads(self.state_path.read_bytes())
        state["merge_request_url"] = None
        self.state_path.write_text(json.dumps(state))
        migration.apply_plan(self.plan(), workers_stopped=True)
        self.assertIsNone(json.loads(self.state_path.read_bytes())["merge_request_url"])

    def test_unexpected_origin_is_rejected_without_mutation(self):
        self.git("--git-dir", str(self.repo), "remote", "set-url", "origin", "https://other.example.test/team/project.git")
        before = self.snapshot()
        with self.assertRaises(migration.MigrationError):
            self.plan()
        self.assertEqual(before, self.snapshot())

    def test_unapproved_push_url_is_rejected(self):
        self.git("--git-dir", str(self.repo), "config", "remote.origin.pushurl", "https://other.example.test/team/project.git")
        with self.assertRaises(migration.MigrationError):
            self.plan()

    def test_inherited_push_url_is_rejected(self):
        self.git("config", "--global", "remote.origin.pushurl", f"{NEW}/{PROJECT}.git")
        with self.assertRaisesRegex(migration.MigrationError, "inherited"):
            self.plan()

    def test_multiple_origin_urls_are_rejected(self):
        self.git("--git-dir", str(self.repo), "config", "--add", "remote.origin.url", f"{NEW}/{PROJECT}.git")
        with self.assertRaisesRegex(migration.MigrationError, "one explicit"):
            self.plan()

    def test_url_rewrites_are_rejected(self):
        self.git("config", "--global", "url.https://other.example.test/.insteadOf", OLD)
        with self.assertRaisesRegex(migration.MigrationError, "rewrites"):
            self.plan()

    def test_conditional_includes_are_rejected(self):
        self.git("--git-dir", str(self.repo), "config", "include.path", str(self.top / "missing-file"))
        with self.assertRaisesRegex(migration.MigrationError, "include"):
            self.plan()

    def test_disabled_git_tls_verification_is_rejected(self):
        self.git("config", "--global", "http.sslVerify", "false")
        with self.assertRaisesRegex(migration.MigrationError, "verification"):
            self.plan()

    def test_disabled_application_tls_verification_is_rejected(self):
        with patch.dict(os.environ, {"GITLAB_VERIFY_SSL": "false"}):
            with self.assertRaisesRegex(migration.MigrationError, "GITLAB_VERIFY_SSL"):
                self.plan()

    def test_stale_exported_base_url_is_rejected(self):
        with patch.dict(os.environ, {"GITLAB_BASE_URL": OLD}):
            with self.assertRaisesRegex(migration.MigrationError, "stale exported"):
                self.plan()

    def test_exported_new_endpoint_is_supported(self):
        with patch.dict(os.environ, {"GITLAB_BASE_URL": NEW}):
            self.assertTrue(self.plan().changes)

    def test_different_selected_config_is_rejected(self):
        with patch.dict(os.environ, {"GITLAB_AGENT_ENV_FILE": str(self.top / "other.env")}):
            with self.assertRaisesRegex(migration.MigrationError, "different config"):
                self.plan()

    def test_empty_allowlist_is_rejected(self):
        with patch.dict(os.environ, {"GITLAB_ALLOWED_PROJECTS": ""}):
            with self.assertRaisesRegex(migration.MigrationError, "allowlist"):
                self.plan()

    def test_non_allowlisted_cached_project_is_rejected(self):
        with patch.dict(os.environ, {"GITLAB_ALLOWED_PROJECTS": "team/other"}):
            with self.assertRaisesRegex(migration.MigrationError, "allowlist"):
                self.plan()

    def test_environment_git_overrides_are_rejected(self):
        for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_DIR"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "unexpected"}):
                with self.assertRaisesRegex(migration.MigrationError, "override"):
                    self.plan()

    def test_branch_switch_is_rejected(self):
        self.git("checkout", "--detach", "HEAD", cwd=self.worktree)
        with self.assertRaises(migration.MigrationError):
            self.plan()

    def test_metadata_path_escape_is_rejected(self):
        state = json.loads(self.state_path.read_bytes())
        state["repo_path"] = str(self.top)
        self.state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(migration.MigrationError, "paths"):
            self.plan()

    def test_missing_worktree_is_rejected(self):
        self.git("--git-dir", str(self.repo), "worktree", "remove", str(self.worktree))
        with self.assertRaisesRegex(migration.MigrationError, "stale"):
            self.plan()

    def test_foreign_mr_link_is_rejected(self):
        state = json.loads(self.state_path.read_bytes())
        state["merge_request_url"] = "https://other.example.test/team/project/-/merge_requests/42"
        self.state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(migration.MigrationError, "Stored URL"):
            self.plan()

    def test_duplicate_metadata_keys_are_rejected(self):
        self.state_path.write_text('{"workspace_id":"abc123def456","workspace_id":"abc123def456"}')
        with self.assertRaisesRegex(migration.MigrationError, "Duplicate JSON"):
            self.plan()

    def test_duplicate_env_assignments_are_rejected(self):
        with self.config_file.open("a") as handle:
            handle.write(f"GITLAB_BASE_URL={NEW}\n")
        with self.assertRaisesRegex(migration.MigrationError, "Duplicate .env"):
            self.plan()

    def test_preview_fingerprint_detects_metadata_change(self):
        plan = self.plan()
        self.state_path.write_bytes(self.state_path.read_bytes() + b"\n")
        before = self.snapshot()
        with self.assertRaisesRegex(migration.MigrationError, "changed after preview"):
            migration.apply_plan(plan, workers_stopped=True)
        self.assertEqual(before, self.snapshot())

    def test_preview_fingerprint_detects_git_ref_advance(self):
        plan = self.plan()
        self.git("-c", "user.name=Migration Test", "-c", "user.email=test@example.invalid",
                 "commit", "--allow-empty", "-m", "later", cwd=self.worktree)
        before = self.snapshot()
        with self.assertRaisesRegex(migration.MigrationError, "changed after preview"):
            migration.apply_plan(plan, workers_stopped=True)
        self.assertEqual(before, self.snapshot())

    def test_preview_fingerprint_detects_new_untracked_file(self):
        plan = self.plan()
        (self.worktree / "later.txt").write_text("later")
        with self.assertRaisesRegex(migration.MigrationError, "changed after preview"):
            migration.apply_plan(plan, workers_stopped=True)

    def test_preview_fingerprint_detects_configuration_environment_change(self):
        plan = self.plan()
        with patch.dict(os.environ, {"GITLAB_BASE_URL": NEW}):
            with self.assertRaisesRegex(migration.MigrationError, "changed after preview"):
                migration.apply_plan(plan, workers_stopped=True)

    def test_plan_does_not_export_tokens(self):
        plan = self.plan()
        self.assertNotIn("not-a-real-token", json.dumps(plan.summary()))
        self.assertNotIn("not-a-real-token", repr(plan))

    def test_backups_preserve_exact_before_bytes_and_manifest(self):
        plan = self.plan()
        result = migration.apply_plan(plan, workers_stopped=True)
        backup = Path(result["backup_directory"])
        manifest = json.loads((backup / "manifest.json").read_bytes())
        self.assertEqual(manifest["status"], "completed")
        for n, change in enumerate(plan.changes):
            self.assertEqual((backup / f"{n}.before").read_bytes(), change.before)
            self.assertEqual((backup / f"{n}.after").read_bytes(), change.after)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
            for p in backup.iterdir():
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config_file.stat().st_mode), 0o600)

    def test_injected_write_failure_has_journal_and_forward_resume(self):
        plan = self.plan()
        original = migration._atomic_write
        def fail(path, raw):
            if path == self.repo / "config":
                raise OSError("simulated failure; sensitive detail must not be printed")
            return original(path, raw)
        with patch.object(migration, "_atomic_write", side_effect=fail):
            with self.assertRaisesRegex(migration.MigrationError, "interrupted") as cm:
                migration.apply_plan(plan, workers_stopped=True)
        self.assertNotIn("sensitive detail", str(cm.exception))
        backup = next((self.root / "migrations").iterdir())
        self.assertEqual(json.loads((backup / "manifest.json").read_bytes())["status"], "interrupted")
        self.assertTrue(self.worktree.is_dir())
        migration.apply_plan(self.plan(), workers_stopped=True)
        self.assertFalse(self.plan().changes)

    def test_workers_stopped_acknowledgement_is_required(self):
        before = self.snapshot()
        with self.assertRaisesRegex(migration.MigrationError, "workers"):
            migration.apply_plan(self.plan(), workers_stopped=False)
        self.assertEqual(before, self.snapshot())

    def test_second_migration_cannot_take_lock(self):
        with migration._migration_lock(self.root):
            with self.assertRaisesRegex(migration.MigrationError, "Another"):
                migration.apply_plan(self.plan(), workers_stopped=True)

    def test_caller_cannot_replace_operations_without_changing_digest(self):
        plan = self.plan()
        unrelated = self.top / "do-not-touch.txt"
        unrelated.write_text("unchanged")
        plan.changes.append(migration.FileChange(unrelated, b"unchanged", b"modified"))
        migration.apply_plan(plan, workers_stopped=True)
        self.assertEqual(unrelated.read_text(), "unchanged")

    def test_crlf_and_export_config_line(self):
        original = self.config_file.read_text().replace("GITLAB_BASE_URL=", "export GITLAB_BASE_URL=")
        self.config_file.write_bytes(original.replace("\n", "\r\n").encode())
        migration.apply_plan(self.plan(), workers_stopped=True)
        raw = self.config_file.read_bytes()
        self.assertIn(f"GITLAB_BASE_URL={NEW}\r\n".encode(), raw)
        self.assertIn(b"# keep this comment\r\n", raw)

    @unittest.skipIf(os.name == "nt", "Windows symlink creation may require developer-mode privileges")
    def test_symlinked_metadata_is_rejected(self):
        other = self.top / "original.json"
        self.state_path.rename(other)
        self.state_path.symlink_to(other)
        with self.assertRaisesRegex(migration.MigrationError, "Symlink"):
            self.plan()

    @unittest.skipIf(os.name == "nt", "Windows symlink creation may require developer-mode privileges")
    def test_symlinked_backup_root_is_rejected(self):
        (self.root / "migrations").symlink_to(self.top, target_is_directory=True)
        with self.assertRaisesRegex(migration.MigrationError, "Symlink"):
            migration.apply_plan(self.plan(), workers_stopped=True)

    def cli_args(self):
        return ["--config-file", str(self.config_file), "--from-url", OLD, "--to-url", NEW]

    def test_cli_default_is_preview(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = migration.main(self.cli_args())
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["dry_run"])
        self.assertEqual(self.origin(), f"{OLD}/{PROJECT}.git")

    def test_cli_scripted_apply_requires_review_digest(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = migration.main(self.cli_args() + ["--apply", "--yes", "--workers-stopped"])
        self.assertEqual(code, 1)
        self.assertEqual(self.origin(), f"{OLD}/{PROJECT}.git")

    def test_cli_scripted_apply_with_correct_digest(self):
        digest = self.plan().digest
        with redirect_stdout(io.StringIO()):
            code = migration.main(self.cli_args() + ["--apply", "--yes", "--workers-stopped", "--plan-digest", digest])
        self.assertEqual(code, 0)
        self.assertEqual(self.origin(), f"{NEW}/{PROJECT}.git")

    def test_cli_wrong_digest_blocks(self):
        with redirect_stdout(io.StringIO()):
            code = migration.main(self.cli_args() + ["--apply", "--yes", "--workers-stopped", "--plan-digest", "wrong"])
        self.assertEqual(code, 1)
        self.assertEqual(self.origin(), f"{OLD}/{PROJECT}.git")

    def test_cli_failed_optional_tls_probe_prevents_apply(self):
        with patch.object(migration, "check_tls", return_value={"ok": False}), redirect_stdout(io.StringIO()):
            code = migration.main(self.cli_args() + ["--check-tls", "--apply", "--yes", "--workers-stopped",
                                                     "--plan-digest", self.plan().digest])
        self.assertEqual(code, 1)
        self.assertEqual(self.origin(), f"{OLD}/{PROJECT}.git")


class EndpointMapTests(unittest.TestCase):
    def test_same_hostname_and_prefix(self):
        mapping = migration.EndpointMap.parse(OLD + "/gitlab", NEW + "/gitlab")
        self.assertEqual(mapping.upgrade(OLD + "/gitlab/team/p.git", suffix="/team/p.git"),
                         NEW + "/gitlab/team/p.git")

    def test_explicit_default_ports_are_kept_for_exact_mapping(self):
        mapping = migration.EndpointMap.parse(OLD + ":80", NEW + ":443")
        self.assertEqual(mapping.old, OLD + ":80")
        self.assertEqual(mapping.new, NEW + ":443")

    def test_unsafe_or_ambiguous_endpoints_are_rejected(self):
        invalid = [(OLD, "http://gitlab.example.test"), (NEW, OLD),
                   (OLD, "https://other.example.test"), (OLD, NEW + ":8443"),
                   (OLD, NEW + "/other"), (OLD, NEW + "?a=1"), (OLD, NEW + "#x"),
                   ("http://user:password@gitlab.example.test", NEW),
                   (OLD, NEW + "/%2e%2e"), (OLD, NEW + "/../x"),
                   (OLD, NEW + "\\x"), (OLD, NEW + "\n"), (OLD, NEW + "/x//y"),
                   (OLD, NEW + "."), (OLD, NEW + "?")]
        for old, new in invalid:
            with self.subTest(old=old, new=new):
                with self.assertRaises(migration.MigrationError):
                    migration.EndpointMap.parse(old, new)

    def test_malicious_hostname_prefix_is_not_a_match(self):
        mapping = migration.EndpointMap.parse(OLD, NEW)
        with self.assertRaises(migration.MigrationError):
            mapping.project_for(OLD + ".evil.test/team/project.git")


if __name__ == "__main__":
    unittest.main()
