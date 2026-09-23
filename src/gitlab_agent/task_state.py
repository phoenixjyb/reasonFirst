from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import uuid


TASK_SPEC_VERSION = 1
MAX_GOAL_CHARS = 16000
MAX_ATTEMPT_GOAL_CHARS = 4096
MAX_ITEM_CHARS = 2000
MAX_ITEMS = 32
MAX_ATTEMPTS = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: str, *, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"{label} must be <= {limit} characters")
    if "\x00" in text:
        raise ValueError(f"{label} must not contain NUL")
    return text


def _bounded_preview(value: str, *, limit: int) -> tuple[str, bool]:
    text = str(value or "").strip()
    if "\x00" in text:
        raise ValueError("attempt goal must not contain NUL")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _bounded_items(values: list[str] | tuple[str, ...] | None, *, label: str) -> tuple[str, ...]:
    raw = list(values or [])
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"{label} must contain at most {MAX_ITEMS} items")
    result: list[str] = []
    for index, value in enumerate(raw):
        item = _bounded_text(
            str(value),
            label=f"{label}[{index}]",
            limit=MAX_ITEM_CHARS,
        )
        if item:
            result.append(item)
    return tuple(result)


def _items_from_data(data: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = data.get(key, ())
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{key} must be a list")
    return tuple(str(item) for item in raw)


@dataclass(frozen=True)
class TaskSpec:
    version: int
    task_slug: str
    goal: str
    project: str
    base_ref: str
    base_sha: str
    requested_backend: str
    project_config_sha: str | None
    acceptance_criteria: tuple[str, ...]
    non_goals: tuple[str, ...]
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        task_slug: str,
        goal: str,
        project: str,
        base_ref: str,
        base_sha: str,
        requested_backend: str,
        project_config_sha: str | None = None,
        acceptance_criteria: list[str] | tuple[str, ...] | None = None,
        non_goals: list[str] | tuple[str, ...] | None = None,
    ) -> "TaskSpec":
        return cls(
            version=TASK_SPEC_VERSION,
            task_slug=_bounded_text(task_slug, label="task_slug", limit=128) or "task",
            goal=_bounded_text(goal, label="goal", limit=MAX_GOAL_CHARS),
            project=_bounded_text(project, label="project", limit=512),
            base_ref=_bounded_text(base_ref, label="base_ref", limit=256),
            base_sha=_bounded_text(base_sha, label="base_sha", limit=128),
            requested_backend=_bounded_text(
                requested_backend,
                label="requested_backend",
                limit=64,
            ) or "auto",
            project_config_sha=(
                _bounded_text(
                    project_config_sha,
                    label="project_config_sha",
                    limit=128,
                )
                if project_config_sha
                else None
            ),
            acceptance_criteria=_bounded_items(
                acceptance_criteria,
                label="acceptance_criteria",
            ),
            non_goals=_bounded_items(non_goals, label="non_goals"),
            created_at=utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSpec":
        if int(data.get("version", 0)) != TASK_SPEC_VERSION:
            raise ValueError("Unsupported TaskSpec version")
        return cls(
            version=TASK_SPEC_VERSION,
            task_slug=_bounded_text(
                str(data.get("task_slug") or "task"),
                label="task_slug",
                limit=128,
            ),
            goal=_bounded_text(
                str(data.get("goal") or ""),
                label="goal",
                limit=MAX_GOAL_CHARS,
            ),
            project=_bounded_text(
                str(data.get("project") or ""),
                label="project",
                limit=512,
            ),
            base_ref=_bounded_text(
                str(data.get("base_ref") or ""),
                label="base_ref",
                limit=256,
            ),
            base_sha=_bounded_text(
                str(data.get("base_sha") or ""),
                label="base_sha",
                limit=128,
            ),
            requested_backend=_bounded_text(
                str(data.get("requested_backend") or "auto"),
                label="requested_backend",
                limit=64,
            ),
            project_config_sha=(
                _bounded_text(
                    str(data.get("project_config_sha")),
                    label="project_config_sha",
                    limit=128,
                )
                if data.get("project_config_sha")
                else None
            ),
            acceptance_criteria=_bounded_items(
                _items_from_data(data, "acceptance_criteria"),
                label="acceptance_criteria",
            ),
            non_goals=_bounded_items(
                _items_from_data(data, "non_goals"),
                label="non_goals",
            ),
            created_at=_bounded_text(
                str(data.get("created_at") or ""),
                label="created_at",
                limit=128,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["acceptance_criteria"] = list(self.acceptance_criteria)
        data["non_goals"] = list(self.non_goals)
        return data


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    created_at: str
    source: str
    goal: str
    goal_truncated: bool
    requested_backend: str
    selected_backend: str
    ci_context_included: bool
    worker_policy: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        source: str,
        goal: str,
        requested_backend: str,
        selected_backend: str,
        ci_context_included: bool,
        worker_policy: dict[str, Any],
    ) -> "AttemptRecord":
        goal_preview, goal_truncated = _bounded_preview(
            goal,
            limit=MAX_ATTEMPT_GOAL_CHARS,
        )
        return cls(
            attempt_id=uuid.uuid4().hex[:12],
            created_at=utc_now(),
            source=_bounded_text(source, label="source", limit=64),
            goal=goal_preview,
            goal_truncated=goal_truncated,
            requested_backend=_bounded_text(
                requested_backend,
                label="attempt.requested_backend",
                limit=64,
            ),
            selected_backend=_bounded_text(
                selected_backend,
                label="attempt.selected_backend",
                limit=64,
            ),
            ci_context_included=bool(ci_context_included),
            worker_policy=dict(worker_policy),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AttemptRecord":
        policy = data.get("worker_policy")
        if not isinstance(policy, dict):
            policy = {}
        goal_preview, goal_truncated = _bounded_preview(
            str(data.get("goal") or ""),
            limit=MAX_ATTEMPT_GOAL_CHARS,
        )
        return cls(
            attempt_id=_bounded_text(
                str(data.get("attempt_id") or ""),
                label="attempt_id",
                limit=64,
            ),
            created_at=_bounded_text(
                str(data.get("created_at") or ""),
                label="attempt.created_at",
                limit=128,
            ),
            source=_bounded_text(
                str(data.get("source") or ""),
                label="attempt.source",
                limit=64,
            ),
            goal=goal_preview,
            goal_truncated=bool(data.get("goal_truncated", goal_truncated)),
            requested_backend=_bounded_text(
                str(data.get("requested_backend") or ""),
                label="attempt.requested_backend",
                limit=64,
            ),
            selected_backend=_bounded_text(
                str(data.get("selected_backend") or ""),
                label="attempt.selected_backend",
                limit=64,
            ),
            ci_context_included=bool(data.get("ci_context_included")),
            worker_policy=dict(policy),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def bounded_attempts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [AttemptRecord.from_dict(item).to_dict() for item in items]
    return normalized[-MAX_ATTEMPTS:]
