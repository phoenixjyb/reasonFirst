from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

import yaml


TARGET_CONFIG_VERSION = 1
DEFAULT_TARGETS_FILE = "~/.config/reasonfirst/targets.yaml"
_SAFE_TARGET_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SAFE_SSH_HOST = re.compile(r"^[A-Za-z0-9._@-]{1,255}$")


class RemoteTargetError(RuntimeError):
    pass


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.YAMLError("YAML mapping keys must be hashable") from exc
        if duplicate:
            raise yaml.YAMLError(f"Duplicate YAML mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class RemoteTarget:
    """A user-owned, named SSH execution trust destination."""

    name: str
    host: str
    repo: str
    allowed_projects: tuple[str, ...]
    ssh_connect_timeout: int = 8

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": "ssh",
            "host": self.host,
            "repo": self.repo,
            "allowed_projects": list(self.allowed_projects),
            "ssh_connect_timeout": self.ssh_connect_timeout,
        }

    def assert_project_allowed(self, project: str) -> None:
        normalized = str(project or "").strip()
        if normalized not in self.allowed_projects:
            raise RemoteTargetError(
                f"Project {normalized!r} is not allowlisted for SSH target {self.name!r}"
            )


def targets_file_path() -> Path:
    raw = os.getenv("REASONFIRST_TARGETS_FILE", DEFAULT_TARGETS_FILE)
    return Path(raw).expanduser()


def _expect_mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RemoteTargetError(f"{label} must be a mapping")
    return value


def _safe_repo_path(value: str) -> bool:
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    if not path.is_absolute():
        return False
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        return False
    return len(value) <= 1024


def _parse_target(name: str, raw: Any) -> RemoteTarget:
    if not _SAFE_TARGET_NAME.fullmatch(name):
        raise RemoteTargetError(
            f"Invalid target name {name!r}; use letters, digits, dot, underscore or dash"
        )
    data = _expect_mapping(raw, f"targets.{name}")
    unknown = sorted(
        key
        for key in data
        if key
        not in {
            "type",
            "host",
            "repo",
            "allowed_projects",
            "ssh_connect_timeout",
        }
    )
    if unknown:
        raise RemoteTargetError(
            f"Unknown key(s) in targets.{name}: {', '.join(repr(item) for item in unknown)}"
        )

    kind = str(data.get("type") or "ssh").strip().lower()
    if kind != "ssh":
        raise RemoteTargetError(f"targets.{name}.type must be 'ssh'")

    host = str(data.get("host") or "").strip()
    if not _SAFE_SSH_HOST.fullmatch(host) or host.startswith("-"):
        raise RemoteTargetError(
            f"targets.{name}.host must be a conservative SSH host/alias"
        )

    repo = str(data.get("repo") or "").strip()
    if not _safe_repo_path(repo):
        raise RemoteTargetError(
            f"targets.{name}.repo must be a safe absolute POSIX path"
        )

    projects = data.get("allowed_projects")
    if not isinstance(projects, list) or not projects:
        raise RemoteTargetError(
            f"targets.{name}.allowed_projects must be a non-empty list"
        )
    normalized_projects: list[str] = []
    for index, item in enumerate(projects):
        if not isinstance(item, str) or not item.strip() or "/" not in item.strip():
            raise RemoteTargetError(
                f"targets.{name}.allowed_projects[{index}] must be group/project"
            )
        value = item.strip()
        if value not in normalized_projects:
            normalized_projects.append(value)

    timeout = data.get("ssh_connect_timeout", 8)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 30:
        raise RemoteTargetError(
            f"targets.{name}.ssh_connect_timeout must be an integer from 1 to 30"
        )

    return RemoteTarget(
        name=name,
        host=host,
        repo=repo,
        allowed_projects=tuple(normalized_projects),
        ssh_connect_timeout=timeout,
    )


def load_remote_targets(path: Path | None = None) -> dict[str, RemoteTarget]:
    """Load the user-owned SSH target registry.

    Repository-owned files and MCP/tool arguments do not grant new SSH targets.
    """

    config = (path or targets_file_path()).expanduser()
    if not config.exists():
        return {}
    if not config.is_file():
        raise RemoteTargetError(f"SSH target config is not a file: {config}")

    if os.name != "nt":
        mode = stat.S_IMODE(config.stat().st_mode)
        if mode & 0o022:
            raise RemoteTargetError(
                f"SSH target config is writable by group/others ({oct(mode)}): {config}"
            )

    try:
        loaded = yaml.load(config.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise RemoteTargetError(f"Could not parse SSH target config {config}: {exc}") from exc

    root = _expect_mapping(loaded, "root")
    unknown = sorted(key for key in root if key not in {"version", "targets"})
    if unknown:
        raise RemoteTargetError(
            "Unknown top-level SSH target config key(s): "
            + ", ".join(repr(item) for item in unknown)
        )

    version = root.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != TARGET_CONFIG_VERSION
    ):
        raise RemoteTargetError(
            f"version must be integer {TARGET_CONFIG_VERSION}; got {version!r}"
        )

    raw_targets = _expect_mapping(root.get("targets"), "targets")
    result: dict[str, RemoteTarget] = {}
    for raw_name, raw_target in raw_targets.items():
        if not isinstance(raw_name, str):
            raise RemoteTargetError("SSH target names must be strings")
        target = _parse_target(raw_name, raw_target)
        result[target.name] = target
    return result


def resolve_remote_target(
    name: str,
    *,
    project: str,
    targets: dict[str, RemoteTarget] | None = None,
) -> RemoteTarget:
    """Resolve only a named, locally configured target.

    Callers cannot provide an inline host/repository object. This makes the local
    target registry the trust grant rather than an MCP/request payload.
    """

    requested = str(name or "").strip()
    if not requested:
        raise RemoteTargetError("A configured SSH target name is required")
    registry = targets if targets is not None else load_remote_targets()
    target = registry.get(requested)
    if target is None:
        raise RemoteTargetError(
            f"Unknown SSH target {requested!r}; add it to {targets_file_path()} locally"
        )
    target.assert_project_allowed(project)
    return target


def safe_target_summary(
    targets: dict[str, RemoteTarget] | None = None,
) -> list[dict[str, object]]:
    registry = targets if targets is not None else load_remote_targets()
    return [registry[name].to_dict() for name in sorted(registry)]
