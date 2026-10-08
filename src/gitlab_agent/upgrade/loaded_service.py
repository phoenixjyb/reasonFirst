"""Explicit read-only observation of a reviewed job in the caller's user domain.

Use a bounded isolated helper for Apple's structured (deprecated) ServiceManagement
read API. Never parse launchctl diagnostics or load/restart/register any job. The
returned dictionary is private evidence, not an effective process environment or
proof of which Python modules a PID imported.
"""
from __future__ import annotations

import os
from pathlib import Path
import platform
import plistlib
import re
import selectors
import subprocess
import sys
import time
from typing import Any

from . import deployment, launch_review, pairing, runtime

SCOPE = "loaded-user-job-observation-v1"
OBSERVATION_CONTRACT = "partial-selected-fields-v1"
STARTUP_BLOCKER = "managed_startup_confirmation_not_verified"
TIMEOUT = 8
MAX_OUTPUT = 256 * 1024
MAX_STDERR = 16 * 1024

# No package/SDK import, user site, .pth, shell, PATH resolution, or credentials in
# this child. A crashing/hung native framework call cannot kill/hang the planner.
# Copy-rule CF objects are released; the exported domain constant is not owned.
QUERY_SCRIPT = r'''
import ctypes as c
import sys

def query():
    owned = []
    cf = None
    try:
        if sys.platform != "darwin":
            return 60
        cf = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sm = c.CDLL("/System/Library/Frameworks/ServiceManagement.framework/ServiceManagement")
        cf.CFRelease.argtypes = [c.c_void_p]
        cf.CFRelease.restype = None
        cf.CFStringCreateWithCString.argtypes = [c.c_void_p, c.c_char_p, c.c_uint32]
        cf.CFStringCreateWithCString.restype = c.c_void_p
        cf.CFPropertyListCreateData.argtypes = [c.c_void_p, c.c_void_p, c.c_long, c.c_ulong, c.c_void_p]
        cf.CFPropertyListCreateData.restype = c.c_void_p
        cf.CFDataGetLength.argtypes = [c.c_void_p]
        cf.CFDataGetLength.restype = c.c_long
        cf.CFDataGetBytePtr.argtypes = [c.c_void_p]
        cf.CFDataGetBytePtr.restype = c.c_void_p
        sm.SMJobCopyDictionary.argtypes = [c.c_void_p, c.c_void_p]
        sm.SMJobCopyDictionary.restype = c.c_void_p
        domain = c.c_void_p.in_dll(sm, "kSMDomainUserLaunchd").value
        name = cf.CFStringCreateWithCString(None, sys.argv[1].encode("utf-8"), 0x08000100)
        if not domain or not name:
            return 62
        owned.append(name)
        job = sm.SMJobCopyDictionary(domain, name)
        if not job:
            return 61
        owned.append(job)
        data = cf.CFPropertyListCreateData(None, job, 100, 0, None)
        if not data:
            return 62
        owned.append(data)
        size = cf.CFDataGetLength(data)
        ptr = cf.CFDataGetBytePtr(data)
        if not ptr or not 0 < size <= 262144:
            return 63
        sys.stdout.buffer.write(c.string_at(ptr, size))
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 60
    finally:
        if cf is not None:
            for obj in reversed(owned):
                cf.CFRelease(obj)

raise SystemExit(query())
'''


class LoadedServiceError(RuntimeError):
    """Fixed code only, never raw native stderr or private job/config contents."""


def _supported() -> None:
    if platform.system() != "Darwin" or os.name != "posix":
        raise LoadedServiceError("unsupported_loaded_service_platform")
    if os.getuid() == 0 or os.getuid() != os.geteuid():
        raise LoadedServiceError("user_domain_required")


