from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import AgentSettings
from .project_config import (
    PROJECT_CONFIG_FILENAME,
    PROJECT_CONFIG_MAX_BYTES,
    parse_project_config,
)
from .secret_scan import redact_sensitive_text
from .workspace import WorkspaceManager


EVIDENCE_PACK_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_value(value: Any, redactions: set[str]) -> Any:
    if isinstance(value, str):
        text, kinds = redact_sensitive_text(value)
        redactions.update(kinds)
        return text
    if isinstance(value, dict):
        return {
            str(key): _redact_value(item, redactions)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item, redactions) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item, redactions) for item in value]
    return value


def build_evidence_pack(
    *,
    settings: AgentSettings,
    manager: WorkspaceManager,
    workspace_id: str,
    ci_feedback: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build bounded read-only evidence for a managed workspace.

    This function never runs repository code, commits, pushes, or mutates task
    state. It reports incomplete/truncated evidence explicitly.
    """

    status = manager.status(workspace_id)
    state = manager.get_state(workspace_id)

    remote = manager.read_remote_text_file(
        state.project,
        PROJECT_CONFIG_FILENAME,
        ref=state.base_sha,
        refresh_remote=False,
        max_bytes=PROJECT_CONFIG_MAX_BYTES,
    )
    parsed = parse_project_config(
        remote["content"] if remote["exists"] else None,
        settings=settings,
        source_ref=str(remote["ref"]),
        source_path=PROJECT_CONFIG_FILENAME,
    )
    if not parsed.valid:
        raise RuntimeError(
            "Workspace base .actualcoder.yaml is invalid: "
            + "; ".join(parsed.errors)
        )

    changed_paths = manager.changed_paths(workspace_id)
    reviewability = manager.reviewability(workspace_id, changed_paths)
    diff_result = manager.diff(workspace_id)

    redactions: set[str] = set()
    redacted_diff, diff_redactions = redact_sensitive_text(
        str(diff_result.get("diff") or "")
    )
    redactions.update(diff_redactions)
    safe_diff = {
        **diff_result,
        "diff": redacted_diff,
        "redactions": sorted(set(diff_redactions)),
    }

    task = _redact_value(manager.task_context(workspace_id), redactions)
    project_config = _redact_value(
        {
            "found": parsed.found,
            "source": {
                "ref": parsed.source_ref,
                "path": parsed.source_path,
            },
            "commit_sha": remote["commit_sha"],
            "effective": dict(parsed.effective),
            "warnings": parsed.warnings,
        },
        redactions,
    )
    safe_ci = (
        _redact_value(ci_feedback, redactions)
        if ci_feedback is not None
        else None
    )

    warnings: list[str] = []
    ci_incomplete = False
    if not bool(reviewability.get("ok")):
        warnings.append(
            "Some changed paths are not fully reviewable as bounded UTF-8 text."
        )
    if bool(diff_result.get("truncated")):
        warnings.append(
            "The human-facing diff is truncated by the configured output cap."
        )
    if isinstance(ci_feedback, dict):
        if not bool(ci_feedback.get("found", True)):
            ci_incomplete = True
            warnings.append(
                "CI evidence was requested but no pipeline was found for this workspace."
            )
        if bool(ci_feedback.get("stale_for_workspace")):
            ci_incomplete = True
            warnings.append(
                "Attached CI evidence is stale for the current workspace HEAD."
            )
        pipeline = ci_feedback.get("pipeline")
        if isinstance(pipeline, dict):
            status_text = str(pipeline.get("status") or "")
            if status_text in {
                "created", "waiting_for_resource", "preparing", "pending",
                "running", "scheduled", "manual",
            }:
                ci_incomplete = True
                warnings.append(
                    f"Attached CI pipeline is incomplete (status={status_text})."
                )

    workspace_summary = _redact_value(
        {
            key: status.get(key)
            for key in (
                "workspace_id",
                "project",
                "base_ref",
                "base_sha",
                "branch",
                "head",
                "dirty",
                "commits_ahead_of_base",
                "pushed",
                "remote_branch",
                "merge_request_url",
                "created_at",
            )
        },
        redactions,
    )
    safe_changed_paths = _redact_value(changed_paths, redactions)
    safe_reviewability = _redact_value(reviewability, redactions)

    pack: dict[str, object] = {
        "version": EVIDENCE_PACK_VERSION,
        "generated_at": _now(),
        "workspace": workspace_summary,
        "task": task,
        "project_config": project_config,
        "review": {
            "changed_paths": safe_changed_paths,
            "reviewability": safe_reviewability,
            "diff": safe_diff,
        },
        "ci": safe_ci,
        "redactions": sorted(redactions),
        "warnings": warnings,
        "complete_for_human_review": (
            bool(reviewability.get("ok"))
            and not bool(diff_result.get("truncated"))
            and not ci_incomplete
        ),
    }
    return pack
