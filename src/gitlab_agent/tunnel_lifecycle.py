"""Conservative, foreground supervision of an EXISTING stdio tunnel profile.

POSIX only for process control. No remote provisioning, profile rewriting,
credential persistence, PID-file signalling, or automatic access grants.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict, dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import threading
import queue
from typing import Any

import yaml

MAX_FILE = 128 * 1024
MAX_REPLY = 16 * 1024
DEFAULT_SETTINGS = Path("~/.config/reasonfirst/tunnel.json")
DEFAULT_STATE = Path("~/.local/share/reasonfirst/tunnels")


class LifecycleError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def fail(code: str, message: str) -> None:
    raise LifecycleError(code, message)


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def supported() -> None:
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        fail("platform_unsupported", "Lifecycle management requires macOS/Linux POSIX process groups and Unix sockets; use the upstream client on this platform.")


def absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(Path(value).expanduser()))


def private_dir(path: Path, *, create: bool = False) -> bool:
    if not path.exists() and not path.is_symlink():
        if not create:
            return False
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        fail("unsafe_state_directory", "The helper settings/state directory must be owned by this user, not a symlink, and mode 0700. Nothing was chmod-ed automatically.")
    return True


def read_regular(path: Path, *, private: bool = False) -> bytes:
    """No-follow, bounded reads; errors never echo file contents."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            fail("unsafe_file", "Expected a regular file owned by this user, not a symlink.")
        if info.st_mode & (0o077 if private else 0o022):
            fail("unsafe_file_permissions", "Private files must be mode 0600; profiles must not be group/world writable.")
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        fail("file_too_large", "Helper settings/profile exceeds the 128-KiB limit.")
    return raw


