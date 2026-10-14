"""Guided CLI tests require the project's normal MCP dependency, like its existing tests."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gitlab_agent import reasonfirst_cli as c
from gitlab_agent.setup_state import SetupState


class AntigravitySetupCLITests(unittest.TestCase):
    def test_parser_accepts_both_names(self):
        for name in ("agy", "antigravity-cli"):
            self.assertEqual(c._build_parser().parse_args(["worker", "use", name]).backend, name)
            self.assertEqual(c._build_parser().parse_args(["setup", "--worker", name]).worker, name)

    def test_choose_worker_returns_canonical_name(self):
        with patch.object(c, "_available_worker_names", return_value=["antigravity-cli"]), \
             patch.object(c, "plan_worker_use", return_value={"ok": True, "backend": "antigravity-cli"}):
            self.assertEqual(c._choose_worker(requested="agy", current=None), "antigravity-cli")

    def test_full_chat_rejects_explicit_agy_before_secrets_or_writes(self):
        with tempfile.TemporaryDirectory() as td:
            args = c._build_parser().parse_args(["setup", "--mode", "full-chat", "--worker", "agy"])
            secrets = Mock(side_effect=AssertionError("must not request secrets"))
            with patch.object(c.sys.stdin, "isatty", return_value=True), \
                 patch.object(c, "inspect_configuration", return_value=(None, {"status": "missing"})), \
                 patch.object(c, "load_setup_state", return_value=None), \
                 patch.object(c, "resolve_env_file", return_value=Path(td) / "missing.env"), \
                 patch.object(c, "assert_no_effective_env_override"), \
                 patch.object(c, "apply_env_updates") as write, \
                 self.assertRaisesRegex(RuntimeError, "not the full-chat Bridge"):
                c._run_guided_setup(args, input_fn=Mock(), secret_fn=secrets)
            write.assert_not_called()
            secrets.assert_not_called()

    def test_worker_use_preserves_setup_connection_and_canonicalizes_alias(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); saved = []
            previous = SetupState(selected_worker="codex-cli", tunnel_id="synthetic-tunnel",
                                  config_file=str(root / "missing.env"), completed_phases=("gitlab", "tunnel"))
            plan = {"ok": True, "backend": "antigravity-cli"}
            with patch.object(c, "_load_settings_clean", side_effect=RuntimeError("missing")), \
                 patch.object(c, "resolve_env_file", return_value=root / "missing.env"), \
                 patch.object(c, "plan_worker_use", return_value=plan), \
                 patch.object(c, "apply_worker_use", return_value={**plan, "writes_performed": True}) as apply, \
                 patch.object(c, "load_setup_state", return_value=previous), \
                 patch.object(c, "save_setup_state", side_effect=lambda state, path: saved.append(state)), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = c.main(["worker", "use", "agy", "--state-file", str(root / "setup.yaml")])
            self.assertEqual(code, 0)
            apply.assert_called_once_with(plan)
            self.assertEqual(saved[0].selected_worker, "antigravity-cli")
            self.assertEqual(saved[0].tunnel_id, previous.tunnel_id)
            self.assertEqual(saved[0].config_file, previous.config_file)
            self.assertIn("tunnel", saved[0].completed_phases)


if __name__ == "__main__":
    unittest.main()
