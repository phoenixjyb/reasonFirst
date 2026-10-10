"""Explicit, immutable parent policy for one managed Bridge service.

Capture accepts already-loaded objects and absolute references. It does not load
configuration, read referenced files, or change the host environment. The public
projection covers only the named fields below; credentials, their presence and
hashes, Git usernames and author metadata are excluded. A matching projection is
not proof of equal private settings, effective child/provider configuration,
runtime integrity, recovered state, or authorization to activate a service.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
from typing import Any

from ..bridge_http import HTTPLaunch
from ..bridge_preview.bridge_config import ExecutionTarget, _parse_ssh_shorthand
from ..config import AgentSettings
from ..worker_policy import WorkerPolicy
from .startup_state import ManagedStartupState, capture_startup_state


CONFIGURATION_PROJECTION = "reasonfirst-selected-service-policy-v1"
MAX_PROJECTION_BYTES = 64 * 1024
MAX_COLLECTION_ITEMS = 512
MAX_STRING_CHARS = 4096
_MAX_PRIVATE_CHARS = 16 * 1024
_MAX_INTEGER = 2**31 - 1
_PATH_TYPE = type(Path())
_CODES = frozenset({
    "invalid_configuration_objects", "invalid_configuration_reference",
    "invalid_configuration_policy", "invalid_configuration_launch",
    "invalid_configuration_projection", "configuration_projection_too_large",
})
_BACKENDS = frozenset({
    "global-config-local", "desktop-proxy", "desktop-preferred",
    "desktop-required", "desktop-managed", "standalone-local", "remote-ssh",
})
_SETTING_FIELDS = frozenset({
    "config_file", "gitlab_base_url", "api_token", "api_verify_ssl",
    "api_trust_env", "git_token", "git_username", "git_trust_env",
    "allowed_projects", "require_write_allowlist", "workspace_root",
    "branch_prefix", "default_base_ref", "allowed_executables",
    "command_timeout_seconds", "max_output_bytes", "max_file_bytes",
    "git_author_name", "git_author_email", "api_ca_bundle", "default_backend",
    "codex_model", "codex_reasoning_effort", "codex_execution_mode",
    "codex_sandbox_mode", "codex_approval_policy", "codex_network_access",
    "copilot_model", "copilot_reasoning_effort", "copilot_execution_mode",
    "copilot_disable_builtin_mcps", "copilot_allow_tools", "copilot_deny_tools",
})


class ServiceConfigurationError(ValueError):
    """A finite error code with no configuration values or exception details."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _CODES else "invalid_configuration_objects"
        super().__init__(self.code)


def _fail(code: str = "invalid_configuration_policy") -> None:
    raise ServiceConfigurationError(code) from None


