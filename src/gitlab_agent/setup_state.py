from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


SETUP_STATE_VERSION = 1
DEFAULT_SETUP_STATE_PATH = Path("~/.config/reasonfirst/setup.yaml").expanduser()
SETUP_MODES = {"standard", "full-chat", "cli-only"}
SETUP_WORKERS = {"auto", "codex-cli", "copilot-cli", "codex-desktop"}
SETUP_PHASES = {
    "system",
    "gitlab",
    "worker",
    "chatgpt-read",
    "bridge",
    "ready",
}


def _optional_text(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    text = value.strip()
    return text or None


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty strings")
        items.append(item.strip())
    return tuple(items)


@dataclass(frozen=True)
class SetupState:
    """Versioned, non-secret progress state for guided ReasonFirst setup."""

    version: int = SETUP_STATE_VERSION
    mode: str = "standard"
    selected_worker: str | None = None
    config_file: str | None = None
    tunnel_id: str | None = None
    tunnel_runtime: str | None = None
    completed_phases: tuple[str, ...] = ()
    installed_version: str | None = None
    last_verified_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SetupState":
        if not isinstance(data, dict):
            raise ValueError("setup state must be a mapping")

        version = data.get("version", SETUP_STATE_VERSION)
        if version != SETUP_STATE_VERSION:
            raise ValueError(
                f"Unsupported setup state version {version!r}; "
                f"expected {SETUP_STATE_VERSION}"
            )

        mode = str(data.get("mode") or "standard").strip()
        if mode not in SETUP_MODES:
            raise ValueError(
                "mode must be one of: " + ", ".join(sorted(SETUP_MODES))
            )

        worker = _optional_text(data.get("selected_worker"), field="selected_worker")
        if worker is not None and worker not in SETUP_WORKERS:
            raise ValueError(
                "selected_worker must be one of: " + ", ".join(sorted(SETUP_WORKERS))
            )

        phases = _string_tuple(data.get("completed_phases"), field="completed_phases")
        unknown_phases = sorted(set(phases) - SETUP_PHASES)
        if unknown_phases:
            raise ValueError(
                "unsupported completed_phases: " + ", ".join(unknown_phases)
            )

        return cls(
            version=SETUP_STATE_VERSION,
            mode=mode,
            selected_worker=worker,
            config_file=_optional_text(data.get("config_file"), field="config_file"),
            tunnel_id=_optional_text(data.get("tunnel_id"), field="tunnel_id"),
            tunnel_runtime=_optional_text(
                data.get("tunnel_runtime"), field="tunnel_runtime"
            ),
            completed_phases=phases,
            installed_version=_optional_text(
                data.get("installed_version"), field="installed_version"
            ),
            last_verified_at=_optional_text(
                data.get("last_verified_at"), field="last_verified_at"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": self.mode,
            "selected_worker": self.selected_worker,
            "config_file": self.config_file,
            "tunnel_id": self.tunnel_id,
            "tunnel_runtime": self.tunnel_runtime,
            "completed_phases": list(self.completed_phases),
            "installed_version": self.installed_version,
            "last_verified_at": self.last_verified_at,
        }


def load_setup_state(path: Path = DEFAULT_SETUP_STATE_PATH) -> SetupState | None:
    target = path.expanduser()
    if not target.exists():
        return None
    if target.is_symlink():
        raise ValueError(f"Refusing symlinked setup state: {target}")
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if data is None:
        raise ValueError(f"Setup state is empty: {target}")
    if not isinstance(data, dict):
        raise ValueError(f"Setup state must contain a mapping: {target}")
    return SetupState.from_dict(data)


def save_setup_state(
    state: SetupState,
    path: Path = DEFAULT_SETUP_STATE_PATH,
) -> Path:
    """Atomically persist non-secret setup progress.

    Slice 1 does not call this from setup --status. It is the safe persistence
    primitive used by later guided setup slices.
    """

    target = path.expanduser()
    parent = target.parent

    if target.exists() and target.is_symlink():
        raise ValueError(f"Refusing symlinked setup state: {target}")
    if parent.exists() and parent.is_symlink():
        raise ValueError(f"Refusing symlinked setup-state directory: {parent}")

    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        parent.chmod(0o700)

    payload = yaml.safe_dump(
        state.to_dict(),
        sort_keys=False,
        allow_unicode=True,
    )

    fd, tmp_name = tempfile.mkstemp(prefix=".setup.", suffix=".tmp", dir=parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            tmp.chmod(0o600)
        os.replace(tmp, target)
        if os.name != "nt":
            target.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()

    return target
