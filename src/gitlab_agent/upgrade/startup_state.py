"""Selected, non-secret policy for disposable managed startup observations.

This is deliberately not a digest of all effective runtime configuration.  The
snapshot holds the objects supplied to the managed controller; the projection
contains only the named policy fields below.  Credentials, their presence and
their hashes are never part of the projection.  No loader runs in the parent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.bridge_preview.bridge_config import (
    BridgeConfigError,
    ExecutionTarget,
    resolve_configured_target,
    resolve_target,
)
from gitlab_agent.config import AgentSettings
from gitlab_agent.tls import validate_base_url
from gitlab_agent.worker_policy import WorkerPolicy, resolve_worker_policy


CONFIGURATION_PROJECTION = "reasonfirst-selected-startup-policy-v1"
MAX_PROJECTION_BYTES = 64 * 1024


class StartupStateError(ValueError):
    """Fixed errors; callers must not expose rejected configuration values."""


def _path(value: Path) -> Path:
    if not isinstance(value, Path):
        raise StartupStateError("invalid_configuration_reference")
    return value.expanduser().resolve()


def _strings(values: object, *, unordered: bool = False) -> tuple[str, ...]:
    if not isinstance(values, (set, frozenset, tuple, list)):
        raise StartupStateError("invalid_configuration_policy")
    if len(values) > 512 or any(
        not isinstance(item, str) or not item or len(item) > 4096
        for item in values
    ):
        raise StartupStateError("invalid_configuration_policy")
    return tuple(sorted(values)) if unordered else tuple(values)


def _worker_projection(policy: WorkerPolicy) -> dict[str, Any]:
    # An explicit allowlist prevents a future dataclass field silently entering
    # this version of the projection.
    return {
        "backend": policy.backend,
        "model": policy.model,
        "reasoning_effort": policy.reasoning_effort,
        "execution_mode": policy.execution_mode,
        "sandbox_mode": policy.sandbox_mode,
        "approval_policy": policy.approval_policy,
        "network_access": policy.network_access,
        "disable_builtin_mcps": policy.disable_builtin_mcps,
        "allow_tools": list(policy.allow_tools),
        "deny_tools": list(policy.deny_tools),
    }


def _target_projection(target: ExecutionTarget) -> dict[str, Any]:
    return {
        "type": target.type,
        "name": target.name,
        "host": target.host,
        "repo": target.repo,
        "codex_backend": target.codex_backend,
        "remote_codex": target.remote_codex,
        "ssh_connect_timeout": target.ssh_connect_timeout,
        "network_access": target.network_access,
        "validation_engine": target.validation_engine,
        "validation_image": target.validation_image,
        "validation_allowed_executables": list(target.validation_allowed_executables),
        "validation_network_access": target.validation_network_access,
    }


@dataclass(frozen=True)
class ManagedStartupState:
    # Settings may carry credentials for existing internal consumers.  They are
    # neither displayed nor serialized.  The disposable factory supplies none.
    settings: AgentSettings = field(repr=False)
    bridge_config_path: Path
    state_dir: Path
    default_target: str
    default_codex_backend: str
    targets: tuple[tuple[str, ExecutionTarget], ...]
    codex_policy: WorkerPolicy
    copilot_policy: WorkerPolicy

    def configured_target(self, spec: Any = None) -> ExecutionTarget:
        key = self.default_target if spec in (None, "") else spec
        if not isinstance(key, str):
            raise BridgeConfigError("execution target must be a configured name")
        for name, target in self.targets:
            if name == key:
                return target
        raise BridgeConfigError("unknown managed execution target")

    def bridge_configuration(self) -> dict[str, Any]:
        """Return a detached resolver-compatible view of the frozen policy."""
        targets: dict[str, Any] = {}
        for name, target in self.targets:
            raw = _target_projection(target)
            for key in (
                "validation_engine", "validation_image",
                "validation_allowed_executables", "validation_network_access",
            ):
                raw.pop(key)
            raw["validation"] = {
                "engine": target.validation_engine,
                "image": target.validation_image,
                "allowed_executables": list(target.validation_allowed_executables),
                "network_access": target.validation_network_access,
            }
            targets[name] = raw
        return {
            "version": 4,
            "control": {},
            "defaults": {
                "target": self.default_target,
                "codex_backend": self.default_codex_backend,
            },
            "targets": targets,
        }

    def projection(self, launch: HTTPLaunch) -> dict[str, Any]:
        # Shape validation is independent of parent process environment.  The
        # actual child also validates its launch against its loaded environment.
        launch.validate({})
        settings = self.settings
        return {
            "protocol": CONFIGURATION_PROJECTION,
            "scope": "disposable-startup-observation",
            "transport": {
                "transport": "streamable-http",
                "host": launch.host,
                "port": launch.port,
                "path": launch.path,
                "mode": launch.mode,
                "control_policy": launch.control_policy,
                "remote_push_exposed": False,
                "tool_admission": "closed",
            },
            "references": {
                "agent_config_file": str(settings.config_file),
                "bridge_config_file": str(self.bridge_config_path),
                "bridge_state_dir": str(self.state_dir),
                "bridge_state_file": str(self.state_dir / "state.json"),
                "workspace_root": str(settings.workspace_root),
                "api_ca_bundle": (
                    str(settings.api_ca_bundle) if settings.api_ca_bundle is not None else None
                ),
            },
            "agent_policy": {
                "gitlab_base_url": settings.gitlab_base_url,
                "api_verify_ssl": settings.api_verify_ssl,
                "api_trust_env": settings.api_trust_env,
                "git_trust_env": settings.git_trust_env,
                "allowed_projects": sorted(settings.allowed_projects),
                "require_write_allowlist": settings.require_write_allowlist,
                "branch_prefix": settings.branch_prefix,
                "default_base_ref": settings.default_base_ref,
                "allowed_executables": sorted(settings.allowed_executables),
                "command_timeout_seconds": settings.command_timeout_seconds,
                "max_output_bytes": settings.max_output_bytes,
                "max_file_bytes": settings.max_file_bytes,
                "default_backend": settings.default_backend,
            },
            "bridge_policy": {
                "version": 4,
                "default_target": self.default_target,
                "default_codex_backend": self.default_codex_backend,
                "targets": [
                    {"configured_name": name, **_target_projection(target)}
                    for name, target in self.targets
                ],
            },
            "worker_requests": {
                "codex": _worker_projection(self.codex_policy),
                "copilot": _worker_projection(self.copilot_policy),
            },
        }

    def configuration_digest(self, launch: HTTPLaunch) -> str:
        try:
            encoded = json.dumps(
                self.projection(launch), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except Exception:
            raise StartupStateError("invalid_configuration_projection") from None
        if len(encoded) > MAX_PROJECTION_BYTES:
            raise StartupStateError("configuration_projection_too_large")
        return hashlib.sha256(encoded).hexdigest()


def capture_startup_state(
    settings: AgentSettings,
    bridge_config: dict[str, Any],
    *,
    bridge_config_path: Path,
    state_dir: Path,
) -> ManagedStartupState:
    """Detach selected policy from already loaded objects, without loading files."""
    if type(settings) is not AgentSettings or not isinstance(bridge_config, dict):
        raise StartupStateError("invalid_configuration_objects")
    if type(bridge_config.get("version")) is not int or bridge_config["version"] != 4:
        raise StartupStateError("invalid_configuration_version")
    try:
        captured = replace(
            settings,
            config_file=_path(settings.config_file),
            workspace_root=_path(settings.workspace_root),
            api_ca_bundle=(
                _path(settings.api_ca_bundle) if settings.api_ca_bundle is not None else None
            ),
            gitlab_base_url=validate_base_url(settings.gitlab_base_url),
            allowed_projects=frozenset(_strings(settings.allowed_projects, unordered=True)),
            allowed_executables=frozenset(_strings(settings.allowed_executables, unordered=True)),
            copilot_allow_tools=_strings(settings.copilot_allow_tools),
            copilot_deny_tools=_strings(settings.copilot_deny_tools),
        )
        defaults = bridge_config.get("defaults")
        if not isinstance(defaults, dict):
            raise StartupStateError("invalid_configuration_policy")
        raw_targets = bridge_config.get("targets")
        if not isinstance(raw_targets, dict):
            raise StartupStateError("invalid_configuration_policy")
        names = _strings(set(raw_targets) | {"local"}, unordered=True)
        # Use the existing resolver's defaults, normalization and validation.
        # Retain only the actual resolved ExecutionTarget objects.
        targets = tuple((name, resolve_target(name, config=bridge_config)) for name in names)
        default_target = str(defaults.get("target") or "local")
        resolved_default = resolve_configured_target(None, config=bridge_config)
        if dict(targets)[default_target] != resolved_default:
            raise StartupStateError("invalid_configuration_policy")
        state = ManagedStartupState(
            settings=captured,
            bridge_config_path=_path(bridge_config_path),
            state_dir=_path(state_dir),
            default_target=default_target,
            default_codex_backend=str(defaults.get("codex_backend") or "global-config-local"),
            targets=targets,
            codex_policy=resolve_worker_policy(captured, "codex"),
            copilot_policy=resolve_worker_policy(captured, "copilot"),
        )
        return state
    except Exception:
        raise StartupStateError("invalid_configuration_policy") from None


@dataclass(frozen=True)
class DisposableConfiguration:
    root: Path
    home: Path
    snapshot: ManagedStartupState
    environment: Mapping[str, str]


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def create_disposable_configuration(root: Path) -> DisposableConfiguration:
    """Create one synthetic configuration, exclusively inside a new directory.

    The parent constructs expected objects directly from these explicit values.
    The child uses the ordinary loaders.  Every projected setting is explicit,
    so this fixture does not implement a second set of loader defaults.
    """
    root = _path(root)
    root.mkdir(mode=0o700, exist_ok=False)
    home = root / "home"
    config_root = home / ".config"
    agent_dir = config_root / "gitlab-agent"
    bridge_dir = config_root / "reasonfirst"
    temp_dir = root / "tmp"
    for path in (home, config_root, agent_dir, bridge_dir, temp_dir):
        path.mkdir(mode=0o700)
    env_file = agent_dir / ".env"
    bridge_file = bridge_dir / "bridge.yaml"
    settings = AgentSettings(
        config_file=env_file,
        gitlab_base_url="https://reasonfirst.invalid",
        api_token="", api_verify_ssl=True, api_trust_env=False,
        git_token="", git_username="oauth2", git_trust_env=False,
        allowed_projects={"disposable/project"}, require_write_allowlist=True,
        workspace_root=root / "workspaces", branch_prefix="chatgpt/",
        default_base_ref="main", allowed_executables={"python", "python3"},
        command_timeout_seconds=30, max_output_bytes=4096, max_file_bytes=65536,
        git_author_name=None, git_author_email=None, api_ca_bundle=None,
        default_backend="codex", codex_model="gpt-5.6-sol",
        codex_reasoning_effort="high", codex_execution_mode="interactive",
        codex_sandbox_mode="workspace-write", codex_approval_policy="on-request",
        codex_network_access=False, copilot_model=None, copilot_reasoning_effort=None,
        copilot_execution_mode="interactive", copilot_disable_builtin_mcps=True,
        copilot_allow_tools=(), copilot_deny_tools=("shell(git push)",),
    )
    # Field-to-environment serialization only; no environment reads or writes.
    values = {
        "GITLAB_BASE_URL": settings.gitlab_base_url,
        "GITLAB_TOKEN": "", "GITLAB_GIT_TOKEN": "", "GITLAB_GIT_PASSWORD": "",
        "GITLAB_GIT_USERNAME": settings.git_username,
        "GITLAB_VERIFY_SSL": "true", "GITLAB_TRUST_ENV": "false",
        "GITLAB_GIT_TRUST_ENV": "false", "GITLAB_CA_BUNDLE": "",
        "GITLAB_ALLOWED_PROJECTS": ",".join(sorted(settings.allowed_projects)),
        "GITLAB_REQUIRE_WRITE_ALLOWLIST": "true",
        "GITLAB_WORKSPACE_ROOT": str(settings.workspace_root),
        "GITLAB_BRANCH_PREFIX": settings.branch_prefix,
        "GITLAB_DEFAULT_BASE_REF": settings.default_base_ref,
        "GITLAB_ALLOWED_EXECUTABLES": ",".join(sorted(settings.allowed_executables)),
        "GITLAB_COMMAND_TIMEOUT_SECONDS": str(settings.command_timeout_seconds),
        "GITLAB_COMMAND_MAX_OUTPUT_BYTES": str(settings.max_output_bytes),
        "GITLAB_MAX_WRITE_FILE_BYTES": str(settings.max_file_bytes),
        "GITLAB_GIT_AUTHOR_NAME": "", "GITLAB_GIT_AUTHOR_EMAIL": "",
        "REASONFIRST_DEFAULT_BACKEND": settings.default_backend,
        "REASONFIRST_CODEX_MODEL": settings.codex_model,
        "REASONFIRST_CODEX_REASONING_EFFORT": settings.codex_reasoning_effort,
        "REASONFIRST_CODEX_EXECUTION_MODE": settings.codex_execution_mode,
        "REASONFIRST_CODEX_SANDBOX": settings.codex_sandbox_mode,
        "REASONFIRST_CODEX_APPROVAL_POLICY": settings.codex_approval_policy,
        "REASONFIRST_CODEX_NETWORK_ACCESS": "false",
        "REASONFIRST_COPILOT_MODEL": "", "REASONFIRST_COPILOT_REASONING_EFFORT": "",
        "REASONFIRST_COPILOT_EXECUTION_MODE": settings.copilot_execution_mode,
        "REASONFIRST_COPILOT_DISABLE_BUILTIN_MCPS": "true",
        "REASONFIRST_COPILOT_ALLOW_TOOLS": "",
        "REASONFIRST_COPILOT_DENY_TOOLS": ",".join(settings.copilot_deny_tools),
    }
    bridge_config = {
        "version": 4, "control": {},
        "defaults": {"target": "local", "codex_backend": "standalone-local"},
        "targets": {"local": {
            "type": "local", "name": "local", "codex_backend": "standalone-local",
            "network_access": False,
        }},
    }
    _write_private(env_file, "".join(f"{key}={values[key]}\n" for key in sorted(values)))
    # JSON is a strict YAML subset accepted by the existing Bridge loader.
    _write_private(bridge_file, json.dumps(bridge_config, sort_keys=True) + "\n")
    state_dir = root / "state"
    snapshot = capture_startup_state(
        settings, bridge_config, bridge_config_path=bridge_file, state_dir=state_dir,
    )
    environment = MappingProxyType({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(config_root),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "TMPDIR": str(temp_dir), "TMP": str(temp_dir), "TEMP": str(temp_dir),
        "GITLAB_AGENT_ENV_FILE": str(env_file),
        "RF_BRIDGE_CONFIG": str(bridge_file),
        "RF_CODEX_BRIDGE_STATE_DIR": str(state_dir),
        "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH": "false",
        "RF_GITLAB_AUTH_MODE": "git-only",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    return DisposableConfiguration(root, home, snapshot, environment)
