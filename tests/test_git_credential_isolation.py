from __future__ import annotations

import base64
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from gitlab_agent.config import AgentSettings
from gitlab_agent.workspace import WorkspaceManager


class GitCredentialIsolationTests(unittest.TestCase):
    """Real Git + packaged askpass; no live service or real credential needed."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="rf-git-auth-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.global_config = self.root / "global.gitconfig"
        self.global_config.write_text("", encoding="utf-8")
        self.marker = self.root / "helper-called"
        self.synthetic_token = "synthetic-" + uuid.uuid4().hex
        env = {
            key: value for key, value in os.environ.items()
            if not key.upper().startswith(("GIT_", "GITLAB_", "GCM_"))
        }
        env.update({
            "GIT_CONFIG_GLOBAL": str(self.global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "RF_HELPER_MARKER": str(self.marker),
            "HOME": str(self.root),
            "USERPROFILE": str(self.root),
            "LC_ALL": "C",
        })
        self.env_patch = patch.dict(os.environ, env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.settings = AgentSettings(
            config_file=self.root / "unused.env",
            gitlab_base_url="https://gitlab.example.invalid",
            api_token="", api_verify_ssl=True, api_trust_env=False,
            git_token=self.synthetic_token, git_username="configured-user",
            git_trust_env=False, allowed_projects={"team/project"},
            require_write_allowlist=True, workspace_root=self.root / "manager",
            branch_prefix="chatgpt/", default_base_ref="main",
            allowed_executables={"python"}, command_timeout_seconds=30,
            max_output_bytes=120000, max_file_bytes=1000000,
            git_author_name="Example", git_author_email="example@example.invalid",
        )
        self.manager = WorkspaceManager(self.settings)
        self.remote = self.manager.clone_url("team/project")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args], cwd=cwd or self.root, capture_output=True,
            text=True, encoding="utf-8", timeout=30, check=True,
        )
        return result.stdout.strip()

    def poison_config(self, remote: str, *, local_repo: Path | None = None) -> bytes:
        helper = (
            "!f() { echo called >> \"$RF_HELPER_MARKER\"; "
            "printf 'username=wrong-user\\npassword=wrong-value\\n'; }; f"
        )
        filename = (local_repo / "config") if local_repo else self.global_config
        # Helpers and scalar settings at both generic and exact-URL scopes.
        for key, value in (
            ("credential.helper", helper),
            (f"credential.{remote}.helper", helper),
            ("credential.username", "wrong-user"),
            (f"credential.{remote}.username", "wrong-user"),
            ("credential.interactive", "false"),
        ):
            self.git("config", "--file", str(filename), "--add", key, value)
        return filename.read_bytes()

    def fill(self, manager: WorkspaceManager | None = None) -> dict[str, str]:
        result = (manager or self.manager)._run_git(
            ["credential", "fill"], cwd=self.root,
            input_text=f"url={self.remote}\n\n", auth=True, auth_url=self.remote,
        )
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def test_real_git_helpers_and_username_are_overridden_without_config_write(self) -> None:
        before = self.poison_config(self.remote)
        parent_env = dict(os.environ)
        # Demonstrate that askpass alone still accepts the inherited helper.
        with self.manager._git_auth_env(require_token=True) as env:
            old = subprocess.run(
                ["git", "credential", "fill"], cwd=self.root, env=env,
                input=f"url={self.remote}\n\n", text=True, encoding="utf-8",
                capture_output=True, timeout=15, check=True,
            )
        self.assertIn("password=wrong-value", old.stdout)
        self.assertTrue(self.marker.exists())
        self.marker.unlink()

        result = self.fill()
        self.assertEqual(result["username"], self.settings.git_username)
        self.assertEqual(result["password"], self.synthetic_token)
        # Git success and rejection must not store/erase via inherited helpers.
        for operation in ("approve", "reject"):
            self.manager._run_git(
                ["credential", operation], cwd=self.root,
                input_text=f"url={self.remote}\nusername=example\npassword=synthetic\n\n",
                auth=True, auth_url=self.remote,
            )
        self.assertFalse(self.marker.exists())
        self.assertEqual(before, self.global_config.read_bytes())
        self.assertEqual(parent_env, dict(os.environ))

    def test_missing_legacy_helper_is_not_invoked(self) -> None:
        self.git("config", "--file", str(self.global_config), "credential.helper", "rf-missing-example-helper")
        self.assertEqual(self.fill()["password"], self.synthetic_token)
        self.assertFalse(self.marker.exists())

    def test_explicit_git_credential_precedence_is_unchanged(self) -> None:
        # Load the same public settings surface with generated test values only.
        for values, expected in (
            ({"GITLAB_TOKEN": self.synthetic_token}, self.synthetic_token),
            ({"GITLAB_TOKEN": "synthetic-api", "GITLAB_GIT_TOKEN": self.synthetic_token}, self.synthetic_token),
            ({"GITLAB_TOKEN": "synthetic-api", "GITLAB_GIT_PASSWORD": self.synthetic_token}, self.synthetic_token),
        ):
            with self.subTest(source=sorted(values)):
                env = {
                    "GITLAB_AGENT_ENV_FILE": str(self.root / "unused.env"),
                    "GITLAB_BASE_URL": self.settings.gitlab_base_url,
                    "GITLAB_WORKSPACE_ROOT": str(self.root / "precedence-manager"),
                    "GITLAB_ALLOWED_PROJECTS": "team/project", **values,
                }
                with patch.dict(os.environ, env):
                    manager = WorkspaceManager(AgentSettings.load())
                    self.assertEqual(self.fill(manager)["password"], expected)

    def test_missing_credentials_fail_before_git_not_fall_back_to_host(self) -> None:
        self.poison_config(self.remote)
        manager = WorkspaceManager(replace(self.settings, git_token=""))
        with patch("gitlab_agent.workspace.subprocess.run") as runner:
            with self.assertRaisesRegex(RuntimeError, "No Git credential"):
                manager._run_git(["ls-remote", self.remote], auth=True, auth_url=self.remote)
            runner.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_credential_argv_requires_clean_explicit_remote(self) -> None:
        for remote in (None, "", "https://" + "user:password" + "@example.invalid/repo", "file:///tmp/example", "https://example.invalid/repo\nvalue", "https://example.invalid/repo?token=value"):
            with self.subTest(remote=remote):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.manager._git_credential_config(remote)
        argv = self.manager._git_credential_config(self.remote)
        self.assertNotIn(self.synthetic_token, " ".join(argv))

    def test_configured_auth_does_not_change_non_auth_git_invocation(self) -> None:
        with patch("gitlab_agent.workspace.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as runner:
            self.manager._run_git(["status", "--porcelain"], cwd=self.root)
            args = runner.call_args.args[0]
            self.assertFalse(any("credential." in value for value in args))

    def test_authenticated_child_suppresses_traces_without_changing_parent(self) -> None:
        trace = str(self.root / "trace")
        with patch.dict(os.environ, {"GIT_TRACE": trace, "GIT_TRACE_CURL": trace, "GIT_CURL_VERBOSE": "1", "GCM_INTERACTIVE": "always"}):
            before = dict(os.environ)
            with self.manager._git_auth_env(require_token=True) as env:
                self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
                self.assertEqual(env["GCM_INTERACTIVE"], "never")
                self.assertFalse(any(key.upper().startswith("GIT_TRACE") for key in env))
                self.assertNotIn("GIT_CURL_VERBOSE", env)
                script = Path(env["GIT_ASKPASS"])
                self.assertNotIn(self.synthetic_token, script.read_text())
            self.assertFalse(script.exists())
            self.assertEqual(before, dict(os.environ))

    def test_real_authenticated_clone_and_fetch_bypass_helpers(self) -> None:
        seed = self.root / "seed"
        seed.mkdir()
        self.git("init", "-b", "main", cwd=seed)
        self.git("config", "user.name", "Example", cwd=seed)
        self.git("config", "user.email", "example@example.invalid", cwd=seed)
        (seed / "README.md").write_text("first\n", encoding="utf-8")
        self.git("add", "README.md", cwd=seed)
        self.git("commit", "-m", "synthetic baseline", cwd=seed)
        remote = self.root / "served" / "team" / "project.git"
        remote.parent.mkdir(parents=True)
        self.git("clone", "--bare", str(seed), str(remote))
        self.git("--git-dir", str(remote), "update-server-info")
        expected_auth = "Basic " + base64.b64encode(
            f"{self.settings.git_username}:{self.synthetic_token}".encode()
        ).decode()
        observed = {"authenticated": 0}

        class AuthenticatedFiles(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.headers.get("Authorization") != expected_auth:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="synthetic"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                observed["authenticated"] += 1
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(AuthenticatedFiles, directory=str(self.root / "served")))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        # HTTP is deliberately confined to loopback with a fabricated credential;
        # production TLS verification and trust settings are not changed.
        settings = replace(self.settings, gitlab_base_url=f"http://127.0.0.1:{server.server_port}")
        manager = WorkspaceManager(settings)
        url = manager.clone_url("team/project")
        before = self.poison_config(url)
        cached = manager._ensure_cached_repo("team/project")
        first = manager._run_git(["--git-dir", str(cached), "rev-parse", "main"]).stdout.strip()
        self.assertEqual(first, self.git("rev-parse", "HEAD", cwd=seed))
        self.assertGreater(observed["authenticated"], 0)
        # Also exercise helpers/scalars from the cached repository's own config.
        cache_before = self.poison_config(url, local_repo=cached)
        (seed / "README.md").write_text("second\n", encoding="utf-8")
        self.git("commit", "-am", "synthetic update", cwd=seed)
        self.git("push", str(remote), "main", cwd=seed)
        self.git("--git-dir", str(remote), "update-server-info")
        self.assertEqual(manager._ensure_cached_repo("team/project"), cached)
        fetched = manager._run_git(["--git-dir", str(cached), "rev-parse", "refs/remotes/origin/main"]).stdout.strip()
        self.assertEqual(fetched, self.git("rev-parse", "HEAD", cwd=seed))
        self.assertNotEqual(first, fetched)
        self.assertFalse(self.marker.exists())
        self.assertEqual(before, self.global_config.read_bytes())
        self.assertEqual(cache_before, (cached / "config").read_bytes())
        self.assertNotIn(self.synthetic_token.encode(), (cached / "config").read_bytes())


if __name__ == "__main__":
    unittest.main()
