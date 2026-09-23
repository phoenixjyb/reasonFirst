from __future__ import annotations

from typing import Any

from .project_config import PROJECT_CONFIG_FILENAME
from .secret_scan import redact_sensitive_text, scan_added_diff_for_secrets


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


def evaluate_review_gates(
    *,
    project_context: dict[str, object],
    validations: list[dict[str, object]],
    changed_paths: list[str],
    reviewability: dict[str, object],
    diff_result: dict[str, object],
    security_diff: str,
    history_scan: dict[str, object],
    allow_protected: bool = False,
    allow_secret_match: bool = False,
) -> dict[str, object]:
    """Apply transport-neutral candidate review/security gates.

    Callers are responsible for collecting complete transport-specific evidence.
    This function decides policy and redacts the human-facing diff.
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
        and not bool(item.get("passed", False))
        for item in validations
        if isinstance(item, dict)
    )

    reviewable = bool(reviewability.get("ok"))
    history_complete = bool(history_scan.get("coverage_complete"))

    secret_findings: list[dict[str, Any]] = []
    if reviewable:
        secret_findings.extend(scan_added_diff_for_secrets(security_diff))
        history_findings = history_scan.get("findings")
        if isinstance(history_findings, list):
            secret_findings.extend(
                item for item in history_findings if isinstance(item, dict)
            )

    review_output = dict(diff_result)
    if reviewable:
        redacted_diff, redactions = redact_sensitive_text(
            str(review_output.get("diff") or "")
        )
        review_output["diff"] = redacted_diff
        review_output["redactions"] = redactions

    blockers: list[str] = []
    warnings: list[str] = []

    if validation_blocked:
        blockers.append("One or more required project validation commands failed.")

    if not reviewable:
        blockers.append(
            "One or more changed paths cannot be fully reviewed/secret-scanned "
            "by ReasonFirst."
        )

    if reviewable and bool(review_output.get("truncated")):
        blockers.append(
            "The human-facing review diff was truncated by the configured output cap. "
            "Increase the output cap or split the change before publication."
        )

    if protected_changes and not allow_protected:
        blockers.append(
            "Protected paths changed; explicit protected-path approval is required."
        )

    if not history_complete:
        blockers.append(
            "Commit-history secret coverage is incomplete; publication is blocked."
        )

    if secret_findings and not allow_secret_match:
        blockers.append(
            "Potential credentials/secrets were detected in candidate or commit-history "
            "additions; remove them from the candidate and unpublished history, or "
            "explicitly approve a reviewed false positive."
        )

    if protected_changes and allow_protected:
        warnings.append(
            "Protected-path changes were explicitly allowed for this review."
        )
    if secret_findings and allow_secret_match:
        warnings.append(
            "Secret-scan findings were explicitly overridden after review."
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
        "review_diff": review_output,
        "validation_blocked": validation_blocked,
        "warnings": warnings,
        "blockers": blockers,
    }
