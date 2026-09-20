"""Explicit, offline-first HTTP -> HTTPS migration of existing local state.

No workspace creation/cleanup, Git ref writes, credentials sent to GitLab, or
changes to the GitLab server. Run only with coding workers and MCP stopped.
This maintenance tool's lock coordinates migrations, not other applications.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Iterator
from urllib.parse import urlsplit
import uuid


MAX_FILE_BYTES = 1024 * 1024
MAX_INVENTORY = 512
MAX_GIT_OUTPUT = 1024 * 1024
_ID = re.compile(r"[a-f0-9]{12}")
_PROJECT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_SHA = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?")


class MigrationError(RuntimeError):
    """Safe diagnostic: never include file contents, tokens, or Git stderr."""


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("Duplicate JSON keys; repair the local metadata explicitly")
        result[key] = value
    return result


def _read_regular(path: Path) -> bytes:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise MigrationError("Migration input is not a bounded regular file")
        # O_NOFOLLOW is extra protection where supported, not a same-user sandbox.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise MigrationError("Migration input changed file type")
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise MigrationError("Migration input grew beyond the size limit")
        return raw
    except OSError as exc:
        raise MigrationError("Cannot read a required local migration input") from exc


def _inside(root: Path, path: Path) -> Path:
    # Do not resolve away symlinks before checking the path components.
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise MigrationError("Local metadata path escaped the selected workspace root") from exc
    current = root
    for part in relative.parts:
        if part in {"..", "."}:
            raise MigrationError("Unsafe local metadata path")
        current = current / part
        if current.is_symlink():
            raise MigrationError("Symlinked migration inputs are not supported")
    if path.resolve() != path:
        raise MigrationError("Migration path is not canonical")
    return path


def _git_env() -> dict[str, str]:
    # Shell-injected Git configuration could hide a destination rewrite. Refuse
    # it rather than silently inspecting a different environment than workers.
    rejected = {"GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CONFIG",
                "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
                "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"}
    if any(k in os.environ for k in rejected):
        raise MigrationError("Unset Git configuration/path override variables before migration")
    if os.getenv("GIT_SSL_NO_VERIFY", "").lower() not in {"", "0", "false", "no"}:
        raise MigrationError("Unset GIT_SSL_NO_VERIFY; migration requires certificate verification")
    if os.getenv("GIT_SSL_CAINFO") or os.getenv("GIT_SSL_CAPATH"):
        raise MigrationError("Environment-only Git CA settings need separate TLS review before migration")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("GIT_")
           and not any(s in k.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY"))}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"})
    return env


def _git(args: list[str], *, cwd: Path | None = None, absent_ok: bool = False) -> bytes:
    # These are local metadata commands only. No credential helper or transport
    # is used. Spooling avoids buffering arbitrary repository output in memory.
    env = _git_env()
    try:
        with tempfile.TemporaryFile() as output:
            proc = subprocess.run(
                ["git", "--no-pager", "--no-replace-objects", "-c", "protocol.allow=never", *args],
                cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
            if proc.returncode != 0:
                if absent_ok and proc.returncode == 1:
                    return b""
                raise MigrationError("Local Git inspection failed; no remote request was made")
            if output.tell() > MAX_GIT_OUTPUT:
                raise MigrationError("Local Git metadata exceeds the inspection limit")
            output.seek(0)
            return output.read(MAX_GIT_OUTPUT + 1)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError("Local Git inspection could not complete") from exc


def _git_values(repo: Path, key: str, *, local: bool = False) -> list[str]:
    args = ["--git-dir", str(repo), "config"]
    if local:
        args.append("--local")
    raw = _git([*args, "--null", "--get-all", key], absent_ok=True)
    try:
        return raw.decode("utf-8").rstrip("\0").split("\0") if raw else []
    except UnicodeDecodeError as exc:
        raise MigrationError("Git configuration is not valid UTF-8") from exc


def _policy_snapshot(repo: Path) -> str:
    raw = _git(["--git-dir", str(repo), "config", "--null", "--list", "--includes"])
    try:
        entries = raw.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise MigrationError("Git configuration is not valid UTF-8") from exc
    relevant: list[str] = []
    for entry in entries:
        if not entry:
            continue
        key, _, value = entry.partition("\n")
        lower = key.lower()
        if lower.startswith(("include.", "includeif.")):
            raise MigrationError("Git include/includeIf requires manual review; no automatic migration")
        if lower.startswith("url.") and lower.endswith((".insteadof", ".pushinsteadof")):
            raise MigrationError("Git URL rewrites must be removed or reviewed separately before migration")
        if lower == "extensions.worktreeconfig" and value.lower() not in {"false", "no", "0"}:
            raise MigrationError("Per-worktree Git configuration requires manual migration")
        if lower.startswith("http") and lower.endswith(".sslverify"):
            if value.lower() not in {"true", "yes", "on", "1", ""}:
                raise MigrationError("Git certificate verification is disabled or ambiguous")
        if lower.startswith("remote.") and lower.endswith((".url", ".pushurl")):
            if lower not in {"remote.origin.url", "remote.origin.pushurl"}:
                raise MigrationError("Additional Git remotes require explicit manual review")
        if lower.startswith(("remote.", "http.", "https.", "url.", "core.ssh", "credential.")):
            relevant.append(entry)
    # Digest may cover credential settings; values are never exported in a plan.
    return _digest("\0".join(relevant).encode())


@dataclass(frozen=True)
class EndpointMap:
    old: str
    new: str

    @classmethod
    def parse(cls, old: str, new: str) -> "EndpointMap":
        values = []
        for raw, scheme, default_port in ((old, "http", 80), (new, "https", 443)):
            if not raw or any(ord(c) < 33 or ord(c) > 126 for c in raw) or "\\" in raw:
                raise MigrationError("Endpoints must be plain ASCII URLs without whitespace")
            try:
                url = urlsplit(raw)
                valid_port = url.port in {None, default_port}
            except ValueError as exc:
                raise MigrationError("Malformed endpoint URL") from exc
            if (url.scheme != scheme or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment or not valid_port
                or "?" in raw or "#" in raw):
                raise MigrationError("Only credential-free default-port HTTP -> HTTPS endpoints are supported")
            host = url.hostname
            if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or ".." in host or host.endswith("."):
                raise MigrationError("Use a canonical DNS hostname or IPv4 address")
            prefix = url.path.rstrip("/")
            if prefix and (not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+)+", prefix)
                           or any(p in {".", ".."} for p in prefix.split("/"))):
                raise MigrationError("Repository URL prefix is not supported for automatic migration")
            canonical = f"{scheme}://{host.lower()}{prefix}"
            # Canonical spelling makes stored-URL comparisons unambiguous.
            if raw.rstrip("/") not in {canonical, f"{scheme}://{host.lower()}:{default_port}{prefix}"}:
                raise MigrationError("Use canonical lowercase endpoint spelling")
            values.append(raw.rstrip("/"))
        a, b = (urlsplit(x) for x in values)
        if (a.hostname, a.path) != (b.hostname, b.path):
            raise MigrationError("Changed hostnames or repository prefixes require a separate migration")
        return cls(*values)

    def upgrade(self, url: str, *, suffix: str) -> str:
        expected_old, expected_new = self.old + suffix, self.new + suffix
        if url == expected_old:
            return expected_new
        if url == expected_new:
            return url
        raise MigrationError("Stored URL does not match the approved old/new endpoint and project")

    def project_for(self, url: str) -> str:
        for base in (self.old, self.new):
            if url.startswith(base + "/") and url.endswith(".git"):
                project = url[len(base) + 1:-4]
                _validate_project(project)
                return project
        raise MigrationError("Cached origin is not a repository on the approved endpoints")


def _validate_project(project: str) -> None:
    if not _PROJECT.fullmatch(project) or any(p in {".", ".."} for p in project.split("/")):
        raise MigrationError("Unsafe or unsupported project namespace")


def _repo_name(project: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", project.replace("/", "-").lower()).strip("-._")
    return f"{(slug[:48] or 'task')[:36]}-{hashlib.sha256(project.encode()).hexdigest()[:12]}.git"


def _env_data(raw: bytes) -> tuple[dict[str, str], list[str]]:
    try:
        lines = raw.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise MigrationError("Selected .env must be UTF-8") from exc
    data: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[7:].strip()
        if "=" not in text:
            continue
        key, value = (s.strip() for s in text.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in data:
            raise MigrationError("Duplicate .env assignments require explicit cleanup first")
        data[key] = value
    return data, lines


@dataclass
class FileChange:
    path: Path
    before: bytes = field(repr=False)
    after: bytes = field(repr=False)
    kind: str = "metadata"

    def summary(self) -> dict[str, object]:
        return {"path": str(self.path), "kind": self.kind,
                "before_sha256": _digest(self.before), "after_sha256": _digest(self.after)}


@dataclass
class MigrationPlan:
    config_file: Path
    root: Path
    mapping: EndpointMap
    changes: list[FileChange]
    inputs: dict[str, str]
    contexts: list[dict[str, str]]
    config_policy: dict[str, object]
    digest: str
    projects: list[str]
    workspace_count: int

    def summary(self) -> dict[str, object]:
        return {"ok": True, "dry_run": True, "old_endpoint": self.mapping.old,
                "new_endpoint": self.mapping.new, "config_file": str(self.config_file),
                "workspace_root": str(self.root), "plan_digest": self.digest,
                "changes": [item.summary() for item in self.changes],
                "projects": self.projects, "workspace_count": self.workspace_count,
                "no_op": not self.changes,
                "warnings": ["Stop coding workers, other writers, and MCP before apply.",
                             "Backups include credentials from the selected .env; keep them private.",
                             "This tool does not configure the server, runtime CA trust, or other .env files.",
                             "Plan creation is offline; HTTPS readiness is checked only with --check-tls."]}


def build_plan(*, config_file: Path, old_url: str, new_url: str) -> MigrationPlan:
    mapping = EndpointMap.parse(old_url, new_url)
    config_file = config_file.expanduser().absolute()
    if config_file.is_symlink():
        raise MigrationError("Selected configuration must not be a symlink")
    config_file = config_file.resolve()
    raw = _read_regular(config_file)
    config, lines = _env_data(raw)
    configured = config.get("GITLAB_BASE_URL", "").rstrip("/")
    if configured not in {mapping.old, mapping.new}:
        raise MigrationError("Selected .env does not contain the approved old/new GITLAB_BASE_URL")
    exported = os.getenv("GITLAB_BASE_URL")
    if exported is not None and exported.rstrip("/") != mapping.new:
        raise MigrationError("Unset stale exported GITLAB_BASE_URL before migration; .env cannot override it")
    selected = os.getenv("GITLAB_AGENT_ENV_FILE")
    if selected and Path(selected).expanduser().resolve() != config_file:
        raise MigrationError("GITLAB_AGENT_ENV_FILE selects a different config file")
    verify = os.getenv("GITLAB_VERIFY_SSL", config.get("GITLAB_VERIFY_SSL", "true")).lower()
    if verify not in {"true", "yes", "1", "on"}:
        raise MigrationError("Enable GITLAB_VERIFY_SSL before HTTPS migration")
    root_value = os.getenv("GITLAB_WORKSPACE_ROOT", config.get(
        "GITLAB_WORKSPACE_ROOT", "~/.local/share/chatgpt-gitlab-mcp"))
    if not Path(root_value).expanduser().is_absolute():
        raise MigrationError("Set an absolute GITLAB_WORKSPACE_ROOT before migration")
    root = Path(root_value).expanduser().resolve()
    if not root.is_dir():
        raise MigrationError("Selected workspace root does not exist; no state needs automatic migration")
    if config_file.is_relative_to(root / "repos") or config_file.is_relative_to(root / "worktrees"):
        raise MigrationError("User configuration must not live in a managed repository/worktree")
    allowed_raw = os.getenv("GITLAB_ALLOWED_PROJECTS", config.get("GITLAB_ALLOWED_PROJECTS", ""))
    allowed = {x.strip() for x in allowed_raw.split(",") if x.strip()}
    if not allowed:
        raise MigrationError("An explicit project allowlist is required for migration")
    _git_env()
    policy = {"verify": verify, "allowed": sorted(allowed), "exported_base": exported,
              "selected_config": selected, "workspace_root": str(root)}
    changes: list[FileChange] = []
    inputs = {str(config_file): _digest(raw)}
    if configured != mapping.new:
        updated = []
        for line in lines:
            if re.match(r"^\s*(?:export\s+)?GITLAB_BASE_URL\s*=", line):
                newline = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
                line = "GITLAB_BASE_URL=" + mapping.new + newline
            updated.append(line)
        changes.append(FileChange(config_file, raw, "".join(updated).encode(), "user_config"))

    repos_dir, state_dir = _inside(root, root / "repos"), _inside(root, root / "state")
    repos = sorted(repos_dir.glob("*.git")) if repos_dir.is_dir() else []
    states = sorted(state_dir.glob("*.json")) if state_dir.is_dir() else []
    if len(repos) + len(states) > MAX_INVENTORY:
        raise MigrationError("Workspace inventory exceeds the migration limit")
    contexts: list[dict[str, str]] = []
    projects: dict[str, Path] = {}
    for repo in repos:
        _inside(root, repo)
        if not repo.is_dir():
            raise MigrationError("Cached repository must be a directory")
        local_config = _inside(root, repo / "config")
        before = _read_regular(local_config)
        inputs[str(local_config)] = _digest(before)
        policy_digest = _policy_snapshot(repo)
        origins = _git_values(repo, "remote.origin.url", local=True)
        if len(origins) != 1 or origins != _git_values(repo, "remote.origin.url"):
            raise MigrationError("Origin must be one explicit local URL without inherited alternatives")
        project = mapping.project_for(origins[0])
        if project not in allowed or repo.name != _repo_name(project):
            raise MigrationError("Cached repository identity or project allowlist mismatch")
        projects[project] = repo
        pushes = _git_values(repo, "remote.origin.pushurl", local=True)
        if len(pushes) > 1 or pushes != _git_values(repo, "remote.origin.pushurl"):
            raise MigrationError("Multiple or inherited push URLs require separate review")
        new_origin = mapping.upgrade(origins[0], suffix=f"/{project}.git")
        new_push = mapping.upgrade(pushes[0], suffix=f"/{project}.git") if pushes else None
        contexts.append({"repo": str(repo), "policy": policy_digest,
                         "refs": _digest(_git(["--git-dir", str(repo), "show-ref"], absent_ok=True))})
        if new_origin != origins[0] or (pushes and new_push != pushes[0]):
            # Ask Git to edit a private scratch copy; plan never edits live config.
            with tempfile.TemporaryDirectory(prefix="reasonfirst-plan-") as tmp:
                private = Path(tmp) / "private"
                _private_directory(private)
                candidate = private / "config"
                candidate.write_bytes(before)
                candidate.chmod(0o600)
                _git(["config", "--file", str(candidate), "--replace-all", "remote.origin.url", new_origin])
                if pushes:
                    _git(["config", "--file", str(candidate), "--replace-all", "remote.origin.pushurl", str(new_push)])
                after = _read_regular(candidate)
            changes.append(FileChange(local_config, before, after, "git_config"))

    for path in states:
        _inside(root, path)
        before = _read_regular(path)
        inputs[str(path)] = _digest(before)
        try:
            state = json.loads(before.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (ValueError, UnicodeError) as exc:
            raise MigrationError("Malformed workspace metadata; preserve and repair it first") from exc
        if not isinstance(state, dict):
            raise MigrationError("Workspace metadata is not an object")
        ws, project = state.get("workspace_id"), state.get("project")
        if not isinstance(ws, str) or not _ID.fullmatch(ws) or path.stem != ws:
            raise MigrationError("Workspace ID does not match its metadata filename")
        if not isinstance(project, str) or project not in projects:
            raise MigrationError("Workspace project has no matching inspected repository")
        repo = projects[project]
        worktree = _inside(root, root / "worktrees" / ws)
        if (state.get("repo_path") != str(repo) or state.get("worktree_path") != str(worktree)
            or not worktree.is_dir()):
            raise MigrationError("Workspace paths are stale, noncanonical, or outside the selected cache")
        common = _git(["rev-parse", "--git-common-dir"], cwd=worktree).decode().strip()
        if (worktree / common).resolve() != repo:
            raise MigrationError("Worktree belongs to a different Git repository")
        head = _git(["rev-parse", "HEAD"], cwd=worktree).decode().strip()
        branch = _git(["symbolic-ref", "--short", "HEAD"], cwd=worktree).decode().strip()
        if not _SHA.fullmatch(head) or branch != state.get("branch"):
            raise MigrationError("Workspace branch/HEAD differs from managed metadata")
        contexts.append({"workspace": ws, "head": head, "branch": branch,
                         "status": _digest(_git(["status", "--porcelain=v1", "-z",
                                                  "--untracked-files=all"], cwd=worktree))})
        link = state.get("merge_request_url")
        if link is not None:
            if not isinstance(link, str):
                raise MigrationError("Saved MR link is not a string")
            found = re.search(r"/-/merge_requests/([0-9]+)$", link)
            if found is None:
                raise MigrationError("Saved MR link has an unsupported form")
            new_link = mapping.upgrade(link, suffix=f"/{project}/-/merge_requests/{found[1]}")
            if new_link != link:
                state["merge_request_url"] = new_link
                changes.append(FileChange(path, before, _json_bytes(state), "workspace_metadata"))
    # Include inventory, effective policy and inspected refs/status in the plan.
    payload = {"inputs": inputs, "contexts": contexts, "config_policy": policy,
               "old": mapping.old, "new": mapping.new,
               "changes": [x.summary() for x in changes]}
    return MigrationPlan(config_file, root, mapping, changes, inputs, contexts, policy,
                         _digest(_json_bytes(payload)), sorted(projects), len(states))


def check_tls(plan: MigrationPlan) -> dict[str, object]:
    """Optional unauthenticated probe with HTTPX's default CA and direct network.

    Does not prove Git trust/auth, instance identity, or private CA readiness.
    No token/header from the developer environment is used.
    """
    import httpx
    try:
        with httpx.Client(verify=True, trust_env=False, follow_redirects=False, timeout=10.0) as client:
            # Streaming avoids retaining an arbitrary login/error response body.
            with client.stream("GET", plan.mapping.new + "/api/v4/version") as response:
                if 300 <= response.status_code < 400:
                    return {"ok": False, "scope": "unauthenticated_https",
                            "error": "Endpoint redirects; inspect the canonical HTTPS endpoint manually"}
                return {"ok": True, "scope": "unauthenticated_https", "certificate_verified": True,
                        "status_code": response.status_code, "api_auth_checked": False,
                        "git_tls_checked": False}
    except httpx.HTTPError:
        return {"ok": False, "scope": "unauthenticated_https",
                "error": "HTTPS verification/connectivity failed; no credential was sent"}


def _windows_private(path: Path, *, directory: bool) -> None:
    # chmod does not set a private DACL on Windows. Protect the empty target
    # before writing any backed-up configuration or credential-bearing bytes.
    try:
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                  capture_output=True, text=True, check=True, timeout=10)
        sid = re.search(r"S-1-[0-9-]+", identity.stdout)
        if sid is None:
            raise MigrationError("Cannot resolve the current Windows security identity")
        rights = "(OI)(CI)F" if directory else "F"
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"*{sid.group(0)}:{rights}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError("Cannot establish private Windows file permissions") from exc


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    if os.name == "nt":
        _windows_private(path, directory=True)
    else:
        path.chmod(0o700)


def _atomic_write(path: Path, raw: bytes) -> None:
    # POSIX mode is preserved. Windows replacements are explicitly restricted
    # to the current user rather than inheriting a potentially broader parent ACL.
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tempname = tempfile.mkstemp(prefix=".https-migrate-", dir=path.parent)
    temporary = Path(tempname)
    try:
        if os.name == "nt":
            _windows_private(temporary, directory=False)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)


@contextmanager
def _migration_lock(root: Path) -> Iterator[None]:
    lockpath = _inside(root, root / ".https-migration.lock")
    with lockpath.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MigrationError("Another endpoint migration is in progress") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def apply_plan(plan: MigrationPlan, *, workers_stopped: bool) -> dict[str, object]:
    if not workers_stopped:
        raise MigrationError("Stop coding workers and MCP, then acknowledge --workers-stopped")
    with _migration_lock(plan.root):
        current = build_plan(config_file=plan.config_file, old_url=plan.mapping.old, new_url=plan.mapping.new)
        if current.digest != plan.digest:
            raise MigrationError("Migration inputs changed after preview; no endpoint file was modified")
        plan = current  # Use freshly rebuilt operations, not caller-mutated payloads.
        if not plan.changes:
            return {"ok": True, "applied": False, "no_op": True, "changed_files": 0}
        backup_root = _inside(plan.root, plan.root / "migrations")
        if not backup_root.exists():
            _private_directory(backup_root)
        elif not backup_root.is_dir():
            raise MigrationError("Backup destination is not a directory")
        backup = _inside(plan.root, backup_root / ("https-" + uuid.uuid4().hex))
        _private_directory(backup)
        manifest = {"version": 1, "plan_digest": plan.digest, "status": "prepared",
                    "old_endpoint": plan.mapping.old, "new_endpoint": plan.mapping.new,
                    "files": []}
        for index, change in enumerate(plan.changes):
            (backup / f"{index}.before").write_bytes(change.before)
            (backup / f"{index}.after").write_bytes(change.after)
            if os.name != "nt":
                (backup / f"{index}.before").chmod(0o600)
                (backup / f"{index}.after").chmod(0o600)
            manifest["files"].append({**change.summary(), "backup_index": index, "state": "pending"})
        manifest_path = backup / "manifest.json"
        _atomic_write(manifest_path, _json_bytes(manifest))
        try:
            for index, change in enumerate(plan.changes):
                if _read_regular(change.path) != change.before:
                    raise MigrationError("An input changed during apply")
                manifest["files"][index]["state"] = "writing"
                _atomic_write(manifest_path, _json_bytes(manifest))
                _atomic_write(change.path, change.after)
                manifest["files"][index]["state"] = "applied"
                _atomic_write(manifest_path, _json_bytes(manifest))
            remaining = build_plan(config_file=plan.config_file, old_url=plan.mapping.old, new_url=plan.mapping.new)
            if remaining.changes:
                raise MigrationError("Post-migration verification still finds old endpoint metadata")
            before_workspaces = [x for x in plan.contexts if "workspace" in x]
            after_workspaces = [x for x in remaining.contexts if "workspace" in x]
            before_refs = {x["repo"]: x["refs"] for x in plan.contexts if "repo" in x}
            after_refs = {x["repo"]: x["refs"] for x in remaining.contexts if "repo" in x}
            if before_workspaces != after_workspaces or before_refs != after_refs:
                raise MigrationError("Git/worktree state changed during apply; inspect the preserved journal")
            manifest["status"] = "completed"
            _atomic_write(manifest_path, _json_bytes(manifest))
        except Exception as exc:
            manifest["status"] = "interrupted"
            try:
                _atomic_write(manifest_path, _json_bytes(manifest))
            except Exception:
                pass  # Prepared manifest and all before/after copies already exist.
            raise MigrationError(
                f"Migration interrupted. Preserve the private journal at {backup}. "
                "Stop all writers, inspect it, then rerun the same preview to finish forward; "
                "do not reset/reclone or blindly restore HTTP settings."
            ) from exc
        return {"ok": True, "applied": True, "changed_files": len(plan.changes),
                "backup_directory": str(backup), "plan_digest": plan.digest,
                "restart_required": "Restart MCP and shells/services with the updated effective configuration"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="actual-coder-migrate-https",
        description="Preview/confirm a same-host HTTPS upgrade of existing ReasonFirst metadata.",
    )
    parser.add_argument("--config-file", type=Path, required=True,
                        help="Exact effective user .env; this is never inferred from a repository")
    parser.add_argument("--from-url", required=True)
    parser.add_argument("--to-url", required=True)
    parser.add_argument("--check-tls", action="store_true",
                        help="Also make one verified, unauthenticated HTTPS probe; no redirects")
    parser.add_argument("--apply", action="store_true", help="Apply after printing and confirming the plan")
    parser.add_argument("--workers-stopped", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Scripted apply; requires --plan-digest")
    parser.add_argument("--plan-digest", help="Expected fingerprint from an earlier preview")
    args = parser.parse_args(argv)
    try:
        if args.yes and (not args.apply or not args.plan_digest):
            raise MigrationError("--yes requires --apply and the exact --plan-digest from a preview")
        plan = build_plan(config_file=args.config_file, old_url=args.from_url, new_url=args.to_url)
        if args.plan_digest and args.plan_digest != plan.digest:
            raise MigrationError("Preview fingerprint differs; inspect a fresh plan before applying")
        print(json.dumps(plan.summary(), indent=2, ensure_ascii=False))
        if args.check_tls:
            tls = check_tls(plan)
            print(json.dumps({"tls_check": tls}, indent=2))
            if not tls["ok"]:
                return 1
        if not args.apply:
            return 0
        if not args.workers_stopped:
            raise MigrationError("Stop coding workers and MCP, then acknowledge --workers-stopped")
        if not args.yes:
            if not sys.stdin.isatty():
                raise MigrationError("Apply requires a TTY confirmation or --yes with --plan-digest")
            print("Apply these local URL changes and create private backups? [y/N] ", end="", file=sys.stderr, flush=True)
            if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
                print(json.dumps({"ok": False, "cancelled": True, "applied": False}))
                return 1
        print(json.dumps(apply_plan(plan, workers_stopped=args.workers_stopped), indent=2))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted; inspect any existing migration journal"}))
        return 130
    except MigrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "error": "Migration failed; inspect local state and private journal"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
