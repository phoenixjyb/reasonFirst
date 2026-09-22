from __future__ import annotations

import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import shlex
import subprocess
import time
from typing import Any
from urllib.parse import urlparse
import uuid

from .bridge_config import ExecutionTarget


class RemoteWorkspaceError(RuntimeError):
    pass


def _slug(value: str, limit: int = 40) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    return (text or "task")[:limit]


def _safe_relative(path: str) -> str:
    raw = str(path or ".").strip() or "."
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts:
        raise RemoteWorkspaceError(f"Path must stay inside the managed remote worktree: {path!r}")
    return str(p)


class RemoteWorkspaceManager:
    def __init__(
        self,
        target: ExecutionTarget,
        *,
        gitlab_host: str = "",
        git_username: str = "",
        git_password: str = "",
        allowed_executables: set[str] | None = None,
        max_command_timeout_seconds: int = 300,
        max_output_bytes: int = 120000,
    ) -> None:
        if target.type != "ssh":
            raise RemoteWorkspaceError("RemoteWorkspaceManager requires an SSH target")
        self.target = target
        self.gitlab_host = str(gitlab_host or "").strip().lower()
        self.git_username = str(git_username or "")
        self.git_password = str(git_password or "")
        self.allowed_executables = set(allowed_executables or set())
        self.max_command_timeout_seconds = max(1, int(max_command_timeout_seconds))
        self.max_output_bytes = max(2000, int(max_output_bytes))

    def _ssh(self, command: str, *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
        # Send the shell script through stdin so OpenSSH cannot corrupt quoting
        # by rebuilding a remote `sh -lc <script>` command string.
        argv = [
            "ssh", "-T",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.target.ssh_connect_timeout}",
            "--", self.target.host,
            "sh -s",
        ]
        try:
            proc = subprocess.run(
                argv,
                input=command.rstrip("\n") + "\n",
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemoteWorkspaceError(f"SSH command timed out on {self.target.host}") from exc
        if check and proc.returncode != 0:
            raise RemoteWorkspaceError(
                f"SSH command failed on {self.target.host} (exit {proc.returncode}): {proc.stderr[-3000:]}"
            )
        return proc

    def probe(self) -> dict[str, Any]:
        cmd = " && ".join([
            "printf 'host='; hostname",
            "printf 'user='; id -un",
            f"printf 'repo='; git -C {shlex.quote(self.target.repo)} rev-parse --show-toplevel",
            "printf 'codex='; command -v " + shlex.quote(self.target.remote_codex) + " || true",
        ])
        proc = self._ssh(cmd, timeout=30, check=False)
        return {
            "ok": proc.returncode == 0,
            "target": self.target.to_dict(),
            "stdout": proc.stdout[-6000:],
            "stderr": proc.stderr[-3000:],
            "returncode": proc.returncode,
        }

    def _origin_url(self) -> str:
        repo = shlex.quote(self.target.repo)
        proc = self._ssh(f"git -C {repo} remote get-url origin", timeout=30)
        value = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not value:
            raise RemoteWorkspaceError("Remote repository has no origin URL")
        return value

    @staticmethod
    def _safe_origin_url(origin_url: str) -> str:
        parsed = urlparse(origin_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return origin_url
        host = parsed.hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        return parsed._replace(netloc=host).geturl()

    def _can_forward_git_credential(self, origin_url: str) -> bool:
        if not (self.git_username and self.git_password and self.gitlab_host):
            return False
        parsed = urlparse(origin_url)
        return (
            parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").lower() == self.gitlab_host
        )

    def _git_fetch_script(self, *, base: str, with_forwarded_credential: bool) -> str:
        qrepo = shlex.quote(self.target.repo)
        qbase = shlex.quote(base)
        lines = [
            "set -eu",
            f"repo={qrepo}",
            'git -C "$repo" rev-parse --is-inside-work-tree >/dev/null',
            "export GIT_TERMINAL_PROMPT=0",
        ]
        if with_forwarded_credential:
            lines += [
                'rf_auth_dir=$(mktemp -d "${TMPDIR:-/tmp}/reasonfirst-git.XXXXXX")',
                'trap \'rm -rf "$rf_auth_dir"\' EXIT HUP INT TERM',
                'cat >"$rf_auth_dir/askpass" <<\'RF_ASKPASS\'',
                '#!/bin/sh',
                'case "$1" in',
                '  *sername*|*SERNAME*) printf \'%s\\n\' "$RF_GIT_USERNAME" ;;',
                '  *) printf \'%s\\n\' "$RF_GIT_PASSWORD" ;;',
                'esac',
                'RF_ASKPASS',
                'chmod 700 "$rf_auth_dir/askpass"',
                f"export RF_GIT_USERNAME={shlex.quote(self.git_username)}",
                f"export RF_GIT_PASSWORD={shlex.quote(self.git_password)}",
                'export GIT_ASKPASS="$rf_auth_dir/askpass"',
            ]
        lines.append(f'git -C "$repo" fetch --prune origin {qbase}')
        return "\n".join(lines) + "\n"

    def create_workspace(self, *, project: str, base_ref: str, task: str) -> dict[str, Any]:
        repo = self.target.repo
        base = str(base_ref or "main").strip()
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", base) or base.startswith("-") or ".." in base.split("/"):
            raise RemoteWorkspaceError(f"Unsafe base ref: {base!r}")

        origin = self._origin_url()
        safe_origin = self._safe_origin_url(origin)
        fetch = self._ssh(
            self._git_fetch_script(base=base, with_forwarded_credential=False),
            timeout=180,
            check=False,
        )
        auth_forwarded = False
        if fetch.returncode != 0 and self._can_forward_git_credential(origin):
            fetch = self._ssh(
                self._git_fetch_script(base=base, with_forwarded_credential=True),
                timeout=180,
                check=False,
            )
            auth_forwarded = True
        if fetch.returncode != 0:
            raise RemoteWorkspaceError(
                "Remote Git fetch failed. SSH login and GitLab repository authentication are separate. "
                f"git stderr: {fetch.stderr[-2500:]}"
            )

        workspace_id = "ssh-" + uuid.uuid4().hex[:12]
        branch = f"chatgpt/{_slug(task)}-{workspace_id[-6:]}"
        qrepo = shlex.quote(repo)
        qbranch = shlex.quote(branch)
        qremote_base = shlex.quote("origin/" + base)
        qorigin = shlex.quote(safe_origin)
        cmd = f"""
set -eu
repo={qrepo}
base_sha=$(git -C "$repo" rev-parse --verify {qremote_base})
root="$HOME/.local/share/reasonfirst/worktrees"
mkdir -p "$root"
wt="$root/{workspace_id}"
if [ -e "$wt" ]; then echo 'worktree already exists' >&2; exit 9; fi
git -C "$repo" worktree add -b {qbranch} "$wt" "$base_sha" >/dev/null
printf '{{"workspace_id":"%s","worktree_path":"%s","base_sha":"%s","branch":"%s","origin_url":"%s"}}\n' \\
  {shlex.quote(workspace_id)} "$wt" "$base_sha" {qbranch} {qorigin}
"""
        proc = self._ssh(cmd, timeout=120)
        line = proc.stdout.strip().splitlines()[-1]
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RemoteWorkspaceError(f"Remote workspace creation returned invalid JSON: {line!r}") from exc
        data.update({
            "project": project,
            "base_ref": base,
            "target": self.target.to_dict(),
            "created_at": int(time.time()),
            "kind": "ssh",
            "git_auth_forwarded": auth_forwarded,
        })
        return data

    def status(self, state: dict[str, Any]) -> dict[str, Any]:
        wt = str(state["worktree_path"])
        qwt = shlex.quote(wt)
        cmd = f"""
set -eu
wt={qwt}
head=$(git -C "$wt" rev-parse HEAD)
branch=$(git -C "$wt" branch --show-current)
dirty=false
if [ -n "$(git -C "$wt" status --porcelain)" ]; then dirty=true; fi
printf '{{"head":"%s","branch":"%s","dirty":%s}}\n' "$head" "$branch" "$dirty"
"""
        data = json.loads(self._ssh(cmd, timeout=30).stdout.strip().splitlines()[-1])
        return {**state, **data}

    def list_files(self, state: dict[str, Any], path: str = ".", *, recursive: bool = False, max_entries: int = 300) -> dict[str, Any]:
        rel = _safe_relative(path)
        script = r'''
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
rel = sys.argv[2]
recursive = sys.argv[3] == "1"
limit = int(sys.argv[4])
target = (root / rel).resolve()
target.relative_to(root)
items=[]
if target.is_file():
    st=target.stat(); items.append({"path":str(target.relative_to(root)),"type":"file","size":st.st_size})
elif target.is_dir():
    it = target.rglob("*") if recursive else target.iterdir()
    for p in it:
        try:
            rp=p.resolve(); rp.relative_to(root)
        except Exception: continue
        kind="dir" if p.is_dir() else "file" if p.is_file() else "other"
        size=p.stat().st_size if p.is_file() else None
        items.append({"path":str(p.relative_to(root)),"type":kind,"size":size})
        if len(items)>=limit: break
else:
    raise SystemExit("path does not exist")
print(json.dumps({"path":rel,"items":items,"truncated":len(items)>=limit}))
'''
        cmd = "python3 -c {} {} {} {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])), shlex.quote(rel),
            "1" if recursive else "0", max(1, min(int(max_entries), 500)),
        )
        proc = self._ssh(cmd, timeout=60)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def read_file(self, state: dict[str, Any], path: str, *, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
        rel = _safe_relative(path)
        script = r'''
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve(); rel=sys.argv[2]; cap=int(sys.argv[3])
p=(root/rel).resolve(); p.relative_to(root)
if not p.is_file(): raise SystemExit("not a file")
raw=p.read_bytes()
if len(raw)>cap: raw=raw[:cap]
print(json.dumps({"path":rel,"content":raw.decode("utf-8",errors="replace"),"original_bytes":p.stat().st_size,"truncated":p.stat().st_size>cap}))
'''
        cmd = "python3 -c {} {} {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])), shlex.quote(rel), int(max_bytes),
        )
        proc = self._ssh(cmd, timeout=60)
        return json.loads(proc.stdout.strip().splitlines()[-1])


    def write_file(self, state: dict[str, Any], path: str, content: str) -> dict[str, Any]:
        rel = _safe_relative(path)
        raw = str(content).encode("utf-8")
        if len(raw) > 4 * 1024 * 1024:
            raise RemoteWorkspaceError("write payload is too large")
        import base64
        payload = base64.b64encode(raw).decode("ascii")
        script = r'''
import base64, json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve(); rel=sys.argv[2]; data=sys.argv[3]
p=(root/rel).resolve(); p.relative_to(root)
p.parent.mkdir(parents=True, exist_ok=True)
p.write_bytes(base64.b64decode(data.encode("ascii")))
print(json.dumps({"path":rel,"bytes":p.stat().st_size}))
'''
        cmd = "python3 -c {} {} {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])), shlex.quote(rel), shlex.quote(payload),
        )
        proc = self._ssh(cmd, timeout=90)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def apply_patch(self, state: dict[str, Any], patch: str) -> dict[str, Any]:
        raw = str(patch).encode("utf-8")
        if len(raw) > 4 * 1024 * 1024:
            raise RemoteWorkspaceError("patch payload is too large")
        import base64
        payload = base64.b64encode(raw).decode("ascii")
        wt = shlex.quote(str(state["worktree_path"]))
        script = r'''
import base64, pathlib, subprocess, sys
root=pathlib.Path(sys.argv[1]).resolve(); payload=sys.argv[2]
patch=base64.b64decode(payload.encode("ascii"))
proc=subprocess.run(["git","-C",str(root),"apply","--whitespace=nowarn","-"],input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
sys.stdout.buffer.write(proc.stdout)
sys.stderr.buffer.write(proc.stderr)
raise SystemExit(proc.returncode)
'''
        cmd = "python3 -c {} {} {}".format(
            shlex.quote(script), wt, shlex.quote(payload),
        )
        proc = self._ssh(cmd, timeout=90, check=False)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
        }

    def validation_enabled(self) -> bool:
        return bool(
            self.target.validation_engine
            and self.target.validation_image
            and self.target.validation_allowed_executables
        )

    def run_argv(
        self,
        state: dict[str, Any],
        argv: list[str],
        *,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run validation inside an explicitly configured container sandbox."""

        if not self.validation_enabled():
            raise RemoteWorkspaceError(
                "Remote validation is disabled. Configure an SSH target validation "
                "container before exposing execution."
            )
        if not argv or len(argv) > 64:
            raise RemoteWorkspaceError("argv must contain between 1 and 64 items")
        if any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or len(item.encode("utf-8", errors="replace")) > 4096
            for item in argv
        ):
            raise RemoteWorkspaceError("argv contains an invalid or oversized item")

        executable = argv[0]
        if "/" in executable or "\\" in executable:
            raise RemoteWorkspaceError("validation executable must be a bare command name")
        if executable == "git":
            raise RemoteWorkspaceError(
                "git is not exposed through the validation runner; Git publication "
                "remains a separate reviewed ReasonFirst operation"
            )
        target_allowed = set(self.target.validation_allowed_executables)
        effective_allowed = target_allowed & self.allowed_executables
        if executable not in effective_allowed:
            raise RemoteWorkspaceError(
                f"validation executable {executable!r} is not allowed by both "
                "the user target policy and ReasonFirst executable policy"
            )

        rel = _safe_relative(cwd)
        timeout = max(
            1,
            min(int(timeout_seconds), self.max_command_timeout_seconds),
        )
        import base64
        payload = base64.b64encode(
            json.dumps(
                {
                    "engine": self.target.validation_engine,
                    "image": self.target.validation_image,
                    "worktree": str(state["worktree_path"]),
                    "cwd": rel,
                    "argv": argv,
                    "timeout": timeout,
                    "network": bool(
                        self.target.network_access
                        and self.target.validation_network_access
                    ),
                    "max_output_bytes": self.max_output_bytes,
                },
                ensure_ascii=False,
            ).encode("utf-8")
        ).decode("ascii")
        script = r'''
import base64,json,os,pathlib,shutil,subprocess,sys,time
cfg=json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
engine=cfg["engine"]
engine_path=shutil.which(engine)
if not engine_path:
    raise SystemExit(f"container engine not found: {engine}")
root=pathlib.Path(cfg["worktree"]).resolve()
rel=cfg["cwd"]
cwd=(root/rel).resolve()
cwd.relative_to(root)
if not cwd.is_dir():
    raise SystemExit("cwd is not a directory")
image=cfg["image"]
if not image or image.startswith("-") or any(ch.isspace() for ch in image):
    raise SystemExit("unsafe validation image")
container_cwd="/workspace" if rel in {".",""} else "/workspace/"+rel
uid=str(os.getuid()) if hasattr(os,"getuid") else "1000"
gid=str(os.getgid()) if hasattr(os,"getgid") else "1000"
cmd=[
    engine_path,"run","--rm","--pull=never","--read-only",
    "--cap-drop=ALL","--security-opt","no-new-privileges",
    "--pids-limit=256","--memory=4g","--cpus=4",
    "--user",uid+":"+gid,
    "--tmpfs","/tmp:rw,nosuid,nodev,size=1g",
    "--env","HOME=/tmp/reasonfirst-home",
    "--volume",str(root)+":/workspace:rw",
    "--workdir",container_cwd,
]
if not cfg["network"]:
    cmd.extend(["--network","none"])
cmd.append(image)
cmd.extend(cfg["argv"])
start=time.monotonic()
try:
    proc=subprocess.run(
        cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=int(cfg["timeout"]),check=False,
        env={"PATH":os.environ.get("PATH","")},
    )
    timed_out=False
    rc=proc.returncode
    stdout=proc.stdout
    stderr=proc.stderr
except subprocess.TimeoutExpired as exc:
    timed_out=True
    rc=None
    stdout=exc.stdout or ""
    stderr=exc.stderr or ""
    if isinstance(stdout,bytes):
        stdout=stdout.decode("utf-8",errors="replace")
    if isinstance(stderr,bytes):
        stderr=stderr.decode("utf-8",errors="replace")
cap=max(1000,int(cfg["max_output_bytes"])//2)
def clip(value):
    raw=str(value).encode("utf-8",errors="replace")
    return raw[:cap].decode("utf-8",errors="ignore"), len(raw)>cap, len(raw)
out,out_truncated,out_bytes=clip(stdout)
err,err_truncated,err_bytes=clip(stderr)
print(json.dumps({
    "argv":cfg["argv"],
    "cwd":rel,
    "container_engine":engine,
    "container_image":image,
    "network_access":bool(cfg["network"]),
    "returncode":rc,
    "timed_out":timed_out,
    "timeout_seconds":int(cfg["timeout"]),
    "duration_ms":int((time.monotonic()-start)*1000),
    "stdout":out,
    "stderr":err,
    "stdout_truncated":out_truncated,
    "stderr_truncated":err_truncated,
    "stdout_original_bytes":out_bytes,
    "stderr_original_bytes":err_bytes,
}))
'''
        command = "python3 -c {} {}".format(
            shlex.quote(script),
            shlex.quote(payload),
        )
        proc = self._ssh(command, timeout=timeout + 30)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        result["workspace_id"] = state["workspace_id"]
        return result

    def run_command(
        self,
        state: dict[str, Any],
        command: str,
        *,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        raise RemoteWorkspaceError(
            "Arbitrary remote shell execution is disabled; use run_argv with an "
            "explicitly configured container validation runner"
        )

    def diff(self, state: dict[str, Any], *, max_chars: int = 120000) -> dict[str, Any]:
        wt = shlex.quote(str(state["worktree_path"]))
        base = shlex.quote(str(state["base_sha"]))
        cmd = f"""
set -eu
wt={wt}
base={base}
git -C "$wt" diff --no-ext-diff "$base" --
printf '\n__RF_UNTRACKED__\n'
git -C "$wt" ls-files --others --exclude-standard
"""
        proc = self._ssh(cmd, timeout=120)
        raw = proc.stdout
        marker = "\n__RF_UNTRACKED__\n"
        tracked, _, untracked_blob = raw.partition(marker)
        untracked = [line for line in untracked_blob.splitlines() if line.strip()][:100]
        text = tracked[:max_chars]
        return {
            "workspace_id": state["workspace_id"],
            "base_sha": state["base_sha"],
            "diff": text,
            "truncated": len(tracked) > len(text),
            "original_chars": len(tracked),
            "untracked": untracked,
        }

    def snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return a publication identity bound to the exact candidate Git tree."""
        script = r'''
import json, os, pathlib, subprocess, sys, tempfile
root=pathlib.Path(sys.argv[1]).resolve()

def out(*args, env=None):
    return subprocess.check_output(list(args), cwd=str(root), env=env)

head=out("git","rev-parse","HEAD").decode().strip()
branch=out("git","branch","--show-current").decode().strip()
origin=out("git","remote","get-url","origin").decode().strip()
try:
    push_url=out("git","remote","get-url","--push","origin").decode().strip()
except subprocess.CalledProcessError:
    push_url=origin
rewrites=subprocess.run(
    ["git","config","--get-regexp",r"^url\..*\.(insteadOf|pushInsteadOf)$"],
    cwd=str(root), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
).stdout.strip().splitlines()

fd,index_path=tempfile.mkstemp(prefix="rf-index-")
os.close(fd)
os.unlink(index_path)
env=dict(os.environ)
env["GIT_INDEX_FILE"]=index_path
try:
    subprocess.check_call(["git","read-tree",head],cwd=str(root),env=env,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.check_call(["git","add","-A","--"],cwd=str(root),env=env,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    tree=out("git","write-tree",env=env).decode().strip()
finally:
    try: os.unlink(index_path)
    except FileNotFoundError: pass

head_tree=out("git","rev-parse","HEAD^{tree}").decode().strip()
changed=out("git","diff","--name-only","HEAD","--").decode("utf-8",errors="replace").splitlines()
untracked=[x.decode("utf-8",errors="surrogateescape") for x in out("git","ls-files","--others","--exclude-standard","-z").split(b"\0") if x]
print(json.dumps({
    "head":head,
    "branch":branch,
    "origin_url":origin,
    "push_url":push_url,
    "url_rewrites":rewrites,
    "candidate_tree":tree,
    "dirty":tree != head_tree,
    "changed_paths":changed+untracked,
    "untracked":untracked,
}))
'''
        cmd = "python3 -c {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])),
        )
        proc = self._ssh(cmd, timeout=90)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        data["origin_url"] = self._safe_origin_url(str(data.get("origin_url") or ""))
        data["push_url"] = self._safe_origin_url(str(data.get("push_url") or ""))
        data["base_sha"] = str(state.get("base_sha") or "")
        data["project"] = str(state.get("project") or "")
        target_identity = {
            "type": self.target.type,
            "host": self.target.host,
            "repo": self.target.repo,
        }
        data["target_identity"] = target_identity
        identity = {
            "project": data["project"],
            "target": target_identity,
            "base_sha": data["base_sha"],
            "head": data.get("head"),
            "branch": data.get("branch"),
            "origin_url": data.get("origin_url"),
            "push_url": data.get("push_url"),
            "candidate_tree": data.get("candidate_tree"),
        }
        data["digest"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return data

    @staticmethod
    def _push_path_block_reason(paths: list[str]) -> str | None:
        blocked_names = {
            ".env", ".env.local", ".env.production", "id_rsa", "id_ed25519",
            "credentials", "credentials.json", "secrets.json",
        }
        blocked_suffixes = {".pem", ".key", ".p12", ".pfx", ".jks"}
        for raw in paths:
            p = PurePosixPath(str(raw))
            lower_parts = {part.lower() for part in p.parts}
            name = p.name.lower()
            if ".ssh" in lower_parts or name in blocked_names or p.suffix.lower() in blocked_suffixes:
                return f"sensitive path is not allowed in automatic push: {raw}"
        return None

    def _push_script(
        self,
        *,
        worktree: str,
        approved_url: str,
        branch: str,
        commit_sha: str,
        with_forwarded_credential: bool,
    ) -> str:
        qwt = shlex.quote(worktree)
        qurl = shlex.quote(approved_url)
        qbranch = shlex.quote(branch)
        qcommit = shlex.quote(commit_sha)
        lines = [
            "set -eu",
            f"wt={qwt}",
            f"url={qurl}",
            f"branch={qbranch}",
            f"commit={qcommit}",
            'case "$branch" in chatgpt/*) ;; *) echo "unsafe branch: $branch" >&2; exit 41 ;; esac',
            'if git -C "$wt" config --get-regexp \'^url\\..*\\.(insteadOf|pushInsteadOf)$\' >/dev/null 2>&1; then echo "git URL rewrite configuration is not allowed for reviewed push" >&2; exit 42; fi',
            "export GIT_TERMINAL_PROMPT=0",
        ]
        if with_forwarded_credential:
            lines += [
                'rf_auth_dir=$(mktemp -d "${TMPDIR:-/tmp}/reasonfirst-push.XXXXXX")',
                'trap \'rm -rf "$rf_auth_dir"\' EXIT HUP INT TERM',
                'cat >"$rf_auth_dir/askpass" <<\'RF_ASKPASS\'',
                '#!/bin/sh',
                'case "$1" in',
                '  *sername*|*SERNAME*) printf \'%s\\n\' "$RF_GIT_USERNAME" ;;',
                '  *) printf \'%s\\n\' "$RF_GIT_PASSWORD" ;;',
                'esac',
                'RF_ASKPASS',
                'chmod 700 "$rf_auth_dir/askpass"',
                f"export RF_GIT_USERNAME={shlex.quote(self.git_username)}",
                f"export RF_GIT_PASSWORD={shlex.quote(self.git_password)}",
                'export GIT_ASKPASS="$rf_auth_dir/askpass"',
            ]
        lines.append('git -C "$wt" push "$url" "$commit:refs/heads/$branch"')
        return "\n".join(lines) + "\n"

    def commit_push(
        self,
        state: dict[str, Any],
        *,
        expected_snapshot: dict[str, Any],
        message: str,
    ) -> dict[str, Any]:
        """Publish exactly the reviewed candidate tree to the reviewed destination."""
        message = str(message or "").strip()
        if not message or len(message) > 240 or "\n" in message or "\r" in message:
            raise RemoteWorkspaceError("commit message must be one non-empty line up to 240 characters")

        snap = self.snapshot(state)
        required = (
            "digest", "candidate_tree", "head", "branch", "origin_url",
            "push_url", "base_sha", "project", "target_identity",
        )
        mismatched = [
            key for key in required
            if snap.get(key) != expected_snapshot.get(key)
        ]
        if mismatched:
            raise RemoteWorkspaceError(
                "workspace or publication destination changed after ChatGPT push approval: "
                + ", ".join(mismatched)
            )
        if snap.get("url_rewrites"):
            raise RemoteWorkspaceError(
                "Git URL rewrite configuration is not allowed for reviewed push"
            )
        if not snap.get("dirty"):
            raise RemoteWorkspaceError("workspace has no changes to commit")

        branch = str(snap.get("branch") or "")
        if not branch.startswith("chatgpt/"):
            raise RemoteWorkspaceError(
                f"automatic push is allowed only from chatgpt/* branches, got {branch!r}"
            )
        paths = [str(x) for x in (snap.get("changed_paths") or [])]
        reason = self._push_path_block_reason(paths)
        if reason:
            raise RemoteWorkspaceError(reason)

        approved_url = str(snap.get("push_url") or snap.get("origin_url") or "")
        if not approved_url:
            raise RemoteWorkspaceError("approved push URL is empty")
        if self._safe_origin_url(approved_url) != approved_url:
            raise RemoteWorkspaceError("approved push URL must not contain embedded credentials")

        wt = shlex.quote(str(state["worktree_path"]))
        expected_head = shlex.quote(str(snap["head"]))
        expected_branch = shlex.quote(branch)
        expected_tree = shlex.quote(str(snap["candidate_tree"]))
        expected_origin = shlex.quote(str(snap["origin_url"]))
        expected_push = shlex.quote(str(snap["push_url"]))
        qmessage = shlex.quote(message)
        gate = r'''
set -eu
wt=__WT__
expected_head=__HEAD__
expected_branch=__BRANCH__
expected_tree=__TREE__
expected_origin=__ORIGIN__
expected_push=__PUSH__
[ "$(git -C "$wt" rev-parse HEAD)" = "$expected_head" ] || { echo "HEAD changed after approval" >&2; exit 51; }
[ "$(git -C "$wt" branch --show-current)" = "$expected_branch" ] || { echo "branch changed after approval" >&2; exit 52; }
[ "$(git -C "$wt" remote get-url origin)" = "$expected_origin" ] || { echo "origin changed after approval" >&2; exit 53; }
[ "$(git -C "$wt" remote get-url --push origin)" = "$expected_push" ] || { echo "push URL changed after approval" >&2; exit 54; }
if git -C "$wt" config --get-regexp '^url\..*\.(insteadOf|pushInsteadOf)$' >/dev/null 2>&1; then
  echo "git URL rewrite configuration is not allowed for reviewed push" >&2
  exit 55
fi
idx=$(mktemp "${TMPDIR:-/tmp}/reasonfirst-index.XXXXXX")
trap 'rm -f "$idx"' EXIT HUP INT TERM
export GIT_INDEX_FILE="$idx"
rm -f "$idx"
git -C "$wt" read-tree "$expected_head"
git -C "$wt" add -A --
tree=$(git -C "$wt" write-tree)
[ "$tree" = "$expected_tree" ] || { echo "candidate tree changed after approval" >&2; exit 56; }
git -C "$wt" diff --cached --check "$expected_head" --
python3 - "$wt" "$expected_head" <<'PYRF'
import os,pathlib,re,subprocess,sys
root=pathlib.Path(sys.argv[1]).resolve(); head=sys.argv[2]
env=dict(os.environ)
patterns=[
 re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
 re.compile(rb"glpat-[A-Za-z0-9_-]{12,}"),
 re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
]
blob=subprocess.check_output(["git","-C",str(root),"diff","--cached","--binary",head,"--"],env=env)
for pat in patterns:
 if pat.search(blob): raise SystemExit("secret-like material detected in candidate tree")
PYRF
commit=$(printf '%s\n' __MSG__ | git -C "$wt" commit-tree "$tree" -p "$expected_head")
rm -f "$idx"
git -C "$wt" read-tree "$expected_head"
git -C "$wt" add -A --
tree2=$(git -C "$wt" write-tree)
[ "$tree2" = "$expected_tree" ] || { echo "candidate changed before push" >&2; exit 57; }
printf '%s\n' "$commit"
'''.replace("__WT__", wt).replace("__HEAD__", expected_head).replace(
            "__BRANCH__", expected_branch
        ).replace("__TREE__", expected_tree).replace(
            "__ORIGIN__", expected_origin
        ).replace("__PUSH__", expected_push).replace("__MSG__", qmessage)

        committed = self._ssh(gate, timeout=180, check=False)
        if committed.returncode != 0:
            raise RemoteWorkspaceError(
                f"reviewed commit construction failed (exit {committed.returncode}): "
                f"{committed.stderr[-3000:]}"
            )
        commit_sha = committed.stdout.strip().splitlines()[-1]

        pushed = self._ssh(
            self._push_script(
                worktree=str(state["worktree_path"]),
                approved_url=approved_url,
                branch=branch,
                commit_sha=commit_sha,
                with_forwarded_credential=False,
            ),
            timeout=180,
            check=False,
        )
        auth_forwarded = False
        if pushed.returncode != 0 and self._can_forward_git_credential(approved_url):
            pushed = self._ssh(
                self._push_script(
                    worktree=str(state["worktree_path"]),
                    approved_url=approved_url,
                    branch=branch,
                    commit_sha=commit_sha,
                    with_forwarded_credential=True,
                ),
                timeout=180,
                check=False,
            )
            auth_forwarded = True
        if pushed.returncode != 0:
            raise RemoteWorkspaceError(
                f"reviewed commit {commit_sha} was created, but push failed "
                f"(exit {pushed.returncode}): {pushed.stderr[-3000:]}"
            )

        qbranch_ref = shlex.quote("refs/heads/" + branch)
        qcommit = shlex.quote(commit_sha)
        qhead = shlex.quote(str(snap["head"]))
        update = self._ssh(
            (
                f"set -eu\n"
                f"git -C {wt} update-ref {qbranch_ref} {qcommit} {qhead}\n"
                f"git -C {wt} read-tree {qcommit}\n"
            ),
            timeout=30,
            check=False,
        )
        return {
            "ok": True,
            "branch": branch,
            "commit_sha": commit_sha,
            "pushed": True,
            "git_auth_forwarded": auth_forwarded,
            "local_ref_updated": update.returncode == 0,
            "stdout": pushed.stdout[-6000:],
            "stderr": pushed.stderr[-3000:],
        }

    def cleanup(self, state: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        repo = shlex.quote(self.target.repo)
        wt = shlex.quote(str(state["worktree_path"]))
        flag = "--force" if force else ""
        proc = self._ssh(f"git -C {repo} worktree remove {flag} {wt}", timeout=60, check=False)
        return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stderr": proc.stderr[-2000:]}

    def artifact_candidates(self, state: dict[str, Any], path: str = ".", *, since_epoch: int = 0, max_entries: int = 50) -> list[dict[str, Any]]:
        rel = _safe_relative(path)
        script = r'''
import json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve(); rel=sys.argv[2]; since=int(sys.argv[3]); limit=int(sys.argv[4])
start=(root/rel).resolve(); start.relative_to(root)
exts={".md",".txt",".json",".jsonl",".csv",".tsv",".yaml",".yml",".xml",".html",".htm",".log",".svg",".png",".jpg",".jpeg",".webp",".gif",".bmp",".pdf",".docx"}
out=[]
paths=[start] if start.is_file() else start.rglob("*") if start.is_dir() else []
for p in paths:
    if not p.is_file() or p.suffix.lower() not in exts: continue
    st=p.stat()
    if since and int(st.st_mtime)<since: continue
    out.append({"path":str(p.relative_to(root)),"size":st.st_size,"mtime":int(st.st_mtime),"suffix":p.suffix.lower()})
    if len(out)>=limit: break
print(json.dumps(out))
'''
        cmd = "python3 -c {} {} {} {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])), shlex.quote(rel),
            int(since_epoch), max(1, min(int(max_entries), 100)),
        )
        proc = self._ssh(cmd, timeout=60)
        value = json.loads(proc.stdout.strip().splitlines()[-1])
        return value if isinstance(value, list) else []

    def read_bytes_b64(self, state: dict[str, Any], path: str, *, max_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
        rel = _safe_relative(path)
        script = r'''
import base64, json, pathlib, sys
root=pathlib.Path(sys.argv[1]).resolve(); rel=sys.argv[2]; cap=int(sys.argv[3])
p=(root/rel).resolve(); p.relative_to(root)
if not p.is_file(): raise SystemExit("not a file")
size=p.stat().st_size
if size>cap: raise SystemExit(f"file too large: {size}>{cap}")
raw=p.read_bytes()
print(json.dumps({"path":rel,"size":size,"base64":base64.b64encode(raw).decode("ascii")}))
'''
        cmd = "python3 -c {} {} {} {}".format(
            shlex.quote(script), shlex.quote(str(state["worktree_path"])), shlex.quote(rel), int(max_bytes),
        )
        proc = self._ssh(cmd, timeout=90)
        return json.loads(proc.stdout.strip().splitlines()[-1])