def _text(value: Any, *, optional: bool = False, empty: bool = False,
          limit: int = MAX_STRING_CHARS) -> None:
    if value is None and optional:
        return
    if (type(value) is not str or len(value) > limit or (not value and not empty)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail()


def _choice(value: Any, choices: set[str] | frozenset[str], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    _text(value)
    if value not in choices:
        _fail()


def _boolean(value: Any) -> None:
    if type(value) is not bool:
        _fail()


def _integer(value: Any, minimum: int = 1, maximum: int = _MAX_INTEGER) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _strings(value: Any, *, ordered: bool = False) -> None:
    accepted = (tuple, list) if ordered else (tuple, list, set, frozenset)
    if type(value) not in accepted or len(value) > MAX_COLLECTION_ITEMS:
        _fail()
    for item in value:
        _text(item)


def _reference(value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if type(value) is not _PATH_TYPE or not value.is_absolute():
        _fail("invalid_configuration_reference")
    text = str(value)
    if (len(text) > MAX_STRING_CHARS
            or any(ord(char) < 32 or ord(char) == 127 for char in text)):
        _fail("invalid_configuration_reference")


def _settings(settings: AgentSettings) -> None:
    if (type(settings) is not AgentSettings
            or {item.name for item in fields(AgentSettings)} != _SETTING_FIELDS):
        _fail("invalid_configuration_objects")
    _reference(settings.config_file)
    _reference(settings.workspace_root)
    _reference(settings.api_ca_bundle, optional=True)
    for name in ("api_token", "git_token", "git_username"):
        _text(getattr(settings, name), empty=True, limit=_MAX_PRIVATE_CHARS)
    for name in ("git_author_name", "git_author_email"):
        _text(getattr(settings, name), optional=True, empty=True, limit=_MAX_PRIVATE_CHARS)
    for name in ("gitlab_base_url", "branch_prefix", "default_base_ref", "codex_model"):
        _text(getattr(settings, name))
    if " " in settings.branch_prefix:
        _fail()
    for name in ("api_verify_ssl", "api_trust_env", "git_trust_env",
                 "require_write_allowlist", "codex_network_access",
                 "copilot_disable_builtin_mcps"):
        _boolean(getattr(settings, name))
    for name in ("command_timeout_seconds", "max_output_bytes", "max_file_bytes"):
        _integer(getattr(settings, name))
    for name in ("allowed_projects", "allowed_executables"):
        _strings(getattr(settings, name))
    for name in ("copilot_allow_tools", "copilot_deny_tools"):
        _strings(getattr(settings, name), ordered=True)
    _choice(settings.default_backend,
            {"auto", "codex", "codex-cli", "copilot", "copilot-cli", "codex-desktop"})
    _choice(settings.codex_reasoning_effort, {"minimal", "low", "medium", "high", "xhigh", "max"})
    _choice(settings.codex_execution_mode, {"interactive", "exec"})
    _choice(settings.codex_sandbox_mode, {"read-only", "workspace-write"})
    _choice(settings.codex_approval_policy, {"on-request", "never"})
    _text(settings.copilot_model, optional=True)
    _choice(settings.copilot_reasoning_effort, {"low", "medium", "high", "xhigh"}, optional=True)
    _choice(settings.copilot_execution_mode, {"interactive", "programmatic"})


def _bridge_config(config: dict[str, Any]) -> None:
    if type(config) is not dict:
        _fail("invalid_configuration_objects")
    if type(config.get("version")) is not int or config["version"] != 4:
        _fail()
    defaults, targets = config.get("defaults"), config.get("targets")
    if type(defaults) is not dict or type(targets) is not dict or len(targets) > MAX_COLLECTION_ITEMS:
        _fail()
    for name in ("target", "codex_backend"):
        if name in defaults:
            _text(defaults[name], optional=True, empty=True)
    # The ordinary resolver includes ambient config_path() in its unknown-name
    # error. Reject that case here so even failed explicit captures stay local.
    default_target = defaults.get("target") or "local"
    if default_target != "local" and default_target not in targets:
        _fail()
    for name, raw in targets.items():
        _text(name)
        # Configured targets must remain names. The general resolver interprets
        # SSH shorthand before consulting the mapping, even for nondefault keys.
        if _parse_ssh_shorthand(name) is not None:
            _fail()
        if type(raw) is not dict:
            _fail()
        for field in ("type", "name", "host", "repo", "project_root", "codex_backend", "remote_codex"):
            if field in raw:
                _text(raw[field], optional=True, empty=True)
        if "network_access" in raw:
            _boolean(raw["network_access"])
        if "ssh_connect_timeout" in raw:
            _integer(raw["ssh_connect_timeout"], 1, 30)
        validation = raw.get("validation")
        if validation is not None:
            if type(validation) is not dict:
                _fail()
            for field in ("engine", "image"):
                if field in validation:
                    _text(validation[field], optional=True, empty=True)
            if "network_access" in validation:
                _boolean(validation["network_access"])
            if "allowed_executables" in validation:
                if type(validation["allowed_executables"]) is not list:
                    _fail()
                _strings(validation["allowed_executables"], ordered=True)


def _target(target: ExecutionTarget) -> None:
    if type(target) is not ExecutionTarget:
        _fail()
    _choice(target.type, {"local", "ssh"})
    _choice(target.codex_backend, _BACKENDS)
    for name in ("name", "remote_codex"):
        _text(getattr(target, name))
    for name in ("host", "repo", "validation_engine", "validation_image"):
        _text(getattr(target, name), empty=True)
    _integer(target.ssh_connect_timeout, 1, 30)
    _boolean(target.network_access)
    _boolean(target.validation_network_access)
    _strings(target.validation_allowed_executables, ordered=True)


def _worker_projection(policy: WorkerPolicy) -> dict[str, Any]:
    return {
        "backend": policy.backend, "model": policy.model,
        "reasoning_effort": policy.reasoning_effort,
        "execution_mode": policy.execution_mode, "sandbox_mode": policy.sandbox_mode,
        "approval_policy": policy.approval_policy, "network_access": policy.network_access,
        "disable_builtin_mcps": policy.disable_builtin_mcps,
        "allow_tools": list(policy.allow_tools), "deny_tools": list(policy.deny_tools),
    }


def _target_projection(target: ExecutionTarget) -> dict[str, Any]:
    return {
        "type": target.type, "name": target.name, "host": target.host,
        "repo": target.repo, "codex_backend": target.codex_backend,
        "remote_codex": target.remote_codex, "ssh_connect_timeout": target.ssh_connect_timeout,
        "network_access": target.network_access,
        "validation_engine": target.validation_engine, "validation_image": target.validation_image,
        "validation_allowed_executables": list(target.validation_allowed_executables),
        "validation_network_access": target.validation_network_access,
    }


def _encode(projection: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except Exception:
        _fail("invalid_configuration_projection")
    if len(encoded) > MAX_PROJECTION_BYTES:
        _fail("configuration_projection_too_large")
    return encoded


@dataclass(frozen=True, repr=False, eq=False, init=False)
class ManagedServiceConfiguration:
    """Factory-created immutable policy; service binding uses object identity."""

    _selected: ManagedStartupState
    approval_timeout_seconds: int
    gitlab_auth_mode: str

    def __init__(self, *args, **kwargs):
        _fail("invalid_configuration_objects")

    @property
    def settings(self) -> AgentSettings:
        return self._selected.settings

    @property
    def state_dir(self) -> Path:
        return self._selected.state_dir

    @property
    def bridge_config_path(self) -> Path:
        return self._selected.bridge_config_path

    @property
    def codex_policy(self) -> WorkerPolicy:
        return self._selected.codex_policy

    @property
    def copilot_policy(self) -> WorkerPolicy:
        return self._selected.copilot_policy

    def bridge_configuration(self) -> dict[str, Any]:
        return self._selected.bridge_configuration()

    def configured_target(self, spec: Any = None) -> ExecutionTarget:
        if spec is not None:
            _text(spec, empty=True)
        try:
            return self._selected.configured_target(spec)
        except Exception:
            _fail()

    def projection(self, launch: HTTPLaunch) -> dict[str, Any]:
        if type(launch) is not HTTPLaunch:
            _fail("invalid_configuration_launch")
        try:
            if any(type(getattr(launch, name)) is not str
                   for name in ("host", "path", "mode", "control_policy")):
                _fail("invalid_configuration_launch")
            launch.validate({})
        except Exception:
            _fail("invalid_configuration_launch")
        settings, selected = self.settings, self._selected
        result = {
            "protocol": CONFIGURATION_PROJECTION,
            "scope": "selected-parent-service-policy",
            "transport": {
                "transport": "streamable-http", "host": launch.host, "port": launch.port,
                "path": launch.path, "mode": launch.mode, "control_policy": launch.control_policy,
                "remote_push_exposed": False,
            },
            "references": {
                "agent_config_file": str(settings.config_file),
                "bridge_config_file": str(self.bridge_config_path),
                "bridge_state_dir": str(self.state_dir),
                "bridge_state_file": str(self.state_dir / "state.json"),
                "workspace_root": str(settings.workspace_root),
                "api_ca_bundle": str(settings.api_ca_bundle) if settings.api_ca_bundle is not None else None,
            },
            "agent_policy": {
                "gitlab_base_url": settings.gitlab_base_url,
                "api_verify_ssl": settings.api_verify_ssl, "api_trust_env": settings.api_trust_env,
                "git_trust_env": settings.git_trust_env,
                "allowed_projects": sorted(settings.allowed_projects),
                "require_write_allowlist": settings.require_write_allowlist,
                "branch_prefix": settings.branch_prefix, "default_base_ref": settings.default_base_ref,
                "allowed_executables": sorted(settings.allowed_executables),
                "command_timeout_seconds": settings.command_timeout_seconds,
                "max_output_bytes": settings.max_output_bytes, "max_file_bytes": settings.max_file_bytes,
                "default_backend": settings.default_backend,
            },
            "bridge_policy": {
                "version": 4, "default_target": selected.default_target,
                "default_codex_backend": selected.default_codex_backend,
                "targets": [{"configured_name": name, **_target_projection(target)}
                            for name, target in selected.targets],
            },
            "worker_requests": {
                "codex": _worker_projection(self.codex_policy),
                "copilot": _worker_projection(self.copilot_policy),
            },
            "service_policy": {
                "approval_timeout_seconds": self.approval_timeout_seconds,
                "gitlab_auth_mode": self.gitlab_auth_mode,
            },
        }
        _encode(result)
        return result

    def configuration_digest(self, launch: HTTPLaunch) -> str:
        return hashlib.sha256(_encode(self.projection(launch))).hexdigest()


def capture_service_configuration(
    settings: AgentSettings, bridge_config: dict[str, Any], *,
    bridge_config_path: Path, state_dir: Path,
    approval_timeout_seconds: int = 300, gitlab_auth_mode: str = "auto",
) -> ManagedServiceConfiguration:
    """Detach already-resolved inputs without loading configuration or user files."""
    _settings(settings)
    _bridge_config(bridge_config)
    _reference(bridge_config_path)
    _reference(state_dir)
    _integer(approval_timeout_seconds, 30, 1800)
    _choice(gitlab_auth_mode, {"auto", "api", "git-only"})
    try:
        selected = capture_startup_state(settings, bridge_config,
                                         bridge_config_path=bridge_config_path, state_dir=state_dir)
        for path in (selected.settings.config_file, selected.settings.workspace_root,
                     selected.settings.api_ca_bundle, selected.bridge_config_path, selected.state_dir):
            _reference(path, optional=True)
        _choice(selected.default_codex_backend, _BACKENDS)
        for _, target in selected.targets:
            _target(target)
    except ServiceConfigurationError:
        raise
    except Exception:
        _fail()
    captured = object.__new__(ManagedServiceConfiguration)
    object.__setattr__(captured, "_selected", selected)
    object.__setattr__(captured, "approval_timeout_seconds", approval_timeout_seconds)
    object.__setattr__(captured, "gitlab_auth_mode", gitlab_auth_mode)
    return captured
