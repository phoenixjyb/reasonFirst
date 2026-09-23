from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from .config import AgentSettings
from .gitlab_api import GitLabAPI
from .project_config import (
    PROJECT_CONFIG_FILENAME,
    PROJECT_CONFIG_MAX_BYTES,
    parse_project_config,
)
from .workspace import WorkspaceManager


PRACTICE_REQUIRED_FILES = (
    "README.md",
    "EXERCISE.md",
    "AGENTS.md",
    PROJECT_CONFIG_FILENAME,
    ".gitlab-ci.yml",
    "clip_summary.py",
    "tests/test_clip_summary.py",
)

PRACTICE_PROTECTED_PATHS = {
    PROJECT_CONFIG_FILENAME,
    ".gitlab-ci.yml",
    "AGENTS.md",
    "EXERCISE.md",
}

PRACTICE_STAGE1_TASK = "stage1-strict-summary"
PRACTICE_STAGE1_GOAL = (
    "Implement only Stage 1 strict clip-duration summary semantics from EXERCISE.md. "
    "Preserve summarize_clips(durations) and the existing output keys. Add the required "
    "regression tests. Do not implement Stage 2 or Stage 3."
)
PRACTICE_STAGE1_ACCEPTANCE = (
    "summarize_clips accepts a finite iterable, including a one-shot generator, containing Python int/float values but rejects bool",
    "every duration must be nonnegative, finite, and representable as float; invalid items raise ValueError",
    "numeric strings, None, NaN, infinity, negative values, unrepresentable values, and a nonfinite aggregate such as [1e308, 1e308] are rejected",
    "input is not mutated and a one-shot iterable is consumed only once",
    "empty input returns count 0, total_seconds 0.0, mean_seconds 0.0",
    "nonempty output has integer count and float total_seconds/mean_seconds while ordinary existing examples remain compatible",
    "tests cover the Stage 1 requirements beyond the original five baseline cases and the configured unit-tests command passes",
)
PRACTICE_STAGE1_NON_GOALS = (
    "Do not add min_seconds or any Stage 2 filtering behavior",
    "Do not add summary_json or any Stage 3 JSON behavior",
    "Do not modify .actualcoder.yaml, .gitlab-ci.yml, AGENTS.md, or EXERCISE.md",
    "Do not add dependencies, CLI behavior, file/network I/O, timestamps, paths, or unrelated refactors",
    "Do not commit, push, open or merge an MR from the coding worker",
)

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


def _finish(
    checks: list[dict[str, object]],
    *,
    project: str,
    ref: str,
    resolved_commit_sha: str | None,
    agent: str,
) -> dict[str, object]:
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

    ready = counts["fail"] == 0
    return {
        "ok": ready,
        "ready_for_stage1": ready,
        "overall": overall,
        "summary": counts,
        "project": project,
        "ref": ref,
        "resolved_commit_sha": resolved_commit_sha,
        "requested_agent": agent,
        "checks": checks,
        "stage1": {
            "task": PRACTICE_STAGE1_TASK,
            "goal": PRACTICE_STAGE1_GOAL,
            "acceptance_criteria": list(PRACTICE_STAGE1_ACCEPTANCE),
            "non_goals": list(PRACTICE_STAGE1_NON_GOALS),
            "next": (
                "Run actual-coder practice-start with the same project/ref/agent, "
                "inspect the persisted TaskSpec/evidence, then explicitly resume --launch."
                if ready
                else "Resolve failed checks before creating a practice workspace."
            ),
        },
    }


def _normalize_agent(agent: str) -> str:
    aliases = {
        "codex": "codex-cli",
        "copilot": "copilot-cli",
    }
    return aliases.get(agent, agent)


def _agent_executable(agent: str) -> str | None:
    normalized = _normalize_agent(agent)
    if normalized == "codex-cli":
        return "codex"
    if normalized == "copilot-cli":
        return "copilot"
    return None


def _no_proxy_covers(host: str, env: Mapping[str, str]) -> bool:
    raw = env.get("NO_PROXY") or env.get("no_proxy") or ""
    for item in raw.split(","):
        token = item.strip().lower()
        if not token:
            continue
        token = token.split(":", 1)[0]
        if token == "*":
            return True
        if token.startswith("."):
            token = token[1:]
        if host == token or host.endswith("." + token):
            return True
    return False


