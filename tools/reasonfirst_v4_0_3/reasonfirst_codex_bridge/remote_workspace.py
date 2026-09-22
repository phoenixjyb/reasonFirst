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
    ) -> None:
        if target.type != "ssh":
            raise RemoteWorkspaceError("RemoteWorkspaceManager requires an SSH target")
        self.target = target
        self.gitlab_host = str(gitlab_host or "").strip().lower()
        self.git_username = str(git_username or "")
        self.git_password = str(git_password or "")

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

    def run_argv(
        self,
        state: dict[str, Any],
        argv: list[str],
        *,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run one explicitly allowed executable without a shell.

        This is an executable-policy boundary, not a general OS sandbox. The
        target defaults to no executable authority; operators must explicitly
        configure remote_allowed_executables for trusted build/test entrypoints.
        """
        if not isinstance(argv, list) or not argv or any(
            not isinstance(item, str) or not item for item in argv
        ):
            raise RemoteWorkspaceError("Remote argv must be a non-empty list of strings")
        executable = argv[0]
        if "/" in executable or "\\" in executable or executable.startswith("-"):
            raise RemoteWorkspaceError("Remote executable must be a bare command name")
        allowed = set(self.target.remote_allowed_executables)
        if executable not in allowed:
            raise RemoteWorkspaceError(
                f"Remote executable {executable!r} is not approved for target "
                f"{self.target.name!r}; configure remote_allowed_executables locally"
            )
        if len(argv) > 128:
            raise RemoteWorkspaceError("Remote argv contains too many arguments")
        if any(len(item.encode("utf-8", errors="replace")) > 8192 for item in argv):
            raise RemoteWorkspaceError("Remote argv argument exceeds 8192 bytes")

        rel = _safe_relative(cwd)
        timeout_seconds = max(1, min(int(timeout_seconds), 1800))
        import base64
        payload = base64.b64encode(
            json.dumps(argv, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        script = r'''
import base64, json, os, pathlib, subprocess, sys, time
root=pathlib.Path(sys.argv[1]).resolve(); rel=sys.argv[2]; payload=sys.argv[3]; timeout=int(sys.argv[4])
cwd=(root/rel).resolve(); cwd.relative_to(root)
if not cwd.is_dir(): raise SystemExit("cwd is not a directory")
argv=json.loads(base64.b64decode(payload.encode("ascii")).decode("utf-8"))
if not isinstance(argv,list) or not argv or not all(isinstance(x,str) and x for x in argv):
    raise SystemExit("invalid argv")
tmp=root/".reasonfirst-tmp"; tmp.mkdir(exist_ok=True)
env={
    "PATH": os.environ.get("PATH","/usr/local/bin:/usr/bin:/bin"),
    "HOME": str(root),
    "TMPDIR": str(tmp),
    "LANG": os.environ.get("LANG","C.UTF-8"),
}
start=time.monotonic()
try:
    proc=subprocess.run(argv,cwd=str(cwd),env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
    result={"returncode":proc.returncode,"timed_out":False,"stdout":proc.stdout[-60000:],"stderr":proc.stderr[-60000:],"duration_ms":int((time.monotonic()-start)*1000)}
except subprocess.TimeoutExpired as exc:
    out=exc.stdout or ""; err=exc.stderr or ""
    if isinstance(out,bytes): out=out.decode("utf-8",errors="replace")
    if isinstance(err,bytes): err=err.decode("utf-8",errors="replace")
    result={"returncode":None,"timed_out":True,"stdout":str(out)[-60000:],"stderr":str(err)[-60000:],"duration_ms":int((time.monotonic()-start)*1000)}
print(json.dumps(result))
'''
        cmd = "python3 -c {} {} {} {} {}".format(
            shlex.quote(script),
            shlex.quote(str(state["worktree_path"])),
            shlex.quote(rel),
            shlex.quote(payload),
            timeout_seconds,
        )
        proc = self._ssh(cmd, timeout=timeout_seconds + 30)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def run_command(
        self,
        state: dict[str, Any],
        command: str,
        *,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Compatibility wrapper: parse shell-like text but never invoke a shell."""
        try:
            argv = shlex.split(str(command or ""))
        except ValueError as exc:
            raise RemoteWorkspaceError(f"Invalid remote command syntax: {exc}") from exc
        return self.run_argv(
            state,
            argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
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
        """Return an exact review digest for tracked and untracked workspace changes."""
        script = r'''
import hashlib, json, pathlib, subprocess, sys
root=pathlib.Path(sys.argv[1]).resolve()
def out(*args):
    return subprocess.check_output(list(args), cwd=str(root))
head=out("git","rev-parse","HEAD").decode().strip()
branch=out("git","branch","--show-current").decode().strip()
origin=out("git","remote","get-url","origin").decode().strip()
diff=out("git","diff","--binary","HEAD","--")
untracked=out("git","ls-files","--others","--exclude-standard","-z").split(b"\0")
paths=[]
for raw in sorted(x for x in untracked if x):
    rel=raw.decode("utf-8",errors="surrogateescape")
    p=(root/rel).resolve(); p.relative_to(root)
    if p.is_file(): paths.append(rel)
import os,tempfile
fd,index_path=tempfile.mkstemp(prefix=".rf-review-index-",dir=str(root)); os.close(fd); os.unlink(index_path)
env=os.environ.copy(); env["GIT_INDEX_FILE"]=index_path
try:
    subprocess.check_call(["git","read-tree","HEAD"],cwd=str(root),env=env,stdout=subprocess.DEVNULL)
    subprocess.check_call(["git","add","-A"],cwd=str(root),env=env,stdout=subprocess.DEVNULL)
    candidate_tree=subprocess.check_output(["git","write-tree"],cwd=str(root),env=env).decode().strip()
finally:
    try: os.unlink(index_path)
    except FileNotFoundError: pass
tracked=out("git","diff","--name-only","HEAD","--").decode("utf-8",errors="replace").splitlines()
identity={
    "project":sys.argv[2],
    "base_sha":sys.argv[3],
    "target_host":sys.argv[4],
    "target_repo":sys.argv[5],
    "head":head,
    "branch":branch,
    "origin_url":origin,
    "candidate_tree":candidate_tree,
}
h=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":")).encode())
print(json.dumps({**identity,"digest":h.hexdigest(),"dirty":bool(diff or paths),"changed_paths":tracked+paths,"untracked":paths}))
'''
        cmd = "python3 -c {} {} {} {} {} {}".format(
            shlex.quote(script),
            shlex.quote(str(state["worktree_path"])),
            shlex.quote(str(state.get("project") or "")),
            shlex.quote(str(state.get("base_sha") or "")),
            shlex.quote(self.target.host),
            shlex.quote(self.target.repo),
        )
        proc = self._ssh(cmd, timeout=60)
        return json.loads(proc.stdout.strip().splitlines()[-1])

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

    def _push_script(self, state: dict[str, Any], *, with_forwarded_credential: bool) -> str:
        qwt = shlex.quote(str(state["worktree_path"]))
        lines = [
            "set -eu",
            f"wt={qwt}",
            'branch=$(git -C "$wt" branch --show-current)',
            'case "$branch" in chatgpt/*) ;; *) echo "unsafe branch: $branch" >&2; exit 41 ;; esac',
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
        lines.append('git -C "$wt" push --set-upstream origin "HEAD:refs/heads/$branch"')
        return "\n".join(lines) + "\n"

    def commit_push(
        self,
        state: dict[str, Any],
        *,
        expected_digest: str,
        message: str,
    ) -> dict[str, Any]:
        """Commit and push the exact ChatGPT-reviewed snapshot without force push."""
        message = str(message or "").strip()
        if not message or len(message) > 240 or "\n" in message or "\r" in message:
            raise RemoteWorkspaceError("commit message must be one non-empty line up to 240 characters")
        snap = self.snapshot(state)
        if not snap.get("dirty"):
            raise RemoteWorkspaceError("workspace has no changes to commit")
        if str(snap.get("digest") or "") != str(expected_digest or ""):
            raise RemoteWorkspaceError(
                "workspace changed after ChatGPT push approval; review the new diff and approve again"
            )
        branch = str(snap.get("branch") or "")
        if not branch.startswith("chatgpt/"):
            raise RemoteWorkspaceError(f"automatic push is allowed only from chatgpt/* branches, got {branch!r}")
        paths = [str(x) for x in (snap.get("changed_paths") or [])]
        reason = self._push_path_block_reason(paths)
        if reason:
            raise RemoteWorkspaceError(reason)

        wt = shlex.quote(str(state["worktree_path"]))
        qmessage = shlex.quote(message)
        gate = r'''
set -eu
wt=__WT__
git -C "$wt" diff --check HEAD --
python3 - "$wt" <<'PYRF'
import pathlib,re,subprocess,sys
root=pathlib.Path(sys.argv[1]).resolve()
patterns=[
 re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
 re.compile(rb"glpat-[A-Za-z0-9_-]{12,}"),
 re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
]
blob=subprocess.check_output(["git","-C",str(root),"diff","--binary","HEAD","--"])
for pat in patterns:
 if pat.search(blob): raise SystemExit("secret-like material detected in diff")
for raw in subprocess.check_output(["git","-C",str(root),"ls-files","--others","--exclude-standard","-z"]).split(b"\0"):
 if not raw: continue
 p=(root/raw.decode("utf-8",errors="surrogateescape")).resolve(); p.relative_to(root)
 if p.is_file() and p.stat().st_size <= 2*1024*1024:
  data=p.read_bytes()
  for pat in patterns:
   if pat.search(data): raise SystemExit(f"secret-like material detected in {p.name}")
PYRF
git -C "$wt" add -A
git -C "$wt" diff --cached --check
git -C "$wt" commit -m __MSG__
git -C "$wt" rev-parse HEAD
'''.replace("__WT__", wt).replace("__MSG__", qmessage)
        committed = self._ssh(gate, timeout=180, check=False)
        if committed.returncode != 0:
            raise RemoteWorkspaceError(
                f"commit safety/commit step failed (exit {committed.returncode}): {committed.stderr[-3000:]}"
            )
        commit_sha = committed.stdout.strip().splitlines()[-1]

        origin = self._origin_url()
        pushed = self._ssh(self._push_script(state, with_forwarded_credential=False), timeout=180, check=False)
        auth_forwarded = False
        if pushed.returncode != 0 and self._can_forward_git_credential(origin):
            pushed = self._ssh(self._push_script(state, with_forwarded_credential=True), timeout=180, check=False)
            auth_forwarded = True
        if pushed.returncode != 0:
            raise RemoteWorkspaceError(
                f"commit {commit_sha} was created, but push failed (exit {pushed.returncode}): {pushed.stderr[-3000:]}"
            )
        return {
            "ok": True,
            "branch": branch,
            "commit_sha": commit_sha,
            "pushed": True,
            "git_auth_forwarded": auth_forwarded,
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
