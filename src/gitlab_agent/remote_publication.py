from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
import subprocess
import time
from typing import Any, Callable
from urllib.parse import urlparse

from .remote_targets import RemoteTarget


class RemotePublicationError(RuntimeError):
    pass


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _safe_abs_posix(path: str, label: str) -> str:
    value = str(path or "").strip()
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise RemotePublicationError(f"{label} is invalid")
    parsed = PurePosixPath(value)
    if not parsed.is_absolute() or ".." in parsed.parts:
        raise RemotePublicationError(
            f"{label} must be an absolute safe POSIX path"
        )
    return value


def _safe_branch(branch: str) -> str:
    value = str(branch or "").strip()
    if (
        not value.startswith("chatgpt/")
        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", value)
        or ".." in value
        or value.endswith(("/", ".lock"))
    ):
        raise RemotePublicationError(
            f"Reviewed remote publication requires a safe chatgpt/* branch, got {value!r}"
        )
    return value


def _safe_sha(value: str, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise RemotePublicationError(f"{label} is not a full Git object id")
    return normalized


def _safe_origin(value: str) -> str:
    """Return a credential-free exact Git destination identity."""

    raw = str(value or "").strip()
    if not raw or "\n" in raw or "\r" in raw or "\x00" in raw:
        raise RemotePublicationError("Git origin URL is invalid")
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = parsed.hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        if parsed.username or parsed.password:
            raw = parsed._replace(netloc=host).geturl()
        if parsed.query or parsed.fragment:
            raise RemotePublicationError(
                "Git origin URL must not contain query or fragment"
            )
    return raw.rstrip("/")


def _identity_digest(identity: dict[str, Any]) -> str:
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ReviewedRemoteCandidate:
    """Exact remote Git candidate reviewed before publication."""

    project: str
    target_name: str
    target_host: str
    target_repo: str
    workspace_root: str
    worktree_path: str
    base_sha: str
    head_sha: str
    branch: str
    origin_url: str
    candidate_tree: str
    changed_paths: tuple[str, ...]
    captured_at: int
    digest: str

    def identity(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "target_name": self.target_name,
            "target_host": self.target_host,
            "target_repo": self.target_repo,
            "workspace_root": self.workspace_root,
            "worktree_path": self.worktree_path,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "branch": self.branch,
            "origin_url": self.origin_url,
            "candidate_tree": self.candidate_tree,
            "changed_paths": list(self.changed_paths),
            "captured_at": self.captured_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def verify_digest(self) -> None:
        if _identity_digest(self.identity()) != self.digest:
            raise RemotePublicationError(
                "Reviewed remote candidate digest is invalid"
            )


_CAPTURE_SCRIPT = r"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

payload = json.loads(sys.stdin.read())
root = pathlib.Path(payload["workspace_root"]).resolve()
wt = pathlib.Path(payload["worktree"]).resolve()
wt.relative_to(root)
if not wt.is_dir():
    raise SystemExit("managed worktree does not exist")

top = subprocess.check_output(
    ["git", "-C", str(wt), "rev-parse", "--show-toplevel"],
    text=True,
).strip()
if pathlib.Path(top).resolve() != wt:
    raise SystemExit("managed worktree path is not the Git worktree root")

branch = subprocess.check_output(
    ["git", "-C", str(wt), "branch", "--show-current"],
    text=True,
).strip()
head = subprocess.check_output(
    ["git", "-C", str(wt), "rev-parse", "HEAD"],
    text=True,
).strip()
base = payload["base_sha"]
subprocess.check_call(
    ["git", "-C", str(wt), "merge-base", "--is-ancestor", base, head],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

origin = subprocess.check_output(
    ["git", "-C", str(wt), "remote", "get-url", "origin"],
    text=True,
).strip()
push_origin = subprocess.check_output(
    ["git", "-C", str(wt), "remote", "get-url", "--push", "origin"],
    text=True,
).strip()
rewrites = subprocess.run(
    [
        "git", "-C", str(wt), "config", "--show-origin", "--get-regexp",
        r"^url\..*\.(insteadOf|pushInsteadOf)$",
    ],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
).stdout.strip()

fd, index_path = tempfile.mkstemp(prefix="reasonfirst-index-")
os.close(fd)
os.unlink(index_path)
try:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    subprocess.check_call(["git", "-C", str(wt), "read-tree", "HEAD"], env=env)
    subprocess.check_call(["git", "-C", str(wt), "add", "-A"], env=env)
    tree = subprocess.check_output(
        ["git", "-C", str(wt), "write-tree"],
        env=env,
        text=True,
    ).strip()
    changed = subprocess.check_output(
        ["git", "-C", str(wt), "diff", "--cached", "--name-only", base, "--"],
        env=env,
        text=True,
    ).splitlines()
finally:
    try:
        os.unlink(index_path)
    except FileNotFoundError:
        pass

print(json.dumps({
    "branch": branch,
    "head_sha": head,
    "origin_url": origin,
    "push_origin_url": push_origin,
    "url_rewrites": rewrites,
    "candidate_tree": tree,
    "changed_paths": changed,
}))
"""


_COMMIT_PUSH_SCRIPT = r"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

payload = json.loads(sys.stdin.read())
root = pathlib.Path(payload["workspace_root"]).resolve()
wt = pathlib.Path(payload["worktree"]).resolve()
wt.relative_to(root)
if not wt.is_dir():
    raise SystemExit("managed worktree does not exist")

top = subprocess.check_output(
    ["git", "-C", str(wt), "rev-parse", "--show-toplevel"],
    text=True,
).strip()
if pathlib.Path(top).resolve() != wt:
    raise SystemExit("managed worktree path is not the Git worktree root")

git_lock = subprocess.check_output(
    ["git", "-C", str(wt), "rev-parse", "--git-path", "reasonfirst-publish.lock"],
    text=True,
).strip()
lock = pathlib.Path(git_lock)
if not lock.is_absolute():
    lock = (wt / lock).resolve()
try:
    lock.mkdir(mode=0o700)
except FileExistsError:
    raise SystemExit("another ReasonFirst publication is already in progress")

index_path = None
try:
    branch = subprocess.check_output(
        ["git", "-C", str(wt), "branch", "--show-current"],
        text=True,
    ).strip()
    if branch != payload["branch"]:
        raise SystemExit("branch changed after review")

    head = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if head != payload["head_sha"]:
        raise SystemExit("HEAD changed after review")

    subprocess.check_call(
        ["git", "-C", str(wt), "merge-base", "--is-ancestor", payload["base_sha"], head],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    rewrites = subprocess.run(
        [
            "git", "-C", str(wt), "config", "--show-origin", "--get-regexp",
            r"^url\..*\.(insteadOf|pushInsteadOf)$",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    ).stdout.strip()
    if rewrites:
        raise SystemExit(
            "Git URL rewrite rules are not allowed for reviewed publication"
        )

    origin = subprocess.check_output(
        ["git", "-C", str(wt), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    push_origin = subprocess.check_output(
        ["git", "-C", str(wt), "remote", "get-url", "--push", "origin"],
        text=True,
    ).strip()
    if origin != payload["origin_url"] or push_origin != payload["origin_url"]:
        raise SystemExit("Git origin/push destination changed after review")

    fd, index_path = tempfile.mkstemp(prefix="reasonfirst-index-")
    os.close(fd)
    os.unlink(index_path)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    subprocess.check_call(["git", "-C", str(wt), "read-tree", "HEAD"], env=env)
    subprocess.check_call(["git", "-C", str(wt), "add", "-A"], env=env)
    tree = subprocess.check_output(
        ["git", "-C", str(wt), "write-tree"],
        env=env,
        text=True,
    ).strip()
    if tree != payload["candidate_tree"]:
        raise SystemExit("candidate tree changed after review")

    subprocess.check_call(
        ["git", "-C", str(wt), "diff", "--cached", "--check", "HEAD", "--"],
        env=env,
    )
    head_tree = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()

    if tree == head_tree:
        commit_sha = head
    else:
        commit_proc = subprocess.run(
            ["git", "-C", str(wt), "commit-tree", tree, "-p", head],
            input=payload["message"] + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if commit_proc.returncode != 0:
            sys.stderr.write(commit_proc.stderr)
            raise SystemExit("git commit-tree failed")
        commit_sha = commit_proc.stdout.strip()
        subprocess.check_call(
            [
                "git", "-C", str(wt), "update-ref",
                f"refs/heads/{branch}", commit_sha, head,
            ]
        )
        # Synchronize the normal index to the exact committed tree without
        # overwriting worktree files. A concurrent/post-review worktree change
        # remains visible as a new dirty change rather than entering the commit.
        subprocess.check_call(
            ["git", "-C", str(wt), "read-tree", commit_sha]
        )

    current_ref = subprocess.check_output(
        ["git", "-C", str(wt), "rev-parse", f"refs/heads/{branch}"],
        text=True,
    ).strip()
    if current_ref != commit_sha:
        raise SystemExit("branch ref changed before push")

    push_env = os.environ.copy()
    push_env["GIT_TERMINAL_PROMPT"] = "0"
    for key in ("GIT_ASKPASS", "GIT_SSH_COMMAND", "SSH_AUTH_SOCK"):
        push_env.pop(key, None)
    push = subprocess.run(
        [
            "git", "-C", str(wt), "push",
            payload["origin_url"],
            f"{commit_sha}:refs/heads/{branch}",
        ],
        env=push_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if push.returncode != 0:
        sys.stderr.write(push.stderr)
        raise SystemExit("reviewed push failed")

    print(json.dumps({
        "commit_sha": commit_sha,
        "candidate_tree": tree,
        "branch": branch,
        "origin_url": origin,
        "stdout": push.stdout[-6000:],
        "stderr": push.stderr[-3000:],
    }))
finally:
    if index_path:
        try:
            os.unlink(index_path)
        except FileNotFoundError:
            pass
    try:
        lock.rmdir()
    except OSError:
        pass
"""


class ReviewedRemotePublisher:
    """Exact-candidate remote publication primitive.

    This is deliberately not exposed as an MCP/CLI write command. A higher
    level controlled-finish flow must complete validation, protected-path,
    reviewability, candidate/history-secret and human-review gates first.
    """

    def __init__(
        self,
        target: RemoteTarget,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        max_candidate_age_seconds: int = 900,
    ) -> None:
        self.target = target
        self._runner = runner or subprocess.run
        self.max_candidate_age_seconds = max(
            1, int(max_candidate_age_seconds)
        )

    def _ssh_json(
        self,
        script: str,
        payload: dict[str, Any],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        remote_command = "python3 -c " + shlex.quote(script)
        argv = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.target.ssh_connect_timeout}",
            "--",
            self.target.host,
            remote_command,
        ]
        try:
            proc = self._runner(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RemotePublicationError(
                f"SSH publication transport timed out for target {self.target.name!r}"
            ) from exc
        except OSError as exc:
            raise RemotePublicationError(
                f"Could not launch SSH publication transport for target {self.target.name!r}"
            ) from exc

        if proc.returncode != 0:
            detail = (
                proc.stderr
                or proc.stdout
                or "remote publication operation failed"
            )[-4000:]
            raise RemotePublicationError(
                f"Remote publication operation failed (exit {proc.returncode}): {detail}"
            )
        try:
            result = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            raise RemotePublicationError(
                "Remote publication operation returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise RemotePublicationError(
                "Remote publication operation returned a non-object result"
            )
        return result

    def capture(
        self,
        *,
        project: str,
        worktree_path: str,
        base_sha: str,
        branch: str,
        expected_origin_url: str,
    ) -> ReviewedRemoteCandidate:
        self.target.assert_project_allowed(project)
        worktree = _safe_abs_posix(worktree_path, "worktree_path")
        base = _safe_sha(base_sha, "base_sha")
        expected_branch = _safe_branch(branch)
        expected_origin = _safe_origin(expected_origin_url)

        result = self._ssh_json(
            _CAPTURE_SCRIPT,
            {
                "workspace_root": self.target.workspace_root,
                "worktree": worktree,
                "base_sha": base,
            },
            timeout=120,
        )

        current_branch = _safe_branch(str(result.get("branch") or ""))
        if current_branch != expected_branch:
            raise RemotePublicationError(
                "Remote branch does not match managed workspace state"
            )
        head = _safe_sha(str(result.get("head_sha") or ""), "head_sha")
        tree = _safe_sha(
            str(result.get("candidate_tree") or ""),
            "candidate_tree",
        )

        if str(result.get("url_rewrites") or "").strip():
            raise RemotePublicationError(
                "Git URL rewrite rules are not allowed for reviewed publication"
            )
        origin = _safe_origin(str(result.get("origin_url") or ""))
        push_origin = _safe_origin(
            str(result.get("push_origin_url") or "")
        )
        if origin != expected_origin or push_origin != expected_origin:
            raise RemotePublicationError(
                "Remote Git origin/push destination does not match managed workspace"
            )

        raw_paths = result.get("changed_paths")
        if not isinstance(raw_paths, list):
            raise RemotePublicationError(
                "Remote candidate did not return changed paths"
            )
        changed_paths = tuple(str(item) for item in raw_paths)
        captured_at = int(time.time())
        identity = {
            "project": project,
            "target_name": self.target.name,
            "target_host": self.target.host,
            "target_repo": self.target.repo,
            "workspace_root": self.target.workspace_root,
            "worktree_path": worktree,
            "base_sha": base,
            "head_sha": head,
            "branch": current_branch,
            "origin_url": origin,
            "candidate_tree": tree,
            "changed_paths": list(changed_paths),
            "captured_at": captured_at,
        }
        digest = _identity_digest(identity)
        return ReviewedRemoteCandidate(
            project=project,
            target_name=self.target.name,
            target_host=self.target.host,
            target_repo=self.target.repo,
            workspace_root=self.target.workspace_root,
            worktree_path=worktree,
            base_sha=base,
            head_sha=head,
            branch=current_branch,
            origin_url=origin,
            candidate_tree=tree,
            changed_paths=changed_paths,
            captured_at=captured_at,
            digest=digest,
        )

    def commit_and_push(
        self,
        candidate: ReviewedRemoteCandidate,
        *,
        message: str,
    ) -> dict[str, Any]:
        candidate.verify_digest()
        self.target.assert_project_allowed(candidate.project)
        if candidate.target_name != self.target.name:
            raise RemotePublicationError(
                "Reviewed candidate target name changed"
            )
        if candidate.target_host != self.target.host:
            raise RemotePublicationError(
                "Reviewed candidate target host changed"
            )
        if candidate.target_repo != self.target.repo:
            raise RemotePublicationError(
                "Reviewed candidate target repository changed"
            )
        if candidate.workspace_root != self.target.workspace_root:
            raise RemotePublicationError(
                "Reviewed candidate workspace root changed"
            )

        age = int(time.time()) - int(candidate.captured_at)
        if age < 0 or age > self.max_candidate_age_seconds:
            raise RemotePublicationError(
                "Reviewed remote candidate is stale; capture and review it again"
            )

        commit_message = str(message or "").strip()
        if (
            not commit_message
            or len(commit_message) > 240
            or "\n" in commit_message
            or "\r" in commit_message
        ):
            raise RemotePublicationError(
                "Commit message must be one non-empty line up to 240 characters"
            )

        result = self._ssh_json(
            _COMMIT_PUSH_SCRIPT,
            {
                "workspace_root": candidate.workspace_root,
                "worktree": candidate.worktree_path,
                "base_sha": candidate.base_sha,
                "head_sha": candidate.head_sha,
                "branch": candidate.branch,
                "origin_url": candidate.origin_url,
                "candidate_tree": candidate.candidate_tree,
                "message": commit_message,
            },
            timeout=240,
        )
        commit_sha = _safe_sha(
            str(result.get("commit_sha") or ""),
            "commit_sha",
        )
        returned_tree = _safe_sha(
            str(result.get("candidate_tree") or ""),
            "candidate_tree",
        )
        if returned_tree != candidate.candidate_tree:
            raise RemotePublicationError(
                "Published tree does not match reviewed candidate"
            )
        if str(result.get("branch") or "") != candidate.branch:
            raise RemotePublicationError(
                "Published branch does not match reviewed candidate"
            )
        if (
            _safe_origin(str(result.get("origin_url") or ""))
            != candidate.origin_url
        ):
            raise RemotePublicationError(
                "Published origin does not match reviewed candidate"
            )

        return {
            "ok": True,
            "review_digest": candidate.digest,
            "commit_sha": commit_sha,
            "candidate_tree": returned_tree,
            "branch": candidate.branch,
            "origin_url": candidate.origin_url,
            "stdout": str(result.get("stdout") or ""),
            "stderr": str(result.get("stderr") or ""),
        }
