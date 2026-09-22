from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import AgentSettings


BACKEND_ALIASES = {
    "codex": "codex-cli",
    "copilot": "copilot-cli",
}
CANONICAL_BACKENDS = {"codex-cli", "copilot-cli", "codex-desktop"}


def normalize_backend(backend: str) -> str:
    value = str(backend or "").strip()
    canonical = BACKEND_ALIASES.get(value, value)
    if canonical not in CANONICAL_BACKENDS:
        raise ValueError(f"Unsupported coding backend {backend!r}")
    return canonical


@dataclass(frozen=True)
class WorkerPolicy:
    """Resolved, non-secret execution policy for one coding backend."""

    backend: str
    model: str | None
    reasoning_effort: str | None
    execution_mode: str
    sandbox_mode: str | None = None
    approval_policy: str | None = None
    network_access: bool | None = None
    disable_builtin_mcps: bool = False
    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerPolicy":
        return cls(
            backend=str(data["backend"]),
            model=(str(data["model"]) if data.get("model") else None),
            reasoning_effort=(
                str(data["reasoning_effort"])
                if data.get("reasoning_effort")
                else None
            ),
            execution_mode=str(data["execution_mode"]),
            sandbox_mode=(
                str(data["sandbox_mode"]) if data.get("sandbox_mode") else None
            ),
            approval_policy=(
                str(data["approval_policy"])
                if data.get("approval_policy")
                else None
            ),
            network_access=(
                bool(data["network_access"])
                if data.get("network_access") is not None
                else None
            ),
            disable_builtin_mcps=bool(data.get("disable_builtin_mcps", False)),
            allow_tools=tuple(str(item) for item in data.get("allow_tools", ())),
            deny_tools=tuple(str(item) for item in data.get("deny_tools", ())),
        )


def default_worker_policy(backend: str) -> WorkerPolicy:
    """Return stable defaults used when a handoff is built outside loaded settings."""

    backend = normalize_backend(backend)
    if backend in {"codex-cli", "codex-desktop"}:
        return WorkerPolicy(
            backend=backend,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            execution_mode="interactive",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_access=False,
        )
    if backend == "copilot-cli":
        return WorkerPolicy(
            backend="copilot-cli",
            model=None,
            reasoning_effort=None,
            execution_mode="interactive",
            disable_builtin_mcps=True,
            deny_tools=("shell(git push)",),
        )
    raise ValueError(f"Unsupported coding backend {backend!r}")


def resolve_worker_policy(settings: AgentSettings, backend: str) -> WorkerPolicy:
    """Resolve the user-owned worker policy for the selected provider."""

    backend = normalize_backend(backend)
    if backend in {"codex-cli", "codex-desktop"}:
        return WorkerPolicy(
            backend=backend,
            model=settings.codex_model,
            reasoning_effort=settings.codex_reasoning_effort,
            execution_mode=settings.codex_execution_mode,
            sandbox_mode=settings.codex_sandbox_mode,
            approval_policy=settings.codex_approval_policy,
            network_access=settings.codex_network_access,
        )
    if backend == "copilot-cli":
        return WorkerPolicy(
            backend="copilot-cli",
            model=settings.copilot_model,
            reasoning_effort=settings.copilot_reasoning_effort,
            execution_mode=settings.copilot_execution_mode,
            disable_builtin_mcps=settings.copilot_disable_builtin_mcps,
            allow_tools=settings.copilot_allow_tools,
            deny_tools=settings.copilot_deny_tools,
        )
    raise ValueError(f"Unsupported coding backend {backend!r}")


def _codex_argv(policy: WorkerPolicy, prompt: str) -> list[str]:
    if policy.execution_mode not in {"interactive", "exec"}:
        raise ValueError(
            f"Unsupported Codex execution mode {policy.execution_mode!r}"
        )

    argv = ["codex"]
    if policy.execution_mode == "exec":
        argv.append("exec")

    if policy.model:
        argv.extend(["--model", policy.model])
    if policy.sandbox_mode:
        argv.extend(["--sandbox", policy.sandbox_mode])
    if policy.approval_policy:
        argv.extend(["--ask-for-approval", policy.approval_policy])
    if policy.reasoning_effort:
        argv.extend(
            [
                "--config",
                f'model_reasoning_effort="{policy.reasoning_effort}"',
            ]
        )
    if policy.network_access is not None:
        argv.extend(
            [
                "--config",
                "sandbox_workspace_write.network_access="
                + ("true" if policy.network_access else "false"),
            ]
        )

    argv.append(prompt)
    return argv


def _copilot_argv(policy: WorkerPolicy, prompt: str) -> list[str]:
    if policy.execution_mode not in {"interactive", "programmatic"}:
        raise ValueError(
            f"Unsupported Copilot execution mode {policy.execution_mode!r}"
        )

    argv = ["copilot"]
    if policy.model:
        argv.append(f"--model={policy.model}")
    if policy.reasoning_effort:
        argv.append(f"--effort={policy.reasoning_effort}")
    if policy.disable_builtin_mcps:
        argv.append("--disable-builtin-mcps")
    for item in policy.allow_tools:
        argv.append(f"--allow-tool={item}")
    for item in policy.deny_tools:
        argv.append(f"--deny-tool={item}")

    if policy.execution_mode == "programmatic":
        argv.extend(["-p", prompt])
    else:
        argv.extend(["-i", prompt])
    return argv


def build_worker_argv(policy: WorkerPolicy, prompt: str) -> list[str]:
    """Build direct subprocess argv for CLI workers; never invokes a shell."""

    backend = normalize_backend(policy.backend)
    if backend == "codex-cli":
        return _codex_argv(policy, prompt)
    if backend == "copilot-cli":
        return _copilot_argv(policy, prompt)
    if backend == "codex-desktop":
        raise ValueError(
            "codex-desktop uses the App Server adapter and has no direct CLI argv"
        )
    raise ValueError(f"Unsupported coding backend {policy.backend!r}")


def display_worker_argv(policy: WorkerPolicy) -> list[str]:
    """Return a non-secret launch shape without embedding the task prompt."""

    if normalize_backend(policy.backend) == "codex-desktop":
        return ["codex-desktop", "app-server", "<agent_prompt>"]
    marker = "<agent_prompt>"
    return build_worker_argv(policy, marker)
