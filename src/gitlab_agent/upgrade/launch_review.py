"""Read-only review of a known legacy HTTP launcher and saved policy.

This is deliberately NOT a migration transaction. It does not contact launchd,
read credentials/configuration/workspaces, construct controllers, or start code.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
from typing import Any

from . import deployment, pairing, runtime

SCOPE = "saved-http-launch-review-v1"
SOURCE_LIMIT = 128 * 1024
KNOWN_LEGACY_BLOBS = {
    "run_reasonfirst.sh": "3cd07b50988e09ad8408c8ff56fc5e7c4fb37175",
    "run_mcp_server.sh": "18c4abe674d77face579dbe5803810699ced9a3b",
    "set_local_no_proxy.sh": "b23bbffbdcd8e83325d05c443007dbb0b48f9180",
    "reasonfirst_mcp_server.py": "4668a14aaef180d3aeb109a848bb7532f3095309",
}
KNOWN_TARGET_BLOBS = {
    "bridge_http.py": "0e4ba620f267a3d32c0027dd006c531a090129e4",
    "bridge_mcp.py": "fd3e41f6e4cc26f2944a23734c249b3f40f3d235",
}
UNRESOLVED = (
    "loaded_launchd_definition_and_effective_environment",
    "running_core_and_dependency_identity",
    "application_configuration_and_state_schema",
    "active_work_and_approval_admission",
    "controlled_switch_and_durable_recovery",
)
CODES = frozenset({
    "unsupported_platform", "invalid_review_digest", "pairing_not_verified",
    "saved_registration_changed", "invalid_saved_plist", "invalid_saved_policy",
    "invalid_saved_port", "known_launcher_modified", "legacy_launcher_unverified",
    "target_http_profile_unknown", "target_record_changed",
    "launch_input_changed", "launch_review_changed", "invalid_launch_action",
})


class LaunchReviewError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code if code in CODES else "launch_inspection_failed")


def fail(code: str) -> None:
    raise LaunchReviewError(code)


def _git_blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _read_named(home: Path, path: Path, *, limit: int = SOURCE_LIMIT) -> tuple[bytes, dict[str, Any]]:
    """Only a path already derived from known home-scoped layout can be read."""
    try:
        relative = path.relative_to(home)
    except ValueError:
        fail("legacy_launcher_unverified")
    if (not relative.parts or ".." in relative.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in str(relative))):
        fail("legacy_launcher_unverified")
    with deployment._directory(home, relative.parts[:-1]) as fd:
        if fd is None:
            fail("legacy_launcher_unverified")
        raw = deployment._read_at(fd, relative.name, limit=limit)
        if raw is None:
            fail("legacy_launcher_unverified")
    return raw, {"path": str(path), "blob": _git_blob(raw), "bytes": len(raw)}


def _verify_blob(home: Path, path: Path, expected: str) -> dict[str, Any]:
    _, evidence = _read_named(home, path)
    if evidence["blob"] != expected:
        fail("known_launcher_modified")
    return evidence


class _Unique(dict):
    def __setitem__(self, key: object, value: object) -> None:
        if key in self:
            fail("invalid_saved_plist")
        super().__setitem__(key, value)


def _saved_policy(raw: bytes) -> dict[str, Any]:
    try:
        data = plistlib.loads(raw, dict_type=_Unique)
        if not isinstance(data, dict) or data.get("Label") != deployment.LABEL:
            fail("invalid_saved_plist")
        env = data.get("EnvironmentVariables", {})
        if (not isinstance(env, dict) or len(env) > 256 or
                any(not isinstance(k, str) or not isinstance(v, str)
                    for k, v in env.items())):
            fail("invalid_saved_plist")
    except LaunchReviewError:
        raise
    except Exception:
        fail("invalid_saved_plist")

    def boolean(name: str) -> bool | None:
        if name not in env:
            return None
        value = env[name].strip().lower()
        if value in ("true", "1", "yes", "on"):
            return True
        if value in ("false", "0", "no", "off"):
            return False
        fail("invalid_saved_policy")

    readonly = boolean("RF_MCP_READ_ONLY")
    push = boolean("RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH")
    port: int | None = None
    if "RF_MCP_PORT" in env:
        supplied = env["RF_MCP_PORT"]
        if not re.fullmatch(r"[0-9]{1,5}", supplied) or not 1 <= int(supplied) <= 65535:
            fail("invalid_saved_port")
        port = int(supplied)
    # Do not return any environment value other than the above bounded policy
    # fields. Referenced configuration paths are NOT followed or authenticated.
    config_refs = ("RF_BRIDGE_CONFIG", "GITLAB_AGENT_ENV_FILE",
                   "RF_CODEX_BRIDGE_STATE_DIR", "RF_V4_RUNTIME_DIR")
    return {
        "source": "saved_plist_not_running_process",
        "endpoint": {"host": "127.0.0.1", "path": "/mcp", "port": port,
                     "port_source": "saved" if port is not None else "not_recorded"},
        "mode": "read-only" if readonly is True else "full-chat" if readonly is False else "unknown",
        "legacy_control": "absent" if readonly is True else "required" if readonly is False else "unknown",
        "remote_push": False if readonly is True else push,
        "configuration_reference_presence": {name: name in env for name in config_refs},
        "prelaunch_code_override_recorded": any(env.get(name) for name in
            ("BASH_ENV", "ENV", "DYLD_INSERT_LIBRARIES", "PYTHONPATH", "PYTHONHOME")),
        "inherited_environment_not_inspected": True,
        "configuration_contents_read": False,
    }


def _launcher(home: Path, registration: dict[str, Any]) -> dict[str, Any]:
    source_root = home / ".local/share/reasonfirst/v4-service/tools/codex_web_bridge"
    if registration["layout"] == "legacy_staged_http":
        if registration["program"] != str(source_root / "run_reasonfirst.sh"):
            fail("legacy_launcher_unverified")
        files = [_verify_blob(home, source_root / name, blob)
                 for name, blob in KNOWN_LEGACY_BLOBS.items()]
        return {"layout": "legacy_staged_http", "source_profile": "reviewed-v051-legacy",
                "verified_files": files, "fully_verified": True,
                "old_imported_core_verified": False}
    if registration["layout"] == "versioned_http_sidecar":
        # This old user's sidecar may contain generated bootstrap code that is
        # absent from the repository's audited source profiles. Until that exact
        # bootstrap is reviewed, do not classify it as known/restart-compatible.
        return {"layout": "versioned_http_sidecar",
                "source_profile": "not_audited_in_this_slice",
                "verified_files": [], "fully_verified": False,
                "old_imported_core_verified": False}
    fail("legacy_launcher_unverified")


def _target(home: Path, pair: dict[str, Any]) -> dict[str, Any]:
    info = pair["identity"]["runtime"]
    root = Path(info["runtime_path"])
    raw, record_data = _read_named(home, root / "runtime.json", limit=runtime.MAX_MANIFEST)
    if record_data["bytes"] > runtime.MAX_MANIFEST or hashlib.sha256(raw).hexdigest() != info["record_sha256"]:
        fail("target_record_changed")
    record = json.loads(raw, object_pairs_hook=runtime.unique)
    origin = record["observation"]["origins"]
    paths = [name for name in origin if isinstance(name, str)
             and re.fullmatch(r"lib/python3\.[0-9]+/site-packages/gitlab_agent/bridge_http\.py", name)]
    if len(paths) != 1:
        fail("target_http_profile_unknown")
    folder = root / "venv" / Path(paths[0]).parent
    files = [_verify_blob(home, folder / name, blob)
             for name, blob in KNOWN_TARGET_BLOBS.items()]
    return {"profile": "reviewed-packaged-http-no-control", "files": files,
            "control_route_exposed": False, "verified_known_source_files": True,
            "dependency_closure_or_runtime_load_verified": False}


def _observe(home: Path, pair: dict[str, Any]) -> dict[str, Any]:
    registration = pair["identity"]["deployment"]["registration"]
    raw = deployment._registration_bytes(home)
    if hashlib.sha256(raw).hexdigest() != registration["registration_sha256"]:
        fail("saved_registration_changed")
    return {"launcher": _launcher(home, registration),
            "saved_policy": _saved_policy(raw), "target": _target(home, pair)}


def _boundary(operation: str) -> dict[str, Any]:
    return {"operation": operation, "scope": SCOPE,
            "mutating": False, "commands_executed": False,
            "application_configuration_read": False, "workspace_state_read": False,
            "loaded_service_inspected": False, "compatibility_verified": False,
            "activation_authorized": False, "ready_for_activation": False,
            "proposed_actions": []}


def plan(*, runtime_id: str, expect_pairing_digest: str,
         home: Path | None = None) -> dict[str, Any]:
    first = pairing.check(runtime_id=runtime_id, expect_digest=expect_pairing_digest, home=home)
    selected = deployment._home(home)
    initial = _observe(selected, first)
    if (initial != _observe(selected, first) or
            first != pairing.check(runtime_id=runtime_id,
                                   expect_digest=expect_pairing_digest, home=home)):
        fail("launch_input_changed")
    policy, launcher = initial["saved_policy"], initial["launcher"]
    blockers = list(UNRESOLVED)
    if not launcher["fully_verified"]:
        blockers.append("sidecar_bootstrap_and_imports_not_audited")
    if policy["legacy_control"] == "required":
        blockers.append("legacy_control_required_but_target_has_no_control")
    elif policy["legacy_control"] == "unknown":
        blockers.append("legacy_control_requirement_unknown")
    if policy["remote_push"] is not False:
        blockers.append("remote_push_unsupported_or_unknown")
    if policy["endpoint"]["port"] is None:
        blockers.append("saved_listener_port_unknown")
    if policy["prelaunch_code_override_recorded"]:
        blockers.append("prelaunch_override_requires_review")
    identity = {"schema_version": 1, "scope": SCOPE,
                "pairing_digest": expect_pairing_digest, "evidence": initial}
    result = _boundary("launch-plan")
    result.update(ok=True, identity=identity, plan_digest=runtime.digest(identity),
                  launcher_source_verified=launcher["fully_verified"],
                  target_source_verified=True,
                  saved_control_policy_compatible=(policy["legacy_control"] == "absent"),
                  compatibility="not_established", blockers=blockers,
                  message="Read-only source/policy review; no activation permission.")
    return result


def check(*, runtime_id: str, expect_pairing_digest: str, expect_digest: str,
          home: Path | None = None) -> dict[str, Any]:
    if not isinstance(expect_digest, str) or not runtime.HEX.fullmatch(expect_digest):
        fail("invalid_review_digest")
    result = plan(runtime_id=runtime_id, expect_pairing_digest=expect_pairing_digest, home=home)
    if result["plan_digest"] != expect_digest:
        fail("launch_review_changed")
    result.update(operation="launch-check", review_digest_matches=True)
    return result


def run_command(action: str, *, runtime_id: str, expect_pairing_digest: str,
                expect_digest: str | None = None, home: Path | None = None) -> dict[str, Any]:
    try:
        if action == "launch-plan":
            return plan(runtime_id=runtime_id, expect_pairing_digest=expect_pairing_digest, home=home)
        if action == "launch-check":
            return check(runtime_id=runtime_id, expect_pairing_digest=expect_pairing_digest,
                         expect_digest=expect_digest, home=home)
        fail("invalid_launch_action")
    except LaunchReviewError as exc:
        code = str(exc)
    except pairing.PairingError:
        code = "pairing_not_verified"
    except (deployment.DeploymentError, runtime.RuntimeErrorCode):
        code = "storage_or_runtime_inspection_failed"
    except (Exception, KeyboardInterrupt):
        code = "launch_inspection_failed"
    result = _boundary("launch-review-error")
    result.update(ok=False, error_code=code,
                  message="Review failed; no files or services were changed.")
    return result
