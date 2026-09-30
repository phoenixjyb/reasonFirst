from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from gitlab_agent.setup_state import (
    SETUP_STATE_VERSION,
    SetupState,
    load_setup_state,
    save_setup_state,
)


class SetupStateTests(unittest.TestCase):
    def test_round_trip_preserves_only_non_secret_setup_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "reasonfirst" / "setup.yaml"
            state = SetupState(
                mode="standard",
                selected_worker="codex-cli",
                config_file="~/.config/gitlab-agent/.env",
                tunnel_id="tunnel_example",
                tunnel_runtime="reasonfirst",
                tunnel_client_path="/tools/tunnel-client",
                bridge_tunnel_id="tunnel_" + "b" * 32,
                bridge_runtime="reasonfirst-bridge",
                completed_phases=("system", "gitlab", "worker", "tunnel", "bridge"),
                installed_version="0.5.1.dev0",
                last_verified_at="2026-09-29T00:00:00Z",
            )
            written = save_setup_state(state, path)
            self.assertEqual(written, path)
            self.assertEqual(load_setup_state(path), state)

            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("token", raw.lower())
            self.assertNotIn("password", raw.lower())
            self.assertNotIn("api_key", raw.lower())
            self.assertEqual(yaml.safe_load(raw)["version"], SETUP_STATE_VERSION)

            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_missing_state_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(load_setup_state(Path(td) / "missing.yaml"))

    def test_unknown_state_version_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported setup state version"):
            SetupState.from_dict({"version": 99, "mode": "standard"})

    def test_unknown_mode_worker_or_phase_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode must be one of"):
            SetupState.from_dict({"version": 1, "mode": "magic"})
        with self.assertRaisesRegex(ValueError, "selected_worker must be one of"):
            SetupState.from_dict(
                {"version": 1, "mode": "standard", "selected_worker": "shell-agent"}
            )
        with self.assertRaisesRegex(ValueError, "unsupported completed_phases"):
            SetupState.from_dict(
                {
                    "version": 1,
                    "mode": "standard",
                    "completed_phases": ["system", "unknown"],
                }
            )

    def test_symlinked_state_is_rejected(self) -> None:
        if os.name == "nt":
            self.skipTest("Symlink privileges vary on Windows")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "target.yaml"
            target.write_text("version: 1\nmode: standard\n", encoding="utf-8")
            link = root / "setup.yaml"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlinked setup state"):
                load_setup_state(link)
            with self.assertRaisesRegex(ValueError, "symlinked setup state"):
                save_setup_state(SetupState(), link)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "version: 1\nmode: standard\n",
            )


if __name__ == "__main__":
    unittest.main()
