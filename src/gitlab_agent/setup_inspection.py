"""Read-only configuration inspection for setup/reuse, not a config migration.

Keep the established loader's precedence. The stricter selected-file check makes
missing, unreadable and malformed inputs distinct before guided setup can write.
It never prints file values/errors, changes environment overrides, or calls APIs.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from typing import Callable

from .config import AgentSettings, resolve_env_file
from .project_access import assert_project_allowed

MAX_CONFIG_BYTES = 128 * 1024
_KEY = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")


class UnsupportedConfig(ValueError):
    pass


def read_config_bytes(path: Path) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise UnsupportedConfig("Config must be a regular, non-symlink file")
    if info.st_size > MAX_CONFIG_BYTES:
        raise ValueError("Config exceeds the inspection limit")
    with path.open("rb") as handle:
        raw = handle.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Config exceeds the inspection limit")
    return raw


def validate_config_text(raw: bytes) -> None:
    """Accept the loader's simple, single-line .env dialect, without eval/source."""
    text = raw.decode("utf-8")
    if "\x00" in text:
        raise ValueError("NUL in configuration")
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError("Expected a simple assignment")
        key, value = (part.strip() for part in line.split("=", 1))
        if not _KEY.fullmatch(key) or key in seen:
            raise ValueError("Invalid or duplicate assignment")
        seen.add(key)
        if value[:1] in {"'", '"'} and (len(value) < 2 or value[-1] != value[0]):
            raise ValueError("Unterminated single-line value")


def inspect_configuration(
    *,
    path: Path | None = None,
    loader: Callable[[], AgentSettings] | None = None,
) -> tuple[AgentSettings | None, dict[str, object]]:
    selected = (path if path is not None else resolve_env_file()).expanduser()
    overrides = sorted(
        key for key in os.environ
        if key != "GITLAB_AGENT_ENV_FILE" and _KEY.fullmatch(key)
        and key.startswith(("GITLAB_", "REASONFIRST_"))
    )
    source = (
        "explicit_file" if os.getenv("GITLAB_AGENT_ENV_FILE") else
        "user_file" if selected == Path("~/.config/gitlab-agent/.env").expanduser() else
        "working_directory_file"
    )
    result: dict[str, object] = {
        "path": str(selected.absolute()), "source": source,
        "exists": False, "valid": False, "status": "missing",
        "error": None, "error_code": None,
        "environment_override_names": overrides,
        "authentication_verified": False,
    }
    raw: bytes | None = None
    try:
        # Mark a denied/unsupported path as not absent, without reading its target.
        result["exists"] = selected.exists() or selected.is_symlink()
        raw = read_config_bytes(selected)
        result["exists"] = raw is not None
        if raw is not None:
            validate_config_text(raw)
        before = dict(os.environ)
        try:
            settings = (loader or AgentSettings.load)()
        finally:
            os.environ.clear()
            os.environ.update(before)
        if read_config_bytes(selected) != raw:
            raise ValueError("Selected file changed during inspection")
        for project in settings.allowed_projects:
            assert_project_allowed(project, set())
    except UnsupportedConfig:
        result.update(status="unsupported_layout", error_code="configuration_unsupported",
                      error="Selected config is not a regular non-symlink file; inspect it locally. No automatic migration.")
        return None, result
    except OSError:
        result.update(status="unreadable", error_code="configuration_unreadable",
                      error="Selected config could not be read. Fix access locally; do not replace it with a new config.")
        return None, result
    except Exception:
        # Do not echo parser exceptions: they may contain input lines or values.
        has_environment_config = any(key.startswith("GITLAB_") for key in overrides)
        if raw is None and not result["exists"] and not has_environment_config:
            result.update(error_code="configuration_missing", error="GitLab configuration is missing.")
        else:
            result.update(status="invalid", error_code="invalid_configuration",
                          error="Existing configuration is invalid, ambiguous, or changed during inspection. Inspect it locally; no new setup was written.")
        return None, result

    missing: list[str] = []
    if not (settings.api_token or settings.git_token):
        missing.append("gitlab_credential")
    if not settings.allowed_projects and settings.require_write_allowlist:
        missing.append("approved_projects")
    result.update(
        path=str(settings.config_file.absolute()), exists=settings.config_file.is_file(),
        valid=True, status="valid" if settings.config_file.is_file() else "environment_only",
        gitlab_base_url=settings.gitlab_base_url,
        has_api_token=bool(settings.api_token), has_git_credential=bool(settings.git_token),
        allowed_projects=sorted(settings.allowed_projects),
        require_write_allowlist=settings.require_write_allowlist,
        default_backend=settings.default_backend, workspace_root=str(settings.workspace_root),
        missing_fields=missing,
    )
    return settings, result
