"""Retained local inputs for children owned by one selected service context.

The private environment is passed intact to covered children. It is never a
public identity, and referenced provider, SSH and Desktop configuration remains
external. Executable files are observed before covered launches; their bytes do
not attest a running image, wrapper dependencies or remote execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import threading

from ..config import AgentSettings
from .service_configuration import ManagedServiceConfiguration
from .service_runtime import MAX_EXECUTABLE_BYTES, ServiceRuntimeError, _observe_file


MAX_ENV_ENTRIES = 4096
MAX_ENV_NAME_BYTES = 4096
MAX_ENV_VALUE_BYTES = 256 * 1024
MAX_ENV_BYTES = 2 * 1024 * 1024
MAX_SELECTIONS = 256
_WINDOWS = os.name == "nt"
_CONTEXT_AUTHORITY = object()
_WINDOWS_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"
_CODES = frozenset({
    "invalid_child_context", "child_wrong_process", "child_capture_failed",
    "child_environment_limit", "child_executable_unavailable",
    "child_executable_changed", "child_selection_limit", "child_endpoint_unavailable",
    "child_probe_failed", "child_launch_failed", "child_binding_failed",
    "child_trust_unavailable", "child_trust_failed",
})


class ServiceChildBindingError(RuntimeError):
    """A finite diagnostic, independent of private inputs and child output."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "child_binding_failed"
        super().__init__(self.code)


def _fail(code):
    raise ServiceChildBindingError(code) from None


def _environment_snapshot():
    entries, size = [], 0
    for name, value in os.environ.items():
        if (type(name) is not str or type(value) is not str or not name
                or "=" in name or "\0" in name or "\0" in value):
            _fail("child_capture_failed")
        name_size = len(name.encode("utf-8", "surrogatepass"))
        value_size = len(value.encode("utf-8", "surrogatepass"))
        size += name_size + value_size + 2
        if (len(entries) >= MAX_ENV_ENTRIES or name_size > MAX_ENV_NAME_BYTES
                or value_size > MAX_ENV_VALUE_BYTES or size > MAX_ENV_BYTES):
            _fail("child_environment_limit")
        entries.append((name, value))
    return tuple(entries)


def _environment_value(entries, name, windows):
    if windows:
        name = name.upper()
        return next((value for key, value in entries if key.upper() == name), None)
    return next((value for key, value in entries if key == name), None)


def _text_path(value):
    if isinstance(value, Path):
        value = str(value)
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail("child_executable_unavailable")
    return value


def _absolute_path(value, cwd, home):
    value = _text_path(value)
    if value == "~" or value.startswith(("~/", "~\\")):
        if home is None:
            _fail("child_executable_unavailable")
        value = str(home) + value[1:]
    elif value.startswith("~"):
        # Only this process's retained home participates in the binding.
        _fail("child_executable_unavailable")
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path


def _capture_home(entries, cwd, windows):
    if windows:
        raw = _environment_value(entries, "USERPROFILE", True)
        if raw is None:
            homepath = _environment_value(entries, "HOMEPATH", True)
            raw = ((_environment_value(entries, "HOMEDRIVE", True) or "") + homepath
                   if homepath is not None else None)
    else:
        raw = _environment_value(entries, "HOME", False)
        if raw is None:
            try:
                import pwd
                raw = pwd.getpwuid(os.getuid()).pw_dir
            except (ImportError, KeyError):
                raw = None
        if raw is not None:
            raw = raw.rstrip("/") or "/"
    if raw is None:
        return None
    return _absolute_path(raw or str(cwd), cwd, None).resolve(strict=False)


@dataclass(frozen=True, repr=False, slots=True)
class _ExecutableSelection:
    invocation: Path
    observed: object


