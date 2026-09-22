from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any

import yaml

from .config import AgentSettings

PROJECT_CONFIG_FILENAME = ".actualcoder.yaml"
PROJECT_CONFIG_VERSION = 1
KNOWN_BACKENDS = {"codex", "copilot", "codex-cli", "copilot-cli", "codex-desktop"}

PROJECT_CONFIG_MAX_BYTES = 64 * 1024
MAX_PREFERRED_AGENTS = 16
MAX_VALIDATION_COMMANDS = 32
MAX_VALIDATION_ARGV = 64
MAX_VALIDATION_ARG_BYTES = 4096
MAX_PROTECTED_PATHS = 128
MAX_PROTECTED_PATH_LENGTH = 512
MAX_INSTRUCTIONS = 32
MAX_INSTRUCTION_LENGTH = 2000
MAX_REQUIRED_EXECUTABLES = 64
MAX_EXECUTABLE_LENGTH = 128
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


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
class ProjectConfigResult:
    found: bool
    valid: bool
    source_ref: str
    source_path: str
    contract: dict[str, object]
    effective: dict[str, object]
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "valid": self.valid,
            "source": {
                "ref": self.source_ref,
                "path": self.source_path,
            },
            "contract": self.contract,
            "effective": self.effective,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _expect_mapping(value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a mapping")
        return {}
    return value


def _unknown_keys(
    data: dict[Any, Any],
    allowed: set[str],
    label: str,
    errors: list[str],
) -> None:
    unknown = [key for key in data if key not in allowed]
    for key in sorted(unknown, key=lambda item: repr(item)):
        errors.append(f"Unknown key {label}.{key!r}")


def _string_list(
    value: Any,
    label: str,
    errors: list[str],
    *,
    max_items: int,
    max_length: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    if len(value) > max_items:
        errors.append(f"{label} must contain at most {max_items} items")

    out: list[str] = []
    for index, item in enumerate(value[:max_items]):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")
            continue
        normalized = item.strip()
        if len(normalized) > max_length:
            errors.append(
                f"{label}[{index}] must be <= {max_length} characters"
            )
            continue
        if "\x00" in normalized:
            errors.append(f"{label}[{index}] must not contain NUL")
            continue
        out.append(normalized)
    return out


def _safe_git_ref(value: str) -> bool:
    if not _SAFE_REF_RE.fullmatch(value):
        return False
    if value.startswith(("-", "/", ".")) or value.endswith(("/", ".", ".lock")):
        return False
    if any(token in value for token in ("..", "//", "@{")):
        return False
    return True


def _safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized in {".", "./"} or ":" in normalized:
        return False
    path = PurePosixPath(normalized)
    if path.is_absolute():
        return False
    if any(part in {"..", "", "."} for part in path.parts):
        return False
    return len(normalized) <= MAX_PROTECTED_PATH_LENGTH


def parse_project_config(
    text: str | None,
    *,
    settings: AgentSettings,
    source_ref: str,
    source_path: str = PROJECT_CONFIG_FILENAME,
) -> ProjectConfigResult:
    """Parse and policy-check a repository-owned ActualCoder contract."""

    errors: list[str] = []
    warnings: list[str] = []

    if text is None:
        effective = {
            "base_branch": settings.default_base_ref,
            "preferred_agents": [],
            "validation_commands": [],
            "protected_paths": [],
            "instructions": [],
            "required_executables": [],
            "mr": {
                "target_branch": settings.default_base_ref,
                "title_prefix": "",
            },
        }
        warnings.append(
            f"{source_path} not found; user/default configuration will be used"
        )
        return ProjectConfigResult(
            found=False,
            valid=True,
            source_ref=source_ref,
            source_path=source_path,
            contract={},
            effective=effective,
            errors=errors,
            warnings=warnings,
        )

    text_bytes = len(text.encode("utf-8", errors="replace"))
    if text_bytes > PROJECT_CONFIG_MAX_BYTES:
        errors.append(
            f"{source_path} is too large: {text_bytes} bytes; "
            f"maximum is {PROJECT_CONFIG_MAX_BYTES}"
        )
        return ProjectConfigResult(
            found=True,
            valid=False,
            source_ref=source_ref,
            source_path=source_path,
            contract={},
            effective={},
            errors=errors,
            warnings=warnings,
        )

    try:
        loaded = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        errors.append(f"Invalid YAML: {exc}")
        loaded = {}

    root = _expect_mapping(loaded, "root", errors)
    _unknown_keys(
        root,
        {
            "version",
            "project",
            "agents",
            "validation",
            "protected_paths",
            "instructions",
            "executables",
            "mr",
        },
        "root",
        errors,
    )

    version = root.get("version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != PROJECT_CONFIG_VERSION
    ):
        errors.append(
            f"version must be integer {PROJECT_CONFIG_VERSION}; got {version!r}"
        )

    project = _expect_mapping(root.get("project"), "project", errors)
    _unknown_keys(project, {"base_branch"}, "project", errors)
    base_branch = project.get("base_branch", settings.default_base_ref)
    if not isinstance(base_branch, str) or not base_branch.strip():
        errors.append("project.base_branch must be a non-empty string")
        base_branch = settings.default_base_ref
    else:
        base_branch = base_branch.strip()
        if not _safe_git_ref(base_branch):
            errors.append(
                "project.base_branch must be a conservative Git ref using only "
                "ASCII letters/digits and . _ / - without traversal/special ref syntax"
            )
            base_branch = settings.default_base_ref

    agents = _expect_mapping(root.get("agents"), "agents", errors)
    _unknown_keys(agents, {"preferred"}, "agents", errors)
    preferred_agents = _string_list(
        agents.get("preferred"),
        "agents.preferred",
        errors,
        max_items=MAX_PREFERRED_AGENTS,
        max_length=64,
    )
    for agent in preferred_agents:
        if agent not in KNOWN_BACKENDS:
            warnings.append(
                f"agents.preferred contains unsupported backend {agent!r}; "
                "current supported backends are codex-cli, copilot-cli and codex-desktop (codex/copilot remain aliases)"
            )

    validation = _expect_mapping(root.get("validation"), "validation", errors)
    _unknown_keys(validation, {"commands"}, "validation", errors)
    raw_commands = validation.get("commands", [])
    commands: list[dict[str, object]] = []
    if raw_commands is None:
        raw_commands = []
    if not isinstance(raw_commands, list):
        errors.append("validation.commands must be a list")
        raw_commands = []
    if len(raw_commands) > MAX_VALIDATION_COMMANDS:
        errors.append(
            f"validation.commands must contain at most {MAX_VALIDATION_COMMANDS} items"
        )

    for index, raw_command in enumerate(raw_commands[:MAX_VALIDATION_COMMANDS]):
        label = f"validation.commands[{index}]"
        command = _expect_mapping(raw_command, label, errors)
        _unknown_keys(
            command,
            {"name", "argv", "required", "timeout_seconds"},
            label,
            errors,
        )

        name = command.get("name", f"command-{index + 1}")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name must be a non-empty string")
            name = f"command-{index + 1}"
        else:
            name = name.strip()
            if len(name) > 200:
                errors.append(f"{label}.name must be <= 200 characters")
                name = name[:200]

        argv = command.get("argv")
        if not isinstance(argv, list) or not argv:
            errors.append(f"{label}.argv must be a non-empty list of strings")
            argv = []
        if len(argv) > MAX_VALIDATION_ARGV:
            errors.append(
                f"{label}.argv must contain at most {MAX_VALIDATION_ARGV} items"
            )
        normalized_argv: list[str] = []
        for arg_index, arg in enumerate(argv[:MAX_VALIDATION_ARGV]):
            if not isinstance(arg, str) or not arg:
                errors.append(f"{label}.argv[{arg_index}] must be a non-empty string")
                continue
            if "\x00" in arg:
                errors.append(f"{label}.argv[{arg_index}] must not contain NUL")
                continue
            if len(arg.encode("utf-8", errors="replace")) > MAX_VALIDATION_ARG_BYTES:
                errors.append(
                    f"{label}.argv[{arg_index}] must be <= "
                    f"{MAX_VALIDATION_ARG_BYTES} UTF-8 bytes"
                )
                continue
            normalized_argv.append(arg)

        if normalized_argv:
            executable = normalized_argv[0]
            if "/" in executable or "\\" in executable:
                errors.append(
                    f"{label}.argv[0] must be a bare executable name, not a path"
                )
            elif executable not in settings.allowed_executables:
                errors.append(
                    f"{label} requires executable {executable!r}, which is not approved "
                    "by GITLAB_ALLOWED_EXECUTABLES"
                )

        required = command.get("required", True)
        if not isinstance(required, bool):
            errors.append(f"{label}.required must be true/false")
            required = True

        timeout = command.get("timeout_seconds", settings.command_timeout_seconds)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            errors.append(f"{label}.timeout_seconds must be a positive integer")
            timeout = settings.command_timeout_seconds
        effective_timeout = min(timeout, settings.command_timeout_seconds)
        if timeout > settings.command_timeout_seconds:
            warnings.append(
                f"{label}.timeout_seconds={timeout} exceeds the user maximum "
                f"{settings.command_timeout_seconds}; it will be capped"
            )

        commands.append(
            {
                "name": name,
                "argv": normalized_argv,
                "required": required,
                "timeout_seconds": effective_timeout,
            }
        )

    protected_paths = _string_list(
        root.get("protected_paths"),
        "protected_paths",
        errors,
        max_items=MAX_PROTECTED_PATHS,
        max_length=MAX_PROTECTED_PATH_LENGTH,
    )
    for path in protected_paths:
        if not _safe_relative_path(path):
            errors.append(
                f"protected_paths entry {path!r} must be a safe repository-relative path"
            )

    instructions = _string_list(
        root.get("instructions"),
        "instructions",
        errors,
        max_items=MAX_INSTRUCTIONS,
        max_length=MAX_INSTRUCTION_LENGTH,
    )

    executables = _expect_mapping(root.get("executables"), "executables", errors)
    _unknown_keys(executables, {"required"}, "executables", errors)
    required_executables = _string_list(
        executables.get("required"),
        "executables.required",
        errors,
        max_items=MAX_REQUIRED_EXECUTABLES,
        max_length=MAX_EXECUTABLE_LENGTH,
    )
    for executable in required_executables:
        if "/" in executable or "\\" in executable:
            errors.append(
                f"executables.required entry {executable!r} must be a bare executable"
            )
        elif executable not in settings.allowed_executables:
            errors.append(
                f"executables.required entry {executable!r} is not approved by "
                "GITLAB_ALLOWED_EXECUTABLES"
            )

    mr = _expect_mapping(root.get("mr"), "mr", errors)
    _unknown_keys(mr, {"target_branch", "title_prefix"}, "mr", errors)
    target_branch = mr.get("target_branch", base_branch)
    if not isinstance(target_branch, str) or not target_branch.strip():
        errors.append("mr.target_branch must be a non-empty string")
        target_branch = base_branch
    else:
        target_branch = target_branch.strip()
        if not _safe_git_ref(target_branch):
            errors.append(
                "mr.target_branch must be a conservative Git ref using only "
                "ASCII letters/digits and . _ / - without traversal/special ref syntax"
            )
            target_branch = base_branch

    title_prefix = mr.get("title_prefix", "")
    if not isinstance(title_prefix, str):
        errors.append("mr.title_prefix must be a string")
        title_prefix = ""
    elif len(title_prefix) > 100:
        errors.append("mr.title_prefix must be <= 100 characters")

    contract: dict[str, object] = {
        "version": version,
        "project": {"base_branch": base_branch},
        "agents": {"preferred": preferred_agents},
        "validation": {"commands": commands},
        "protected_paths": protected_paths,
        "instructions": instructions,
        "executables": {"required": required_executables},
        "mr": {
            "target_branch": target_branch,
            "title_prefix": title_prefix,
        },
    }

    effective = {
        "base_branch": base_branch,
        "preferred_agents": preferred_agents,
        "validation_commands": commands,
        "protected_paths": protected_paths,
        "instructions": instructions,
        "required_executables": required_executables,
        "mr": {
            "target_branch": target_branch,
            "title_prefix": title_prefix,
        },
    }

    return ProjectConfigResult(
        found=True,
        valid=not errors,
        source_ref=source_ref,
        source_path=source_path,
        contract=contract,
        effective=effective,
        errors=errors,
        warnings=warnings,
    )