def _runner_config_entries(path: Path) -> list[dict[str, object]]:
    """Parse only non-secret runner fields needed for diagnostics.

    This intentionally avoids a TOML dependency on Python 3.10 and never returns
    runner tokens. It recognizes the small subset written by GitLab Runner.
    """

    if not path.is_file():
        return []

    runners: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    section = ""

    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line == "[[runners]]":
            current = {}
            runners.append(current)
            section = "runner"
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]").strip()
            continue
        if current is None or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip('"').strip("'")

        if section == "runner" and key in {
            "name",
            "url",
            "executor",
            "request_concurrency",
        }:
            current[key] = value
        elif section == "runners.custom" and key in {
            "run_exec",
            "prepare_exec",
            "cleanup_exec",
        }:
            custom = current.setdefault("custom", {})
            if isinstance(custom, dict):
                custom[key] = value

    return runners


def _safe_runner_api_summary(item: Mapping[str, Any]) -> dict[str, object]:
    return {
        "id": item.get("id"),
        "description": item.get("description"),
        "status": item.get("status"),
        "paused": item.get("paused"),
        "active": item.get("active"),
        "runner_type": item.get("runner_type"),
        "run_untagged": item.get("run_untagged"),
        "access_level": item.get("access_level"),
        "tag_list": list(item.get("tag_list") or []),
    }


def _runner_is_eligible_for_untagged_mr(item: Mapping[str, Any]) -> bool:
    if item.get("paused") is True or item.get("active") is False:
        return False
    if item.get("run_untagged") is not True:
        return False
    if item.get("access_level") == "ref_protected":
        return False
    status = str(item.get("status") or "").lower()
    return status in {"online", "idle", "active"}