def check_private_file(path: Path) -> None:
    """Metadata only: do not read the GitLab .env or runtime credential file."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        fail("unsafe_credential_file", "The selected private configuration/key file must be a regular, user-owned, nonsymlink file with mode 0600.")


@dataclass(frozen=True)
class Settings:
    profile: str
    profile_dir: str
    source_dir: str
    env_file: str
    state_dir: str
    tunnel_bin: str
    keychain_service: str = ""
    keychain_account: str = ""
    version: int = 1

    @property
    def profile_path(self) -> Path:
        return Path(self.profile_dir) / (self.profile + ".yaml")

    @property
    def key(self) -> str:
        return hashlib.sha256(str(self.profile_path).encode()).hexdigest()[:16]

    @property
    def socket_path(self) -> Path:
        return Path(self.state_dir) / (self.key + ".sock")


def load_settings(path: Path) -> Settings:
    try:
        data = json.loads(read_regular(path, private=True))
        settings = Settings(**data)
    except (OSError, ValueError, TypeError):
        fail("settings_unavailable", "Run configure once, or select an existing valid private --settings file. No credential contents are needed.")
    if type(settings.version) is not int or settings.version != 1:
        fail("settings_version", "Unsupported helper settings version.")
    if not all(isinstance(v, str) for k, v in asdict(settings).items() if k != "version"):
        fail("settings_invalid", "Helper settings must contain only the documented path/reference fields.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", settings.profile):
        fail("profile_name_invalid", "Use an existing named profile, without a path or URL.")
    for field in (settings.profile_dir, settings.source_dir, settings.env_file, settings.state_dir, settings.tunnel_bin):
        if not Path(field).is_absolute() or any(ord(c) < 32 for c in field):
            fail("settings_path_invalid", "Saved paths must be absolute and free of control characters.")
    if len(os.fsencode(settings.socket_path)) >= 100:
        fail("state_path_too_long", "Select a shorter private state directory for the Unix control socket.")
    return settings


class ProfileLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: ProfileLoader, node: Any, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result or key == "<<":
            fail("profile_invalid", "Profile keys must be unique strings; YAML merges are unsupported.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


ProfileLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


@dataclass(frozen=True)
class Profile:
    digest: str
    tunnel_id: str
    key_ref: str
    host: str
    port: int


def read_profile(settings: Settings) -> Profile:
    raw = read_regular(settings.profile_path)
    try:
        for token in yaml.scan(raw):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)):
                fail("profile_invalid", "YAML aliases/anchors are unsupported by this helper.")
        data = yaml.load(raw, Loader=ProfileLoader)
        if not isinstance(data, dict) or set(data) - {"config_version", "control_plane", "health", "admin_ui", "log", "mcp"}:
            raise ValueError
        if type(data.get("config_version")) is not int or data["config_version"] != 1:
            raise ValueError
        cp = data["control_plane"]
        if not isinstance(cp, dict) or set(cp) != {"base_url", "tunnel_id", "api_key"}:
            raise ValueError
        if cp["base_url"] != "https://api.openai.com":
            fail("control_plane_unsupported", "This helper supports the canonical OpenAI HTTPS control plane only; it never guesses a credential destination.")
        if not isinstance(cp["tunnel_id"], str) or not re.fullmatch(r"tunnel_[A-Za-z0-9_-]{1,128}", cp["tunnel_id"]):
            raise ValueError
        ref = cp["api_key"]
        if not isinstance(ref, str):
            raise ValueError
        if ref.startswith("env:"):
            name = ref[4:]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) or name.startswith("GITLAB_") or name in {"PATH", "HOME", "USER"}:
                raise ValueError
        elif ref.startswith("file:"):
            if not Path(ref[5:]).is_absolute() or any(ord(c) < 32 for c in ref):
                raise ValueError
        else:
            fail("literal_key_forbidden", "Use env:NAME or file:/absolute/path for the runtime key. Never put a literal secret in a profile or CLI argument.")
        health = data["health"]
        if not isinstance(health, dict) or set(health) != {"listen_addr"}:
            raise ValueError
        match = re.fullmatch(r"(127\.0\.0\.1|\[::1\]):([0-9]{1,5})", health["listen_addr"])
        if not match or not 1 <= int(match[2]) <= 65535:
            fail("health_address_unsupported", "Use a fixed loopback health address: 127.0.0.1:PORT or [::1]:PORT, with a nonzero port.")
        mcp = data["mcp"]
        if not isinstance(mcp, dict) or set(mcp) != {"commands"} or len(mcp["commands"]) != 1:
            raise ValueError
        command = mcp["commands"][0]
        if set(command) != {"channel", "command"} or command["channel"] != "main":
            raise ValueError
        argv = shlex.split(command["command"])
        launcher = Path(settings.source_dir) / "run_mcp.sh"
        allowed = [[str(launcher)], ["bash", str(launcher)], ["/bin/bash", str(launcher)]]
        if argv not in allowed:
            fail("launcher_mismatch", "The profile must launch run_mcp.sh from the configured source checkout. Correct the existing profile locally; it was not rewritten.")
        if not launcher.is_file() or not (Path(settings.source_dir) / "server.py").is_file():
            fail("launcher_missing", "The configured checkout must contain run_mcp.sh and server.py; check for an obsolete or temporary checkout.")
        if len(argv) == 1 and not os.access(launcher, os.X_OK):
            fail("launcher_not_executable", "Use bash plus the absolute launcher path, or deliberately set its executable permission.")
        log = data.get("log", {})
        ui = data.get("admin_ui", {})
        if not isinstance(log, dict) or set(log) - {"level", "format"}:
            raise ValueError
        if not isinstance(ui, dict) or set(ui) - {"open_browser"}:
            raise ValueError
        if "open_browser" in ui and type(ui["open_browser"]) is not bool:
            raise ValueError
        return Profile(hashlib.sha256(raw).hexdigest(), cp["tunnel_id"], ref, match[1].strip("[]"), int(match[2]))
    except (KeyError, TypeError, ValueError, yaml.YAMLError, RecursionError):
        fail("profile_unsupported", "Expected a v1, single-main-stdio profile with reference-only credentials and fixed loopback health. No profile was changed; advanced profiles can keep using upstream run.")


def fingerprint(settings: Settings, profile: Profile) -> str:
    return hashlib.sha256((json.dumps(asdict(settings), sort_keys=True) + profile.digest).encode()).hexdigest()


def runtime_environment(settings: Settings, profile: Profile) -> dict[str, str]:
    """Opt-in Keychain lookup; no key is persisted or placed on command lines."""
    check_private_file(Path(settings.env_file))
    # Upstream uses flag > environment > profile precedence. Inherit only
    # operational OS/uv/proxy settings, never arbitrary CONTROL_PLANE_*, MCP_*,
    # HEALTH_* or GITLAB_* overrides that could change the reviewed destination.
    operational = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR", "TMP", "TEMP",
                   "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                   "SSL_CERT_FILE", "SSL_CERT_DIR"}
    env = {k: v for k, v in os.environ.items() if k in operational or k.startswith(("LC_", "UV_"))}
    env["GITLAB_AGENT_ENV_FILE"] = settings.env_file
    if profile.key_ref.startswith("file:"):
        check_private_file(Path(profile.key_ref[5:]))
        return env  # upstream resolves the reference, not this helper
    name = profile.key_ref[4:]
    if name in os.environ:
        env[name] = os.environ[name]
    if settings.keychain_service:
        if sys.platform != "darwin":
            fail("keychain_unavailable", "The configured Keychain source requires macOS; use an explicit env/file reference on other platforms.")
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-a", settings.keychain_account,
                 "-s", settings.keychain_service, "-w"], capture_output=True,
                timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            fail("keychain_unavailable", "Could not access the configured Keychain item. Nothing was started; no error text or secret was exported.")
        if result.returncode or not result.stdout or len(result.stdout) > 8192:
            fail("keychain_unavailable", "The exact configured Keychain item is missing, empty, or access was denied. No fallback credential was used.")
        try:
            key = result.stdout.decode("utf-8").strip()
        except UnicodeError:
            fail("keychain_invalid", "The selected Keychain value is not a usable runtime key.")
        env[name] = key
    if not env.get(name):
        fail("runtime_key_missing", "The profile's runtime-key environment variable is missing in THIS session. Configure its exact Keychain item once, or load the existing runtime key privately. No new tunnel is needed.")
    if env[name].startswith("tunnel_") or any(c in env[name] for c in "\r\n") or len(env[name]) > 8192:
        fail("runtime_key_invalid", "Supply an existing runtime API key, not a tunnel ID or multiline value; its permissions are checked by the upstream service.")
    return env


def port_busy(profile: Profile) -> bool:
    try:
        with socket.create_connection((profile.host, profile.port), timeout=0.25):
            return True
    except ConnectionRefusedError:
        return False
    except OSError:
        fail("health_port_unverifiable", "Could not establish that the configured loopback port is unused. Refusing to start a possible duplicate.")


def request_health(profile: Profile, path: str, *, body: bool = False) -> tuple[int | None, dict]:
    connection = http.client.HTTPConnection(profile.host, profile.port, timeout=0.4)
    try:
        connection.request("GET", path, headers={"Connection": "close", "Accept": "application/json"})
        response = connection.getresponse()
        if not body or response.status != 200:
            return response.status, {}
        if response.getheader("Content-Encoding", "identity") != "identity":
            return response.status, {}
        raw = bytearray()
        deadline = time.monotonic() + 1
        while len(raw) <= MAX_FILE:
            if time.monotonic() >= deadline:
                return response.status, {}
            chunk = response.read1(min(4096, MAX_FILE + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > MAX_FILE:
            return response.status, {}
        data = json.loads(raw)
        return response.status, data if isinstance(data, dict) else {}
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        return None, {}
    finally:
        connection.close()


def probe(profile: Profile) -> dict[str, Any]:
    health, _ = request_health(profile, "/healthz")
    ready, _ = request_health(profile, "/readyz")
    _, info = request_health(profile, "/api/status", body=True)
    base = info.get("control_plane_base_url")
    identity = (info.get("control_plane_tunnel_id") == profile.tunnel_id
                and isinstance(base, str) and base.rstrip("/") == "https://api.openai.com")
    metadata = "unknown"
    if identity:
        metadata = "error" if info.get("tunnel_metadata_error") else (
            "fetched" if isinstance(info.get("tunnel_metadata"), dict) and info["tunnel_metadata"] else "pending")
    ok = health == 200 and ready == 200 and identity and metadata == "fetched"
    state = "ready_for_chatgpt_check" if ok else (
        "control_plane_error" if metadata == "error" else "running_not_ready")
    return {"ok": ok, "state": state, "health_http": health, "ready_http": ready,
            "runtime_identity_matches": identity, "control_plane_metadata": metadata,
            "chatgpt_connection_verified": False, "project_access_checked": False}


def bind_private_control_socket(path: Path) -> socket.socket:
    """Create the control socket private from the instant it is bound.

    chmod after bind is still retained as defense in depth, but the restrictive
    umask closes the observable bind->chmod race seen by concurrent status polls.
    """
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous_umask = os.umask(0o077)
    try:
        listener.bind(str(path))
    except Exception:
        listener.close()
        raise
    finally:
        os.umask(previous_umask)
    try:
        os.chmod(path, 0o600)
    except Exception:
        listener.close()
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return listener


def acquire_lock(path: Path):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stream = os.fdopen(fd, "a+b")
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        stream.close()
        fail("unsafe_lock", "Helper lock must be a private regular file owned by this user.")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        fail("runtime_busy", "An owner already holds the profile/tunnel lock. Use status; do not launch a duplicate.")
    return stream  # deliberately never unlink lock files


def lock_held(settings: Settings) -> bool:
    import fcntl
    path = Path(settings.state_dir) / (settings.key + ".lock")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return False
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            fail("unsafe_lock", "Cannot inspect an untrusted helper lock.")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return False


def control(settings: Settings, action: str = "status", instance: str = "") -> dict | None:
    if not private_dir(Path(settings.state_dir)):
        return None
    path = settings.socket_path
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        fail("unsafe_control_socket", "Refusing an untrusted control endpoint. No process was signalled.")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(str(path))
            conn.sendall(json.dumps({"action": action, "instance_id": instance}).encode() + b"\n")
            raw = bytearray()
            while not raw.endswith(b"\n") and len(raw) <= MAX_REPLY:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw.extend(chunk)
        if len(raw) > MAX_REPLY:
            raise ValueError
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("protocol") != 1 or data.get("profile_key") != settings.key:
            raise ValueError
        return data
    except (OSError, ValueError):
        return None  # never derive a kill target from a PID or stale file


def status(settings: Settings) -> dict:
    live = control(settings)
    if live is not None:
        try:
            changed = live.get("fingerprint") != fingerprint(settings, read_profile(settings))
        except (LifecycleError, OSError):
            changed = True
        live["restart_required"] = changed
        if changed:
            live.update(ok=False, state="configuration_changed")
        return live
    if private_dir(Path(settings.state_dir)) and lock_held(settings):
        return {"ok": False, "state": "owner_unresponsive_or_starting", "managed": True,
                "action": "Wait for startup or inspect the owner Terminal. No PID-based cleanup is performed."}
    profile = read_profile(settings)
    busy = port_busy(profile)
    return {"ok": False, "state": "unmanaged_listener" if busy else "not_running", "managed": False,
            "chatgpt_connection_verified": False, "project_access_checked": False,
            "action": "An existing listener is not adopted or stopped. Inspect its Terminal." if busy else "Run start with the saved settings."}


def stop(settings: Settings, *, expected: str | None = None) -> dict:
    live = control(settings)
    if live is None:
        result = status(settings)
        if result["state"] == "not_running":
            return {**result, "ok": True, "state": "already_stopped"}
        fail("not_owned", "No responsive helper owner was found. Use the original Terminal for an unmanaged tunnel; no process was signalled.")
    identity = live.get("instance_id")
    if not isinstance(identity, str) or (expected is not None and identity != expected):
        fail("instance_changed", "The owner changed during this operation. Nothing was stopped; check status again.")
    reply = control(settings, "stop", identity)
    if not reply or reply.get("state") != "stop_requested":
        fail("stop_not_acknowledged", "Owner did not acknowledge this instance-specific stop. No PID-based fallback is used.")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current = control(settings)
        if current and current.get("instance_id") != identity:
            fail("instance_changed", "A replacement owner appeared; it was not stopped.")
        if current is None and not lock_held(settings):
            return {"ok": True, "state": "stopped", "managed": True, "instance_id": identity}
        time.sleep(0.1)
    fail("stop_timeout", "The owner did not finish stopping. Inspect its Terminal; no unrelated process was signalled.")


def darwin_zombie_only_group(pgid: int) -> bool:
    """Disambiguate Darwin killpg EPERM without reaping the owned leader.

    XNU killpg1 excludes SZOMB members, so an all-zombie group can yield EPERM.
    Accept only a successful numeric ps snapshot containing the pinned leader
    and no live member of its group. No command lines, environments, usernames
    or returned PIDs are used as signal targets. Never generalize EPERM away.
    """
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["/bin/ps", "-ax", "-o", "pid=,pgid=,stat="],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            capture_output=True, timeout=2, check=False,
        )
        if result.returncode or not result.stdout or len(result.stdout) > MAX_FILE:
            return False
        leader_seen = False
        seen = set()
        for line in result.stdout.decode("ascii").splitlines():
            fields = line.split()
            if (len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit()
                    or len(fields[0]) > 10 or len(fields[1]) > 10
                    or not re.fullmatch(r"[A-Za-z?][A-Za-z0-9+<>/=\-]{0,15}", fields[2])):
                return False
            pid, group = int(fields[0]), int(fields[1])
            if (pid == 0 and group != 0) or pid in seen:
                return False
            seen.add(pid)
            if pid == pgid and group != pgid:
                return False
            if group == pgid:
                if not fields[2].startswith("Z"):
                    return False
                leader_seen = leader_seen or pid == pgid
        return leader_seen
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def signal_owned_group(pgid: int, signum: int) -> bool:
    try:
        os.killpg(pgid, signum)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if darwin_zombie_only_group(pgid):
            return False
        raise  # a real permission failure or incomplete evidence is not success


def terminate_owned(child: subprocess.Popen) -> None:
    """Finish signalling the owned group BEFORE reaping its leader.

    This owner is the only waiter. An unreaped leader pins its PID/PGID even
    after it exits; poll()/wait() during the grace period would remove that
    protection and also mistake leader exit for descendant shutdown.
    """
    if child.returncode is not None:
        return  # already reaped: never signal a potentially reused PID/PGID
    if not signal_owned_group(child.pid, signal.SIGTERM):
        child.wait(timeout=5)
        return
    # Keep the full existing five-second TERM grace for every member, including
    # descendants that outlive the leader. Do not poll/wait/reap during it.
    deadline = time.monotonic() + 5
    while (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(0.1, remaining))
    signal_owned_group(child.pid, signal.SIGKILL)
    child.wait(timeout=5)


def spawn(argv: list[str], settings: Settings, env: dict[str, str]) -> subprocess.Popen:
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        fail("child_reaping_unsupported", "Owned group shutdown requires the default SIGCHLD disposition and this helper as the sole child waiter. No process was started.")
    return subprocess.Popen(argv, cwd=settings.source_dir, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)


def start(settings: Settings, *, startup_timeout: float = 30, restarting: bool = False) -> int:
    profile = read_profile(settings)
    live = control(settings)
    if live is not None and not restarting:
        result = status(settings)
        result["already_running"] = True
        emit(result)
        return 0 if result.get("ok") else 1
    if not Path(settings.tunnel_bin).is_file() or not os.access(settings.tunnel_bin, os.X_OK):
        fail("tunnel_client_missing", "The configured tunnel-client executable is unavailable; configure its current installed path.")
    if not shutil.which("uv"):
        fail("uv_missing", "uv is needed by run_mcp.sh. Install it through the normal project setup.")
    if live is None and port_busy(profile):
        fail("unmanaged_listener", "The selected health port is already occupied by an unmanaged listener. No credential was loaded and no process was adopted or stopped.")
    # Prepare the replacement's local configuration/key before disturbing the
    # owner. Remote permission/health checks still happen after restart.
    env = runtime_environment(settings, profile)
    if restarting:
        stop(settings, expected=live.get("instance_id") if live else None)
    private_dir(Path(settings.state_dir), create=True)
    with ExitStack() as stack:
        stack.enter_context(acquire_lock(Path(settings.state_dir) / (settings.key + ".lock")))
        tunnel_key = hashlib.sha256(profile.tunnel_id.encode()).hexdigest()[:16]
        stack.enter_context(acquire_lock(Path(settings.state_dir) / ("tunnel-" + tunnel_key + ".lock")))
        if port_busy(profile):
            fail("unmanaged_listener", "The loopback health port is already in use. No process was adopted or killed; stop the previous tunnel in its own Terminal.")
        requested_stop = False
        def request_stop(signum, frame):
            nonlocal requested_stop
            requested_stop = True
        old_handlers = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        child = None
        listener = None
        socket_identity = None
        try:
            common = ["--profile", settings.profile, "--profile-dir", settings.profile_dir]
            doctor = spawn([settings.tunnel_bin, "doctor", *common, "--explain"], settings, env)
            child = doctor
            deadline = time.monotonic() + 60
            while doctor.poll() is None and not requested_stop and time.monotonic() < deadline:
                time.sleep(0.1)
            if requested_stop:
                terminate_owned(doctor)
                emit({"ok": True, "state": "stopped_before_start"})
                return 0
            if doctor.poll() is None:
                terminate_owned(doctor)
                fail("doctor_timeout", "Tunnel doctor exceeded 60 seconds. Only its owned process group was stopped.")
            if doctor.returncode:
                fail("doctor_failed", "Tunnel doctor failed; check the selected profile and runtime-key permissions locally. Raw provider output is intentionally not copied into helper diagnostics.")
            if read_profile(settings).digest != profile.digest:
                fail("profile_changed", "The profile changed during preflight. Retry after reviewing it; no runtime was launched.")
            if port_busy(profile):
                fail("unmanaged_listener", "Another listener appeared during preflight; no runtime was launched or adopted.")
            path = settings.socket_path
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    fail("unsafe_control_socket", "An unexpected state entry exists; refusing to replace it.")
                path.unlink()  # stale socket, and the exclusive owner lock is held
            listener = bind_private_control_socket(path)
            socket_identity = (path.stat().st_dev, path.stat().st_ino)
            listener.listen(8)
            listener.settimeout(0.15)
            child = spawn([settings.tunnel_bin, "run", *common], settings, env)
            instance = os.urandom(16).hex()
            snapshot = {"ok": False, "state": "starting"}
            fixed = {"protocol": 1, "managed": True, "profile_key": settings.key, "profile": settings.profile,
                     "instance_id": instance, "fingerprint": fingerprint(settings, profile),
                     "owner_pid": os.getpid(), "child_pid": child.pid,
                     "chatgpt_connection_verified": False, "project_access_checked": False}
            emit({**fixed, **snapshot, "foreground": True, "action": "Keep this Terminal running. Use status/stop from another Terminal; no localhost Assistant is needed."})
            deadline = time.monotonic() + startup_timeout
            last_probe = 0.0
            sampled_at = 0.0
            results: queue.SimpleQueue = queue.SimpleQueue()
            probing: threading.Thread | None = None
            def sample():
                try:
                    results.put(probe(profile))
                except Exception:
                    results.put({"ok": False, "state": "probe_failed"})
            announced = False
            while not requested_stop:
                if child.poll() is not None:
                    fail("runtime_exited", "The owned tunnel process exited. No unrelated process was signalled. Inspect upstream diagnostics locally before retrying.")
                try:
                    snapshot = results.get_nowait()
                    sampled_at = time.monotonic()
                except queue.Empty:
                    pass
                if probing is None or (not probing.is_alive() and time.monotonic() - last_probe >= 1):
                    # One bounded probe at a time. Even a broken local HTTP
                    # server must not prevent control requests or startup expiry.
                    probing = threading.Thread(target=sample, daemon=True)
                    probing.start()
                    last_probe = time.monotonic()
                if sampled_at and time.monotonic() - sampled_at > 3:
                    snapshot = {**snapshot, "ok": False, "state": "probe_stale"}
                if snapshot["ok"] and not announced:
                    emit({**fixed, **snapshot, "action": "In normal ChatGPT, verify gitlab_whoami, project preflight and a pinned file read."})
                    announced = True
                if not announced and time.monotonic() >= deadline:
                    emit({**fixed, **snapshot, "ok": False, "state": "startup_timeout", "action": "Owned runtime will be stopped. Local health alone is not end-to-end acceptance."})
                    return 1
                try:
                    conn, _ = listener.accept()
                except socket.timeout:
                    continue
                with conn:
                    conn.settimeout(0.5)
                    try:
                        request = conn.recv(4096)
                        data = json.loads(request)
                        action = data.get("action")
                        response = {**fixed, **snapshot, "sample_age_seconds": round(time.monotonic() - sampled_at, 2) if sampled_at else None}
                        if action == "stop" and data.get("instance_id") == instance:
                            response.update(ok=True, state="stop_requested")
                            requested_stop = True
                        elif action != "status":
                            response.update(ok=False, state="request_rejected")
                        conn.sendall(json.dumps(response).encode() + b"\n")
                    except (OSError, ValueError, AttributeError):
                        pass
            emit({"ok": True, "state": "stopping", "instance_id": instance})
            return 0
        finally:
            if child is not None:
                terminate_owned(child)
            if listener is not None:
                listener.close()
            if socket_identity:
                try:
                    info = settings.socket_path.lstat()
                    if (info.st_dev, info.st_ino) == socket_identity and stat.S_ISSOCK(info.st_mode):
                        settings.socket_path.unlink()
                except FileNotFoundError:
                    pass
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def configure(args: argparse.Namespace, path: Path) -> dict:
    binary = shutil.which("tunnel-client")
    if not binary:
        fail("tunnel_client_missing", "Install tunnel-client before configuring the helper.")
    settings = Settings(profile=args.profile, profile_dir=str(absolute(args.profile_dir)),
                        source_dir=str(absolute(args.source_dir)), env_file=str(absolute(args.env_file)),
                        state_dir=str(absolute(args.state_dir)), tunnel_bin=str(absolute(binary)),
                        keychain_service=args.keychain_service, keychain_account=args.keychain_account)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", settings.profile):
        fail("profile_name_invalid", "Use an existing profile name, not a path.")
    if len(os.fsencode(settings.socket_path)) >= 100:
        fail("state_path_too_long", "Choose a shorter private state directory.")
    if settings.keychain_service and (not settings.keychain_account or len(settings.keychain_service) > 200 or len(settings.keychain_account) > 200):
        fail("keychain_reference_invalid", "Specify a bounded, exact Keychain service and account; never a secret value.")
    profile = read_profile(settings)
    if settings.keychain_service and not profile.key_ref.startswith("env:"):
        fail("keychain_reference_unused", "Keychain loading requires an env: key reference in the existing profile. A file: profile uses its own protected key file.")
    check_private_file(Path(settings.env_file))
    private_dir(path.parent, create=True)
    data = (json.dumps(asdict(settings), indent=2, sort_keys=True) + "\n").encode()
    if path.exists() or path.is_symlink():
        existing = read_regular(path, private=True)
        if existing == data:
            return {"ok": True, "state": "configured", "changed": False, "settings_file": str(path)}
        old_settings = load_settings(path)
        if control(old_settings) is not None or (private_dir(Path(old_settings.state_dir)) and lock_held(old_settings)):
            fail("owner_running", "Stop the current helper-owned runtime before changing its saved settings. No settings were overwritten.")
        if not args.replace:
            fail("settings_exist", "Different settings already exist. Review them locally and use configure --replace deliberately; nothing was overwritten.")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tunnel-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if args.replace:
            os.replace(temp, path)
        else:
            os.link(temp, path)  # fail rather than overwrite a concurrent configure
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"ok": True, "state": "configured", "changed": True, "settings_file": str(path),
            "action": "Only helper path/reference settings were saved. No credential, tunnel profile, or project allowlist was changed. Run start next."}


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default echoes unknown argv, which can contain an
        # accidentally pasted secret. Help remains available without config.
        emit({"ok": False, "error": {"code": "arguments_invalid", "message": "Invalid lifecycle arguments. Use --help; never pass a literal credential as an argument."}})
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("configure", "start", "status", "stop", "restart"):
        command = commands.add_parser(action)
        command.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
        if action in {"start", "restart"}:
            command.add_argument("--startup-timeout", type=float, default=30)
        if action == "configure":
            command.add_argument("--profile", required=True)
            command.add_argument("--profile-dir", default="~/.config/tunnel-client")
            command.add_argument("--source-dir", required=True)
            command.add_argument("--env-file", default="~/.config/gitlab-agent/.env")
            command.add_argument("--state-dir", default=str(DEFAULT_STATE))
            command.add_argument("--keychain-service", default="")
            command.add_argument("--keychain-account", default="")
            command.add_argument("--replace", action="store_true")
    args = parser.parse_args(argv)
    try:
        supported()
        path = absolute(args.settings)
        if not 1 <= getattr(args, "startup_timeout", 30) <= 120:
            fail("timeout_invalid", "Startup timeout must be between 1 and 120 seconds.")
        if args.action == "configure":
            emit(configure(args, path))
            return 0
        settings = load_settings(path)
        if args.action in {"start", "restart"}:
            return start(settings, startup_timeout=args.startup_timeout, restarting=args.action == "restart")
        result = status(settings) if args.action == "status" else stop(settings)
        emit(result)
        return 0 if result.get("ok") else 1
    except LifecycleError as exc:
        emit({"ok": False, "error": {"code": exc.code, "message": str(exc)}})
    except Exception:
        emit({"ok": False, "error": {"code": "local_operation_failed", "message": "A local file/process operation failed. No raw provider output, credential contents or PID-based cleanup was used; inspect the selected paths and permissions locally."}})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
