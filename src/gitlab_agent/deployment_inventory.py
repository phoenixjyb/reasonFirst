"""Bounded static deployment evidence. Discovery is NOT adoption or live health.

No process, HTTP, provider, controller or package-manager calls. Inspect only the
two known user LaunchAgents on macOS; all other service managers are not inspected.
Never follow its arbitrary argv/env into scripts, credentials or network URLs.
"""
from __future__ import annotations

from pathlib import Path
import plistlib
import re
import stat
import sys

from . import __version__
from .static_service_files import read_bounded_regular
from .setup_state import SetupState

MAX_PLIST_BYTES = 128 * 1024
LABEL = "com.reasonfirst.v4-mcp"
TUNNEL_LABEL = "com.reasonfirst.v4-tunnel"


def _safe_location(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or not Path(value).is_absolute():
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def _marker(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "not_found"
    except OSError:
        return "unreadable"
    return "present" if stat.S_ISREG(info.st_mode) else "unsupported_layout"


class _UniquePlistDict(dict):
    def __setitem__(self, key: object, value: object) -> None:
        if key in self:
            raise ValueError("duplicate plist key")
        super().__setitem__(key, value)


def _read_definition(home: Path, label: str):
    path = home / "Library/LaunchAgents" / (label + ".plist")
    read = read_bounded_regular(path, max_bytes=MAX_PLIST_BYTES, anchor=home)
    value = None
    if read.status == "ok":
        try:
            if read.data is None:
                raise ValueError("missing bytes")
            value = plistlib.loads(read.data, dict_type=_UniquePlistDict)
            if not isinstance(value, dict):
                raise ValueError("invalid plist root")
        except Exception:
            return {"status": "invalid", "value": None, "read": read}
        if value.get("Label") != label:
            return {"status": "unsupported_layout", "value": None, "read": read}
    status = {"ok": "discovered_legacy", "missing": "not_found",
              "unsupported": "unsupported_layout"}.get(read.status, read.status)
    return {"status": status, "value": value, "read": read}


def _definition_evidence(role: str, evidence: dict[str, object]) -> dict[str, object]:
    status = evidence["status"]
    identity = "not_observed"
    if evidence["status"] == "discovered_legacy":
        identity = "known_label_matches"
    return {
        "kind": "service_definition_file",
        "role": role,
        "source": "known_user_launch_agent_path",
        "scope": "static_file_only",
        "status": status,
        "identity": identity,
        "definition_file_owner": (
            "verified_static_metadata_and_label_not_runtime"
            if status == "discovered_legacy" else "unobserved"
        ),
        "reader_guarantees": list(evidence["read"].guarantees),
        "reader_reason": evidence["read"].reason,
        "runtime_owner": "unobserved",
    }


def _definition_not_inspected(role: str) -> dict[str, object]:
    return {
        "kind": "service_definition_file",
        "role": role,
        "source": "platform_adapter_unavailable",
        "scope": "static_file_only",
        "status": "not_inspected",
        "identity": "not_observed",
        "definition_file_owner": "unobserved",
        "reader_guarantees": [],
        "reader_reason": None,
        "runtime_owner": "unobserved",
    }


def _launch_agent(home: Path, evidence: dict[str, object] | None = None) -> dict[str, object]:
    path = home / "Library/LaunchAgents" / (LABEL + ".plist")
    report: dict[str, object] = {
        "kind": "launchd_registration", "path": str(path), "label": LABEL,
        "status": "not_found", "ownership_verified": False,
        "ownership_verified_scope": "static_file_metadata_and_label_only",
        "layout": None, "transport": None, "transport_basis": None,
        "running_version": None, "process_status": "not_inspected",
        "health": "not_inspected",
    }
    evidence = evidence if evidence is not None else _read_definition(home, LABEL)
    read = evidence["read"]
    if evidence["status"] != "discovered_legacy":
        report["status"] = evidence["status"]
        report["reader_guarantees"] = list(read.guarantees)
        return report
    data = evidence["value"]
    report["ownership_verified"] = True
    argv = data.get("ProgramArguments")
    if not isinstance(argv, list) or len(argv) != 1:
        report["status"] = "unsupported_layout"
        return report
    executable = _safe_location(argv[0])
    cwd = _safe_location(data.get("WorkingDirectory"))
    root = home / ".local/share/reasonfirst/v4-service"
    if executable is None or cwd != str(root) or data.get("Program", executable) != executable:
        report["status"] = "unsupported_layout"
        return report
    program = Path(executable)
    if program == root / "tools/codex_web_bridge/run_reasonfirst.sh":
        layout = "legacy_staged_http"
    elif (program.name == "run_reasonfirst.sh" and
          program.parent.parent == home / ".local/share/reasonfirst/upgrades" and
          re.fullmatch(r"uv-http-v[0-9]+\.[0-9]+\.[0-9]+-[A-Za-z0-9_-]+", program.parent.name)):
        layout = "versioned_http_sidecar"
    else:
        report["status"] = "unsupported_layout"
        return report
    report.update(status="discovered_legacy", layout=layout,
                  program=executable, working_directory=cwd,
                  transport="streamable-http",
                  transport_basis="recognized_launcher_layout_not_live_probe")
    return report


def build_deployment_inventory(
    *,
    state: SetupState | None,
    system_name: str,
    home: Path | None = None,
) -> dict[str, object]:
    selected_home = home if home is not None else Path.home()
    mcp_definition = _read_definition(selected_home, LABEL) if system_name.lower() in {"darwin", "macos"} else None
    registration = (
        _launch_agent(selected_home, mcp_definition) if mcp_definition is not None else
        {"kind": "service_registration", "status": "not_inspected",
         "reason": "No static adapter for this platform's service manager in this version.",
         "running_version": None, "health": "not_inspected"}
    )
    definitions = [
        _definition_not_inspected("bridge"),
        _definition_not_inspected("tunnel"),
    ]
    if system_name.lower() in {"darwin", "macos"}:
        definitions = [
            _definition_evidence("bridge", mcp_definition),
            _definition_evidence("tunnel", _read_definition(selected_home, TUNNEL_LABEL)),
        ]
    # Existence only, never parse the user's Bridge configuration or session data.
    marker = _marker(selected_home / ".config/reasonfirst/bridge.yaml")
    legacy_evidence = (
        registration["status"] not in {"not_found", "not_inspected"}
        or marker != "not_found"
        or any(item["status"] not in {"not_found", "not_inspected"} for item in definitions)
    )
    records = []
    if state is not None:
        for role, runtime, tunnel in (
            ("read", state.tunnel_runtime, state.tunnel_id),
            ("bridge", state.bridge_runtime, state.bridge_tunnel_id),
        ):
            records.append({"role": role, "status": "recorded" if runtime and tunnel else "not_recorded",
                            "running_version": None, "health": "not_inspected"})
    return {
        "scope": "bounded-static-inventory-not-live-service-acceptance",
        "installed_cli": {"version": __version__, "python_executable": sys.executable,
                          "package_directory": str(Path(__file__).parent.absolute())},
        "running_service_version": None, "live_inspection": "not_inspected",
        "registration": registration, "bridge_config_marker": marker,
        "service_definitions": definitions,
        "legacy_evidence": legacy_evidence,
        "recorded_runtimes": records,
        "adoption_performed": False, "service_manager_changed": False,
    }
