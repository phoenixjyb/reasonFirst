"""Process-local observation of selected service runtime files.

This is bounded change detection for the interpreter and an explicit module
allowlist.  File bytes are not an attestation of Python code already loaded in
memory, a prepared-runtime identity, or activation authority.  No application
configuration, environment, credentials, repository metadata, or external command
is inspected. ReasonFirst modules must share one package root; selected SDK and
dependency modules must live under this interpreter's prefix. System-site-package
mixtures and non-file import origins are outside this observation contract.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys
from types import ModuleType


SCOPE = "selected-service-runtime-files-v1"
MAX_MODULE_BYTES = 2 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
MAX_VENV_CONFIG_BYTES = 32 * 1024
_READ_CHUNK = 1024 * 1024
_SDK_VERSIONS = (("mcp", "2.3.0"), ("uvicorn", "0.54.0"))
_MODULE_NAMES = (
    "gitlab_agent", "gitlab_agent.bridge_http", "gitlab_agent.bridge_mcp",
    "gitlab_agent.bridge_preview.admission", "gitlab_agent.bridge_preview.bridge_config",
    "gitlab_agent.bridge_preview.controller", "gitlab_agent.config",
    "gitlab_agent.worker_policy", "gitlab_agent.upgrade.startup_state",
    "gitlab_agent.upgrade.service_configuration", "gitlab_agent.upgrade.service_managed",
    "gitlab_agent.upgrade.service_runtime", "mcp", "mcp.server.mcpserver.server",
    "mcp.server.streamable_http", "mcp.server.streamable_http_manager",
    "uvicorn", "uvicorn.config", "uvicorn.server", "uvicorn.protocols.http.h11_impl",
    "h11._connection", "h11._events",
)
_CODES = frozenset({
    "runtime_wrong_process", "runtime_identity_changed", "runtime_module_changed",
    "runtime_file_changed", "runtime_file_unavailable", "runtime_file_limit",
    "runtime_capture_failed",
})


class ServiceRuntimeError(RuntimeError):
    """Fixed codes only; no paths, module values, or underlying exceptions."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "runtime_capture_failed"
        super().__init__(self.code)


def _fail(code):
    raise ServiceRuntimeError(code) from None


def _path(value):
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail("runtime_file_unavailable")
    path = Path(value)
    if not path.is_absolute():
        _fail("runtime_file_unavailable")
    return path


def _invocation(value):
    # Preserve the executable leaf: two venvs may share one base binary.
    path = _path(value)
    return path.parent.resolve(strict=True) / path.name


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True, repr=False, slots=True)
class _FileObservation:
    path: Path
    identity: tuple[int, ...]
    sha256: str

    def projection(self):
        return (str(self.path), self.identity, self.sha256)


