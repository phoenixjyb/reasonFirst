"""Read-only pairing of a saved macOS registration and a prepared runtime.

This binds two measured static inputs for review, not live deployment identity,
configuration/state compatibility, a maintenance lease or activation permission.
No controller, application configuration, launcher or service manager is loaded.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
from typing import Any

from . import deployment as storage
from . import runtime

SCOPE = "static-deployment-runtime-pairing-v1"
UNRESOLVED = (
    "saved_registration_vs_loaded_service",
    "current_launcher_and_configuration_bindings",
    "endpoint_mode_and_tool_privileges",
    "legacy_control_requirement",
    "target_configuration_and_state_compatibility",
    "active_work_admission",
    "controlled_activation_and_durable_recovery",
)


class PairingError(RuntimeError):
    """Fixed internal codes only; input and exception text must not be echoed."""

    def __init__(self, code: str):
        allowed = {
            "unsupported_pairing_platform", "unknown_host_architecture",
            "invalid_runtime_id", "invalid_pairing_digest", "deployment_not_recorded",
            "registration_drifted", "runtime_manifest_missing", "invalid_runtime_record",
            "runtime_host_mismatch", "runtime_not_prepared", "changed_during_inspection",
            "pairing_changed", "invalid_pairing_action",
        }
        super().__init__(code if code in allowed else "inspection_failed")


def _fail(code: str) -> None:
    raise PairingError(code)


def _supported() -> tuple[str, str]:
    system = platform.system()
    # The runtime store supports Linux too, but the saved-registration adapter
    # does not. Reject BEFORE discovering HOME or inspecting either store.
    if os.name != "posix" or system != "Darwin":
        _fail("unsupported_pairing_platform")
    machine = platform.machine()
    if not machine:
        _fail("unknown_host_architecture")
    return system, machine


def _hex(value: str, code: str) -> None:
    if not isinstance(value, str) or not runtime.HEX.fullmatch(value):
        _fail(code)


def _read(home: Path, parts: tuple[str, ...], name: str, limit: int) -> bytes | None:
    with storage._directory(home, parts) as fd:
        if fd is None:
            return None
        storage._check(os.fstat(fd), directory=True, private=True)
        return storage._read_at(fd, name, limit=limit, private=True)


def _deployment(home: Path) -> dict[str, Any]:
    raw = _read(home, storage._PARTS, storage._NAME, storage.MAX_RECORD_BYTES)
    if raw is None:
        _fail("deployment_not_recorded")
    record = storage._decode(raw, home)
    snapshot = storage._snapshot(home)
    if record["plan_digest"] != storage._digest(snapshot):
        _fail("registration_drifted")
    return {
        "record_path": str(home.joinpath(*storage._PARTS, storage._NAME)),
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "adoption_digest": record["plan_digest"],
        "registration": snapshot,
    }


def _runtime(home: Path, runtime_id: str, system: str, machine: str) -> dict[str, Any]:
    parts = runtime.PARTS + (runtime_id,)
    raw = _read(home, parts, "runtime.json", runtime.MAX_MANIFEST)
    if raw is None:
        _fail("runtime_manifest_missing")
    record = json.loads(raw, object_pairs_hook=runtime.unique)
    if not isinstance(record, dict) or not isinstance(record.get("input"), dict):
        _fail("invalid_runtime_record")
    if (record["input"].get("platform") != system
            or record["input"].get("machine") != machine):
        _fail("runtime_host_mismatch")
    # Reuse the full strict tree/base-interpreter validator. A stored status or
    # version string supplied by the caller is never a substitute for this read.
    status = runtime.status(runtime_id=runtime_id, home=home)
    if status.get("prepared") is not True or status.get("runtime_status") != "prepared_matches_record":
        _fail("runtime_not_prepared")
    if (status.get("manifest_digest") != record.get("manifest_digest")
            or raw != _read(home, parts, "runtime.json", runtime.MAX_MANIFEST)):
        _fail("changed_during_inspection")
    return {
        "runtime_id": runtime_id,
        "runtime_path": str(home.joinpath(*parts)),
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_digest": status["manifest_digest"],
    }


def _boundary(operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "scope": SCOPE,
        "mutating": False,
        "pairing_verified": False,
        "compatibility_verified": False,
        "ready_for_activation": False,
        "activation_authorized": False,
        "commands_executed": False,
        "live_service_verified": False,
        "proposed_actions": [],
    }


def plan(*, runtime_id: str, home: Path | None = None) -> dict[str, Any]:
    """Inspect both existing records twice and return one non-authorizing digest."""
    system, machine = _supported()
    _hex(runtime_id, "invalid_runtime_id")
    home = storage._home(home)
    deployment = _deployment(home)
    prepared = _runtime(home, runtime_id, system, machine)
    # Detect changes observed across the inspection window. This is NOT a lock,
    # atomic snapshot, ABA protection or hostile-same-user isolation.
    if (deployment != _deployment(home)
            or prepared != _runtime(home, runtime_id, system, machine)
            or (system, machine) != _supported()):
        _fail("changed_during_inspection")
    identity = {
        "schema_version": 1, "scope": SCOPE, "home": str(home),
        "platform": system, "machine": machine,
        "deployment": deployment, "runtime": prepared,
    }
    result = _boundary("deployment-plan")
    result.update(
        ok=True, pairing_verified=True, identity=identity,
        plan_digest=runtime.digest(identity),
        compatibility="not_established", legacy_control_requirement="unknown",
        unresolved_requirements=list(UNRESOLVED),
        message="Static pairing verified only; compatibility and activation remain blocked.",
    )
    return result


def check(*, runtime_id: str, expect_digest: str, home: Path | None = None) -> dict[str, Any]:
    """Reinspect and compare; neither accepting nor saving execution approval."""
    _supported()
    _hex(expect_digest, "invalid_pairing_digest")
    result = plan(runtime_id=runtime_id, home=home)
    if result["plan_digest"] != expect_digest:
        _fail("pairing_changed")
    result.update(operation="deployment-check", review_digest_matches=True)
    return result


def run_command(action: str, *, runtime_id: str, expect_digest: str | None = None,
                home: Path | None = None) -> dict[str, Any]:
    """CLI boundary: fixed error classes, no raw metadata or exception messages."""
    try:
        if action == "deployment-plan":
            return plan(runtime_id=runtime_id, home=home)
        if action == "deployment-check":
            return check(runtime_id=runtime_id, expect_digest=expect_digest, home=home)
        _fail("invalid_pairing_action")
    except PairingError as exc:
        code = str(exc)
    except storage.DeploymentError:
        code = "deployment_or_storage_inspection_failed"
    except runtime.RuntimeErrorCode:
        code = "runtime_inspection_failed"
    except (Exception, KeyboardInterrupt):
        code = "inspection_failed"
    # Never return a partially populated identity/digest after a failed read.
    result = _boundary("deployment-pairing-error")
    result.update(ok=False, error_code=code,
                  message="Inspection failed; no files or services were changed.")
    return result
