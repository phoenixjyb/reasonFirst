from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx

from .codex_app_server import managed_app_server_socket
from .config import AgentSettings
from .project_access import (
    ProjectAccessError,
    assert_project_allowed,
    check_project_access,
)
from .setup_config import (
    ConfigMutationError,
    apply_env_updates,
    assert_no_effective_env_override,
    ensure_persistent_config_target,
    selected_user_config_path,
)
from .tls import api_client_options, validate_base_url


CANONICAL_WORKERS = ("auto", "codex-cli", "copilot-cli", "codex-desktop")


def detect_workers(
    *,
    which: Callable[[str], str | None] | None = None,
    desktop_socket: Path | None = None,
) -> list[dict[str, object]]:
    resolve = which or shutil.which
    socket = desktop_socket or managed_app_server_socket()
    return [
        {
            "name": "codex-cli",
            "available": bool(resolve("codex")),
            "path": resolve("codex"),
            "authentication_verified": False,
        },
        {
            "name": "copilot-cli",
            "available": bool(resolve("copilot")),
            "path": resolve("copilot"),
            "authentication_verified": False,
        },
        {
            "name": "codex-desktop",
            "available": socket.exists(),
            "path": str(socket),
            "authentication_verified": False,
        },
    ]


def _project_client_factory(settings: AgentSettings):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={
                "PRIVATE-TOKEN": settings.api_token,
                "Accept": "application/json",
            },
            timeout=10,
            trust_env=settings.api_trust_env,
            **api_client_options(
                settings.gitlab_base_url,
                verify_ssl=settings.api_verify_ssl,
                ca_bundle=settings.api_ca_bundle,
                asynchronous=True,
            ),
        )

    return factory


def preflight_project(
    settings: AgentSettings,
    project: str,
    *,
    ref: str = "",
    required_files: list[str] | None = None,
) -> dict[str, Any]:
    key = assert_project_allowed(project, set())
    transient_allowed = set(settings.allowed_projects)
    transient_allowed.add(key)
    result = asyncio.run(
        check_project_access(
            key,
            allowed=transient_allowed,
            token_present=bool(settings.api_token),
            client_factory=_project_client_factory(settings),
            base_url=settings.gitlab_base_url,
            ref=ref,
            required_files=required_files or [],
        )
    )
    result["workspace_policy_allowed"] = key in transient_allowed
    result["writes_performed"] = False
    return result


def plan_project_add(
    settings: AgentSettings,
    project: str,
    *,
    ref: str = "",
    required_files: list[str] | None = None,
) -> dict[str, Any]:
    key = assert_project_allowed(project, set())
    preflight = preflight_project(
        settings,
        key,
        ref=ref,
        required_files=required_files,
    )
    config_target = ensure_persistent_config_target(settings.config_file)
    existing = sorted(settings.allowed_projects)
    after = sorted(set(existing) | {key})
    return {
        "ok": bool(preflight.get("ok")),
        "operation": "project-add",
        "project": key,
        "requested_ref": ref or None,
        "resolved_commit_sha": preflight.get("resolved_commit_sha"),
        "already_allowed": key in settings.allowed_projects,
        "config_file": str(config_target),
        "before_projects": existing,
        "after_projects": after,
        "preflight": preflight,
        "writes_performed": False,
        "reload_required": key not in settings.allowed_projects,
    }


def apply_project_add(
    settings: AgentSettings,
    plan: dict[str, Any],
) -> dict[str, Any]:
    if not bool(plan.get("ok")):
        raise ConfigMutationError("Cannot grant a project whose preflight did not pass")

    project = str(plan.get("project") or "")
    assert_project_allowed(project, set())
    expected_before = sorted(settings.allowed_projects)
    if plan.get("before_projects") != expected_before:
        raise ConfigMutationError("Project allowlist changed after planning; rerun project add")

    if bool(plan.get("already_allowed")):
        return {
            **plan,
            "writes_performed": False,
            "reload_required": False,
            "config_change": {
                "changed": False,
                "config_file": str(selected_user_config_path()),
                "backup_file": None,
                "updated_keys": ["GITLAB_ALLOWED_PROJECTS"],
            },
        }

    assert_no_effective_env_override(["GITLAB_ALLOWED_PROJECTS"])
    config_path = Path(str(plan["config_file"])).expanduser()
    result = apply_env_updates(
        config_path,
        {
            "GITLAB_ALLOWED_PROJECTS": ",".join(plan["after_projects"]),
            "GITLAB_REQUIRE_WRITE_ALLOWLIST": "true",
        },
    )
    return {
        **plan,
        "writes_performed": bool(result["changed"]),
        "reload_required": bool(result["changed"]),
        "config_change": result,
    }