def _observe_file(path, limit):
    """Read one stable regular file with a byte limit and retained descriptor."""
    fd = named_fd = None
    try:
        resolved = path.resolve(strict=True)
        flags = os.O_RDONLY
        for name in ("O_NONBLOCK", "O_NOFOLLOW", "O_BINARY", "O_CLOEXEC"):
            flags |= getattr(os, name, 0)
        fd = os.open(resolved, flags)
        os.set_inheritable(fd, False)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("runtime_file_unavailable")
        if before.st_size > limit:
            _fail("runtime_file_limit")
        digest, size = hashlib.sha256(), 0
        while True:
            chunk = os.read(fd, min(_READ_CHUNK, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                _fail("runtime_file_limit")
            digest.update(chunk)
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or size != before.st_size:
            _fail("runtime_file_changed")
        # Compare handle metadata consistently. CPython 3.12 Windows path stat
        # adds execute bits for .exe names and substitutes creation time for
        # ctime; fstat reports neither adjustment. Retain both handles so the
        # name must still designate the exact file read, without dropping any
        # identity field to accommodate those API differences.
        named_fd = os.open(resolved, flags)
        os.set_inheritable(named_fd, False)
        named = os.fstat(named_fd)
        if (_identity(after) != _identity(named)
                or path.resolve(strict=True) != resolved):
            _fail("runtime_file_changed")
        return _FileObservation(resolved, _identity(after), digest.hexdigest())
    except ServiceRuntimeError:
        raise
    except Exception:
        _fail("runtime_file_unavailable")
    finally:
        try:
            if named_fd is not None:
                os.close(named_fd)
        finally:
            if fd is not None:
                os.close(fd)


@dataclass(frozen=True, repr=False, slots=True)
class _InterpreterObservation:
    invoked: Path
    base_invoked: Path
    prefix: Path
    base_prefix: Path
    implementation: str
    version: tuple[int, ...]
    files: tuple[_FileObservation, ...]

    def projection(self):
        return {"invoked": str(self.invoked), "base_invoked": str(self.base_invoked),
                "prefix": str(self.prefix), "base_prefix": str(self.base_prefix),
                "implementation": self.implementation, "version": self.version,
                "files": [item.projection() for item in self.files]}


def _observe_interpreter():
    invoked = _invocation(sys.executable)
    base_invoked = _invocation(sys._base_executable)
    prefix = _path(sys.prefix).resolve(strict=True)
    base_prefix = _path(sys.base_prefix).resolve(strict=True)
    version = tuple(sys.version_info[:3])
    implementation = sys.implementation.name
    if (not prefix.is_dir() or not base_prefix.is_dir() or len(version) != 3
            or any(type(item) is not int or item < 0 for item in version)
            or type(implementation) is not str or not 1 <= len(implementation) <= 64):
        _fail("runtime_identity_changed")
    files = [_observe_file(invoked, MAX_EXECUTABLE_BYTES),
             _observe_file(base_invoked, MAX_EXECUTABLE_BYTES)]
    # Look at the selected prefix, never an ambient VIRTUAL_ENV or PATH.  The
    # absence of pyvenv.cfg is accepted only for a non-venv interpreter.
    cfg = prefix / "pyvenv.cfg"
    if prefix != base_prefix or cfg.exists():
        files.append(_observe_file(cfg, MAX_VENV_CONFIG_BYTES))
    return _InterpreterObservation(invoked, base_invoked, prefix, base_prefix,
                                   implementation, version, tuple(files))


def _module_origin(name, module):
    if (type(module) is not ModuleType or sys.modules.get(name) is not module
            or module.__name__ != name or getattr(module.__spec__, "name", None) != name):
        _fail("runtime_module_changed")
    declared = _path(module.__file__)
    spec_origin = _path(module.__spec__.origin)
    origin = declared.resolve(strict=True)
    if origin != spec_origin.resolve(strict=True):
        _fail("runtime_module_changed")
    return declared, spec_origin, origin


@dataclass(frozen=True, repr=False, slots=True)
class _ModuleObservation:
    name: str
    module: ModuleType
    declared: Path
    spec_origin: Path
    file: _FileObservation

    def projection(self):
        return (self.name, str(self.declared), str(self.spec_origin), self.file.projection())


def _observe_modules(prefix):
    package = importlib.import_module("gitlab_agent")
    package_root = _module_origin("gitlab_agent", package)[2].parent
    observed = []
    for name in _MODULE_NAMES:
        module = importlib.import_module(name)
        declared, spec_origin, origin = _module_origin(name, module)
        expected_root = package_root if name == "gitlab_agent" or name.startswith("gitlab_agent.") else prefix
        if not origin.is_relative_to(expected_root):
            _fail("runtime_module_changed")
        file = _observe_file(origin, MAX_MODULE_BYTES)
        observed.append(_ModuleObservation(name, module, declared, spec_origin, file))
    return tuple(observed)


def _sdk_versions():
    versions = tuple((name, importlib.metadata.version(name)) for name, _ in _SDK_VERSIONS)
    if versions != _SDK_VERSIONS:
        _fail("runtime_identity_changed")
    return versions


@dataclass(frozen=True, repr=False, slots=True)
class ServiceRuntimeObservation:
    """Private immutable evidence, useful only in its creator process."""

    _pid: int
    _interpreter: _InterpreterObservation
    _modules: tuple[_ModuleObservation, ...]
    _versions: tuple[tuple[str, str], ...]
    _digest: str

    def __repr__(self):
        return "ServiceRuntimeObservation(selected_runtime_evidence=private)"

    def _check_pid(self):
        if os.getpid() != self._pid:
            _fail("runtime_wrong_process")

    @property
    def digest(self):
        self._check_pid()
        return self._digest

    def summary(self):
        self._check_pid()
        return {"scope": SCOPE, "digest": self._digest,
                "selected_module_count": len(self._modules),
                "interpreter_files_observed": True, "selected_module_origins_observed": True,
                "selected_module_files_observed": True, "current_process_only": True,
                "runtime_identity_verified": False, "running_code_verified": False,
                "activation_authorized": False}

    def revalidate(self):
        # A fork may inherit arbitrary mutex/file state. Reject it before any
        # filesystem access, imported module lookup, or interpreter observation.
        self._check_pid()
        try:
            if _sdk_versions() != self._versions or _observe_interpreter() != self._interpreter:
                _fail("runtime_identity_changed")
            for item in self._modules:
                declared, spec_origin, origin = _module_origin(item.name, item.module)
                if (declared != item.declared or spec_origin != item.spec_origin
                        or origin != item.file.path):
                    _fail("runtime_module_changed")
                if _observe_file(origin, MAX_MODULE_BYTES) != item.file:
                    _fail("runtime_file_changed")
            self._check_pid()
        except ServiceRuntimeError:
            raise
        except Exception:
            _fail("runtime_capture_failed")


def capture_service_runtime():
    """Derive evidence internally; callers cannot supply expected identity claims."""
    pid = os.getpid()
    try:
        versions = _sdk_versions()
        interpreter = _observe_interpreter()
        modules = _observe_modules(interpreter.prefix)
        projection = {"scope": SCOPE, "pid": pid, "interpreter": interpreter.projection(),
                      "modules": [item.projection() for item in modules], "sdk_versions": versions}
        digest = hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
        observed = ServiceRuntimeObservation(pid, interpreter, modules, versions, digest)
        observed.revalidate()
        return observed
    except ServiceRuntimeError:
        raise
    except Exception:
        _fail("runtime_capture_failed")