def _capture(argv: list[str], env: dict[str, str]) -> tuple[int, bytes]:
    """Bound wall time and both byte streams; kill only our helper on failure."""
    proc = None
    buffers = {"out": bytearray(), "err": bytearray()}
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd="/", close_fds=True)
        deadline = time.monotonic() + TIMEOUT
        with selectors.DefaultSelector() as poll:
            for stream, name in ((proc.stdout, "out"), (proc.stderr, "err")):
                poll.register(stream, selectors.EVENT_READ, name)
            while poll.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LoadedServiceError("native_query_timeout")
                for key, _ in poll.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        poll.unregister(key.fileobj)
                    else:
                        buffers[key.data].extend(chunk)
                        limit = MAX_OUTPUT if key.data == "out" else MAX_STDERR
                        if len(buffers[key.data]) > limit:
                            raise LoadedServiceError("native_output_limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LoadedServiceError("native_query_timeout")
            return proc.wait(timeout=remaining), bytes(buffers["out"])
    except subprocess.TimeoutExpired:
        raise LoadedServiceError("native_query_timeout") from None
    except OSError:
        raise LoadedServiceError("native_query_failed") from None
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()


def _query_job(label: str, home: Path) -> dict[str, Any]:
    """Internal adapter. The public command always supplies deployment.LABEL."""
    _supported()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", label):
        raise LoadedServiceError("invalid_job_label")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    code, raw = _capture([sys.executable, "-I", "-S", "-B", "-c", QUERY_SCRIPT, label], env)
    if code != 0:
        errors = {60: "native_api_unavailable", 61: "job_unavailable_or_query_failed",
                  62: "native_job_unserializable", 63: "native_output_limit"}
        raise LoadedServiceError(errors.get(code, "native_query_failed"))
    return _parse(raw, label)


def _parse(raw: bytes, label: str) -> dict[str, Any]:
    try:
        if not raw or len(raw) > MAX_OUTPUT:
            raise ValueError
        result = plistlib.loads(raw, dict_type=launch_review._Unique)
        if not isinstance(result, dict) or result.get("Label") != label:
            raise ValueError
        _projection(result)
        return result
    except Exception:
        raise LoadedServiceError("invalid_native_job") from None


def _projection(job: dict[str, Any]) -> dict[str, Any]:
    """Validate private selected values; do not treat absent data as a match."""
    values = {name: job.get(name) for name in ("Program", "ProgramArguments", "WorkingDirectory", "EnvironmentVariables")}
    args = values["ProgramArguments"]
    if args is not None and (not isinstance(args, list) or not 1 <= len(args) <= 128 or
                            any(not isinstance(arg, str) or len(arg) > 16384 for arg in args)):
        raise LoadedServiceError("invalid_native_job")
    for name in ("Program", "WorkingDirectory"):
        value = values[name]
        if value is not None and (not isinstance(value, str) or not value or len(value) > 16384):
            raise LoadedServiceError("invalid_native_job")
    env = values["EnvironmentVariables"]
    if env is not None and (not isinstance(env, dict) or len(env) > 256 or
                           any(not isinstance(k, str) or not isinstance(v, str)
                               or len(k) > 1024 or len(v) > 16384 for k, v in env.items())):
        raise LoadedServiceError("invalid_native_job")
    # SM may explicitly materialize Program from argv[0]; normalize only this
    # documented launchd default, never a missing argv/cwd/environment block.
    if values["Program"] is None and args:
        values["Program"] = args[0]
    for name in ("PID", "LastExitStatus"):
        value = job.get(name)
        if value is not None and (type(value) is not int or
                (name == "PID" and not 0 <= value <= 2**31 - 1) or
                (name == "LastExitStatus" and not -(2**31) <= value <= 2**32 - 1)):
            raise LoadedServiceError("invalid_native_job")
        values[name] = value
    return values


def _compare(saved: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    a, b = _projection(saved), _projection(loaded)
    fields = {}
    for name in ("Program", "ProgramArguments", "WorkingDirectory", "EnvironmentVariables"):
        fields[name] = "not_reported" if b[name] is None else "matches" if a[name] == b[name] else "differs"
    # Absence in a saved plist declares no explicit env; absence in an API
    # response is not evidence of an empty block. A present empty block is.
    if a["EnvironmentVariables"] is None and b["EnvironmentVariables"] == {}:
        fields["EnvironmentVariables"] = "matches"
    unreported = [name for name, value in fields.items() if value == "not_reported"]
    reported = [value for value in fields.values() if value != "not_reported"]
    return {"selected_fields": fields,
            # The original four-field gate is unchanged. Partial observations
            # must never satisfy it, even when every returned field matches.
            "selected_launch_fields_match": all(v == "matches" for v in fields.values()),
            "field_coverage": "none" if not reported else "partial" if unreported else "complete",
            "unreported_fields": unreported,
            "reported_fields_match": bool(reported) and all(v == "matches" for v in reported),
            "pid": b["PID"], "running_pid_reported": bool(b["PID"]),
            "last_exit_status": b["LastExitStatus"]}


def _observation_blockers(comparison: dict[str, Any]) -> list[str]:
    """No native observation, even four matching fields, permits activation."""
    blockers = [STARTUP_BLOCKER]
    if not comparison["selected_launch_fields_match"]:
        blockers.append("loaded_launch_fields_differ_or_not_reported")
    if not comparison["running_pid_reported"]:
        blockers.append("no_running_pid_reported")
    return blockers


def _boundary() -> dict[str, Any]:
    return {"operation": "loaded-inspect", "scope": SCOPE,
            "observation_contract": OBSERVATION_CONTRACT, "mutating": False,
            "managed_startup_confirmation_verified": False,
            "native_query_requested": False, "loaded_job_observed": False,
            "service_changed": False, "service_restarted": False, "state_files_written": False,
            "application_configuration_read": False, "workspace_state_read": False,
            "effective_environment_verified": False, "running_code_verified": False,
            "compatibility_verified": False, "activation_authorized": False,
            "ready_for_activation": False, "proposed_actions": []}


def inspect(*, runtime_id: str, expect_pairing_digest: str, expect_digest: str,
            home: Path | None = None) -> dict[str, Any]:
    result = _boundary()
    try:
        _supported()
        selected = deployment._home(home)
        def recheck():
            return launch_review.check(runtime_id=runtime_id, expect_pairing_digest=expect_pairing_digest,
                                       expect_digest=expect_digest, home=selected)
        before = recheck()
        saved_raw = deployment._registration_bytes(selected)
        # launch_review binds this data through pairing. Recheck before the query
        # too, so the private comparison cannot silently use a different plist.
        if before != recheck():
            raise LoadedServiceError("review_changed")
        saved = plistlib.loads(saved_raw, dict_type=launch_review._Unique)
        result["native_query_requested"] = True
        first = _query_job(deployment.LABEL, selected)
        second = _query_job(deployment.LABEL, selected)
        if _projection(first) != _projection(second):
            raise LoadedServiceError("loaded_job_changed")
        if saved_raw != deployment._registration_bytes(selected) or before != recheck():
            raise LoadedServiceError("review_changed")
        comparison = _compare(saved, second)
        blockers = list(dict.fromkeys([*before["blockers"], *_observation_blockers(comparison)]))
        return {**result, "ok": True, "loaded_job_observed": True,
                "source": "SMJobCopyDictionary(kSMDomainUserLaunchd)", "deprecated_api": True,
                "domain": "caller_user_launchd", "uid": os.getuid(), "label": deployment.LABEL,
                "launch_review_digest": expect_digest, "comparison": comparison,
                "stable_selected_samples": 2, "blockers": blockers,
                "message": "Partial-observation contract only: ok means the read completed, not configuration equivalence or activation readiness."}
    except LoadedServiceError as exc:
        code = str(exc)
    except (launch_review.LaunchReviewError, pairing.PairingError):
        code = "launch_review_not_verified"
    except (deployment.DeploymentError, runtime.RuntimeErrorCode):
        code = "storage_or_runtime_inspection_failed"
    except (Exception, KeyboardInterrupt):
        code = "loaded_inspection_failed"
    return {**result, "ok": False, "error_code": code,
            "message": "Loaded-job inspection could not be completed; no service was changed."}
