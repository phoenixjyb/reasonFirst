"""Standalone stdlib supervisor embedded in a worker handoff, not a host service.

No imports from ReasonFirst or the project. The worker executes this source under
its existing sandbox. A supplied recipe is neither an authorization nor attestation.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time


def _digest(path, limit):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("unbounded runtime file")
    result = hashlib.sha256()
    count = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            count += len(block)
            if count > limit:
                raise ValueError("runtime file grew")
            result.update(block)
    return result.hexdigest()


def _invocation(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("absolute interpreter required")
    return os.path.normcase(str(path.parent.resolve(strict=True) / path.name))


def _verify(payload):
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("unsupported recipe")
    argv, requested = payload["resolved_argv"], payload["requested_argv"]
    if (not isinstance(argv, list) or not isinstance(requested, list)
            or not 2 <= len(argv) <= 128 or len(argv) != len(requested)
            or any(not isinstance(a, str) or "\x00" in a or len(a) > 32768
                   for a in argv + requested)
            or requested[0] not in ("python", "python3")
            or argv[1:] != requested[1:]):
        raise ValueError("missing or changed arguments")
    for name, maximum in (("timeout_seconds", 86400), ("output_limit_bytes", 4194304)):
        value = payload[name]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("invalid execution limit")
    runtime = payload["runtime"]
    executable = Path(runtime["executable"])
    if (argv[0] != str(executable)
            or _invocation(sys.executable) != _invocation(executable)
            or runtime["version"] != list(sys.version_info[:3])
            or runtime["implementation"] != sys.implementation.name
            or payload["platform"] != sys.platform
            or Path.cwd().resolve(strict=True) != Path(payload["worktree"]).resolve(strict=True)):
        raise ValueError("worker interpreter or directory mismatch")
    cfg = executable.parent.parent / "pyvenv.cfg"
    actual = {
        "invocation_directory": str(executable.parent.resolve(strict=True)),
        "resolved_file": str(executable.resolve(strict=True)),
        "sha256": _digest(executable, 256 * 1024 * 1024),
        "venv_config_sha256": _digest(cfg, 32768) if cfg.exists() else None,
    }
    if actual != runtime["fingerprint"]:
        raise ValueError("runtime changed since approval")


def _child_env():
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY")):
            continue
        if upper.startswith("GIT_") or upper in {
                "SSH_AUTH_SOCK", "PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                "__PYVENV_LAUNCHER__", "VIRTUAL_ENV"}:
            continue
        env[key] = value
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", GIT_TERMINAL_PROMPT="0")
    return env


def execute_recipe(payload):
    result = {
        "operation": "worker-python-recipe", "schema_version": 1,
        "requested_argv": payload.get("requested_argv"),
        "resolved_argv": payload.get("resolved_argv"),
        "timeout_seconds": payload.get("timeout_seconds"),
        "identity_verified": False, "child_started": False,
        "child_returncode": None, "timed_out": False,
        "output_complete": False, "command_succeeded": False,
        "stdout": "", "stderr": "", "error": None,
        "test_semantics": "review-project-test-output-not-session-exit",
    }
    try:
        _verify(payload)
        result["identity_verified"] = True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        result["error"] = "recipe_identity_or_arguments_invalid"
        return result, 2
    try:
        child = subprocess.Popen(
            payload["resolved_argv"], shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env(),
        )
    except (OSError, ValueError):
        result["error"] = "child_launch_failed"
        return result, 126
    result["child_started"] = True
    buffers = [bytearray(), bytearray()]
    failed_read, overflow = threading.Event(), threading.Event()
    limit = payload["output_limit_bytes"]

    def drain(index, pipe):
        try:
            while True:
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    break
                room = limit - len(buffers[index])
                buffers[index].extend(chunk[:room])
                if len(chunk) > room:
                    overflow.set()
                    break
        except (OSError, ValueError):
            failed_read.set()
        finally:
            pipe.close()

    readers = [threading.Thread(target=drain, args=(i, pipe), daemon=True)
               for i, pipe in enumerate((child.stdout, child.stderr))]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + payload["timeout_seconds"]
    interrupted = False
    try:
        while child.poll() is None:
            if overflow.is_set() or failed_read.is_set():
                break
            if time.monotonic() >= deadline:
                result["timed_out"] = True
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if child.poll() is None:
            # Only this supervisor's direct child; never kill unrelated processes.
            try:
                child.kill()
            except OSError:
                pass
        try:
            result["child_returncode"] = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            result["error"] = "direct_child_cleanup_incomplete"
        for reader in readers:
            reader.join(timeout=0.5)
    complete = (not overflow.is_set() and not failed_read.is_set()
                and not any(reader.is_alive() for reader in readers))
    for name, data in zip(("stdout", "stderr"), buffers):
        try:
            result[name] = data.decode("utf-8")
        except UnicodeError:
            result[name] = data.decode("utf-8", errors="replace")
            complete = False
    result["output_complete"] = complete
    if interrupted:
        result["error"] = "interrupted"
        return result, 130
    if result["timed_out"]:
        result["error"] = "child_timeout"
        return result, 124
    if not complete or result["error"]:
        result["error"] = result["error"] or "output_incomplete_or_invalid_utf8"
        return result, 1
    code = result["child_returncode"]
    result["command_succeeded"] = code == 0
    # Preserve the raw code above; never wrap a native failure into shell zero.
    return result, code if type(code) is int and 0 <= code <= 255 else 1


if __name__ == "__main__":
    payload = json.loads(RECIPE_JSON)
    if "recipes" in payload:
        try:
            if len(sys.argv) != 2 or not sys.argv[1].isdigit():
                raise ValueError("command index required")
            payload = payload["recipes"][int(sys.argv[1])]
        except (IndexError, ValueError):
            print(json.dumps({"operation": "worker-python-recipe", "error": "invalid_command_index",
                              "child_started": False, "command_succeeded": False,
                              "supervisor_exit_code": 2}))
            raise SystemExit(2)
    report, exit_code = execute_recipe(payload)
    report["supervisor_exit_code"] = exit_code
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    raise SystemExit(exit_code)