@dataclass(frozen=True, repr=False, eq=False, init=False)
class ServiceChildContext:
    """Factory-created private input retention, usable only by its creator PID."""

    _configuration: object
    _authority: object
    _pid: int
    _environment: tuple[tuple[str, str], ...]
    _cwd: Path
    _home: Path | None
    _python: _ExecutableSelection
    _windows: bool
    _platform: str
    _lock: object
    _selections: dict
    _endpoint: Path | None
    _api_trust: object | None
    _api_trust_anchor: object | None
    _api_trust_observation: dict | None
    _allow_api_trust_selection: bool
    _failure: str | None
    _summary: dict

    def __init__(self, *args, **kwargs):
        _fail("invalid_child_context")

    def __repr__(self):
        return "ServiceChildContext(retained_child_inputs=private)"

    def _check_pid(self):
        if getattr(self, "_authority", None) is not _CONTEXT_AUTHORITY:
            _fail("invalid_child_context")
        if os.getpid() != self._pid:
            # Do not acquire an inherited mutex in the wrong process.
            object.__setattr__(self, "_failure", "child_wrong_process")
            _fail("child_wrong_process")

    def _check_valid_locked(self):
        if self._failure is not None:
            _fail(self._failure)

    def _invalidate_locked(self, code):
        if self._failure is None:
            object.__setattr__(self, "_failure", code)
        _fail(self._failure)

    @property
    def configuration(self):
        self._check_pid()
        return self._configuration

    @property
    def working_directory(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            return self._cwd

    @property
    def python_invocation(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            return str(self._python.invocation)

    def environment_copy(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            return dict(self._environment)

    def summary(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            self._check_api_trust_locked()
            return dict(self._summary)

    def _selected_settings_locked(self):
        if type(self._configuration) is ManagedServiceConfiguration:
            return self._configuration.settings
        if type(self._configuration) is AgentSettings:
            return self._configuration
        self._invalidate_locked("invalid_child_context")

    def _require_api_settings_locked(self, settings):
        if settings is not self._selected_settings_locked():
            self._invalidate_locked("child_trust_failed")

    def _check_api_trust_locked(self):
        from .service_trust import ServiceAPITrust

        if self._api_trust is not self._api_trust_anchor:
            self._invalidate_locked("child_trust_failed")
        trust = self._api_trust
        if trust is None:
            return None
        try:
            if (type(trust) is not ServiceAPITrust
                    or trust.settings is not self._selected_settings_locked()):
                self._invalidate_locked("child_trust_failed")
            # This is cached authority evidence, not a file read.
            trust.summary()
        except Exception:
            self._invalidate_locked("child_trust_failed")
        return trust

    @property
    def api_trust(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            return self._check_api_trust_locked()

    def api_trust_summary(self):
        """Historical selected-material evidence, including after invalidation."""
        self._check_pid()
        with self._lock:
            return (dict(self._api_trust_observation)
                    if self._api_trust_observation is not None else None)

    def select_api_trust(self, settings):
        """Select once in the owning parent; helpers only use transferred input."""
        from .service_trust import ServiceAPITrust, capture_api_trust

        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            self._require_api_settings_locked(settings)
            self._revalidate_locked()
            trust = self._check_api_trust_locked()
            if trust is not None:
                return trust
            if not self._allow_api_trust_selection:
                self._invalidate_locked("child_trust_failed")
            try:
                candidate = capture_api_trust(settings)
            except Exception:
                # Like an optional executable unavailable before selection,
                # an unused/invalid CA must not poison unrelated local work.
                _fail("child_trust_unavailable")
            try:
                if type(candidate) is not ServiceAPITrust or candidate.settings is not settings:
                    self._invalidate_locked("child_trust_failed")
                summary = candidate.summary()
            except Exception:
                self._invalidate_locked("child_trust_failed")
            object.__setattr__(self, "_api_trust", candidate)
            object.__setattr__(self, "_api_trust_anchor", candidate)
            object.__setattr__(self, "_api_trust_observation", dict(summary))
            return candidate

    def _guard_api_request(self, settings, trust):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            self._require_api_settings_locked(settings)
            if self._check_api_trust_locked() is not trust:
                self._invalidate_locked("child_trust_failed")
            self._revalidate_locked()

    def _fail_api_trust(self):
        self._check_pid()
        with self._lock:
            self._invalidate_locked("child_trust_failed")

    def api_client_options(self, settings, *, asynchronous=False):
        """Construct fresh TLS options from this context's exact retained input."""
        from ..tls import api_client_options
        from .service_trust import ServiceAPITrustError

        if type(asynchronous) is not bool:
            self._fail_api_trust()
        trust = self.select_api_trust(settings)
        try:
            options = api_client_options(
                settings.gitlab_base_url, verify_ssl=settings.api_verify_ssl,
                ca_bundle=settings.api_ca_bundle, asynchronous=asynchronous,
                retained_trust=trust,
            )
        except ServiceAPITrustError:
            self._fail_api_trust()
        hooks = tuple(options["event_hooks"]["request"])

        def guard(request):
            self._guard_api_request(settings, trust)
            try:
                for hook in hooks:
                    hook(request)
            except ServiceAPITrustError:
                self._fail_api_trust()

        async def aguard(request):
            self._guard_api_request(settings, trust)
            try:
                for hook in hooks:
                    await hook(request)
            except ServiceAPITrustError:
                self._fail_api_trust()

        options["event_hooks"]["request"] = [aguard if asynchronous else guard]
        return options

    def _revalidate_locked(self):
        self._check_valid_locked()
        trust = self._check_api_trust_locked()
        if trust is not None:
            try:
                trust.revalidate()
            except Exception:
                self._invalidate_locked("child_trust_failed")
        try:
            if not self._cwd.is_dir():
                self._invalidate_locked("child_binding_failed")
            selected = {item.invocation: item for item in
                        (self._python, *self._selections.values())}
            for selection in selected.values():
                if _observe_file(selection.invocation, MAX_EXECUTABLE_BYTES) != selection.observed:
                    self._invalidate_locked("child_executable_changed")
        except ServiceChildBindingError:
            raise
        except Exception:
            self._invalidate_locked("child_executable_changed")

    def revalidate(self):
        self._check_pid()
        with self._lock:
            self._revalidate_locked()

    def _environment_value(self, name):
        return _environment_value(self._environment, name, self._windows)

    def _directory(self, cwd):
        return (_absolute_path(cwd, self._cwd, self._home).resolve(strict=True)
                if cwd is not None else self._cwd)

    def _candidate(self, path):
        if path.is_file() and os.access(path, os.X_OK):
            # Preserve an invocation leaf such as a venv shim or executable link.
            return path.parent.resolve(strict=True) / path.name
        return None

    def _search_executable(self, command, cwd):
        raw = _text_path(command)
        path = _absolute_path(raw, cwd, self._home)
        if Path(raw).is_absolute():
            # An already approved invocation must remain that exact path, even
            # if PATHEXT would allow another suffixed executable beside it.
            return self._candidate(path)
        qualified = Path(raw).is_absolute() or bool(Path(raw).parent != Path(".")) or raw.startswith("~")
        if qualified:
            directories, leaf = [path.parent], path.name
        else:
            configured = self._environment_value("PATH")
            if configured == "":
                return None
            configured = os.defpath if configured is None else configured
            separator = ";" if self._windows else os.pathsep
            directories = [_absolute_path(part or str(cwd), cwd, self._home)
                           for part in configured.split(separator)]
            leaf = raw
            # Match the Windows default current-directory search using the
            # retained switch, without calling a WinAPI that reads ambient env.
            if self._windows and self._environment_value("NoDefaultCurrentDirectoryInExePath") is None:
                directories.insert(0, cwd)
        if self._windows:
            extensions = [ext.rstrip(".") for ext in
                          (self._environment_value("PATHEXT") or _WINDOWS_PATHEXT).split(";") if ext]
            if any("/" in ext or "\\" in ext or "\0" in ext for ext in extensions):
                _fail("child_executable_unavailable")
            names = [leaf + ext for ext in extensions]
            if any(leaf.upper().endswith(ext.upper()) for ext in extensions):
                names.insert(0, leaf)
        else:
            names = [leaf]
        seen = set()
        for directory in directories:
            key = str(directory).casefold() if self._windows else str(directory)
            if key in seen:
                continue
            seen.add(key)
            for name in names:
                selected = self._candidate(directory / name)
                if selected is not None:
                    return selected
        return None

    def _select_locked(self, key, select):
        self._revalidate_locked()
        existing = self._selections.get(key)
        if existing is not None:
            return str(existing.invocation)
        if len(self._selections) >= MAX_SELECTIONS:
            _fail("child_selection_limit")
        try:
            invoked = select()
            if invoked is None:
                _fail("child_executable_unavailable")
            observed = _observe_file(invoked, MAX_EXECUTABLE_BYTES)
        except ServiceChildBindingError:
            raise
        except ServiceRuntimeError as exc:
            if exc.code == "runtime_file_changed":
                self._invalidate_locked("child_executable_changed")
            _fail("child_executable_unavailable")
        except Exception:
            _fail("child_executable_unavailable")
        self._selections[key] = _ExecutableSelection(invoked, observed)
        self._summary["selected_executable_count"] = len({
            item.invocation for item in self._selections.values()
        })
        return str(invoked)

    def resolve_executable(self, command, *, cwd=None):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            try:
                command = _text_path(command)
                directory = self._directory(cwd)
                # Absolute invocation identity is independent of a probe's
                # disposable working directory. Relative PATH entries are not.
                key = ("command", command, None if Path(command).is_absolute() else str(directory))
                return self._select_locked(key,
                                           lambda: self._search_executable(command, directory))
            except ServiceChildBindingError:
                raise
            except Exception:
                _fail("child_executable_unavailable")

    def _desktop_candidates(self):
        if self._platform != "darwin":
            return []
        candidates = [Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                      Path("/Applications/Codex.app/Contents/Resources/codex")]
        if self._home is not None:
            candidates.extend([self._home / "Applications/ChatGPT.app/Contents/Resources/codex",
                               self._home / "Applications/Codex.app/Contents/Resources/codex"])
        return candidates

    def resolve_codex_binary(self, *, prefer_desktop=False):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            if type(prefer_desktop) is not bool:
                _fail("invalid_child_context")

            def select():
                explicit = (self._environment_value("CODEX_BRIDGE_CODEX_BIN") or "").strip()
                if explicit:
                    # An explicit user choice must not become another executable
                    # merely because that choice is absent or unusable now.
                    return self._candidate(_absolute_path(explicit, self._cwd, self._home))
                desktop = self._desktop_candidates()
                if prefer_desktop:
                    for candidate in desktop:
                        chosen = self._candidate(candidate)
                        if chosen is not None:
                            return chosen
                chosen = self._search_executable("codex", self._cwd)
                if chosen is not None:
                    return chosen
                if not prefer_desktop:
                    for candidate in desktop:
                        chosen = self._candidate(candidate)
                        if chosen is not None:
                            return chosen
                return None

            return self._select_locked(("codex", prefer_desktop), select)

    def managed_app_server_socket(self):
        self._check_pid()
        with self._lock:
            self._revalidate_locked()
            if self._endpoint is None:
                try:
                    configured = (self._environment_value("CODEX_HOME") or "").strip()
                    if configured:
                        root = _absolute_path(configured, self._cwd, self._home)
                    elif self._home is not None:
                        root = self._home / ".codex"
                    else:
                        fallback = (self._environment_value("LOCALAPPDATA")
                                    or self._environment_value("TEMP") or str(self._cwd))
                        root = _absolute_path(fallback, self._cwd, self._home) / ".reasonfirst-unavailable-codex-home"
                    endpoint = root.resolve(strict=False) / "app-server-control" / "app-server-control.sock"
                    object.__setattr__(self, "_endpoint", endpoint)
                except ServiceChildBindingError:
                    raise
                except Exception:
                    _fail("child_endpoint_unavailable")
            return self._endpoint


def _capture(configuration, *, api_trust=None):
    pid = os.getpid()
    try:
        environment = _environment_snapshot()
        cwd = Path.cwd().resolve(strict=True)
        home = _capture_home(environment, cwd, _WINDOWS)
        invocation = _absolute_path(sys.executable, cwd, home)
        invocation = invocation.parent.resolve(strict=True) / invocation.name
        observed = _observe_file(invocation, MAX_EXECUTABLE_BYTES)
        if os.getpid() != pid:
            _fail("child_wrong_process")
        context = object.__new__(ServiceChildContext)
        fields = {
            "_configuration": configuration, "_authority": _CONTEXT_AUTHORITY,
            "_pid": pid, "_environment": environment,
            "_cwd": cwd, "_home": home, "_python": _ExecutableSelection(invocation, observed),
            "_windows": _WINDOWS, "_platform": sys.platform, "_lock": threading.RLock(),
            "_selections": {}, "_endpoint": None, "_failure": None,
            "_api_trust": api_trust, "_api_trust_anchor": api_trust,
            "_api_trust_observation": None,
            "_allow_api_trust_selection": type(configuration) is ManagedServiceConfiguration,
            "_summary": {
                "schema_version": 1, "scope": "owned-local-child-inputs",
                "environment_retained": True, "working_directory_retained": True,
                "python_invocation_observed": True, "selected_executable_count": 0,
                "current_process_only": True, "child_runtime_verified": False,
                "provider_configuration_verified": False, "remote_runtime_verified": False,
                "desktop_daemon_verified": False, "activation_authorized": False,
            },
        }
        for name, value in fields.items():
            object.__setattr__(context, name, value)
        trust = context._check_api_trust_locked()
        if trust is not None:
            object.__setattr__(context, "_api_trust_observation", dict(trust.summary()))
        return context
    except ServiceChildBindingError:
        raise
    except Exception:
        _fail("child_capture_failed")


def capture_service_children(configuration):
    """Capture this process's local inputs for an exact selected service policy."""
    if type(configuration) is not ManagedServiceConfiguration:
        _fail("invalid_child_context")
    return _capture(configuration)


def capture_helper_children(settings, *, api_trust=None):
    """Retain an authenticated helper's inherited inputs after request decoding."""
    if type(settings) is not AgentSettings:
        _fail("invalid_child_context")
    return _capture(settings, api_trust=api_trust)
