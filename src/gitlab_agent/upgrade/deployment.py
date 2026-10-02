"""Explicit, create-only recording of a recognized macOS registration.

Recording is not execution authorization, runtime adoption, health, authentication,
configuration migration, or a promise that a future upgrade can be applied. No
controller is constructed and no command, listener, package manager or API runs.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import stat
import uuid
from typing import Iterator, NoReturn

from .. import __version__
from ..deployment_inventory import LABEL, MAX_PLIST_BYTES, _launch_agent

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 32 * 1024
SCOPE = "registration-snapshot-only-not-runtime-adoption"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_PARTS = (".config", "reasonfirst", "deployments")
_NAME = LABEL + ".json"
_SNAPSHOT_KEYS = {
    "label", "manager", "registration_path", "registration_sha256",
    "layout", "program", "working_directory", "transport", "transport_basis",
}


class DeploymentError(RuntimeError):
    """Fixed, non-secret message with a machine-readable failure code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str) -> NoReturn:
    messages = {
        "unsupported_platform": "Deployment recording currently supports macOS user LaunchAgents only.",
        "unsafe_path": "Deployment storage or registration has an unsupported path, owner, or permissions.",
        "unreadable": "Deployment metadata could not be read safely; existing files were not replaced.",
        "unsupported_layout": "The known registration is absent, invalid, or unsupported; no record can be adopted.",
        "changed_registration": "Registration changed during review; generate and review a fresh plan.",
        "invalid_record": "Existing deployment record is invalid or unsupported; it will not be overwritten.",
        "approval_required": "Recording requires --yes and the exact --expect-digest from a reviewed plan.",
        "existing_record_conflict": "A different deployment record already exists; replacement is not supported.",
        "write_failed": "Deployment record write failed; inspect status before retrying. No service was changed.",
    }
    raise DeploymentError(code, messages[code])


def _platform(system_name: str) -> None:
    if system_name.lower() not in {"darwin", "macos"} or os.name != "posix":
        _fail("unsupported_platform")


def _home(home: Path | None) -> Path:
    value = home if home is not None else Path.home()
    # Do not resolve a supplied symlink silently. macOS may have /var -> /private/var
    # ancestors; the selected HOME itself and each owned child must be real dirs.
    if not value.is_absolute() or ".." in value.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(value)):
        _fail("unsafe_path")
    return value


def _check(info: os.stat_result, *, directory: bool = False, private: bool = False) -> None:
    correct_kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mask = 0o077 if private else 0o022
    if not correct_kind or info.st_uid != os.getuid() or info.st_mode & mask:
        _fail("unsafe_path")
    if not directory and info.st_nlink != 1:
        _fail("unsafe_path")


@contextmanager
def _directory(home: Path, parts: tuple[str, ...], *, create: bool = False) -> Iterator[int | None]:
    """Anchor traversal to no-follow directory handles; never chmod existing dirs."""
    handles: list[int] = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(home, flags)
        handles.append(fd)
        _check(os.fstat(fd), directory=True)
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass  # A competing creator must still pass the same checks.
                child = os.open(part, flags, dir_fd=fd)
            handles.append(child)
            _check(os.fstat(child), directory=True,
                   private=parts == _PARTS and index == len(parts) - 1)
            fd = child
        yield fd
    except DeploymentError:
        raise
    except OSError:
        _fail("unsafe_path")
    finally:
        for fd in reversed(handles):
            os.close(fd)


