"""Opt-in, read-only native service-manager observations.

This is NOT owner adoption, readiness, effective boot/login acceptance, or a
service controller. Fixed candidate identifiers are not a host-wide search.
Only trusted OS query commands execute, with no shell or credential access.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
from typing import Callable

from .validation_process import run_bounded_command

# These identifiers are observation candidates, not installed service claims.
TARGETS = {
    "darwin": (("bridge", "com.reasonfirst.v4-mcp"),
               ("tunnel", "com.reasonfirst.v4-tunnel")),
    "linux": (("bridge", "reasonfirst-v4-mcp.service"),
              ("tunnel", "reasonfirst-v4-tunnel.service")),
    "windows": (("bridge", r"\ReasonFirst\V4 MCP"),
                ("tunnel", r"\ReasonFirst\V4 Tunnel")),
}
MAX_OUTPUT = 8192
QUERY_TIMEOUT_SECONDS = 3

# Intentionally constant. No user-controlled values or shell expansion.
_WIN_QUERY = r"""
$ErrorActionPreference = 'Stop'
$path = '\ReasonFirst\'
$name = 'TASK_PLACEHOLDER'
$t = Get-ScheduledTask -TaskPath $path -TaskName $name -ErrorAction Stop
if ($null -eq $t) { exit 3 }
$enabled = [string]$t.Settings.Enabled
$state = [string]$t.State
$login = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' }).Count -gt 0
Write-Output ('State=' + $state)
Write-Output ('Enabled=' + $enabled)
Write-Output ('LogonTrigger=' + [string]$login)
"""

def _observation(status: str, *, manager: str, registration: str = "unknown",
                 manager_activity: str = "unknown", login_setting: str = "unknown",
                 observed_at: str | None = None) -> dict[str, object]:
    return {
        "status": status,
        "manager": manager,
        "registration": registration,
        "manager_activity": manager_activity,
        "login_setting": login_setting,
        "observed_at": observed_at,
        "process_identity": "not_verified",
        "runtime_owner": "not_verified",
        "mcp_readiness": "not_checked",
        "tunnel_readiness": "not_checked",
        "login_acceptance": "not_verified",
    }

def _fields(raw: str, allowed: set[str]) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or "=" not in line:
            return None
        key, value = line.split("=", 1)
        if key not in allowed or key in result or not re.fullmatch(r"[A-Za-z-]+", value):
            return None
        result[key] = value
    return result if result.keys() == allowed else None

def _interpret(system: str, returncode: int, stdout: str) -> tuple[str, str, str, str]:
    """Return bounded classifications; never expose stdout or the command line."""
    if system == "darwin":
        if returncode != 0:
            return ("unknown", "unknown", "unknown", "unknown")
        states = re.findall(r"(?m)^\s*state = ([a-z-]+)\s*$", stdout)
        if len(states) != 1:
            return ("unknown", "unknown", "unknown", "unknown")
        return ("observed", "registered", "running" if states[0] == "running" else "not_running", "unknown")
    if system == "linux":
        if returncode != 0:
            return ("unknown", "unknown", "unknown", "unknown")
        fields = _fields(stdout, {"LoadState", "ActiveState", "UnitFileState"})
        if not fields:
            return ("unknown", "unknown", "unknown", "unknown")
        if fields["LoadState"] == "not-found":
            return ("observed", "not_found_at_known_identifier", "unknown", "unknown")
        if fields["LoadState"] != "loaded":
            return ("unknown", "unknown", "unknown", "unknown")
        activity = {"active": "active", "inactive": "inactive", "failed": "failed"}.get(
            fields["ActiveState"], "unknown")
        login = {"enabled": "enabled", "disabled": "disabled", "masked": "masked",
                 "static": "not_configured"}.get(fields["UnitFileState"], "unknown")
        return ("observed", "registered", activity, login)
    if system == "windows":
        if returncode != 0:
            return ("unknown", "unknown", "unknown", "unknown")
        fields = _fields(stdout, {"State", "Enabled", "LogonTrigger"})
        if not fields or fields["Enabled"] not in {"True", "False"} or fields["LogonTrigger"] not in {"True", "False"}:
            return ("unknown", "unknown", "unknown", "unknown")
        activity = {"Running": "running", "Ready": "not_running",
                    "Disabled": "not_running"}.get(fields["State"], "unknown")
        login = ("enabled_logon_trigger" if fields["Enabled"] == "True" and fields["LogonTrigger"] == "True"
                 else "no_enabled_logon_trigger")
        return ("observed", "registered", activity, login)
    return ("unsupported", "unknown", "unknown", "unknown")

def observe_native_services(*, system_name: str | None = None,
                            run: Callable | None = None) -> dict[str, object]:
    """Query only two fixed candidate labels; never enumerate/adopt processes.

    A supplied run callable is for synthetic tests. Without one, never query a
    different OS from the actual host. The command runner is bounded and
    terminates only its own diagnostic subprocess on timeout.
    """
    actual = platform.system().lower()
    system = (system_name or actual).lower()
    if system == "macos":
        system = "darwin"
    native = run is None
    manager = {"darwin": "launchd-user", "linux": "systemd-user",
               "windows": "scheduled-task-user"}.get(system, "unsupported")
    if system not in TARGETS or (native and system != actual):
        return {"scope": "known_identifiers_only_no_host_scan",
                "manager": manager, "status": "unsupported",
                "services": [], "mutating": False}
    invoke = run or run_bounded_command
    env = {key: val for key, val in os.environ.items()
           if key.upper() in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR",
                              "PATHEXT", "COMSPEC", "TMP", "TEMP", "LANG", "LC_ALL"}}
    services = []
    for role, identifier in TARGETS[system]:
        if system == "darwin":
            # gui/UID is not necessarily present in an SSH/non-GUI session.
            if not hasattr(os, "getuid"):
                services.append({"role": role, "identifier_scope": "fixed_candidate",
                                 **_observation("unavailable", manager=manager)})
                continue
            argv = ["launchctl", "print", f"gui/{os.getuid()}/{identifier}"]
        elif system == "linux":
            argv = ["systemctl", "--user", "show", "--no-pager",
                    "--property=LoadState,ActiveState,UnitFileState", identifier]
        else:
            name = "V4 MCP" if role == "bridge" else "V4 Tunnel"
            argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                    _WIN_QUERY.replace("TASK_PLACEHOLDER", name)]
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            result = invoke(argv, cwd=Path.home(), env=env, timeout=QUERY_TIMEOUT_SECONDS,
                            max_output_bytes=MAX_OUTPUT, encoding="utf-8")
            if (result["timed_out"] or not result["output_complete"]
                    or result["stdout_truncated"] or result["stderr_truncated"]):
                raise RuntimeError("incomplete_native_query")
            state, reg, activity, login = _interpret(system, result["returncode"], result["stdout"])
        except (OSError, RuntimeError, KeyError, TypeError, ValueError):
            state, reg, activity, login = ("unavailable", "unknown", "unknown", "unknown")
        services.append({"role": role, "identifier_scope": "fixed_candidate",
                         "target_match_scope": "identifier_only_not_runtime_identity",
                         **_observation(state, manager=manager, registration=reg,
                                        manager_activity=activity,
                                        login_setting=login, observed_at=observed_at)})
    return {"scope": "known_identifiers_only_no_host_scan",
            "manager": manager, "status": "queried",
            "services": services, "mutating": False,
            "owner_adopted": False, "service_modified": False}
