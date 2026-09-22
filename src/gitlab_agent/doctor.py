from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .config import AgentSettings
from .gitlab_api import GitLabAPI


_STATUS_ORDER = {"pass": 0, "skip": 1, "warn": 2, "fail": 3}


def _check(
    checks: list[dict[str, object]],
    name: str,
    status: str,
    message: str,
    **details: object,
) -> None:
    item: dict[str, object] = {
        "name": name,
        "status": status,
        "message": message,
    }
    if details:
        item["details"] = details
    checks.append(item)


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _workspace_health(root: Path) -> dict[str, int]:
    state_dir = root / "state"
    if not state_dir.is_dir():
        return {"states": 0, "active": 0, "stale": 0, "malformed": 0}

    total = active = stale = malformed = 0
    for path in state_dir.glob("*.json"):
        total += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            worktree = Path(str(data["worktree_path"])).expanduser()
            if worktree.is_dir():
                active += 1
            else:
                stale += 1
        except Exception:
            malformed += 1
    return {
        "states": total,
        "active": active,
        "stale": stale,
        "malformed": malformed,
    }


def run_doctor(
    *,
    offline: bool = False,
    git_only: bool = False,
    settings_loader: Callable[[], AgentSettings] | None = None,
    which: Callable[[str], str | None] | None = None,
    api_factory: Callable[[AgentSettings], GitLabAPI] | None = None,
) -> dict[str, object]:
    """Run non-destructive environment and connectivity diagnostics."""

    load_settings = settings_loader or AgentSettings.load
    resolve_executable = which or shutil.which
    make_api = api_factory or GitLabAPI

    checks: list[dict[str, object]] = []

    _check(
        checks,
        "actual_coder_version",
        "pass",
        f"ActualCoder {__version__}",
        version=__version__,
    )

    py_ok = sys.version_info >= (3, 10)
    _check(
        checks,
        "python",
        "pass" if py_ok else "fail",
        f"Python {sys.version.split()[0]}",
        executable=sys.executable,
        required=">=3.10",
    )

    for executable in ("git", "uv"):
        resolved = resolve_executable(executable)
        _check(
            checks,
            f"executable_{executable}",
            "pass" if resolved else "fail",
            f"{executable} is {'available' if resolved else 'not available on PATH'}",
            path=resolved,
        )

    installed_agents: list[str] = []
    for agent, executable in (("codex", "codex"), ("copilot", "copilot")):
        resolved = resolve_executable(executable)
        if resolved:
            installed_agents.append(agent)
        _check(
            checks,
            f"agent_{agent}",
            "pass" if resolved else "warn",
            (
                f"{agent} CLI is installed"
                if resolved
                else f"{agent} CLI is not installed; this backend will be unavailable"
            ),
            path=resolved,
            authentication_checked=False,
        )

    _check(
        checks,
        "coding_backend",
        "pass" if installed_agents else "fail",
        (
            "At least one coding backend is available: " + ", ".join(installed_agents)
            if installed_agents
            else "No supported coding backend is installed"
        ),
        installed=installed_agents,
    )

    tunnel = resolve_executable("tunnel-client")
    _check(
        checks,
        "tunnel_client",
        "pass" if tunnel else "skip",
        (
            "tunnel-client is available for the optional ChatGPT MCP path"
            if tunnel
            else "tunnel-client is not installed; ActualCoder still works, but ChatGPT MCP tunnel setup is unavailable"
        ),
        path=tunnel,
        optional=True,
    )

    tunnel_key_set = bool(os.getenv("CONTROL_PLANE_API_KEY"))
    _check(
        checks,
        "tunnel_runtime_key",
        "pass" if tunnel_key_set else "skip",
        (
            "CONTROL_PLANE_API_KEY is present in the current process"
            if tunnel_key_set
            else "CONTROL_PLANE_API_KEY is not present; only needed while running/diagnosing the optional tunnel"
        ),
        optional=True,
        value_exposed=False,
    )

    if os.getenv("OPENAI_API_KEY"):
        _check(
            checks,
            "openai_model_api_key",
            "warn",
            "OPENAI_API_KEY is present, but this project does not require direct OpenAI model API access",
            value_exposed=False,
        )
    else:
        _check(
            checks,
            "openai_model_api_key",
            "pass",
            "No OPENAI_API_KEY is required by ActualCoder",
            value_exposed=False,
        )

    try:
        settings = load_settings()
    except Exception as exc:
        _check(
            checks,
            "configuration",
            "fail",
            f"Configuration could not be loaded: {exc}",
        )
        result = _finish(checks, offline=offline)
    result["git_only"] = git_only
    return result

    config_file = settings.config_file
    if config_file.is_file():
        _check(
            checks,
            "config_file",
            "pass",
            "Configuration file exists",
            path=str(config_file),
        )
        if os.name == "nt":
            _check(
                checks,
                "config_permissions",
                "skip",
                "Windows ACLs are not automatically audited; restrict the config file to the current user",
                path=str(config_file),
            )
        else:
            mode = stat.S_IMODE(config_file.stat().st_mode)
            overly_broad = bool(mode & 0o077)
            _check(
                checks,
                "config_permissions",
                "warn" if overly_broad else "pass",
                (
                    f"Config permissions are broader than recommended ({oct(mode)}); use chmod 600"
                    if overly_broad
                    else f"Config permissions are restricted ({oct(mode)})"
                ),
                path=str(config_file),
                mode=oct(mode),
            )
    else:
        _check(
            checks,
            "config_file",
            "warn",
            "No config file exists; effective settings came from environment variables",
            path=str(config_file),
        )

    _check(
        checks,
        "gitlab_url",
        "warn" if settings.gitlab_base_url.startswith("http://") else "pass",
        (
            "GitLab uses HTTP; keep this hop on a trusted private network/VPN"
            if settings.gitlab_base_url.startswith("http://")
            else "GitLab uses HTTPS"
        ),
        base_url=settings.gitlab_base_url,
    )

    _check(
        checks,
        "gitlab_api_token",
        (
            "pass"
            if settings.api_token
            else ("skip" if git_only else "fail")
        ),
        (
            "GITLAB_TOKEN is configured"
            if settings.api_token
            else (
                "GITLAB_TOKEN is intentionally absent in Git-only mode; "
                "GitLab REST API/MCP/CI metadata features are unavailable"
                if git_only
                else "GITLAB_TOKEN is missing; read MCP and GitLab API metadata operations will fail"
            )
        ),
        value_exposed=False,
        git_only=git_only,
    )
    _check(
        checks,
        "git_credential",
        "pass" if settings.git_token else "fail",
        (
            "A Git credential is configured"
            if settings.git_token
            else "No Git credential is configured for clone/fetch/push"
        ),
        git_credential_source=(
            "git_token"
            if os.getenv("GITLAB_GIT_TOKEN")
            else ("git_password" if os.getenv("GITLAB_GIT_PASSWORD") else "api_token_fallback")
        ),
        scope_checked=False,
        value_exposed=False,
    )

    if settings.allowed_projects:
        _check(
            checks,
            "project_allowlist",
            "pass",
            f"{len(settings.allowed_projects)} project(s) are explicitly allowlisted",
            projects=sorted(settings.allowed_projects),
        )
    elif settings.require_write_allowlist:
        _check(
            checks,
            "project_allowlist",
            "fail",
            "Write allowlist is required but GITLAB_ALLOWED_PROJECTS is empty",
        )
    else:
        _check(
            checks,
            "project_allowlist",
            "warn",
            "Write allowlist protection is explicitly disabled",
        )

    proxy_vars = {
        key: bool(os.getenv(key))
        for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY")
        if os.getenv(key)
    }
    if settings.api_trust_env or settings.git_trust_env:
        _check(
            checks,
            "proxy_policy",
            "warn" if proxy_vars else "pass",
            (
                "Host proxy variables may affect GitLab traffic"
                if proxy_vars
                else "Proxy inheritance is enabled but no common proxy variables are set"
            ),
            api_trust_env=settings.api_trust_env,
            git_trust_env=settings.git_trust_env,
            proxy_variables=sorted(proxy_vars),
        )
    else:
        _check(
            checks,
            "proxy_policy",
            "pass",
            "GitLab API and managed Git traffic bypass host proxy environment/config by default",
            proxy_variables_detected=sorted(proxy_vars),
        )

    root = settings.workspace_root.expanduser()
    parent = _nearest_existing_parent(root)
    writable = os.access(parent, os.W_OK)
    _check(
        checks,
        "workspace_root",
        "pass" if writable else "fail",
        (
            "Workspace root exists or can be created under a writable parent"
            if writable
            else "Workspace root parent is not writable"
        ),
        workspace_root=str(root),
        checked_parent=str(parent),
    )

    try:
        usage = shutil.disk_usage(parent)
        free_gib = usage.free / (1024 ** 3)
        disk_status = "fail" if free_gib < 0.5 else ("warn" if free_gib < 2.0 else "pass")
        _check(
            checks,
            "disk_space",
            disk_status,
            f"{free_gib:.1f} GiB free near workspace root",
            free_gib=round(free_gib, 2),
        )
    except OSError as exc:
        _check(checks, "disk_space", "warn", f"Could not inspect disk space: {exc}")

    health = _workspace_health(root)
    if health["malformed"]:
        workspace_status = "warn"
        workspace_message = "Workspace state contains malformed state files"
    elif health["stale"]:
        workspace_status = "warn"
        workspace_message = "Some managed workspace state points to missing worktree directories"
    else:
        workspace_status = "pass"
        workspace_message = "Managed workspace state is internally consistent"
    _check(
        checks,
        "workspace_state",
        workspace_status,
        workspace_message,
        **health,
    )

    if offline:
        _check(
            checks,
            "gitlab_api_connectivity",
            "skip",
            "GitLab API connectivity check skipped by --offline",
        )
    elif git_only:
        _check(
            checks,
            "gitlab_api_connectivity",
            "skip",
            "GitLab API connectivity intentionally unavailable in Git-only mode",
        )
    elif not settings.api_token:
        _check(
            checks,
            "gitlab_api_connectivity",
            "skip",
            "GitLab API connectivity not attempted because GITLAB_TOKEN is missing",
        )
    else:
        try:
            data = make_api(settings).get_json("/user")
            username = data.get("username") if isinstance(data, dict) else None
            _check(
                checks,
                "gitlab_api_connectivity",
                "pass",
                "GitLab API authentication succeeded",
                username=username,
            )
        except Exception as exc:
            _check(
                checks,
                "gitlab_api_connectivity",
                "fail",
                f"GitLab API authentication/connectivity failed: {exc}",
            )

    return _finish(checks, offline=offline)


def _finish(checks: list[dict[str, object]], *, offline: bool) -> dict[str, object]:
    counts = {status: 0 for status in _STATUS_ORDER}
    for item in checks:
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1

    if counts["fail"]:
        overall = "fail"
    elif counts["warn"]:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "ok": counts["fail"] == 0,
        "overall": overall,
        "offline": offline,
        "summary": counts,
        "checks": checks,
    }