def _read_at(fd: int, name: str, *, limit: int, private: bool = False) -> bytes | None:
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("unreadable")
    try:
        before = os.fstat(child)
        _check(before, private=private)
        if before.st_size > limit:
            _fail("invalid_record" if private else "unsupported_layout")
        with os.fdopen(child, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(child)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if len(data) > limit or identity(before) != identity(after) or identity(after) != identity(named):
            _fail("changed_registration")
        return data
    except DeploymentError:
        raise
    except OSError:
        _fail("unreadable")
    finally:
        os.close(child)


def _registration_bytes(home: Path) -> bytes:
    with _directory(home, ("Library", "LaunchAgents")) as fd:
        if fd is None:
            _fail("unsupported_layout")
        raw = _read_at(fd, LABEL + ".plist", limit=MAX_PLIST_BYTES)
    if raw is None:
        _fail("unsupported_layout")
    return raw


def _snapshot(home: Path) -> dict[str, object]:
    before = _registration_bytes(home)
    registration = _launch_agent(home)
    if before != _registration_bytes(home):
        _fail("changed_registration")
    if registration.get("status") != "discovered_legacy" or not registration.get("ownership_verified"):
        _fail("unsupported_layout")
    # No arbitrary plist argv/environment values are stored or returned. The hash
    # binds ALL registration bytes (including unknown environment fields) without
    # disclosing those values. External config contents and executable bytes are
    # deliberately not inspected; a later runtime plan must inspect them afresh.
    return {
        "label": LABEL, "manager": "launchd-user",
        "registration_path": registration["path"],
        "registration_sha256": hashlib.sha256(before).hexdigest(),
        "layout": registration["layout"], "program": registration["program"],
        "working_directory": registration["working_directory"],
        "transport": registration["transport"],
        "transport_basis": registration["transport_basis"],
    }


def _canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(snapshot: dict[str, object]) -> str:
    # Domain/version/destination binding prevents treating another document as an
    # approval. This is a change detector, not a signature or authentication token.
    return hashlib.sha256(_canonical({"schema_version": SCHEMA_VERSION, "scope": SCOPE,
                                     "record_path_suffix": list(_PARTS) + [_NAME],
                                     "snapshot": snapshot})).hexdigest()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_record")
        result[key] = value
    return result


def _decode(raw: bytes, home: Path) -> dict[str, object]:
    try:
        result = json.loads(raw, object_pairs_hook=_no_duplicates)
        required = {"schema_version", "scope", "snapshot", "plan_digest", "recorded_at",
                    "recorded_by_cli_version", "runtime_id", "running_version", "health", "activation_authorized"}
        if not isinstance(result, dict) or set(result) != required:
            _fail("invalid_record")
        if type(result["schema_version"]) is not int or result["schema_version"] != SCHEMA_VERSION or result["scope"] != SCOPE:
            _fail("invalid_record")
        snap = result["snapshot"]
        if not isinstance(snap, dict) or set(snap) != _SNAPSHOT_KEYS or not all(isinstance(v, str) for v in snap.values()):
            _fail("invalid_record")
        # Reuse the same exact layout classifier, through a data-only validation
        # expressed here. No record-supplied path is ever opened or executed.
        root = home / ".local/share/reasonfirst/v4-service"
        program = Path(snap["program"])
        legacy = program == root / "tools/codex_web_bridge/run_reasonfirst.sh"
        sidecar = (program.name == "run_reasonfirst.sh" and
                   program.parent.parent == home / ".local/share/reasonfirst/upgrades" and
                   re.fullmatch(r"uv-http-v[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z0-9_-]+", program.parent.name))
        expected_layout = "legacy_staged_http" if legacy else "versioned_http_sidecar" if sidecar else None
        if (snap["label"] != LABEL or snap["manager"] != "launchd-user" or
            snap["registration_path"] != str(home / "Library/LaunchAgents" / (LABEL + ".plist")) or
            snap["working_directory"] != str(root) or snap["layout"] != expected_layout or
            snap["transport"] != "streamable-http" or
            snap["transport_basis"] != "recognized_launcher_layout_not_live_probe" or
            not _HEX.fullmatch(snap["registration_sha256"]) or
            result["plan_digest"] != _digest(snap)):
            _fail("invalid_record")
        if (result["runtime_id"] is not None or result["running_version"] is not None or
            result["health"] != "not_inspected" or result["activation_authorized"] is not False):
            _fail("invalid_record")
        recorded = datetime.fromisoformat(result["recorded_at"])
        if recorded.utcoffset() is None or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", result["recorded_by_cli_version"]):
            _fail("invalid_record")
        return result
    except DeploymentError:
        raise
    except Exception:
        _fail("invalid_record")


def _read_record(home: Path) -> dict[str, object] | None:
    with _directory(home, _PARTS) as fd:
        if fd is None:
            return None
        raw = _read_at(fd, _NAME, limit=MAX_RECORD_BYTES, private=True)
    return None if raw is None else _decode(raw, home)


def _result(operation: str) -> dict[str, object]:
    return {"operation": operation, "scope": SCOPE, "ok": True,
            "record_written": False, "service_changed": False, "service_restarted": False,
            "runtime_installed": False, "activation_authorized": False, "ready_for_activation": False,
            "application_configuration_contents_read": False, "setup_state_changed": False,
            "network_requested": False, "live_inspection": "not_inspected"}


def plan_adoption(*, system_name: str, home: Path | None = None) -> dict[str, object]:
    """Read the one known registration and record. Does not write a plan file."""
    _platform(system_name)
    selected = _home(home)
    snap = _snapshot(selected)
    record = _read_record(selected)
    digest = _digest(snap)
    if record is not None and record["plan_digest"] != digest:
        _fail("existing_record_conflict")
    return {**_result("deployment-plan"), "plan_digest": digest,
            "snapshot": snap, "record_path": str(selected.joinpath(*_PARTS, _NAME)),
            "record_status": "recorded" if record else "not_recorded",
            "proposed_action": "none" if record else "record-registration-snapshot",
            "runtime_identity": "not_inspected", "configuration_bindings": "not_inspected",
            "message": "Recording this snapshot does not authorize activation or claim a running version."}


def adopt_deployment(*, system_name: str, expect_digest: str | None, approved: bool,
                     home: Path | None = None) -> dict[str, object]:
    _platform(system_name)
    if approved is not True or not isinstance(expect_digest, str) or not _HEX.fullmatch(expect_digest):
        _fail("approval_required")
    selected = _home(home)
    plan = plan_adoption(system_name=system_name, home=selected)
    if plan["plan_digest"] != expect_digest:
        _fail("changed_registration")
    if plan["record_status"] == "recorded":
        return {**_result("deployment-adopt"), "already_recorded": True,
                "record_path": plan["record_path"], "plan_digest": expect_digest}
    record = {"schema_version": SCHEMA_VERSION, "scope": SCOPE,
              "snapshot": plan["snapshot"], "plan_digest": expect_digest,
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "recorded_by_cli_version": __version__, "runtime_id": None,
              "running_version": None, "health": "not_inspected", "activation_authorized": False}
    raw = _canonical(record) + b"\n"
    _decode(raw, selected)
    temporary = ".pending-" + uuid.uuid4().hex
    linked = False
    temporary_created = False
    with _directory(selected, _PARTS, create=True) as fd:
        assert fd is not None
        try:
            # Commit point uses link(no replacement) after fsync. A concurrent
            # adopter can win but can never overwrite an existing record.
            child = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            temporary_created = True
            with os.fdopen(child, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if _digest(_snapshot(selected)) != expect_digest:
                _fail("changed_registration")
            try:
                os.link(temporary, _NAME, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                linked = True
            except FileExistsError:
                # Preserve the winner. Do not claim success from an unreviewed
                # concurrent write; explicit status inspection is the next step.
                _fail("existing_record_conflict")
        except DeploymentError:
            raise
        except OSError:
            _fail("write_failed")
        finally:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
                except OSError:
                    _fail("write_failed")
        try:
            os.fsync(fd)
        except OSError:
            _fail("write_failed")
    # Re-read canonical record after unlinking the temp hard link. If interrupted
    # before unlink, the extra link fails closed; no destructive auto-repair.
    accepted = _read_record(selected)
    if not accepted or accepted["plan_digest"] != expect_digest:
        _fail("write_failed")
    try:
        unchanged = _digest(_snapshot(selected)) == expect_digest
    except DeploymentError:
        unchanged = False
    result = {**_result("deployment-adopt"), "ok": unchanged, "record_written": linked,
            "already_recorded": False, "plan_digest": expect_digest,
            "record_path": plan["record_path"],
            "registration_unchanged_at_check": unchanged,
            "message": "Registration snapshot recorded; runtime and health remain uninspected." if unchanged else
                       "Record was written but registration changed; inspect status. No service was changed."}
    if not unchanged:
        # The record committed before this observation. Preserve that fact in
        # both JSON and human output; do not let a missing error field mask it.
        result.update(error_code="changed_registration", error=result["message"],
                      inspect_status_before_retry=True)
    return result


def deployment_status(*, system_name: str, home: Path | None = None) -> dict[str, object]:
    _platform(system_name)
    selected = _home(home)
    record = _read_record(selected)
    if record is None:
        return {**_result("deployment-status"), "record_status": "not_recorded",
                "registration_comparison": "not_inspected"}
    try:
        same = record["plan_digest"] == _digest(_snapshot(selected))
        comparison = "matches_record" if same else "drifted"
    except DeploymentError:
        comparison = "unavailable_or_unsupported"
    return {**_result("deployment-status"), "record_status": "recorded",
            "registration_comparison": comparison, "record": record,
            "runtime_identity": "not_inspected", "configuration_bindings": "not_inspected",
            "ready_for_activation": False}


def run_command(command: str, *, expect_digest: str | None = None, approved: bool = False) -> dict[str, object]:
    """CLI boundary: never return raw exceptions from private files or OS calls."""
    try:
        system = platform.system()
        if command == "plan":
            return plan_adoption(system_name=system)
        if command == "status":
            return deployment_status(system_name=system)
        if command == "adopt":
            return adopt_deployment(system_name=system, expect_digest=expect_digest, approved=approved)
        raise DeploymentError("unsupported_command", "Unsupported deployment command.")
    except Exception as exc:
        known = isinstance(exc, DeploymentError)
        return {**_result("deployment-" + command), "ok": False,
                "error_code": exc.code if known else "inspection_failed",
                "error": str(exc) if known else "Deployment operation failed; private diagnostics are not printed.",
                # A write/flush/interruption can have an ambiguous outcome. Do not
                # mistake a failed command for proof that no record was created.
                "record_written": None if command == "adopt" else False,
                "inspect_status_before_retry": command == "adopt"}
