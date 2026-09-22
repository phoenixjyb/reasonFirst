from __future__ import annotations

import hashlib
import json
from pathlib import Path

from typing import Any

from .config import AgentSettings
from .project_config import (
    PROJECT_CONFIG_FILENAME,
    PROJECT_CONFIG_MAX_BYTES,
    parse_project_config,
)
from .runner import CommandRunner
from .review_gates import evaluate_review_gates
from .history_scan import HistoryScanError, scan_history_secrets
from .workspace import WorkspaceManager


def _load_base_contract(
    settings: AgentSettings,
    manager: WorkspaceManager,
    workspace_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
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

    metadata = {
        "found": parsed.found,
        "source": {
            "ref": parsed.source_ref,
            "path": parsed.source_path,
        },
        "commit_sha": remote["commit_sha"],
        "warnings": parsed.warnings,
    }
    return dict(parsed.effective), metadata


def build_finish_plan(
    *,
    settings: AgentSettings,
    manager: WorkspaceManager,
    runner: CommandRunner,
    workspace_id: str,
    commit_message: str | None = None,
    mr_title: str | None = None,
    mr_description: str = "",
    allow_protected: bool = False,
    allow_secret_match: bool = False,
) -> dict[str, object]:
    """Validate a workspace and produce the controlled finish plan."""

    state = manager.get_state(workspace_id)
    project_context, project_metadata = _load_base_contract(
        settings,
        manager,
        workspace_id,
    )

    validations: list[dict[str, object]] = []
    validation_blocked = False
    for raw_command in project_context.get("validation_commands", []):
        if not isinstance(raw_command, dict):
            continue
        argv = [
            str(item)
            for item in raw_command.get("argv", [])
            if isinstance(item, str)
        ]
        if not argv:
            continue

        timeout_raw = raw_command.get(
            "timeout_seconds",
            settings.command_timeout_seconds,
        )
        timeout = (
            int(timeout_raw)
            if isinstance(timeout_raw, int) and not isinstance(timeout_raw, bool)
            else settings.command_timeout_seconds
        )
        required = bool(raw_command.get("required", True))
        result = runner.run(
            workspace_id,
            argv,
            timeout_seconds=timeout,
        )
        passed = (
            not bool(result.get("timed_out"))
            and result.get("returncode") == 0
        )
        blocked = required and not passed
        validation_blocked = validation_blocked or blocked
        validations.append(
            {
                "name": str(raw_command.get("name") or argv[0]),
                "argv": argv,
                "required": required,
                "passed": passed,
                "blocking": blocked,
                "result": result,
            }
        )

    # Re-read the exact workspace state after validations. Validation commands may
    # create/update files, so the reviewed diff and safety scan must reflect the
    # post-validation state that could actually be committed/pushed.
    status = manager.status(workspace_id)
    changed_paths = manager.changed_paths(workspace_id)
    reviewability = manager.reviewability(workspace_id, changed_paths)

    history_scan: dict[str, object] = {
        "coverage_complete": False,
        "findings": [],
        "error": "History scan skipped because candidate content is not reviewable",
    }
    if bool(reviewability.get("ok")):
        diff_result = manager.diff(workspace_id)
        security_diff = manager.security_diff(workspace_id)
        try:
            history_scan = scan_history_secrets(
                worktree=Path(str(status["worktree_path"])),
                base_sha=str(status["base_sha"]),
                head_sha=str(status["head"]),
                timeout_seconds=settings.command_timeout_seconds,
            )
        except HistoryScanError as exc:
            history_scan = {
                "coverage_complete": False,
                "findings": [],
                "error": str(exc),
            }
    else:
        security_diff = ""
        diff_result = {
            "workspace_id": workspace_id,
            "base_sha": status.get("base_sha"),
            "truncated": True,
            "original_bytes": None,
            "diff": (
                "Full diff/security scan skipped because one or more changed "
                "paths are not safely reviewable as bounded UTF-8 text."
            ),
        }

    gates = evaluate_review_gates(
        project_context=project_context,
        validations=validations,
        changed_paths=changed_paths,
        reviewability=reviewability,
        diff_result=diff_result,
        security_diff=security_diff,
        history_scan=history_scan,
        allow_protected=allow_protected,
        allow_secret_match=allow_secret_match,
    )

    dirty = bool(status["dirty"])
    ahead = int(status["commits_ahead_of_base"])
    blockers: list[str] = list(gates["blockers"])
    warnings: list[str] = list(gates["warnings"])

    if dirty and not (commit_message or "").strip():
        blockers.append(
            "Workspace has uncommitted changes; --message is required for finish."
        )

    if not dirty and ahead <= 0:
        blockers.append("Workspace has no changes or commits to finish.")

    if state.pushed and not state.merge_request_url:
        blockers.append(
            "This branch was already pushed without a recorded Merge Request. "
            "finish will not silently create or guess an MR after the first push."
        )

    mr_config = project_context.get("mr", {})
    if not isinstance(mr_config, dict):
        mr_config = {}
    target_branch = str(
        mr_config.get("target_branch")
        or status["base_ref"]
    ).strip()
    title_prefix = str(mr_config.get("title_prefix") or "")

    subject = ""
    if dirty and (commit_message or "").strip():
        subject = str(commit_message).strip()
    elif ahead > 0:
        subject = manager.latest_commit_subject(workspace_id)

    title = (mr_title or "").strip()
    if not title and subject:
        title = title_prefix + subject
    elif title_prefix and title and not title.startswith(title_prefix):
        title = title_prefix + title

    push_action = "push-update" if state.pushed else "push-mr"
    if push_action == "push-mr" and not title:
        blockers.append(
            "A Merge Request title could not be derived; provide --title or --message."
        )

    if not validations:
        warnings.append(
            "No project validation commands are configured in the workspace base contract."
        )
    if not project_metadata["found"]:
        warnings.append(
            "No .actualcoder.yaml was present at the workspace base; finish is using default project policy."
        )


    snapshot_payload = {
        "head": status.get("head"),
        "dirty": bool(status.get("dirty")),
        "status_porcelain": str(status.get("status_porcelain") or ""),
        "commits_ahead_of_base": int(status.get("commits_ahead_of_base", 0)),
        "pushed": bool(state.pushed),
        "merge_request_url": state.merge_request_url,
        "remote_branch": state.remote_branch,
        "security_diff_sha256": hashlib.sha256(
            security_diff.encode("utf-8", errors="replace")
        ).hexdigest(),
    }
    snapshot_digest = hashlib.sha256(
        json.dumps(
            snapshot_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "ok": not blockers,
        "workspace": status,
        "project_config": {
            **project_metadata,
            "effective": project_context,
        },
        "changed_paths": changed_paths,
        "reviewability": reviewability,
        "protected_paths": gates["protected_paths"],
        "protected_path_changes": gates["protected_path_changes"],
        "secret_scan": gates["secret_scan"],
        "validations": validations,
        "review_diff": gates["review_diff"],
        "snapshot": {
            "digest": snapshot_digest,
            **snapshot_payload,
        },
        "plan": {
            "commit_required": dirty,
            "commit_message": (
                str(commit_message).strip()
                if commit_message is not None
                else None
            ),
            "push_action": push_action,
            "target_branch": target_branch,
            "mr_title": title or None,
            "mr_description": mr_description,
            "existing_mr": state.merge_request_url,
        },
        "warnings": warnings,
        "blockers": blockers,
    }


def execute_finish(
    *,
    manager: WorkspaceManager,
    workspace_id: str,
    plan: dict[str, object],
) -> dict[str, object]:
    if not bool(plan.get("ok")):
        raise RuntimeError("Cannot execute a blocked finish plan")

    action = plan["plan"]
    if not isinstance(action, dict):
        raise RuntimeError("Invalid finish plan")

    expected_snapshot = plan.get("snapshot")
    if not isinstance(expected_snapshot, dict):
        raise RuntimeError("Finish plan is missing its reviewed workspace snapshot")

    current_status = manager.status(workspace_id)
    current_state = manager.get_state(workspace_id)
    current_security_diff = manager.security_diff(workspace_id)
    current_payload = {
        "head": current_status.get("head"),
        "dirty": bool(current_status.get("dirty")),
        "status_porcelain": str(current_status.get("status_porcelain") or ""),
        "commits_ahead_of_base": int(
            current_status.get("commits_ahead_of_base", 0)
        ),
        "pushed": bool(current_state.pushed),
        "merge_request_url": current_state.merge_request_url,
        "remote_branch": current_state.remote_branch,
        "security_diff_sha256": hashlib.sha256(
            current_security_diff.encode("utf-8", errors="replace")
        ).hexdigest(),
    }
    current_digest = hashlib.sha256(
        json.dumps(
            current_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    if current_digest != expected_snapshot.get("digest"):
        raise RuntimeError(
            "Workspace changed after the finish plan was reviewed. "
            "No Git write was performed; rerun actual-coder finish."
        )

    commit_result: dict[str, object] | None = None
    if bool(action.get("commit_required")):
        message = str(action.get("commit_message") or "").strip()
        commit_result = manager.commit(workspace_id, message)

    push_action = str(action.get("push_action"))
    if push_action == "push-mr":
        push_result = manager.push_and_create_mr(
            workspace_id,
            target_branch=str(action.get("target_branch") or ""),
            title=str(action.get("mr_title") or ""),
            description=str(action.get("mr_description") or ""),
        )
    elif push_action == "push-update":
        push_result = manager.push(workspace_id)
    else:
        raise RuntimeError(f"Unknown finish push action: {push_action}")

    return {
        "workspace_id": workspace_id,
        "commit": commit_result,
        "push": push_result,
        "final_workspace": manager.status(workspace_id),
    }
