"""Measure known legacy launch sources and saved policy, without executing either.

A saved launch contract is not the effective environment of a running process.
Configuration paths are references, not proof that their contents are compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
from typing import Any

from . import deployment as storage
from . import pairing, runtime

SCOPE = "known-http-launch-review-v1"
MAX_SOURCE = 128 * 1024
LEGACY = {
    "run_reasonfirst.sh": "612f1aec7b3f6ef767ca6ca9f86a797528082146a3ea4c965d562da42d686972",
    "run_mcp_server.sh": "5d572d646b6f8292ac84f05ac62c6d398c9c1b9f75969cb9f119492ad591e25b",
    "set_local_no_proxy.sh": "d32e70972a5d098840b1e9b26e6b289fe18df2d46ea58f55f01d7d8eb70f03e6",
    "reasonfirst_mcp_server.py": "e1bf6f077e743fb6ef06986ccd45a8c29d8c4665f7f854232961d585cf28f790",
}
COMPAT = {
    "reasonfirst_mcp_server.py": LEGACY["reasonfirst_mcp_server.py"],
    "github_control_relay.py": "894acb13e1b34b6fca4b550e54f9fe4da0ac03ecff1b4d1213bc870377d3a229",
    "reasonfirst_codex_bridge/__init__.py": "007053f3dedab099d53e22074392135701667ad9b6ef85b3d157ce2e8871f46e",
    "reasonfirst_codex_bridge/controller.py": "27b3661b34b5ec077d1afc940382a96af124fda0b7080ac35d4ff65029466fd4",
    "reasonfirst_codex_bridge/bridge_config.py": "9e21339001c731e2ecc994db2f79ab855494a778ca665204c0f9ad8793ab6b49",
}
# Known v0.5.1 maintainer sidecar input, NOT a template used to install new code.
BOOTSTRAP_SHA256 = "c964f433d5a36bc571deb3c650cf946e669b302e694d86e6fb3c10d7b53deb9d"
TARGET = {
    "bridge_http.py": "457c6a4e465b3a1d1d1032f34a58d9e5e9261ec2c6526ada6474968e7e4d3337",
    "bridge_mcp.py": "8a4a5e6b5166c5a49c6e2c1236d6e35e0da9a375b5d32ac9476fc34598130edc",
}
SIDECAR_HEADER = (
    '#!/bin/bash\nset -euo pipefail\n'
    'export PATH="$HOME/.local/bin:/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
    'unset PYTHONPATH PYTHONHOME PYTHONEXECUTABLE __PYVENV_LAUNCHER__\n'
)
REFERENCES = ("RF_BRIDGE_CONFIG", "GITLAB_AGENT_ENV_FILE", "RF_CODEX_BRIDGE_STATE_DIR", "RF_V4_RUNTIME_DIR")
ALWAYS_UNRESOLVED = (
    "loaded_service_and_effective_environment",
    "current_core_runtime_not_verified",
    "application_configuration_and_state_compatibility",
    "active_work_admission",
    "controlled_activation_and_durable_recovery",
)


class LaunchError(RuntimeError):
    """Fixed codes only; never include parser messages or file values."""


def fail(code: str) -> None:
    raise LaunchError(code)


class UniqueDict(dict):
    def __setitem__(self, key, value):
        if key in self:
            fail("duplicate_plist_key")
        super().__setitem__(key, value)


def _relative(home: Path, value: str) -> Path:
    if not isinstance(value, str) or len(value) > 4096:
        fail("unsupported_path_reference")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        fail("unsupported_path_reference")
    try:
        part = path.relative_to(home)
    except ValueError:
        fail("unsupported_path_reference")
    if not part.parts:
        fail("unsupported_path_reference")
    return part


def _file(home: Path, path: Path, *, limit: int = MAX_SOURCE) -> tuple[bytes, dict[str, Any]]:
    rel = _relative(home, str(path))
    with storage._directory(home, rel.parts[:-1]) as fd:
        if fd is None:
            fail("launch_source_missing")
        before = os.stat(rel.name, dir_fd=fd, follow_symlinks=False)
        raw = storage._read_at(fd, rel.name, limit=limit)
        after = os.stat(rel.name, dir_fd=fd, follow_symlinks=False)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                                 info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
        if raw is None or identity(before) != identity(after):
            fail("launch_source_changed")
    return raw, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                 "bytes": len(raw), "mode": before.st_mode & 0o777}


def _checked(home: Path, path: Path, expected: str) -> dict[str, Any]:
    _, item = _file(home, path)
    if item["sha256"] != expected:
        fail("unknown_launch_source")
    return item


def _sidecar(home: Path, folder: Path) -> tuple[list[dict[str, Any]], str]:
    raw, shell = _file(home, folder / "run_reasonfirst.sh")
    text = raw.decode("utf-8")
    if not text.startswith(SIDECAR_HEADER):
        fail("unknown_sidecar_launcher")
    # Parse data only, then require byte-for-byte canonical shell construction.
    words = shlex.split(text[len(SIDECAR_HEADER):], posix=True)
    if len(words) != 6 or words[0] != "exec" or words[2:4] != ["-I", "-B"] or words[5] != "$@":
        fail("unknown_sidecar_launcher")
    python = words[1]
    _relative(home, python)
    if words[4] != str(folder / "http_bootstrap.py"):
        fail("unknown_sidecar_launcher")
    expected = SIDECAR_HEADER + 'exec ' + shlex.quote(python) + ' -I -B ' + shlex.quote(words[4]) + ' "$@"\n'
    if text != expected:
        fail("unknown_sidecar_launcher")
    files = [shell, _checked(home, folder / "http_bootstrap.py", BOOTSTRAP_SHA256)]
    raw, item = _file(home, folder / "STAGE.json")
    files.append(item)
    manifest = json.loads(raw, object_pairs_hook=runtime.unique)
    if (not isinstance(manifest, dict) or manifest.get("stage_kind") != "reasonfirst-v051-legacy-http-uv" or
            manifest.get("source_commit") != "9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b" or
            manifest.get("uv_python") != python or
            manifest.get("uv_prefix") != str(Path(python).parent.parent)):
        fail("unsupported_sidecar_manifest")
    declared = manifest.get("compatibility_files")
    modules = manifest.get("package_modules")
    if (not isinstance(declared, dict) or set(declared) != set(COMPAT) or
            not isinstance(modules, dict) or not 1 <= len(modules) <= 128):
        fail("unsupported_sidecar_manifest")
    for name, entry in modules.items():
        if (not isinstance(name, str) or
                not re.fullmatch(r"gitlab_agent/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py", name) or
                not isinstance(entry, dict) or set(entry) != {"sha256", "bytes"} or
                not isinstance(entry["sha256"], str) or not runtime.HEX.fullmatch(entry["sha256"]) or
                type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= MAX_SOURCE * 16):
            fail("unsupported_sidecar_manifest")
    for name, signature in COMPAT.items():
        item = _checked(home, folder / "compat" / name, signature)
        entry = declared[name]
        if (not isinstance(entry, dict) or entry.get("sha256") != signature or
                type(entry.get("bytes")) is not int or entry["bytes"] != item["bytes"]):
            fail("unsupported_sidecar_manifest")
        files.append(item)
    # The manifest's asserted package/module hashes are not used as current-core
    # attestation. No path found only in that manifest is opened or executed.
    return files, python


def _sources(home: Path, registration: dict[str, Any]) -> dict[str, Any]:
    program = Path(registration["program"])
    if registration["layout"] == "legacy_staged_http":
        files = [_checked(home, program.parent / name, sig) for name, sig in LEGACY.items()]
        profile = "staged-http-v051-launcher"
        python = None  # RF_V4_RUNTIME_DIR may be inherited, not just in the plist.
    elif registration["layout"] == "versioned_http_sidecar":
        files, python = _sidecar(home, program.parent)
        profile = "isolated-http-v051-sidecar"
    else:
        fail("unsupported_launcher_layout")
    if not files[0]["mode"] & 0o100:
        fail("launcher_not_executable")
    return {"profile": profile, "files": files, "encoded_python": python,
            "core_runtime_verified": False,
            "legacy_launch_modifies_manager_no_proxy": profile == "staged-http-v051-launcher"}


def _boolean(env: dict[str, str], key: str) -> bool | None:
    if key not in env:
        return None
    value = env[key].strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    fail("invalid_saved_policy")


def _saved_contract(home: Path, raw: bytes) -> dict[str, Any]:
    data = plistlib.loads(raw, dict_type=UniqueDict)
    env = data.get("EnvironmentVariables", {})
    if (not isinstance(env, dict) or len(env) > 256 or
            any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items())):
        fail("invalid_saved_environment")
    if env.get("HOME", str(home)) != str(home):
        fail("home_override_mismatch")
    # These could run code before our measured scripts; never infer their effect.
    injected = any(env.get(k) for k in ("BASH_ENV", "ENV", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"))
    port = None
    if "RF_MCP_PORT" in env:
        value = env["RF_MCP_PORT"]
        if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 65535:
            fail("invalid_saved_port")
        port = int(value)
    read_only = _boolean(env, "RF_MCP_READ_ONLY")
    push = _boolean(env, "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH")
    references = {}
    for name in REFERENCES:
        value = env.get(name)
        if value is not None:
            _relative(home, value)
        references[name] = {"path": value, "source": "saved_registration" if value is not None else "not_recorded",
                            "contents_read": False, "effective_binding_verified": False}
    return {
        "environment_source": "saved_registration_not_running_environment",
        "endpoint": {"host": "127.0.0.1", "path": "/mcp", "port": port,
                     "port_source": "saved_registration" if port is not None else "not_recorded"},
        "mode": None if read_only is None else "read-only" if read_only else "full-chat",
        "control_route": "unknown" if read_only is None else "absent" if read_only else "present",
        "remote_push": False if read_only is True else push if read_only is False else None,
        "configuration_references": references,
        "prelaunch_code_override_present": injected,
        "python_override_recorded": any(env.get(k) for k in ("PYTHONPATH", "PYTHONHOME")),
        "unrecorded_values_may_be_inherited": True,
    }


def _target(home: Path, identity: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(identity["runtime_path"])
    raw, _ = _file(home, root / "runtime.json", limit=runtime.MAX_MANIFEST)
    if hashlib.sha256(raw).hexdigest() != identity["record_sha256"]:
        fail("runtime_record_changed")
    record = json.loads(raw, object_pairs_hook=runtime.unique)
    origins = record["observation"]["origins"]
    paths = [p for p in origins if isinstance(p, str) and p.endswith("/gitlab_agent/bridge_http.py")]
    if len(paths) != 1:
        fail("target_profile_not_supported")
    rel = Path(paths[0])
    if rel.is_absolute() or ".." in rel.parts or rel.parts[0] != "lib":
        fail("target_profile_not_supported")
    package = root / "venv" / rel.parent
    files = []
    for name, sig in TARGET.items():
        _, item = _file(home, package / name)
        if item["sha256"] != sig:
            fail("target_profile_not_supported")
        files.append(item)
    return files


def _observe(home: Path, pair: dict[str, Any]) -> dict[str, Any]:
    snap = pair["identity"]["deployment"]["registration"]
    raw = storage._registration_bytes(home)
    if hashlib.sha256(raw).hexdigest() != snap["registration_sha256"]:
        fail("registration_changed")
    source = _sources(home, snap)
    contract = _saved_contract(home, raw)
    return {"source": source, "saved_contract": contract,
            "target_http_files": _target(home, pair["identity"]["runtime"])}


def _result() -> dict[str, Any]:
    return {"scope": SCOPE, "mutating": False, "commands_executed": False,
            "configuration_contents_read": False, "workspace_state_read": False,
            "loaded_service_inspected": False, "compatibility_verified": False,
            "activation_authorized": False, "ready_for_activation": False,
            "proposed_actions": []}


def plan(*, runtime_id: str, expect_pairing_digest: str, home: Path | None = None) -> dict[str, Any]:
    pair = pairing.check(runtime_id=runtime_id, expect_digest=expect_pairing_digest, home=home)
    home = storage._home(home)
    evidence = _observe(home, pair)
    if (evidence != _observe(home, pair) or pair != pairing.check(
            runtime_id=runtime_id, expect_digest=expect_pairing_digest, home=home)):
        fail("launch_inputs_changed")
    contract = evidence["saved_contract"]
    blockers = list(ALWAYS_UNRESOLVED)
    if contract["control_route"] == "present":
        blockers.append("legacy_control_not_supported_by_target")
    elif contract["control_route"] == "unknown":
        blockers.append("legacy_control_policy_unresolved")
    if contract["remote_push"] is not False:
        blockers.append("remote_push_unsupported_or_unresolved")
    if contract["endpoint"]["port"] is None:
        blockers.append("port_not_explicit_in_saved_registration")
    if contract["prelaunch_code_override_present"]:
        blockers.append("prelaunch_code_override_requires_review")
    if contract["python_override_recorded"] and evidence["source"]["profile"] == "staged-http-v051-launcher":
        blockers.append("legacy_python_import_override_requires_review")
    identity = {"schema_version": 1, "scope": SCOPE, "pairing_digest": expect_pairing_digest,
                "evidence": evidence}
    return {**_result(), "ok": True, "operation": "launch-plan",
            "launcher_sources_verified": True, "identity": identity,
            "plan_digest": runtime.digest(identity), "blockers": blockers,
            "static_control_compatible": contract["control_route"] == "absent",
            "message": "Known launch sources inspected; blockers are not activation permission."}


def run_command(action: str, *, runtime_id: str, expect_pairing_digest: str,
                expect_digest: str | None = None, home: Path | None = None) -> dict[str, Any]:
    try:
        if action not in {"launch-plan", "launch-check"}:
            fail("invalid_launch_action")
        if action == "launch-check" and (not isinstance(expect_digest, str) or not runtime.HEX.fullmatch(expect_digest)):
            fail("invalid_launch_digest")
        result = plan(runtime_id=runtime_id, expect_pairing_digest=expect_pairing_digest, home=home)
        if action == "launch-check":
            if result["plan_digest"] != expect_digest:
                fail("launch_review_changed")
            result.update(operation=action, review_digest_matches=True)
        return result
    except LaunchError as exc:
        code = str(exc)
    except pairing.PairingError:
        code = "pairing_not_verified"
    except (storage.DeploymentError, runtime.RuntimeErrorCode):
        code = "storage_or_runtime_inspection_failed"
    except (Exception, KeyboardInterrupt):
        code = "launch_inspection_failed"
    return {**_result(), "ok": False, "operation": "launch-review-error", "error_code": code,
            "message": "Launch review failed; no files, settings or services changed."}
