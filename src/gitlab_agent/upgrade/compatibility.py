"""Read-only assessment of known saved legacy HTTP launch sources and policy.

A saved registration is not a loaded service. This code never executes inspected
sources, loads credential files, constructs a controller or authorizes activation.
Only an explicitly fingerprinted legacy source profile is interpreted; arbitrary
shell/Python and one-off sidecars are rejected rather than evaluated or guessed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
from typing import Any

from . import deployment as storage, pairing, runtime

SCOPE = "saved-legacy-http-compatibility-assessment-v1"
MAX_SOURCE = 128 * 1024
MAX_CONFIG = 64 * 1024
PROFILE_COMMIT = "9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b"
# Source paths in the historical installed v4-service layout, not repo paths.
# Test against the retained tools/reasonfirst_v4_0_3 sources to catch drift.
LAUNCH_FILES = {
    "run_reasonfirst.sh": "612f1aec7b3f6ef767ca6ca9f86a797528082146a3ea4c965d562da42d686972",
    "run_mcp_server.sh": "5d572d646b6f8292ac84f05ac62c6d398c9c1b9f75969cb9f119492ad591e25b",
    "set_local_no_proxy.sh": "d32e70972a5d098840b1e9b26e6b289fe18df2d46ea58f55f01d7d8eb70f03e6",
    "reasonfirst_mcp_server.py": "e1bf6f077e743fb6ef06986ccd45a8c29d8c4665f7f854232961d585cf28f790",
}
IMPLEMENTATIONS = {
    "controller.py": "4a7163fff97e7bf7f5c5fcb01665ca1c43af410392a8c19867b950f2313d8e3f",
    "bridge_config.py": "d302ea8c67a49c996df972512449c7fee2a8cedd0b1cd0f734c6cfe41bfb3f06",
}
# Filled from exact retained compatibility aliases, never from user metadata.
ALIASES = {'controller.py': '27b3661b34b5ec077d1afc940382a96af124fda0b7080ac35d4ff65029466fd4', 'bridge_config.py': '9e21339001c731e2ecc994db2f79ab855494a778ca665204c0f9ad8793ab6b49'}
TARGET_FILES = {
    "bridge_http.py": "457c6a4e465b3a1d1d1032f34a58d9e5e9261ec2c6526ada6474968e7e4d3337",
    "bridge_mcp.py": "8a4a5e6b5166c5a49c6e2c1236d6e35e0da9a375b5d32ac9476fc34598130edc",
    "bridge_preview/bridge_config.py": IMPLEMENTATIONS["bridge_config.py"],
    "bridge_preview/controller.py": IMPLEMENTATIONS["controller.py"],
}
UNRESOLVED = (
    "saved_registration_vs_loaded_service",
    "inherited_manager_and_process_environment",
    "full_import_closure_and_running_interpreter",
    "live_endpoint_authentication_and_listener_ownership",
    "target_state_and_workspace_compatibility",
    "active_work_admission",
    "controlled_activation_and_durable_recovery",
)


class AssessmentError(RuntimeError):
    """Fixed code only; never reflect inspected source/configuration contents."""


def fail(code: str) -> None:
    raise AssessmentError(code)


def _read(home: Path, path: Path, *, private: bool = False,
          limit: int = MAX_SOURCE) -> bytes | None:
    try:
        parts = path.relative_to(home).parts
    except ValueError:
        fail("unsupported_reference_path")
    if not parts or any(p in {".", ".."} for p in parts):
        fail("unsupported_reference_path")
    with storage._directory(home, parts[:-1]) as fd:
        return None if fd is None else storage._read_at(fd, parts[-1], limit=limit, private=private)


def _known(home: Path, path: Path, expected: str, hashes: dict[str, str]) -> None:
    raw = _read(home, path)
    if raw is None:
        fail("launch_source_missing")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        fail("unrecognized_launch_source")
    hashes[str(path.relative_to(home))] = actual


def _sources(home: Path, registration: dict[str, Any]) -> dict[str, str]:
    if registration["layout"] != "legacy_staged_http":
        fail("unsupported_launcher_profile")
    root = home / ".local/share/reasonfirst/v4-service"
    tools = root / "tools/codex_web_bridge"
    hashes: dict[str, str] = {}
    for name, expected in LAUNCH_FILES.items():
        _known(home, tools / name, expected, hashes)
    for name, expected in IMPLEMENTATIONS.items():
        path = tools / "reasonfirst_codex_bridge" / name
        raw = _read(home, path)
        if raw is None:
            fail("launch_source_missing")
        actual = hashlib.sha256(raw).hexdigest()
        if actual == ALIASES[name]:
            _known(home, root / "src/gitlab_agent/bridge_preview" / name, expected, hashes)
        elif actual != expected:
            fail("unrecognized_launch_source")
        hashes[str(path.relative_to(home))] = actual
    return hashes


def _path(home: Path, value: object, default: Path) -> str:
    if value is None:
        return str(default)
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("unsupported_reference_path")
    if value.startswith("~/"):
        value = str(home) + value[1:]
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(home):
        fail("unsupported_reference_path")
    return str(path)


def _bool(env: dict[str, str], name: str) -> bool:
    value = env.get(name, "false").strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    # The legacy source is permissive. Do not treat a typo as safe false.
    fail("invalid_saved_policy")


class _UniquePlistDict(dict):
    def __init__(self, pairs=()):
        super().__init__()
        for key, value in pairs:
            self[key] = value

    def __setitem__(self, key, value):
        if key in self:
            fail("duplicate_saved_plist_key")
        super().__setitem__(key, value)


def _saved_policy(home: Path, raw: bytes) -> dict[str, Any]:
    data = plistlib.loads(raw, dict_type=_UniquePlistDict)
    env = data.get("EnvironmentVariables", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        fail("invalid_saved_environment")
    if "HOME" in env and env["HOME"] != str(home):
        fail("saved_home_conflict")
    # These can execute/shadow code before the known launch sources take effect.
    if any(env.get(k) for k in ("BASH_ENV", "ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                                "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__", "DYLD_INSERT_LIBRARIES")):
        fail("unsupported_saved_code_override")
    raw_port = env.get("RF_MCP_PORT") or "8765"  # known shell uses ${...:-8765}
    if re.fullmatch(r"[0-9]{1,5}", raw_port) is None or not 1 <= int(raw_port) <= 65535:
        fail("invalid_saved_endpoint")
    read_only = _bool(env, "RF_MCP_READ_ONLY")
    remote_push = _bool(env, "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH")
    bridge = _path(home, env.get("RF_BRIDGE_CONFIG"), home / ".config/reasonfirst/bridge.yaml")
    if Path(bridge).suffix not in {".yaml", ".yml"}:
        fail("unsupported_bridge_config_path")
    state = _path(home, env.get("RF_CODEX_BRIDGE_STATE_DIR"), home / ".local/share/reasonfirst/codex-web-bridge")
    old_runtime = _path(home, env.get("RF_V4_RUNTIME_DIR") or None, home / ".local/share/reasonfirst/v4-runtime")
    gitlab = None if "GITLAB_AGENT_ENV_FILE" not in env else _path(home, env["GITLAB_AGENT_ENV_FILE"], home)
    return {
        "scope": "saved-plist-and-known-source-defaults-not-process-environment",
        "endpoint": {"transport": "streamable-http", "host": "127.0.0.1", "port": int(raw_port), "path": "/mcp"},
        "mode": "read-only" if read_only else "full-chat",
        "legacy_control_route_exposed": not read_only,
        "remote_push_environment_enabled": remote_push,
        "remote_push_tool_exposed": remote_push and not read_only,
        "policy_origins": {k: "saved_plist" if k in env else "source_default_if_not_inherited"
                           for k in ("RF_MCP_READ_ONLY", "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", "RF_MCP_PORT",
                                     "RF_BRIDGE_CONFIG", "RF_CODEX_BRIDGE_STATE_DIR", "RF_V4_RUNTIME_DIR")},
        "references": {"bridge_config": bridge, "bridge_state": state,
                       "legacy_python": str(Path(old_runtime) / ".venv/bin/python"),
                       "explicit_gitlab_config": gitlab,
                       "gitlab_lookup": "explicit_then_canonical_then_cwd_not_inspected"},
        "launcher_sets_manager_no_proxy": True,
    }


def _config(home: Path, path: str) -> dict[str, Any]:
    raw = _read(home, Path(path), private=True, limit=MAX_CONFIG)
    if raw is None:
        return {"path": path, "exists": False, "sha256": None,
                "schema_version": None, "schema_status": "absent_legacy_defaults_not_validated"}
    # Compose data nodes, never instantiate custom YAML objects, expand aliases,
    # load an application controller or echo a parser exception. Limit depth/nodes.
    import yaml
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode
    from yaml.tokens import AliasToken, AnchorToken
    try:
        if any(isinstance(t, (AliasToken, AnchorToken)) for t in yaml.scan(raw)):
            fail("unsupported_bridge_config")
        node = yaml.compose(raw, Loader=yaml.SafeLoader)
        count = 0
        def visit(n, depth=0):
            nonlocal count
            count += 1
            if count > 2000 or depth > 20:
                fail("unsupported_bridge_config")
            if isinstance(n, MappingNode):
                if n.tag != "tag:yaml.org,2002:map":
                    fail("unsupported_bridge_config")
                out = {}
                for key, value in n.value:
                    if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in out:
                        fail("unsupported_bridge_config")
                    out[key.value] = visit(value, depth + 1)
                return out
            if isinstance(n, SequenceNode):
                if n.tag != "tag:yaml.org,2002:seq":
                    fail("unsupported_bridge_config")
                return [visit(v, depth + 1) for v in n.value]
            if not isinstance(n, ScalarNode) or n.tag not in {
                "tag:yaml.org,2002:str", "tag:yaml.org,2002:int", "tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"}:
                fail("unsupported_bridge_config")
            if n.tag.endswith(":str"):
                return n.value
            if n.tag.endswith(":int") and re.fullmatch(r"[-+]?[0-9]+", n.value):
                return int(n.value, 10)
            if n.tag.endswith(":bool") and n.value.lower() in {"true", "yes", "on", "false", "no", "off"}:
                return n.value.lower() in {"true", "yes", "on"}
            if n.tag.endswith(":null") and n.value.lower() in {"", "null", "~"}:
                return None
            fail("unsupported_bridge_config")
        data = visit(node)
        if not isinstance(data, dict) or set(data) - {"version", "control", "defaults", "targets"}:
            fail("unsupported_bridge_config")
        version = data.get("version", 3)
        if type(version) is not int or version not in {3, 4}:
            fail("unsupported_bridge_config")
        if any(not isinstance(data.get(k, {}), dict) for k in ("control", "defaults", "targets")):
            fail("unsupported_bridge_config")
    except AssessmentError:
        raise
    except Exception:
        fail("unsupported_bridge_config")
    # Only top-level source schema is checked, not target policies/state validity.
    return {"path": path, "exists": True, "sha256": hashlib.sha256(raw).hexdigest(),
            "schema_version": version, "schema_status": "known_top_level_shape",
            "target_count": len(data.get("targets", {})),
            "legacy_control_config_present": bool(data.get("control")),
            "target_semantics_verified": False}


def _target(home: Path, identity: dict[str, Any]) -> dict[str, Any]:
    rid = identity["runtime"]["runtime_id"]
    raw = pairing._read(home, runtime.PARTS + (rid,), "runtime.json", runtime.MAX_MANIFEST)
    if raw is None or hashlib.sha256(raw).hexdigest() != identity["runtime"]["record_sha256"]:
        fail("changed_during_assessment")
    record = json.loads(raw, object_pairs_hook=runtime.unique)
    checked = {}
    # pairing has just checked the complete recorded tree and interpreter.
    # Use its file hashes, not a guessed module location or a version string.
    for suffix, expected in TARGET_FILES.items():
        pattern = re.compile(r"lib/python3\.[0-9]+/site-packages/gitlab_agent/" + re.escape(suffix) + r"\Z")
        rows = [row for row in record["tree"] if pattern.fullmatch(row[0])]
        if len(rows) != 1 or len(rows[0]) != 4 or rows[0][1] != "file" or rows[0][3] != expected:
            fail("unrecognized_target_http_profile")
        checked[rows[0][0]] = expected
    return {"source_hashes": checked, "profile": "packaged-http-no-control-v1",
            "legacy_control_route_supported": False, "remote_push_supported": False,
            "explicit_endpoint_and_mode_required": True}


def _boundary() -> dict[str, Any]:
    return {"operation": "deployment-assessment", "scope": SCOPE, "ok": False,
            "mutating": False, "assessment_completed": False, "compatibility_verified": False,
            "activation_authorized": False, "ready_for_activation": False, "live_service_verified": False,
            "commands_executed": False, "credential_store_files_read": False, "workspace_state_read": False,
            "proposed_actions": []}


def assess(*, runtime_id: str, home: Path | None = None) -> dict[str, Any]:
    before = pairing.plan(runtime_id=runtime_id, home=home)
    identity = before["identity"]
    selected = Path(identity["home"])
    registration = identity["deployment"]["registration"]
    raw = storage._registration_bytes(selected)
    if hashlib.sha256(raw).hexdigest() != registration["registration_sha256"]:
        fail("changed_during_assessment")
    sources = _sources(selected, registration)
    policy = _saved_policy(selected, raw)
    config = _config(selected, policy["references"]["bridge_config"])
    target = _target(selected, identity)
    if (raw != storage._registration_bytes(selected) or sources != _sources(selected, registration)
            or config != _config(selected, policy["references"]["bridge_config"])
            or before != pairing.plan(runtime_id=runtime_id, home=selected)):
        fail("changed_during_assessment")
    blockers = []
    if policy["legacy_control_route_exposed"]:
        blockers.append("legacy_control_route_not_supported_by_target")
    if policy["remote_push_environment_enabled"]:
        blockers.append("experimental_remote_push_environment_rejected_by_target")
    evidence = {"scope": SCOPE, "pairing_digest": before["plan_digest"], "home": str(selected),
                "known_legacy_profile_commit": PROFILE_COMMIT, "source_hashes": sources,
                "saved_policy": policy, "bridge_config": config, "target_http_profile": target}
    return dict(_boundary(), ok=True, assessment_completed=True, evidence=evidence,
                assessment_digest=runtime.digest(evidence), blockers=blockers,
                declared_surface_match=not blockers,
                assessment_status="blocked_by_declared_policy" if blockers else "requires_live_compatibility_checks",
                unresolved_requirements=list(UNRESOLVED),
                message="Saved launch sources and policy assessed only; never activation permission.")


def run_command(action: str, *, runtime_id: str, expect_digest: str | None = None,
                home: Path | None = None) -> dict[str, Any]:
    try:
        pairing._supported()  # reject unsupported OS before HOME/file access
        if action not in {"deployment-assess", "deployment-assess-check"}:
            fail("invalid_assessment_action")
        if action.endswith("-check") and (not isinstance(expect_digest, str) or not runtime.HEX.fullmatch(expect_digest)):
            fail("invalid_assessment_digest")
        result = assess(runtime_id=runtime_id, home=home)
        if action.endswith("-check"):
            if result["assessment_digest"] != expect_digest:
                fail("assessment_changed")
            result["review_digest_matches"] = True
        result["operation"] = action
        return result
    except AssessmentError as exc:
        code = str(exc)
    except pairing.PairingError as exc:
        code = str(exc)
    except storage.DeploymentError:
        code = "deployment_or_storage_inspection_failed"
    except runtime.RuntimeErrorCode:
        code = "runtime_inspection_failed"
    except (Exception, KeyboardInterrupt):
        code = "assessment_failed"
    return dict(_boundary(), error_code=code,
                message="Assessment stopped without returning partial evidence or changing files/services.")
