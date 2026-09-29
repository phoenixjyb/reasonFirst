from __future__ import annotations

import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_USER_CONFIG = Path("~/.config/gitlab-agent/.env").expanduser()
DEFAULT_CONFIG_BACKUPS = Path("~/.local/share/reasonfirst/backups/config").expanduser()

_ASSIGNMENT = re.compile(
    r"^(?P<indent>\s*)(?P<export>export\s+)?"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$"
)


class ConfigMutationError(RuntimeError):
    pass


def selected_user_config_path() -> Path:
    """Choose a persistent user config target; never silently write cwd/.env."""
    explicit = os.getenv("GITLAB_AGENT_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return DEFAULT_USER_CONFIG


def _decode_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _validate_value(key: str, value: str) -> str:
    if not isinstance(value, str):
        raise ConfigMutationError(f"{key} must be a string")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ConfigMutationError(f"{key} must be a single-line value")
    if value != value.strip():
        raise ConfigMutationError(f"{key} must not have leading/trailing whitespace")
    return value


def read_env_assignments(path: Path) -> dict[str, str]:
    target = path.expanduser()
    if not target.exists():
        return {}
    if target.is_symlink():
        raise ConfigMutationError(f"Refusing symlinked config file: {target}")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigMutationError(f"Could not read config file: {target}") from exc

    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        key = match.group("key")
        if key in values:
            raise ConfigMutationError(f"Duplicate config assignment for {key}")
        values[key] = _decode_value(match.group("value"))
    return values


def assert_no_effective_env_override(keys: Iterable[str]) -> None:
    """Refuse file mutation when an exported target value would still win."""
    overridden = sorted({key for key in keys if key in os.environ})
    if overridden:
        raise ConfigMutationError(
            "Refusing to edit config while target values are exported in the "
            "current environment: "
            + ", ".join(overridden)
            + ". Clear those overrides or update the intended environment explicitly."
        )


def render_env_updates(raw: bytes, updates: dict[str, str]) -> bytes:
    """Return an updated .env while preserving unrelated bytes/comments/order."""
    if not updates:
        return raw

    normalized = {key: _validate_value(key, value) for key, value in updates.items()}
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ConfigMutationError("Config file is not valid UTF-8") from exc

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    seen: dict[str, int] = {}
    output: list[str] = []

    for line in lines:
        content = line.rstrip("\r\n")
        eol = line[len(content):]
        match = _ASSIGNMENT.match(content)
        if match and match.group("key") in normalized:
            key = match.group("key")
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                raise ConfigMutationError(f"Duplicate config assignment for {key}")
            desired = normalized[key]
            if _decode_value(match.group("value")) == desired:
                output.append(line)
            else:
                output.append(f"{key}={desired}{eol or newline}")
        else:
            output.append(line)

    missing = [key for key in normalized if key not in seen]
    if missing:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += newline
        for key in missing:
            output.append(f"{key}={normalized[key]}{newline}")

    return "".join(output).encode("utf-8")


def _ensure_private_directory(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise ConfigMutationError(f"Refusing symlinked directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _private_write(path: Path, raw: bytes) -> None:
    parent = path.parent
    _ensure_private_directory(parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".reasonfirst.", suffix=".tmp", dir=parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            tmp.chmod(0o600)
        os.replace(tmp, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply_env_updates(
    path: Path,
    updates: dict[str, str],
    *,
    backup_root: Path = DEFAULT_CONFIG_BACKUPS,
) -> dict[str, object]:
    """Atomically update selected config keys and privately back up prior bytes."""
    target = path.expanduser()
    if target.exists() and target.is_symlink():
        raise ConfigMutationError(f"Refusing symlinked config file: {target}")
    if target.parent.exists() and target.parent.is_symlink():
        raise ConfigMutationError(f"Refusing symlinked config directory: {target.parent}")

    before = target.read_bytes() if target.exists() else b""
    after = render_env_updates(before, updates)
    if after == before:
        return {
            "changed": False,
            "config_file": str(target),
            "backup_file": None,
            "updated_keys": sorted(updates),
        }

    backup_file: Path | None = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_dir = backup_root.expanduser() / f"{stamp}-{uuid.uuid4().hex[:8]}"
        _ensure_private_directory(backup_dir)
        backup_file = backup_dir / "gitlab-agent.env.before"
        _private_write(backup_file, before)

    _private_write(target, after)
    return {
        "changed": True,
        "config_file": str(target),
        "backup_file": str(backup_file) if backup_file else None,
        "updated_keys": sorted(updates),
    }