def run_practice_doctor(
    *,
    project: str,
    ref: str | None = None,
    agent: str = "auto",
    settings_loader: Callable[[], AgentSettings] | None = None,
    manager_factory: Callable[[AgentSettings], WorkspaceManager] | None = None,
    api_factory: Callable[[AgentSettings], GitLabAPI] | None = None,
    which: Callable[[str], str | None] | None = None,
    environ: Mapping[str, str] | None = None,
    runner_config_path: Path | None = None,
) -> dict[str, object]:
    """Run a non-destructive preflight for the synthetic ReasonFirst practice lab."""

    load_settings = settings_loader or AgentSettings.load
    make_manager = manager_factory or WorkspaceManager
    make_api = api_factory or GitLabAPI
    resolve_executable = which or shutil.which
    env = os.environ if environ is None else environ

    checks: list[dict[str, object]] = []
    requested_ref = (ref or "main").strip() or "main"
    resolved_commit_sha: str | None = None

    try:
        settings = load_settings()
    except Exception as exc:
        _check(
            checks,
            "configuration",
            "fail",
            f"Configuration could not be loaded: {exc}",
        )
        return _finish(
            checks,
            project=project,
            ref=requested_ref,
            resolved_commit_sha=None,
            agent=agent,
        )

    requested_ref = (ref or settings.default_base_ref).strip() or settings.default_base_ref

    try:
        settings.assert_project_allowed_for_workspace(project)
        _check(
            checks,
            "project_allowlist",
            "pass",
            "Practice project is explicitly allowed by the effective workspace policy",
            project=project,
        )
        project_allowed = True
    except Exception as exc:
        _check(
            checks,
            "project_allowlist",
            "fail",
            f"Practice project is not allowed by the effective workspace policy: {exc}",
            project=project,
        )
        project_allowed = False

    normalized_agent = _normalize_agent(agent)
    if normalized_agent == "auto":
        available = [
            name
            for name, executable in (("codex-cli", "codex"), ("copilot-cli", "copilot"))
            if resolve_executable(executable)
        ]
        _check(
            checks,
            "practice_worker",
            "pass" if available else "fail",
            (
                "At least one practice worker is installed: " + ", ".join(available)
                if available
                else "No supported CLI worker is installed for the practice lab"
            ),
            requested="auto",
            available=available,
        )
    else:
        executable = _agent_executable(normalized_agent)
        resolved = resolve_executable(executable) if executable else None
        _check(
            checks,
            "practice_worker",
            "pass" if resolved else "fail",
            (
                f"{normalized_agent} is installed"
                if resolved
                else f"{normalized_agent} is not available on PATH"
            ),
            requested=normalized_agent,
            executable=executable,
            path=resolved,
        )

    proxy_names = [
        key
        for key in (
            "ALL_PROXY",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "all_proxy",
            "https_proxy",
            "http_proxy",
        )
        if env.get(key)
    ]
    gitlab_host = (urlparse(settings.gitlab_base_url).hostname or "").lower()
    bypassed = bool(gitlab_host and _no_proxy_covers(gitlab_host, env))
    if proxy_names and not bypassed:
        _check(
            checks,
            "external_client_proxy",
            "warn",
            (
                "Host proxy variables are set and NO_PROXY does not cover the GitLab host. "
                "ReasonFirst-managed API/Git may bypass them, but raw git/curl/gitlab-runner "
                "can still be routed through the proxy."
            ),
            proxy_variables=sorted(proxy_names),
            gitlab_host=gitlab_host,
            no_proxy_covers_gitlab=False,
            suggested_no_proxy=f"{gitlab_host},127.0.0.1,localhost" if gitlab_host else None,
        )
    else:
        _check(
            checks,
            "external_client_proxy",
            "pass",
            (
                "External GitLab clients are covered by NO_PROXY"
                if proxy_names
                else "No common host proxy variables are set for external GitLab clients"
            ),
            proxy_variables=sorted(proxy_names),
            gitlab_host=gitlab_host,
            no_proxy_covers_gitlab=bypassed,
        )

    manager: WorkspaceManager | None = None
    parsed = None
    if project_allowed:
        try:
            manager = make_manager(settings)
            remote = manager.read_remote_text_file(
                project,
                PROJECT_CONFIG_FILENAME,
                ref=requested_ref,
                max_bytes=PROJECT_CONFIG_MAX_BYTES,
            )
            resolved_commit_sha = str(remote["commit_sha"])
            parsed = parse_project_config(
                remote["content"] if remote["exists"] else None,
                settings=settings,
                source_ref=resolved_commit_sha,
                source_path=PROJECT_CONFIG_FILENAME,
            )
        except Exception as exc:
            _check(
                checks,
                "practice_project_read",
                "fail",
                f"Could not resolve/read the practice project: {exc}",
                project=project,
                ref=requested_ref,
            )

    if manager is not None and resolved_commit_sha is not None:
        file_results: list[dict[str, object]] = []
        all_readable = True
        for path in PRACTICE_REQUIRED_FILES:
            try:
                if path == PROJECT_CONFIG_FILENAME and parsed is not None:
                    readable = parsed.found
                else:
                    item = manager.read_remote_text_file(
                        project,
                        path,
                        ref=resolved_commit_sha,
                        refresh_remote=False,
                        max_bytes=512 * 1024,
                    )
                    readable = bool(item.get("exists"))
            except Exception:
                readable = False
            file_results.append({"path": path, "readable": readable})
            all_readable = all_readable and readable

        _check(
            checks,
            "practice_required_files",
            "pass" if all_readable else "fail",
            (
                "All required practice-kit files are readable at the pinned commit"
                if all_readable
                else "The practice project is not seeded with the complete current practice kit"
            ),
            commit_sha=resolved_commit_sha,
            files=file_results,
        )

    if parsed is not None:
        contract_ok = parsed.found and parsed.valid
        _check(
            checks,
            "practice_project_contract",
            "pass" if contract_ok else "fail",
            (
                "Practice .actualcoder.yaml is present and valid"
                if contract_ok
                else "Practice .actualcoder.yaml is missing or invalid"
            ),
            found=parsed.found,
            valid=parsed.valid,
            errors=parsed.errors,
            warnings=parsed.warnings,
            commit_sha=resolved_commit_sha,
        )

        effective = parsed.effective if parsed.valid else {}
        validations = effective.get("validation_commands", [])
        unit_test_ready = any(
            isinstance(item, dict)
            and item.get("name") == "unit-tests"
            and item.get("required") is True
            for item in validations
            if isinstance(validations, list)
        )
        _check(
            checks,
            "practice_unit_tests_contract",
            "pass" if unit_test_ready else "fail",
            (
                "Practice contract requires the real unit-tests validation"
                if unit_test_ready
                else "Practice contract does not require a unit-tests validation"
            ),
        )

        protected = set(effective.get("protected_paths", [])) if isinstance(
            effective.get("protected_paths", []), list
        ) else set()
        missing_protected = sorted(PRACTICE_PROTECTED_PATHS - protected)
        _check(
            checks,
            "practice_protected_paths",
            "pass" if not missing_protected else "fail",
            (
                "Practice policy protects the exercise/CI contract files"
                if not missing_protected
                else "Practice policy is missing expected protected paths"
            ),
            missing=missing_protected,
        )

    runner_binary = resolve_executable("gitlab-runner")
    _check(
        checks,
        "gitlab_runner_binary",
        "pass" if runner_binary else "warn",
        (
            "gitlab-runner is installed"
            if runner_binary
            else "gitlab-runner is not installed; GitLab CI may remain pending/stuck"
        ),
        path=runner_binary,
    )

    config_path = (
        runner_config_path.expanduser()
        if runner_config_path is not None
        else Path("~/.gitlab-runner/config.toml").expanduser()
    )
    runner_entries = _runner_config_entries(config_path)
    matching_entries = [
        item
        for item in runner_entries
        if str(item.get("url") or "").rstrip("/") == settings.gitlab_base_url.rstrip("/")
    ]
    if runner_binary and not config_path.is_file():
        _check(
            checks,
            "gitlab_runner_local_config",
            "warn",
            "gitlab-runner is installed but no user-mode config.toml was found",
            path=str(config_path),
        )
    elif runner_entries and not matching_entries:
        _check(
            checks,
            "gitlab_runner_local_config",
            "warn",
            "Local runner config exists but no runner targets the configured GitLab URL",
            path=str(config_path),
            runners=[
                {"name": item.get("name"), "url": item.get("url"), "executor": item.get("executor")}
                for item in runner_entries
            ],
        )
    elif matching_entries:
        unsafe_custom: list[str] = []
        shell_names: list[str] = []
        docker_without_binary: list[str] = []
        safe_summaries: list[dict[str, object]] = []
        for item in matching_entries:
            executor = str(item.get("executor") or "")
            name = str(item.get("name") or "unnamed")
            safe_summaries.append(
                {
                    "name": name,
                    "url": item.get("url"),
                    "executor": executor,
                    "request_concurrency": item.get("request_concurrency"),
                }
            )
            if executor == "custom":
                custom = item.get("custom")
                run_exec = custom.get("run_exec") if isinstance(custom, dict) else None
                if not run_exec:
                    unsafe_custom.append(name)
            elif executor == "shell":
                shell_names.append(name)
            elif executor == "docker" and not resolve_executable("docker"):
                docker_without_binary.append(name)

        if unsafe_custom:
            _check(
                checks,
                "gitlab_runner_local_config",
                "fail",
                "A configured custom executor is missing RunExec and will fail before tests start",
                runners=unsafe_custom,
                path=str(config_path),
            )
        elif docker_without_binary:
            _check(
                checks,
                "gitlab_runner_local_config",
                "fail",
                "A Docker runner is configured but docker is not available on PATH",
                runners=docker_without_binary,
                path=str(config_path),
            )
        else:
            _check(
                checks,
                "gitlab_runner_local_config",
                "pass",
                "Local runner configuration has no known executor bootstrap blocker",
                path=str(config_path),
                runners=safe_summaries,
                shell_executor_note=(
                    "Shell executors ignore .gitlab-ci.yml image: declarations and use host tools."
                    if shell_names
                    else None
                ),
            )

    if project_allowed and settings.api_token:
        try:
            api = make_api(settings)
            project_runners = api.project_runners(project)
            enriched_runners: list[dict[str, Any]] = []
            for item in project_runners:
                merged = dict(item)
                needs_details = any(
                    key not in merged
                    for key in ("run_untagged", "access_level", "tag_list")
                )
                runner_id = merged.get("id")
                if needs_details and isinstance(runner_id, int):
                    try:
                        merged.update(api.runner(runner_id))
                    except Exception:
                        pass
                enriched_runners.append(merged)

            safe_runners = [_safe_runner_api_summary(item) for item in enriched_runners]
            eligible = [
                item
                for item in enriched_runners
                if _runner_is_eligible_for_untagged_mr(item)
            ]
            _check(
                checks,
                "gitlab_project_runner",
                "pass" if eligible else "fail",
                (
                    "At least one online project-visible runner can accept untagged MR jobs"
                    if eligible
                    else "No online project-visible runner is currently eligible for untagged MR jobs"
                ),
                runners=safe_runners,
                eligible_runner_ids=[item.get("id") for item in eligible],
            )
        except Exception as exc:
            _check(
                checks,
                "gitlab_project_runner",
                "warn",
                (
                    "Could not inspect project runner eligibility through the GitLab API; "
                    "verify runner assignment, Online status, Run untagged jobs, and Protected=false in GitLab"
                ),
                error=str(exc),
            )
    elif not settings.api_token:
        _check(
            checks,
            "gitlab_project_runner",
            "skip",
            "Runner eligibility requires GitLab API metadata and was not checked",
        )

    return _finish(
        checks,
        project=project,
        ref=requested_ref,
        resolved_commit_sha=resolved_commit_sha,
        agent=agent,
    )
