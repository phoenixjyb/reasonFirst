from __future__ import annotations

from typing import Any

from .project_config import PROJECT_CONFIG_FILENAME


BUILTIN_PROTECTED_PATHS = {PROJECT_CONFIG_FILENAME}


def path_is_protected(path: str, protected: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    for rule in protected:
        item = rule.replace("\\", "/").lstrip("./")
        if not item:
            continue
        if item.endswith("/"):
            if normalized.startswith(item):
                return True
        elif normalized == item or normalized.startswith(item + "/"):
            return True
    return False


def evaluate_finish_gates(
    *,
    project_context: dict[str, object],
    project_config_found: bool,
    validations: list[dict[str, object]],
    changed_paths: list[str],
    reviewability: dict[str, object],
    review_diff: dict[str, object],
    history_scan: dict[str, object],
    secret_findings: list[dict[str, object]],
    dirty: bool,
    commits_ahead_of_base: int,
    commit_message: str | None,
    pushed: bool,
    merge_request_url: str | None,
    allow_protected: bool = False,
    allow_secret_match: bool = False,
) -> dict[str, object]:
    """Apply transport-neutral controlled-finish candidate policy.

    This function intentionally performs no filesystem, Git or network access.
    Local and remote workspace adapters must collect truthful evidence first,
    then pass it here. A transport is not allowed to weaken these blocker
    semantics merely because its workspace lives elsewhere.
    """

    protected_rules = sorted(
        {
            *[
                str(item)
                for item in project_context.get("protected_paths", [])
                if isinstance(item, str)
            ],
            *BUILTIN_PROTECTED_PATHS,
        }
    )
    protected_changes = [
        path
        for path in changed_paths
        if path_is_protected(path, protected_rules)
    ]

    validation_blocked = any(
        bool(item.get("required", True))
        and not bool(item.get("passed"))
        for item in validations
        if isinstance(item, dict)
    )

    reviewable = bool(reviewability.get("ok"))
    history_complete = bool(history_scan.get("coverage_complete"))

    blockers: list[str] = []
    warnings: list[str] = []

    if dirty and not (commit_message or "").strip():
        blockers.append(
            "Workspace has uncommitted changes; --message is required for finish."
        )

    if not dirty and commits_ahead_of_base <= 0:
        blockers.append("Workspace has no changes or commits to finish.")

    if validation_blocked:
        blockers.append(
            "One or more required project validation commands failed."
        )

    if not reviewable:
        blockers.append(
            "One or more changed paths cannot be fully reviewed/secret-scanned "
            "by ActualCoder; inspect the reviewability issues and use the low-level "
            "workflow intentionally if this change must be handled."
        )

    if reviewable and bool(review_diff.get("truncated")):
        blockers.append(
            "The human-facing review diff was truncated by the configured output cap. "
            "Increase GITLAB_COMMAND_MAX_OUTPUT_BYTES or split the change before finish."
        )

    if protected_changes and not allow_protected:
        blockers.append(
            "Protected paths changed; review them and rerun with --allow-protected "
            "only when the scope is intentional."
        )

    if not history_complete:
        blockers.append(
            "Commit-history secret coverage is incomplete; finish is blocked."
        )

    if secret_findings and not allow_secret_match:
        blockers.append(
            "Potential credentials/secrets were detected in candidate or commit-history additions; "
            "remove them from the candidate AND unpublished history, or use "
            "--allow-secret-match only after explicit false-positive review."
        )

    if pushed and not merge_request_url:
        blockers.append(
            "This branch was already pushed without a recorded Merge Request. "
            "finish will not silently create or guess an MR after the first push."
        )

    if not validations:
        warnings.append(
            "No project validation commands are configured in the workspace base contract."
        )

    if not project_config_found:
        warnings.append(
            "No .actualcoder.yaml was present at the workspace base; finish is using default project policy."
        )

    if protected_changes and allow_protected:
        warnings.append(
            "Protected-path changes were explicitly allowed for this finish invocation."
        )

    if secret_findings and allow_secret_match:
        warnings.append(
            "Secret-scan findings were explicitly overridden for this finish invocation."
        )

    return {
        "ok": not blockers,
        "protected_paths": protected_rules,
        "protected_path_changes": protected_changes,
        "secret_scan": {
            "ok": reviewable and history_complete and not secret_findings,
            "coverage_complete": reviewable and history_complete,
            "history": history_scan,
            "findings": secret_findings,
            "overridden": bool(secret_findings and allow_secret_match),
        },
        "warnings": warnings,
        "blockers": blockers,
    }