def list_projects(settings: AgentSettings) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "project-list",
        "config_file": str(settings.config_file),
        "projects": sorted(settings.allowed_projects),
        "require_write_allowlist": settings.require_write_allowlist,
        "writes_performed": False,
    }


def plan_worker_use(
    backend: str,
    *,
    which: Callable[[str], str | None] | None = None,
    desktop_socket: Path | None = None,
) -> dict[str, Any]:
    choice = backend.strip()
    if choice not in CANONICAL_WORKERS:
        raise ValueError(
            "worker must be one of: " + ", ".join(CANONICAL_WORKERS)
        )
    workers = detect_workers(which=which, desktop_socket=desktop_socket)
    selected = next(
        (item for item in workers if item["name"] == choice),
        None,
    )
    available = True if choice == "auto" else bool(selected and selected["available"])
    return {
        "ok": available,
        "operation": "worker-use",
        "backend": choice,
        "available": available,
        "authentication_verified": False,
        "detected_workers": workers,
        "config_file": str(selected_user_config_path()),
        "writes_performed": False,
        "message": (
            "Worker preference can be saved; provider authentication is not proven "
            "by executable detection."
            if available
            else f"{choice} is not locally available; install/start it before selecting it."
        ),
    }


def apply_worker_use(plan: dict[str, Any]) -> dict[str, Any]:
    if not bool(plan.get("ok")):
        raise ConfigMutationError(str(plan.get("message") or "Worker is unavailable"))
    backend = str(plan.get("backend") or "")
    if backend not in CANONICAL_WORKERS:
        raise ConfigMutationError("Worker plan contains an unsupported backend")

    assert_no_effective_env_override(["REASONFIRST_DEFAULT_BACKEND"])
    result = apply_env_updates(
        selected_user_config_path(),
        {"REASONFIRST_DEFAULT_BACKEND": backend},
    )
    return {
        **plan,
        "writes_performed": bool(result["changed"]),
        "config_change": result,
    }


def candidate_settings(
    *,
    base_url: str,
    api_token: str,
    project: str,
    default_backend: str,
    current: AgentSettings | None = None,
) -> AgentSettings:
    """Build an in-memory candidate for verification before writing config."""
    endpoint = validate_base_url(base_url.strip().rstrip("/"))
    token = api_token.strip()
    if not token:
        raise ValueError("GitLab API token is required for guided setup")
    key = assert_project_allowed(project, set())
    if default_backend not in CANONICAL_WORKERS:
        raise ValueError("Unsupported worker backend")

    if current is not None:
        git_token = (
            token
            if not current.git_token or current.git_token == current.api_token
            else current.git_token
        )
        return replace(
            current,
            gitlab_base_url=endpoint,
            api_token=token,
            git_token=git_token,
            allowed_projects=set(current.allowed_projects) | {key},
            require_write_allowlist=True,
            default_backend=default_backend,
        )

    return AgentSettings(
        config_file=selected_user_config_path(),
        gitlab_base_url=endpoint,
        api_token=token,
        api_verify_ssl=True,
        api_trust_env=False,
        git_token=token,
        git_username="oauth2",
        git_trust_env=False,
        allowed_projects={key},
        require_write_allowlist=True,
        workspace_root=Path("~/.local/share/chatgpt-gitlab-mcp").expanduser(),
        branch_prefix="chatgpt/",
        default_base_ref="main",
        allowed_executables={
            "python",
            "python3",
            "pytest",
            "uv",
            "node",
            "npm",
            "pnpm",
            "yarn",
            "make",
            "cmake",
            "ninja",
            "cargo",
            "go",
            "mvn",
            "gradle",
        },
        command_timeout_seconds=300,
        max_output_bytes=120000,
        max_file_bytes=1000000,
        git_author_name=None,
        git_author_email=None,
        default_backend=default_backend,
    )
