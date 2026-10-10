"""Explicit, workspace-scoped Python bindings; never install or rewrite PATH.

Bindings are local operator policy, not repository configuration. A fingerprint
is change detection, not a sandbox or protection against hostile same-user code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .workspace import WorkspaceManager

PYTHON_COMMANDS = ("python", "python3")
MAX_RECORD = 32768
MAX_EXECUTABLE = 256 * 1024 * 1024
PROBE_TIMEOUT = 10
_PROBE = (
    "import json,sys;print(json.dumps({'executable':sys.executable,"
    "'version':list(sys.version_info[:3]),'implementation':sys.implementation.name},"
    "ensure_ascii=True))"
)


class PythonBindingError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PythonBindingError("Duplicate field in Python runtime evidence")
        result[key] = value
    return result


def _safe_probe_env(*, child_context=None) -> dict[str, str]:
    # Keep OS/DLL lookup settings; remove credentials and interpreter overrides.
    retained = child_context.environment_copy() if child_context is not None else os.environ
    return {
        key: value for key, value in retained.items()
        if not any(part in key.upper() for part in (
            "TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY",
        ))
        and not key.upper().startswith(("GIT_", "GITLAB_", "PYTHON", "UV_", "CONDA"))
        and key.upper() not in {"SSH_AUTH_SOCK", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}
    }


def _capture_probe(argv: list[str], *, child_context=None) -> str:
    # A temporary cwd avoids executing repository startup files. Temporary files
    # keep unexpected probe output out of memory and out of diagnostic messages.
    with tempfile.TemporaryDirectory(prefix="rf-python-probe-") as directory:
        options = {"child_context": child_context} if child_context is not None else {}
        env = _safe_probe_env(**options)
        env.update(HOME=directory, USERPROFILE=directory)
        if child_context is not None:
            if not Path(argv[0]).is_absolute():
                raise PythonBindingError("Select an absolute executable for the managed Python probe")
            selected = child_context.resolve_executable(argv[0])
            argv = [selected, *argv[1:]]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                proc = subprocess.run(
                    argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, timeout=PROBE_TIMEOUT, check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PythonBindingError("Python discovery/probe could not complete; no binding saved") from exc
            if proc.returncode != 0:
                raise PythonBindingError("Python discovery/probe failed; no installation or fallback attempted")
            stdout.seek(0)
            raw = stdout.read(MAX_RECORD + 1)
            if len(raw) > MAX_RECORD:
                raise PythonBindingError("Python probe output exceeded its limit")
            try:
                return raw.decode("utf-8").strip()
            except UnicodeError as exc:
                raise PythonBindingError("Python probe did not return valid UTF-8") from exc


def _invocation_path(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise PythonBindingError("Specify a local absolute Python executable path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise PythonBindingError("Python executable must be an absolute path, not a command or relative path")
    # Do NOT resolve symlinks: a venv's python symlink must retain its invocation
    # path so normal execution still selects that venv rather than base Python.
    path = Path(os.path.abspath(path))
    if not re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", path.name, re.IGNORECASE):
        raise PythonBindingError("Select python/python3/python.exe itself, not a launcher, script, or shell")
    return path


def _matches_invocation_path(reported: str, selected: Path) -> bool:
    # macOS framework launchers canonicalize the parent directory, e.g. /var
    # versus /private/var, but preserve the final executable symlink. Comparing
    # fully resolved files (or samefile) could accept a different venv sharing
    # the base binary. Keep the leaf name AND canonical invocation directory.
    if not reported or len(reported) > 4096 or any(ord(c) < 32 for c in reported):
        return False
    actual = Path(reported)
    if not actual.is_absolute():
        return False
    try:
        actual = actual.parent.resolve(strict=True) / actual.name
        expected = selected.parent.resolve(strict=True) / selected.name
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(str(actual)) == os.path.normcase(str(expected))


def discover_python(version: str, *, child_context=None) -> str:
    if not re.fullmatch(r"3\.\d{1,2}(?:\.\d{1,3})?", version):
        raise PythonBindingError("--python requires an explicit version such as 3.12")
    uv = child_context.resolve_executable("uv") if child_context is not None else shutil.which("uv")
    if not uv:
        raise PythonBindingError("uv is unavailable; select an existing interpreter with --executable")
    # Explicit system discovery only. Never choose the RF tool venv or download.
    options = {"child_context": child_context} if child_context is not None else {}
    found = _capture_probe([
        uv, "--no-config", "--offline", "python", "find", "--system",
        "--no-project", "--no-python-downloads", version,
    ], **options)
    return str(_invocation_path(found))


def _hash_file(path: Path, limit: int) -> str:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise PythonBindingError("Python runtime file is not a bounded regular file")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > limit:
                    raise PythonBindingError("Python runtime file exceeded its size limit")
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise PythonBindingError("The selected Python runtime file is missing or inaccessible") from exc


def executable_fingerprint(executable: str) -> dict[str, Any]:
    path = _invocation_path(executable)
    # pyvenv.cfg determines venv selection; its change also requires re-approval.
    cfg = path.parent.parent / "pyvenv.cfg"
    return {
        "invocation_directory": str(path.parent.resolve(strict=True)),
        "resolved_file": str(path.resolve(strict=True)),
        "sha256": _hash_file(path, MAX_EXECUTABLE),
        "venv_config_sha256": _hash_file(cfg, MAX_RECORD) if cfg.exists() else None,
    }


def probe_python(executable: str, expected_version: str | None = None, *,
                 child_context=None) -> dict[str, Any]:
    if child_context is not None and not Path(executable).is_absolute():
        raise PythonBindingError("Select an absolute Python invocation for the managed child")
    path = _invocation_path(executable)
    before = executable_fingerprint(str(path))
    options = {"child_context": child_context} if child_context is not None else {}
    try:
        data = json.loads(
            _capture_probe([str(path), "-I", "-S", "-B", "-c", _PROBE], **options),
            object_pairs_hook=_unique_object,
        )
    except (ValueError, TypeError) as exc:
        raise PythonBindingError("Python probe did not return valid identity evidence") from exc
    if not isinstance(data, dict) or set(data) != {"executable", "version", "implementation"}:
        raise PythonBindingError("Python probe returned an unexpected identity schema")
    version = data["version"]
    if (
        not isinstance(version, list) or len(version) != 3
        or any(type(x) is not int or x < 0 for x in version)
        or version[0] != 3
        or not isinstance(data["implementation"], str)
        or data["implementation"] not in {"cpython", "pypy"}
        or not isinstance(data["executable"], str)
        or not _matches_invocation_path(data["executable"], path)
    ):
        raise PythonBindingError("Python probe did not match the selected interpreter")
    if expected_version:
        parts = [int(x) for x in expected_version.split(".")]
        if version[:len(parts)] != parts:
            raise PythonBindingError("Discovered interpreter did not match the requested Python version")
    if executable_fingerprint(str(path)) != before:
        raise PythonBindingError("Python executable changed while it was being probed")
    return {"executable": str(path), "version": version, "implementation": data["implementation"], "fingerprint": before}


def _reject_link(path: Path) -> None:
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
        raise PythonBindingError("Python binding storage must not be a symlink or reparse point")


def _command_available(command: str, *, child_context=None, cwd=None) -> bool:
    if child_context is None:
        return bool(shutil.which(command))
    from .upgrade.service_children import ServiceChildBindingError
    try:
        child_context.resolve_executable(command, cwd=cwd)
    except ServiceChildBindingError as exc:
        if exc.code == "child_executable_unavailable":
            return False
        raise
    return True


class PythonBindings:
    def __init__(self, manager: WorkspaceManager) -> None:
        self.manager = manager
        self.settings = manager.settings
        self.child_context = getattr(manager, "child_context", None)
        self.root = manager.root / "python-bindings"

    def _identity(self, workspace_id: str) -> dict[str, Any]:
        state = self.manager.get_state(workspace_id)
        self.settings.assert_project_allowed_for_workspace(state.project)
        worktree = self.manager._worktree(state)
        return {
            "workspace_id": workspace_id, "project": state.project,
            "base_sha": state.base_sha, "worktree": str(worktree),
            "gitlab_base_url": self.settings.gitlab_base_url, "platform": sys.platform,
        }

    def _path(self, workspace_id: str, command: str) -> Path:
        self.manager._state_path(workspace_id)
        if command not in PYTHON_COMMANDS:
            raise PythonBindingError("Only the explicit python or python3 command may be bound")
        _reject_link(self.root)
        path = self.root / f"{workspace_id}-{command}.json"
        _reject_link(path)
        return path

    def _allowed(self, command: str) -> None:
        if command not in self.settings.allowed_executables:
            raise PythonBindingError("Python command is not authorized by the user's executable allowlist")

    def read(self, workspace_id: str, command: str, *, verify: bool = True) -> dict[str, Any] | None:
        path = self._path(workspace_id, command)
        if not path.exists():
            return None
        identity = self._identity(workspace_id)
        self._allowed(command)
        try:
            if path.stat().st_size > MAX_RECORD:
                raise PythonBindingError("Python binding record exceeds its size limit")
            if os.name != "nt" and path.stat().st_mode & 0o077:
                raise PythonBindingError("Python binding record must be private to its owner")
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            if (
                not isinstance(data, dict)
                or set(data) != {"version", "command", "identity", "runtime", "approved_at"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["command"] != command or data["identity"] != identity
                or not isinstance(data["approved_at"], str)
                or not isinstance(data["runtime"], dict)
            ):
                raise PythonBindingError("Python binding does not match this workspace or supported schema")
            if verify:
                runtime = data["runtime"]
                if executable_fingerprint(runtime["executable"]) != runtime["fingerprint"]:
                    raise PythonBindingError("Python runtime changed; explicit re-approval is required")
                options = {"child_context": self.child_context} if self.child_context is not None else {}
                if probe_python(runtime["executable"], **options) != runtime:
                    raise PythonBindingError("Python identity changed; explicit re-approval is required")
            return data
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise PythonBindingError("Invalid Python binding; no PATH/launcher fallback attempted") from exc

    def plan(self, workspace_id: str, command: str, executable: str) -> dict[str, Any]:
        self._allowed(command)
        identity = self._identity(workspace_id)
        path = self._path(workspace_id, command)
        runtime_path = str(_invocation_path(executable))
        fingerprint = executable_fingerprint(runtime_path)
        # Compare-and-replace the old record, including malformed legacy records,
        # without interpreting it as authority for a new executable.
        previous = _hash_file(path, MAX_RECORD) if path.exists() else None
        return {
            "identity": identity, "command": command, "executable": runtime_path,
            "fingerprint": fingerprint, "previous_record_sha256": previous,
        }

    def bind(self, plan: dict[str, Any], *, approved: bool = False, replace: bool = False,
             expected_version: str | None = None) -> dict[str, Any]:
        if not approved:
            raise PythonBindingError("Explicit operator approval is required before probing and saving a binding")
        workspace_id = plan["identity"]["workspace_id"]
        command = plan["command"]
        with self.manager.mutation_lock(workspace_id):
            current = self.plan(workspace_id, command, plan["executable"])
            if current != plan:
                raise PythonBindingError("Python binding plan changed; review a fresh plan")
            if current["previous_record_sha256"] and not replace:
                raise PythonBindingError("A binding already exists; use --replace only after reviewing it")
            options = {"child_context": self.child_context} if self.child_context is not None else {}
            runtime = probe_python(plan["executable"], expected_version, **options)
            if runtime["fingerprint"] != plan["fingerprint"]:
                raise PythonBindingError("Python changed after approval; no binding saved")
            record = {
                "version": 1, "command": command, "identity": plan["identity"],
                "runtime": runtime, "approved_at": datetime.now(timezone.utc).isoformat(),
            }
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self._path(workspace_id, command)
            fd, name = tempfile.mkstemp(prefix=workspace_id + ".", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=True, sort_keys=True, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
            return record

    def resolve(self, workspace_id: str, argv: list[str]) -> dict[str, Any]:
        command = argv[0]
        if command not in PYTHON_COMMANDS:
            return {"requested_argv": list(argv), "resolved_argv": list(argv), "python_binding": None}
        binding = self.read(workspace_id, command)
        resolved = [binding["runtime"]["executable"], *argv[1:]] if binding else list(argv)
        return {"requested_argv": list(argv), "resolved_argv": resolved, "python_binding": binding}

    def validation_plan(self, workspace_id: str, context: dict[str, Any]) -> dict[str, Any]:
        identity = self._identity(workspace_id)
        resolved = []
        missing = []
        names = set(context.get("required_executables", []))
        for command in context.get("validation_commands", []):
            argv = command["argv"]
            evidence = self.resolve(workspace_id, argv)
            resolved.append({"name": command["name"], **evidence})
            if command.get("required", True):
                names.add(argv[0])
        for name in sorted(names):
            if name not in self.settings.allowed_executables:
                missing.append({"command": name, "reason": "not_allowlisted"})
            elif name in PYTHON_COMMANDS and self.read(workspace_id, name):
                continue
            elif not _command_available(name, child_context=self.child_context,
                                        cwd=identity["worktree"] if self.child_context is not None else None):
                missing.append({"command": name, "reason": "not_on_path"})
        return {
            "ok": not missing, "workspace_id": workspace_id,
            "scope": "local-interpreter-preflight-not-worker-sandbox-proof",
            "commands": resolved, "missing": missing,
        }


def handoff_runtime_guidance(manager: WorkspaceManager, workspace_id: str,
                             context: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """No auto-discovery or binding: only expose an already approved mapping."""
    storage = manager.root / "python-bindings"
    _reject_link(storage)
    if not storage.exists():
        names = {item["argv"][0] for item in context.get("validation_commands", [])}
        names.update(context.get("required_executables", []))
        child_context = getattr(manager, "child_context", None)
        cwd = manager._worktree(manager.get_state(workspace_id)) if child_context is not None else None
        missing = [name for name in PYTHON_COMMANDS if name in names
                   and not _command_available(name, child_context=child_context, cwd=cwd)]
        if missing:
            return (
                "\nLocal Python preflight: these command names are missing: "
                + json.dumps(missing)
                + ". This does not prove Python is uninstalled. Ask the operator to use "
                + f"actual-coder python bind {workspace_id} with --python VERSION or --executable ABSOLUTE_PATH. "
                + "Do not install or silently substitute an interpreter or edit the project contract.\n",
                [],
            )
        return "", []
    bindings = PythonBindings(manager)
    evidence = []
    for command in context.get("validation_commands", []):
        resolution = bindings.resolve(workspace_id, command["argv"])
        if resolution["python_binding"]:
            evidence.append({"name": command["name"], **resolution})
    if not evidence:
        return "", []
    text = (
        "\nUser-approved local Python bindings (not changes to repository policy):\n"
        "For these contract commands use the resolved executable and preserve every remaining argument.\n"
        "Probe that exact executable/version inside the existing worker sandbox before tests.\n"
        "Do not fall back to PATH, py, another interpreter, installation, or relaxed permissions.\n"
        "If an older task goal conflicts with this mapping, stop for user clarification.\n"
        "Local preflight is not proof of worker access or test success.\n"
        + json.dumps(evidence, ensure_ascii=True, indent=2) + "\n"
    )
    from .worker_recipe import recipe_guidance
    text += recipe_guidance(evidence, context, manager.settings)
    return text, evidence
