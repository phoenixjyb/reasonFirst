from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gitlab_agent import setup_config
from gitlab_agent.setup_config import (
    ConfigMutationError,
    apply_env_updates,
    assert_no_effective_env_override,
    ensure_persistent_config_target,
    read_env_assignments,
    render_env_updates,
)


class SetupConfigTests(unittest.TestCase):
    def test_render_preserves_comments_unknown_keys_and_crlf(self) -> None:
        raw = (
            b"# keep\r\n"
            b"GITLAB_BASE_URL=https://old.example\r\n"
            b"UNKNOWN_SETTING=keep-me\r\n"
        )
        result = render_env_updates(
            raw,
            {
                "GITLAB_BASE_URL": "https://gitlab.example",
                "GITLAB_ALLOWED_PROJECTS": "team/project",
            },
        )
        self.assertIn(b"# keep\r\n", result)
        self.assertIn(b"UNKNOWN_SETTING=keep-me\r\n", result)
        self.assertIn(b"GITLAB_BASE_URL=https://gitlab.example\r\n", result)
        self.assertTrue(result.endswith(b"GITLAB_ALLOWED_PROJECTS=team/project\r\n"))

    def test_noop_preserves_exact_existing_bytes(self) -> None:
        raw = b"export GITLAB_ALLOWED_PROJECTS='team/project'\n"
        self.assertEqual(
            render_env_updates(raw, {"GITLAB_ALLOWED_PROJECTS": "team/project"}),
            raw,
        )

    def test_duplicate_target_assignment_is_rejected(self) -> None:
        raw = b"GITLAB_ALLOWED_PROJECTS=a/b\nGITLAB_ALLOWED_PROJECTS=c/d\n"
        with self.assertRaisesRegex(ConfigMutationError, "Duplicate"):
            render_env_updates(raw, {"GITLAB_ALLOWED_PROJECTS": "e/f"})

    def test_multiline_value_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigMutationError, "single-line"):
            render_env_updates(b"", {"GITLAB_TOKEN": "a\nb"})

    def test_apply_backs_up_exact_secret_bytes_privately(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config" / ".env"
            path.parent.mkdir()
            before = b"# keep\nGITLAB_TOKEN=secret-value\nGITLAB_ALLOWED_PROJECTS=a/b\n"
            path.write_bytes(before)
            if os.name != "nt":
                path.chmod(0o600)

            result = apply_env_updates(
                path,
                {"GITLAB_ALLOWED_PROJECTS": "a/b,c/d"},
                backup_root=root / "backups",
            )
            self.assertTrue(result["changed"])
            backup = Path(str(result["backup_file"]))
            self.assertEqual(backup.read_bytes(), before)
            self.assertIn("GITLAB_TOKEN=secret-value", path.read_text())
            self.assertIn("GITLAB_ALLOWED_PROJECTS=a/b,c/d", path.read_text())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)

    def test_noop_creates_no_backup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / ".env"
            path.write_text("REASONFIRST_DEFAULT_BACKEND=auto\n", encoding="utf-8")
            result = apply_env_updates(
                path,
                {"REASONFIRST_DEFAULT_BACKEND": "auto"},
                backup_root=root / "backups",
            )
            self.assertFalse(result["changed"])
            self.assertIsNone(result["backup_file"])
            self.assertFalse((root / "backups").exists())

    def test_symlinked_config_is_rejected(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows symlink creation may require developer mode")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "real"
            target.write_text("A=B\n", encoding="utf-8")
            link = root / ".env"
            link.symlink_to(target)
            with self.assertRaisesRegex(ConfigMutationError, "symlinked config"):
                apply_env_updates(link, {"A": "C"})
            self.assertEqual(target.read_text(), "A=B\n")

    def test_exported_target_override_blocks_file_mutation(self) -> None:
        with patch.dict(os.environ, {"GITLAB_ALLOWED_PROJECTS": "external/value"}, clear=True):
            with self.assertRaisesRegex(ConfigMutationError, "exported"):
                assert_no_effective_env_override(["GITLAB_ALLOWED_PROJECTS"])

    def test_persistent_target_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            effective = root / "repo" / ".env"
            effective.parent.mkdir()
            effective.write_text("A=B\n", encoding="utf-8")
            target = root / "user" / ".env"
            with patch.object(setup_config, "selected_user_config_path", return_value=target):
                with self.assertRaisesRegex(ConfigMutationError, "partial higher-precedence"):
                    ensure_persistent_config_target(effective)

    def test_assignment_reader_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"
            path.write_text("A=1\nA=2\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigMutationError, "Duplicate"):
                read_env_assignments(path)


if __name__ == "__main__":
    unittest.main()
