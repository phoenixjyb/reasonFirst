"""Bounded static deployment evidence. Discovery is NOT adoption or live health.

No process, HTTP, provider, controller or package-manager calls. Inspect only the
known user LaunchAgent on macOS; all other service managers are not inspected.
Never follow its arbitrary argv/env into scripts, credentials or network URLs.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import re
import stat
import sys

from . import __version__
from .setup_state import SetupState

MAX_PLIST_BYTES = 128 * 1024
LABEL = "com.reasonfirst.v4-mcp"


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


def _launch_agent(home: Path) -> dict[str, object]:
    path = home / "Library/LaunchAgents" / (LABEL + ".plist")
    report: dict[str, object] = {
        "kind": "launchd_registration", "path": str(path), "label": LABEL,
        "status": "not_found", "ownership_verified": False,
        "layout": None, "transport": None, "transport_basis": None,
        "running_version": None, "process_status": "not_inspected",
        "health": "not_inspected",
    }
    try:
        # Do not discover through symlinked directories or adopt someone else's
        # registration. Resolve no arbitrary command or env-supplied paths.
        for parent in (home, home / "Library", path.parent):
            if parent.is_symlink():
                report["status"] = "unsupported_layout"
                return report
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or (
            hasattr(os, "getuid") and info.st_uid != os.getuid()
        ):
            report["status"] = "unsupported_layout"
            return report
        if info.st_size > MAX_PLIST_BYTES:
            report["status"] = "invalid"
            return report
        with path.open("rb") as handle:
            raw = handle.read(MAX_PLIST_BYTES + 1)
        if len(raw) > MAX_PLIST_BYTES:
            report["status"] = "invalid"
            return report
        data = plistlib.loads(raw)
        if not isinstance(data, dict) or data.get("Label") != LABEL:
            report["status"] = "unsupported_layout"
            return report
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
    except FileNotFoundError:
        pass
    except OSError:
        report["status"] = "unreadable"
    except Exception:
        # Plist errors may contain arbitrary environment/argument values.
        report["status"] = "invalid"
    return report


def build_deployment_inventory(
    *,
    state: SetupState | None,
    system_name: str,
    home: Path | None = None,
) -> dict[str, object]:
    selected_home = home if home is not None else Path.home()
    registration = (
        _launch_agent(selected_home) if system_name.lower() in {"darwin", "macos"} else
        {"kind": "service_registration", "status": "not_inspected",
         "reason": "No static adapter for this platform's service manager in this version.",
         "running_version": None, "health": "not_inspected"}
    )
    # Existence only, never parse the user's Bridge configuration or session data.
    marker = _marker(selected_home / ".config/reasonfirst/bridge.yaml")
    legacy_evidence = registration["status"] not in {"not_found", "not_inspected"} or marker != "not_found"
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
        "legacy_evidence": legacy_evidence,
        "recorded_runtimes": records,
        "adoption_performed": False, "service_manager_changed": False,
    }
