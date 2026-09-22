from __future__ import annotations

from dataclasses import dataclass, asdict
import os
from pathlib import Path
import re
from typing import Any

import yaml


class BridgeConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionTarget:
    type: str = "local"
    name: str = "local"
    host: str = ""
    repo: str = ""
    codex_backend: str = "global-config-local"
    remote_codex: str = "codex"
    ssh_connect_timeout: int = 8
    network_access: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def config_path() -> Path:
    return Path(os.getenv("RF_BRIDGE_CONFIG", "~/.config/reasonfirst/bridge.yaml")).expanduser().resolve()


def load_bridge_config() -> dict[str, Any]:
    """Load v4 config while remaining compatible with v3 state/config.

    v4 no longer requires the GitHub control section. Existing v3 config is
    accepted and migrated in memory so upgrades do not break active workspaces.
    """
    path = config_path()
    if not path.exists():
        return {
            "version": 4,
            "control": {},
            "defaults": {
                "target": "local",
                "codex_backend": "global-config-local",
            },
            "targets": {
                "local": {"type": "local", "codex_backend": "global-config-local"}
            },
        }
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise BridgeConfigError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeConfigError(f"Bridge config must be a YAML object: {path}")
    version = int(data.get("version", 3))
    if version not in {3, 4}:
        raise BridgeConfigError(f"Unsupported bridge config version in {path}")
    data["version"] = 4
    data.setdefault("control", {})  # optional legacy/audit config only
    data.setdefault("defaults", {})
    data.setdefault("targets", {})
    defaults = data["defaults"]
    if isinstance(defaults, dict):
        defaults.setdefault("target", "local")
        # Preserve an explicit v3 backend, but v4 defaults to a dedicated
        # app-server that inherits the user's global Codex config.
        defaults.setdefault("codex_backend", "global-config-local")
    targets = data["targets"]
    if isinstance(targets, dict):
        targets.setdefault("local", {"type": "local", "codex_backend": str(defaults.get("codex_backend") or "global-config-local")})
    return data


def _parse_ssh_shorthand(value: str) -> ExecutionTarget | None:
    match = re.fullmatch(r"(?P<host>[^:\s]+):(?P<repo>/[^\n\r]+)", value.strip())
    if not match:
        return None
    return ExecutionTarget(
        type="ssh",
        name=value.strip(),
        host=match.group("host"),
        repo=match.group("repo"),
        codex_backend="desktop-proxy",
    )


def resolve_configured_target(
    spec: Any = None,
    *,
    config: dict[str, Any] | None = None,
) -> ExecutionTarget:
    """Resolve only a user-owned named target.

    MCP/relay callers may select configured targets but may not introduce a new
    SSH host or repository trust destination inline.
    """
    cfg = config or load_bridge_config()
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    if spec in (None, ""):
        spec = str(defaults.get("target") or "local")
    if not isinstance(spec, str):
        raise BridgeConfigError(
            "execution target must be the name of a user-configured target"
        )
    if _parse_ssh_shorthand(spec) is not None:
        raise BridgeConfigError(
            "ad-hoc SSH targets are not allowed; define the target in bridge.yaml first"
        )
    targets = cfg.get("targets") if isinstance(cfg.get("targets"), dict) else {}
    if spec != "local" and spec not in targets:
        raise BridgeConfigError(
            f"Unknown execution target {spec!r}; define it in {config_path()} before use"
        )
    return resolve_target(spec, config=cfg)


def resolve_target(spec: Any = None, *, config: dict[str, Any] | None = None) -> ExecutionTarget:
    cfg = config or load_bridge_config()
    defaults = cfg.get("defaults") if isinstance(cfg.get("defaults"), dict) else {}
    targets = cfg.get("targets") if isinstance(cfg.get("targets"), dict) else {}

    if spec in (None, "", {}):
        spec = str(defaults.get("target") or "local")

    if isinstance(spec, str):
        shorthand = _parse_ssh_shorthand(spec)
        if shorthand is not None:
            return shorthand
        named = targets.get(spec)
        if named is None:
            if spec == "local":
                named = {"type": "local"}
            else:
                raise BridgeConfigError(
                    f"Unknown execution target {spec!r}. Define it in {config_path()} or pass an SSH target object."
                )
        if not isinstance(named, dict):
            raise BridgeConfigError(f"Target {spec!r} must be a YAML object")
        raw = dict(named)
        raw.setdefault("name", spec)
    elif isinstance(spec, dict):
        raw = dict(spec)
        raw.setdefault("name", str(raw.get("host") or raw.get("type") or "task-target"))
    else:
        raise BridgeConfigError("execution target must be a name, SSH shorthand, object, or omitted")

    kind = str(raw.get("type") or "local").strip().lower()
    if kind not in {"local", "ssh"}:
        raise BridgeConfigError(f"Unsupported execution target type: {kind!r}")
    backend_default = str(defaults.get("codex_backend") or "global-config-local")
    allowed = {
        "global-config-local",
        "desktop-proxy",
        "desktop-preferred",
        "desktop-required",
        "desktop-managed",
        "standalone-local",
        "remote-ssh",
    }
    if kind == "local":
        backend = str(raw.get("codex_backend") or backend_default)
        if backend not in allowed:
            raise BridgeConfigError(f"Unsupported local codex_backend: {backend!r}")
        return ExecutionTarget(
            type="local",
            name=str(raw.get("name") or "local"),
            codex_backend=backend,
            network_access=bool(raw.get("network_access", False)),
        )

    host = str(raw.get("host") or "").strip()
    repo = str(raw.get("repo") or raw.get("project_root") or "").strip()
    if not host or host.startswith("-"):
        raise BridgeConfigError("SSH target requires a valid host/SSH alias")
    if not repo.startswith("/"):
        raise BridgeConfigError("SSH target requires an absolute repo path")
    ssh_backend = str(raw.get("codex_backend") or "desktop-proxy").strip() or "desktop-proxy"
    if ssh_backend not in allowed:
        raise BridgeConfigError(f"Unsupported SSH codex_backend: {ssh_backend!r}")
    return ExecutionTarget(
        type="ssh",
        name=str(raw.get("name") or host),
        host=host,
        repo=repo,
        codex_backend=ssh_backend,
        remote_codex=str(raw.get("remote_codex") or "codex").strip() or "codex",
        ssh_connect_timeout=max(1, min(int(raw.get("ssh_connect_timeout") or 8), 30)),
        network_access=bool(raw.get("network_access", False)),
    )
