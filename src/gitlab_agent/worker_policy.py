from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .config import AgentSettings


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

    if backend == "codex":
        return WorkerPolicy(
            backend="codex",
            model="gpt-5.6-sol",
            reasoning_effort="high",
            execution_mode="interactive",
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_access=False,
        )
    if backend == "antigravity":
        return WorkerPolicy(
            backend="antigravity", model=None, reasoning_effort=None,
            execution_mode="interactive", sandbox_mode="terminal",
        )
    if backend == "copilot":
        return WorkerPolicy(
            backend="copilot",
            model=None,
            reasoning_effort=None,
            execution_mode="interactive",
            disable_builtin_mcps=True,
            deny_tools=("shell(git push)",),
        )
    raise ValueError(f"Unsupported coding backend {backend!r}")


def resolve_worker_policy(settings: AgentSettings, backend: str) -> WorkerPolicy:
    """Resolve the user-owned worker policy for the selected provider."""

    if backend == "codex":
        return WorkerPolicy(
            backend="codex",
            model=settings.codex_model,
            reasoning_effort=settings.codex_reasoning_effort,
            execution_mode=settings.codex_execution_mode,
            sandbox_mode=settings.codex_sandbox_mode,
            approval_policy=settings.codex_approval_policy,
            network_access=settings.codex_network_access,
        )
    if backend == "antigravity":
        return WorkerPolicy(
            backend="antigravity", model=settings.antigravity_model,
            reasoning_effort=settings.antigravity_reasoning_effort,
            execution_mode="interactive", sandbox_mode="terminal",
        )
    if backend == "copilot":
        return WorkerPolicy(
            backend="copilot",
            model=settings.copilot_model,
            reasoning_effort=settings.copilot_reasoning_effort,
            execution_mode=settings.copilot_execution_mode,
            disable_builtin_mcps=settings.copilot_disable_builtin_mcps,
            allow_tools=settings.copilot_allow_tools,
            deny_tools=settings.copilot_deny_tools,
        )
    raise ValueError(f"Unsupported coding backend {backend!r}")


def launch_policy_evidence(
    policy: WorkerPolicy,
    *,
    surface: str,
) -> dict[str, object]:
    """Describe what ReasonFirst can prove before the worker starts.

    CLI surfaces encode requested policy into explicit launch arguments but do
    not expose a provider-resolved model response before execution. Codex
    Desktop upgrades this evidence at runtime through App Server.
    """

    if policy.backend == "antigravity":
        # Validate the exact supported request before labelling it as encoded.
        _antigravity_argv(policy, "<agent_prompt>")
        return {
            "status": "launch_arguments_encoded",
            "verification_scope": "launch-arguments",
            "runtime_verified": False,
            "surface": "antigravity-cli",
            "requested": policy.to_dict(),
            "sandbox_requested": True,
            "permissions_source": "provider-settings-not-inspected",
            "permissions_verified": False,
            "conversation_strategy": "fresh-task-handoff",
            "note": (
                "Antigravity terminal sandbox was requested with --sandbox. "
                "Model/effort are inherited unless explicitly supplied. "
                "Provider mode, permissions, MCP tools, hooks and network policy "
                "are inherited and not inspected or verified; unsandboxed grants "
                "may still apply. CLI exit zero is not task or test acceptance."
            ),
        }
    return {
        "status": "launch_arguments_encoded",
        "verification_scope": "launch-arguments",
        "runtime_verified": False,
        "surface": surface,
        "requested": policy.to_dict(),
        "note": (
            "ReasonFirst encoded the requested policy explicitly. "
            "Runtime/provider resolution has not yet been independently verified."
        ),
    }


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


def _antigravity_argv(policy: WorkerPolicy, prompt: str) -> list[str]:
    """The observed agy 1.3.4 interactive interface, without permission expansion."""
    if (policy.execution_mode != "interactive" or policy.sandbox_mode != "terminal"
            or policy.approval_policy is not None or policy.network_access is not None
            or policy.disable_builtin_mcps or policy.allow_tools or policy.deny_tools):
        raise ValueError("Unsupported Antigravity policy; only interactive terminal sandbox requests are supported")
    if policy.reasoning_effort is not None and policy.reasoning_effort not in {
        "low", "medium", "high", "xhigh", "max",
    }:
        raise ValueError("Unsupported Antigravity reasoning effort")
    argv = ["agy", "--sandbox"]
    if policy.model is not None:
        if (not isinstance(policy.model, str) or not policy.model.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in policy.model)):
            raise ValueError("Invalid Antigravity model")
        # Equals form keeps option-like values and spaces within a single value.
        argv.append("--model=" + policy.model)
    if policy.reasoning_effort is not None:
        argv.append("--effort=" + policy.reasoning_effort)
    # The fixed prefix in _agent_prompt prevents task text being parsed as a flag.
    argv.extend(["--prompt-interactive", prompt])
    return argv


def build_worker_argv(policy: WorkerPolicy, prompt: str) -> list[str]:
    """Build direct subprocess argv; never invokes a shell."""

    if policy.backend == "codex":
        return _codex_argv(policy, prompt)
    if policy.backend == "copilot":
        return _copilot_argv(policy, prompt)
    if policy.backend == "antigravity":
        return _antigravity_argv(policy, prompt)
    raise ValueError(f"Unsupported coding backend {policy.backend!r}")


def display_worker_argv(policy: WorkerPolicy) -> list[str]:
    """Return the policy-bearing argv shape without embedding the task prompt."""

    marker = "<agent_prompt>"
    argv = build_worker_argv(policy, marker)
    return argv
