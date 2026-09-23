from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from functools import wraps
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

from .config import AgentSettings
from .locking import file_lock
from .task_state import AttemptRecord, TaskSpec, bounded_attempts


_MR_URL_RE = re.compile(r"https?://[^\s]+/-/merge_requests/\d+")
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: str, max_bytes: int) -> tuple[str, bool, int]:
    raw = text.encode("utf-8", errors="replace")
    original = len(raw)
    if original <= max_bytes:
        return text, False, original
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return clipped, True, original


def _slug(text: str) -> str:
    value = text.strip().lower().replace(" ", "-")
    value = _SAFE_SLUG_RE.sub("-", value).strip("-._")
    return value[:48] or "task"


def _locked_workspace_mutation(method):
    """Serialize one workspace mutation across threads and ReasonFirst processes."""

    @wraps(method)
    def wrapped(self, workspace_id: str, *args, **kwargs):
        with self.mutation_lock(workspace_id):
            return method(self, workspace_id, *args, **kwargs)

    return wrapped


@dataclass
class WorkspaceState:
    workspace_id: str
    project: str
    repo_path: str
    worktree_path: str
    base_ref: str
    base_sha: str
    branch: str
    created_at: str
    pushed: bool = False
    last_commit: str | None = None
    remote_branch: str | None = None
    merge_request_url: str | None = None
    task_spec: dict[str, object] | None = None
    attempts: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "WorkspaceState":
        payload = dict(data)
        raw_spec = payload.get("task_spec")
        if isinstance(raw_spec, dict):
            payload["task_spec"] = TaskSpec.from_dict(raw_spec).to_dict()
        elif raw_spec is not None:
            raise ValueError("workspace task_spec must be an object or null")

        raw_attempts = payload.get("attempts", [])
        if not isinstance(raw_attempts, list) or any(
            not isinstance(item, dict) for item in raw_attempts
        ):
            raise ValueError("workspace attempts must be a list of objects")
        payload["attempts"] = bounded_attempts(
            [dict(item) for item in raw_attempts if isinstance(item, dict)]
        )
        return cls(**payload)  # type: ignore[arg-type]


class WorkspaceManager:
    """Owns cached Git repositories and isolated task worktrees."""

    @staticmethod
    def _progress(message: str) -> None:
        # Progress goes to stderr so stdout can remain machine-readable JSON.
        print(f"[gitlab-agent] {message}", file=sys.stderr, flush=True)

    def __init__(
        self,
        settings: AgentSettings,
        *,
        url_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.workspace_root.resolve()
        self.repos_dir = self.root / "repos"
        self.worktrees_dir = self.root / "worktrees"
        self.state_dir = self.root / "state"
        self.locks_dir = self.root / "locks"
        self.home_dir = self.root / "runner-home"
        for path in (
            self.repos_dir,
            self.worktrees_dir,
            self.state_dir,
            self.locks_dir,
            self.home_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._url_resolver = url_resolver

    # ------------------------------------------------------------------
    # State and path helpers
    # ------------------------------------------------------------------
    def _repo_key(self, project: str) -> str:
        digest = hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]
        readable = _slug(project.replace("/", "-"))[:36]
        return f"{readable}-{digest}"

    def _repo_path(self, project: str) -> Path:
        return self.repos_dir / f"{self._repo_key(project)}.git"

    def _state_path(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", workspace_id):
            raise ValueError("Invalid workspace_id")
        return self.state_dir / f"{workspace_id}.json"

    def mutation_lock(self, workspace_id: str):
        # Reuse state-path validation so arbitrary caller text cannot choose a lock path.
        self._state_path(workspace_id)
        return file_lock(
            self.locks_dir / f"{workspace_id}.lock",
            timeout_seconds=min(30.0, float(self.settings.command_timeout_seconds)),
        )

    def _save_state(self, state: WorkspaceState) -> None:
        path = self._state_path(state.workspace_id)
        if os.name != "nt":
            try:
                self.state_dir.chmod(0o700)
            except OSError:
                pass

        fd, temp_name = tempfile.mkstemp(
            prefix=f"{state.workspace_id}.",
            suffix=".json.tmp",
            dir=self.state_dir,
            text=True,
        )
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(state), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            if os.name != "nt":
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass

    def get_state(self, workspace_id: str) -> WorkspaceState:
        path = self._state_path(workspace_id)
        if not path.is_file():
            raise RuntimeError(f"Unknown workspace_id: {workspace_id}")
        return WorkspaceState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_states(self) -> list[WorkspaceState]:
        states: list[WorkspaceState] = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                states.append(
                    WorkspaceState.from_dict(json.loads(path.read_text(encoding="utf-8")))
                )
            except Exception:
                continue
        return states

    @_locked_workspace_mutation
    def ensure_task_spec(
        self,
        workspace_id: str,
        *,
        task_slug: str,
        goal: str,
        requested_backend: str,
        project_config_sha: str | None = None,
        acceptance_criteria: list[str] | tuple[str, ...] | None = None,
        non_goals: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, object]:
        state = self.get_state(workspace_id)
        if isinstance(state.task_spec, dict):
            spec = TaskSpec.from_dict(state.task_spec)
            if spec.project != state.project or spec.base_sha != state.base_sha:
                raise RuntimeError(
                    "Stored TaskSpec identity does not match workspace project/base SHA"
                )
            return spec.to_dict()

        spec = TaskSpec.create(
            task_slug=task_slug,
            goal=goal,
            project=state.project,
            base_ref=state.base_ref,
            base_sha=state.base_sha,
            requested_backend=requested_backend,
            project_config_sha=project_config_sha,
            acceptance_criteria=acceptance_criteria,
            non_goals=non_goals,
        )
        state.task_spec = spec.to_dict()
        self._save_state(state)
        return dict(state.task_spec)

    @_locked_workspace_mutation
    def record_attempt(
        self,
        workspace_id: str,
        *,
        source: str,
        goal: str,
        requested_backend: str,
        selected_backend: str,
        ci_context_included: bool,
        worker_policy: dict[str, object],
    ) -> dict[str, object]:
        state = self.get_state(workspace_id)
        record = AttemptRecord.create(
            source=source,
            goal=goal,
            requested_backend=requested_backend,
            selected_backend=selected_backend,
            ci_context_included=ci_context_included,
            worker_policy=dict(worker_policy),
        ).to_dict()
        state.attempts = bounded_attempts([*state.attempts, record])
        self._save_state(state)
        return dict(record)

    def task_context(self, workspace_id: str) -> dict[str, object]:
        state = self.get_state(workspace_id)
        return {
            "task_spec": (
                dict(state.task_spec)
                if isinstance(state.task_spec, dict)
                else None
            ),
            "attempts": [dict(item) for item in state.attempts],
            "attempt_count": len(state.attempts),
        }

    def _worktree(self, state: WorkspaceState) -> Path:
        path = Path(state.worktree_path).resolve()
        expected_root = self.worktrees_dir.resolve()
        try:
            path.relative_to(expected_root)
        except ValueError as exc:
            raise RuntimeError("Workspace path escaped configured worktree root") from exc
        if not path.is_dir():
            raise RuntimeError(f"Workspace directory no longer exists: {path}")
        return path

    def resolve_workspace_path(self, workspace_id: str, relative_path: str) -> Path:
        state = self.get_state(workspace_id)
        root = self._worktree(state)
        if not relative_path or relative_path in {".", "./"}:
            return root
        rel = Path(relative_path)
        if rel.is_absolute():
            raise ValueError("Workspace paths must be relative")
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Path escapes workspace root") from exc
        return candidate

    # ------------------------------------------------------------------
    # Git plumbing
    # ------------------------------------------------------------------
    def clone_url(self, project: str) -> str:
        if self._url_resolver is not None:
            return self._url_resolver(project)
        encoded = quote(project.strip("/"), safe="/-._~")
        return f"{self.settings.gitlab_base_url}/{encoded}.git"

    @contextmanager
    def _git_auth_env(self, *, require_token: bool) -> Iterator[dict[str, str]]:
        env = os.environ.copy()

        # Internal GitLab instances commonly need to bypass a host-wide
        # ALL_PROXY/HTTP_PROXY/HTTPS_PROXY (e.g. Clash/Surge/V2Ray). Match the
        # read-MCP default: direct network access unless explicitly opted in.
        if not self.settings.git_trust_env:
            for key in (
                "ALL_PROXY", "all_proxy",
                "HTTP_PROXY", "http_proxy",
                "HTTPS_PROXY", "https_proxy",
            ):
                env.pop(key, None)

        env["GIT_TERMINAL_PROMPT"] = "0"

        token = self.settings.git_token
        if require_token and not token:
            raise RuntimeError(
                "No Git credential is configured. Set GITLAB_GIT_TOKEN "
                "(recommended for ActualCoder writes) or GITLAB_TOKEN."
            )
        if not token:
            yield env
            return

        suffix = ".cmd" if os.name == "nt" else ""
        fd, script_name = tempfile.mkstemp(
            prefix="gitlab-agent-askpass-",
            suffix=suffix,
            text=True,
        )
        script = Path(script_name)
        try:
            if os.name == "nt":
                askpass = (
                    "@echo off\r\n"
                    "setlocal\r\n"
                    "set \"prompt=%~1\"\r\n"
                    "if /I not \"%prompt:Username=%\"==\"%prompt%\" (\r\n"
                    "  echo %GITLAB_AGENT_GIT_USERNAME%\r\n"
                    ") else (\r\n"
                    "  echo %GITLAB_AGENT_GIT_TOKEN%\r\n"
                    ")\r\n"
                )
            else:
                askpass = (
                    "#!/bin/sh\n"
                    "case \"$1\" in\n"
                    "  *Username*) printf '%s\\n' \"$GITLAB_AGENT_GIT_USERNAME\" ;;\n"
                    "  *)          printf '%s\\n' \"$GITLAB_AGENT_GIT_TOKEN\" ;;\n"
                    "esac\n"
                )

            os.write(fd, askpass.encode("utf-8"))
            os.close(fd)
            if os.name != "nt":
                script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            env["GIT_ASKPASS"] = str(script)
            env["GIT_ASKPASS_REQUIRE"] = "force"
            env["GITLAB_AGENT_GIT_USERNAME"] = self.settings.git_username
            env["GITLAB_AGENT_GIT_TOKEN"] = token
            yield env
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            script.unlink(missing_ok=True)

    def _run_git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
        auth: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        with self._git_auth_env(require_token=auth) as env:
            if self.settings.git_author_name:
                env["GIT_AUTHOR_NAME"] = self.settings.git_author_name
                env["GIT_COMMITTER_NAME"] = self.settings.git_author_name
            if self.settings.git_author_email:
                env["GIT_AUTHOR_EMAIL"] = self.settings.git_author_email
                env["GIT_COMMITTER_EMAIL"] = self.settings.git_author_email

            git_argv = ["git"]
            if not self.settings.git_trust_env:
                # Also override any proxy configured in ~/.gitconfig.
                git_argv.extend(["-c", "http.proxy="])
            git_argv.extend(args)

            proc = subprocess.run(
                git_argv,
                cwd=cwd,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=max(30, self.settings.command_timeout_seconds),
                check=False,
            )

        if check and proc.returncode != 0:
            stdout, _, _ = _clip(proc.stdout, 12000)
            stderr, _, _ = _clip(proc.stderr, 12000)
            raise RuntimeError(
                "git command failed: "
                + " ".join(["git", *args[:6]])
                + f"\nexit={proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
            )
        return proc

    def _remote_needs_auth(self, remote_url: str) -> bool:
        return remote_url.startswith(("http://", "https://"))

    def _is_ancestor(self, repo_path: Path, ancestor: str, descendant: str) -> bool:
        result = self._run_git(
            ["--git-dir", str(repo_path), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError("Could not establish commit ancestry; preserving local work")
        return result.returncode == 0

    def _fetch_published_tip(self, state: WorkspaceState, repo_path: Path) -> str:
        """Fetch fresh publication evidence, never trusting a cached tracking ref."""
        remote_url = self.clone_url(state.project)
        configured_url = self._run_git(
            ["--git-dir", str(repo_path), "remote", "get-url", "origin"]
        ).stdout.strip()
        if configured_url != remote_url:
            raise RuntimeError("Cached repository remote mismatch; preserving local work")

        # A unique ref avoids consulting stale refs or overwriting shared FETCH_HEAD.
        probe_ref = f"refs/actualcoder/publication/{uuid.uuid4().hex}"
        try:
            self._run_git(
                [
                    "--git-dir", str(repo_path), "fetch", "--no-tags",
                    "--no-write-fetch-head", remote_url,
                    f"refs/heads/{state.branch}:{probe_ref}",
                ],
                auth=self._remote_needs_auth(remote_url),
            )
            return self._run_git(
                ["--git-dir", str(repo_path), "rev-parse", "--verify", f"{probe_ref}^{{commit}}"]
            ).stdout.strip()
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                "Cannot verify remote publication; preserving local work. "
                "Check remote branch availability/network/authentication, then retry. "
                "Use cleanup --force only to intentionally discard local work."
            ) from exc
        finally:
            self._run_git(
                ["--git-dir", str(repo_path), "update-ref", "-d", probe_ref],
                check=False,
            )

    def _cleanup_paths(self, state: WorkspaceState) -> tuple[Path, Path]:
        """Validate repository identity before any destructive cleanup operation."""
        self.settings.assert_project_allowed_for_workspace(state.project)
        repo_path = self._repo_path(state.project).resolve()
        worktree = self._worktree(state)
        if Path(state.repo_path).resolve() != repo_path:
            raise RuntimeError("Workspace repository path mismatch; preserving local work")
        common_dir = self._run_git(["rev-parse", "--git-common-dir"], cwd=worktree).stdout.strip()
        if (worktree / common_dir).resolve() != repo_path:
            raise RuntimeError("Workspace repository identity mismatch; preserving local work")
        if (
            not state.branch.startswith(self.settings.branch_prefix)
            or state.branch == state.base_ref
        ):
            raise RuntimeError("Refusing cleanup of a branch outside managed feature-branch policy")
        self._run_git(["check-ref-format", f"refs/heads/{state.branch}"])
        return repo_path, worktree

    def _ensure_cached_repo(self, project: str) -> Path:
        self.settings.assert_project_allowed_for_workspace(project)
        repo_path = self._repo_path(project)
        remote_url = self.clone_url(project)
        auth = self._remote_needs_auth(remote_url)

        if not repo_path.exists():
            self._progress(f"cloning repository cache for {project} ...")
            self._run_git(
                ["clone", "--bare", remote_url, str(repo_path)],
                auth=auth,
            )
            self._progress(f"repository cache ready: {repo_path}")
            self._run_git(
                [
                    "--git-dir",
                    str(repo_path),
                    "config",
                    "remote.origin.fetch",
                    "+refs/heads/*:refs/remotes/origin/*",
                ]
            )
        else:
            current_remote = self._run_git(
                ["--git-dir", str(repo_path), "remote", "get-url", "origin"]
            ).stdout.strip()
            if current_remote != remote_url:
                raise RuntimeError(
                    f"Cached repository remote mismatch for {project!r}: "
                    f"{current_remote!r} != {remote_url!r}"
                )

        self._progress(f"fetching latest refs for {project} ...")
        self._run_git(
            ["--git-dir", str(repo_path), "fetch", "--prune", "--tags", "origin"],
            auth=auth,
        )
        self._progress(f"fetch complete for {project}")
        return repo_path

    def _resolve_base_sha(self, repo_path: Path, base_ref: str) -> str:
        candidates = [
            f"refs/remotes/origin/{base_ref}",
            f"refs/tags/{base_ref}",
            base_ref,
        ]
        for candidate in candidates:
            proc = self._run_git(
                [
                    "--git-dir",
                    str(repo_path),
                    "rev-parse",
                    "--verify",
                    f"{candidate}^{{commit}}",
                ],
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        raise RuntimeError(f"Could not resolve base ref {base_ref!r}")

    def read_remote_text_file(
        self,
        project: str,
        relative_path: str,
        *,
        ref: str | None = None,
        refresh_remote: bool = True,
        max_bytes: int | None = None,
    ) -> dict[str, object]:
        """Read a UTF-8 text file from a fetched remote ref without creating a worktree."""

        project = project.strip().strip("/")
        if not project or "/" not in project:
            raise ValueError("project must be a GitLab path_with_namespace")

        rel = relative_path.strip().replace("\\", "/")
        if (
            not rel
            or rel.startswith("/")
            or ":" in rel
            or any(part in {"", ".", ".."} for part in rel.split("/"))
        ):
            raise ValueError("relative_path must be a safe repository-relative path")

        if refresh_remote:
            repo_path = self._ensure_cached_repo(project)
        else:
            self.settings.assert_project_allowed_for_workspace(project)
            repo_path = self._repo_path(project)
            if not repo_path.is_dir():
                raise RuntimeError(
                    f"Repository cache is not prepared for {project!r}; "
                    "refresh_remote=False requires an existing managed cache"
                )

        effective_ref = (ref or self.settings.default_base_ref).strip()
        commit_sha = self._resolve_base_sha(repo_path, effective_ref)
        spec = f"{commit_sha}:{rel}"

        exists = self._run_git(
            ["--git-dir", str(repo_path), "cat-file", "-e", spec],
            check=False,
        )
        if exists.returncode != 0:
            return {
                "project": project,
                "ref": effective_ref,
                "commit_sha": commit_sha,
                "path": rel,
                "exists": False,
                "content": None,
            }

        if max_bytes is not None:
            if max_bytes <= 0:
                raise ValueError("max_bytes must be positive")
            size_proc = self._run_git(
                ["--git-dir", str(repo_path), "cat-file", "-s", spec],
            )
            try:
                blob_size = int(size_proc.stdout.strip())
            except ValueError as exc:
                raise RuntimeError(
                    f"Could not determine size for {rel!r} at {effective_ref!r}"
                ) from exc
            if blob_size > max_bytes:
                raise RuntimeError(
                    f"{rel} is too large to read: {blob_size} bytes; "
                    f"maximum is {max_bytes}"
                )

        shown = self._run_git(
            ["--git-dir", str(repo_path), "show", spec],
            check=False,
        )
        if shown.returncode != 0:
            raise RuntimeError(
                f"Could not read {rel!r} from {project!r} at ref {effective_ref!r}"
            )

        return {
            "project": project,
            "ref": effective_ref,
            "commit_sha": commit_sha,
            "path": rel,
            "exists": True,
            "content": shown.stdout,
        }

    # ------------------------------------------------------------------
    # Workspace lifecycle
    # ------------------------------------------------------------------
    def create_workspace(
        self,
        project: str,
        *,
        base_ref: str | None = None,
        task_slug: str = "task",
        refresh_remote: bool = True,
    ) -> dict[str, object]:
        project = project.strip().strip("/")
        if not project or "/" not in project:
            raise ValueError(
                "Managed workspaces require GitLab path_with_namespace, e.g. team/project"
            )

        self._progress(f"preparing workspace for {project}")
        if refresh_remote:
            repo_path = self._ensure_cached_repo(project)
        else:
            self.settings.assert_project_allowed_for_workspace(project)
            repo_path = self._repo_path(project)
            if not repo_path.is_dir():
                raise RuntimeError(
                    f"Repository cache is not prepared for {project!r}; "
                    "refresh_remote=False requires a prior fetch in the same workflow"
                )
            self._progress(
                f"reusing already-fetched repository cache for {project}: {repo_path}"
            )

        effective_base = (base_ref or self.settings.default_base_ref).strip()
        self._progress(f"resolving base ref {effective_base} ...")
        base_sha = self._resolve_base_sha(repo_path, effective_base)

        workspace_id = uuid.uuid4().hex[:12]
        branch = f"{self.settings.branch_prefix}{_slug(task_slug)}-{workspace_id[:8]}"
        worktree_path = self.worktrees_dir / workspace_id

        self._progress(f"creating worktree {workspace_id} on branch {branch} ...")
        self._run_git(
            [
                "--git-dir",
                str(repo_path),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_path),
                base_sha,
            ]
        )

        state = WorkspaceState(
            workspace_id=workspace_id,
            project=project,
            repo_path=str(repo_path),
            worktree_path=str(worktree_path),
            base_ref=effective_base,
            base_sha=base_sha,
            branch=branch,
            created_at=_utc_now(),
            last_commit=base_sha,
        )
        self._save_state(state)
        self._progress(f"workspace ready: {worktree_path}")
        return self.status(workspace_id)

    def checkout_remote_branch(
        self,
        project: str,
        branch: str,
        *,
        base_ref: str | None = None,
        merge_request_url: str | None = None,
    ) -> dict[str, object]:
        project = project.strip().strip("/")
        branch = branch.strip()
        if not project or "/" not in project:
            raise ValueError("project must be a GitLab path_with_namespace")
        if not branch:
            raise ValueError("branch must not be empty")
        self.settings.assert_project_allowed_for_workspace(project)
        if not branch.startswith(self.settings.branch_prefix):
            raise RuntimeError(
                f"Refusing to manage remote branch {branch!r}: it does not start with "
                f"configured prefix {self.settings.branch_prefix!r}"
            )

        self._progress(f"reconstructing workspace for {project}:{branch}")
        repo_path = self._ensure_cached_repo(project)

        remote_ref = f"refs/remotes/origin/{branch}"
        remote_sha_proc = self._run_git(
            [
                "--git-dir",
                str(repo_path),
                "rev-parse",
                "--verify",
                f"{remote_ref}^{{commit}}",
            ],
            check=False,
        )
        if remote_sha_proc.returncode != 0:
            raise RuntimeError(
                f"Remote branch {branch!r} was not found after fetch for project {project!r}"
            )
        remote_sha = remote_sha_proc.stdout.strip()

        effective_base = (base_ref or self.settings.default_base_ref).strip()
        base_sha = self._resolve_base_sha(repo_path, effective_base)

        workspace_id = uuid.uuid4().hex[:12]
        worktree_path = self.worktrees_dir / workspace_id

        # The bare cache may retain a local branch from an abandoned workspace.
        # Refuse to reuse a branch that is already checked out by another worktree.
        existing = self._run_git(
            ["--git-dir", str(repo_path), "show-ref", "--verify", f"refs/heads/{branch}"],
            check=False,
        )
        if existing.returncode == 0:
            worktree_list = self._run_git(
                ["--git-dir", str(repo_path), "worktree", "list", "--porcelain"]
            ).stdout
            if f"branch refs/heads/{branch}\n" in worktree_list:
                raise RuntimeError(
                    f"Branch {branch!r} is already checked out in a managed Git worktree. "
                    "Use gitlab-agent list/resume instead of checkout-branch/checkout-mr."
                )
            local_sha = existing.stdout.split()[0]
            if not self._is_ancestor(repo_path, local_sha, remote_sha):
                raise RuntimeError(
                    f"Local branch {branch!r} contains unpublished commits. "
                    "Preserve/recover that branch before reconstructing its remote MR."
                )
            # Delete only the exact tip whose publication was checked above.
            self._run_git(
                ["--git-dir", str(repo_path), "update-ref", "-d", f"refs/heads/{branch}", local_sha]
            )

        self._progress(f"creating worktree {workspace_id} from origin/{branch} ...")
        self._run_git(
            [
                "--git-dir",
                str(repo_path),
                "worktree",
                "add",
                "-b",
                branch,
                str(worktree_path),
                remote_sha,
            ]
        )
        self._run_git(
            [
                "branch",
                "--set-upstream-to",
                f"origin/{branch}",
                branch,
            ],
            cwd=worktree_path,
        )

        state = WorkspaceState(
            workspace_id=workspace_id,
            project=project,
            repo_path=str(repo_path),
            worktree_path=str(worktree_path),
            base_ref=effective_base,
            base_sha=base_sha,
            branch=branch,
            created_at=_utc_now(),
            pushed=True,
            last_commit=remote_sha,
            remote_branch=branch,
            merge_request_url=merge_request_url,
        )
        self._save_state(state)
        self._progress(f"workspace restored: {worktree_path}")
        return self.status(workspace_id)

    def status(self, workspace_id: str) -> dict[str, object]:
        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        porcelain = self._run_git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=worktree,
        ).stdout
        head = self._run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        branch = self._run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree,
        ).stdout.strip()
        ahead = int(
            self._run_git(
                ["rev-list", "--count", f"{state.base_sha}..HEAD"],
                cwd=worktree,
            ).stdout.strip()
            or "0"
        )
        return {
            **asdict(state),
            "head": head,
            "branch": branch,
            "dirty": bool(porcelain.strip()),
            "status_porcelain": porcelain,
            "commits_ahead_of_base": ahead,
        }

    def list_files(
        self,
        workspace_id: str,
        path: str = ".",
        *,
        recursive: bool = False,
        max_entries: int = 500,
    ) -> dict[str, object]:
        root = self.resolve_workspace_path(workspace_id, path)
        if not root.exists():
            raise FileNotFoundError(path)

        items: list[dict[str, object]] = []
        if root.is_file():
            items.append(
                {
                    "path": str(root.relative_to(self._worktree(self.get_state(workspace_id)))),
                    "type": "file",
                    "size": root.stat().st_size,
                }
            )
        else:
            iterator = root.rglob("*") if recursive else root.iterdir()
            workspace_root = self._worktree(self.get_state(workspace_id))
            for child in iterator:
                if len(items) >= max_entries:
                    break
                try:
                    rel = child.relative_to(workspace_root)
                except ValueError:
                    continue
                items.append(
                    {
                        "path": str(rel),
                        "type": "dir" if child.is_dir() else "file",
                        "size": None if child.is_dir() else child.stat().st_size,
                    }
                )
        return {
            "workspace_id": workspace_id,
            "path": path,
            "recursive": recursive,
            "truncated": len(items) >= max_entries,
            "items": items,
        }

    def read_file(self, workspace_id: str, path: str) -> dict[str, object]:
        target = self.resolve_workspace_path(workspace_id, path)
        if not target.is_file():
            raise FileNotFoundError(path)
        raw = target.read_bytes()
        if len(raw) > self.settings.max_file_bytes:
            raise RuntimeError(
                f"File is {len(raw)} bytes; limit is {self.settings.max_file_bytes}"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Only UTF-8 text files are supported") from exc
        return {
            "workspace_id": workspace_id,
            "path": path,
            "size": len(raw),
            "content": text,
        }

    @_locked_workspace_mutation
    def write_file(self, workspace_id: str, path: str, content: str) -> dict[str, object]:
        raw = content.encode("utf-8")
        if len(raw) > self.settings.max_file_bytes:
            raise RuntimeError(
                f"Write is {len(raw)} bytes; limit is {self.settings.max_file_bytes}"
            )
        target = self.resolve_workspace_path(workspace_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {
            "workspace_id": workspace_id,
            "path": path,
            "bytes_written": len(raw),
        }

    @_locked_workspace_mutation
    def apply_patch(self, workspace_id: str, patch: str) -> dict[str, object]:
        if len(patch.encode("utf-8")) > self.settings.max_file_bytes * 2:
            raise RuntimeError("Patch exceeds configured size limit")
        worktree = self._worktree(self.get_state(workspace_id))
        self._run_git(
            ["apply", "--whitespace=nowarn", "-"],
            cwd=worktree,
            input_text=patch,
        )
        return self.diff(workspace_id)

    def _untracked_diff(self, worktree: Path) -> str:
        status = self._run_git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=worktree,
        ).stdout

        chunks: list[str] = []
        for raw_line in status.splitlines():
            if not raw_line.startswith("?? "):
                continue

            rel_path = raw_line[3:]
            target = (worktree / rel_path).resolve()
            try:
                target.relative_to(worktree)
            except ValueError:
                continue

            if not target.is_file():
                continue

            raw = target.read_bytes()
            if len(raw) > self.settings.max_file_bytes:
                chunks.append(
                    f"diff --git a/{rel_path} b/{rel_path}\n"
                    f"new file mode 100644\n"
                    f"--- /dev/null\n"
                    f"+++ b/{rel_path}\n"
                    f"@@ file too large to preview: {len(raw)} bytes @@\n"
                )
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                chunks.append(
                    f"diff --git a/{rel_path} b/{rel_path}\n"
                    f"new file mode 100644\n"
                    f"Binary files /dev/null and b/{rel_path} differ\n"
                )
                continue

            diff_lines = difflib.unified_diff(
                [],
                text.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"b/{rel_path}",
            )
            body = "".join(diff_lines)
            chunks.append(
                f"diff --git a/{rel_path} b/{rel_path}\n"
                f"new file mode 100644\n"
                + body
            )

        return "\n".join(chunks)

    def changed_paths(self, workspace_id: str) -> list[str]:
        """Return tracked + untracked paths changed relative to the workspace base."""

        state = self.get_state(workspace_id)
        worktree = self._worktree(state)

        tracked = self._run_git(
            [
                "diff",
                "--name-only",
                "--no-ext-diff",
                state.base_sha,
                "--",
                ".",
            ],
            cwd=worktree,
        ).stdout.splitlines()
        untracked = self._run_git(
            ["ls-files", "--others", "--exclude-standard"],
            cwd=worktree,
        ).stdout.splitlines()

        return sorted(
            {
                path.strip().replace("\\", "/")
                for path in [*tracked, *untracked]
                if path.strip()
            }
        )

    def reviewability(
        self,
        workspace_id: str,
        changed_paths: list[str] | None = None,
    ) -> dict[str, object]:
        """Check whether changed working-tree content can be fully reviewed as UTF-8 text."""

        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        paths = changed_paths if changed_paths is not None else self.changed_paths(workspace_id)

        issues: list[dict[str, object]] = []
        existing_file_bytes = 0
        max_changed_paths = 256
        max_total_bytes = 16 * 1024 * 1024

        if len(paths) > max_changed_paths:
            issues.append(
                {
                    "path": "<workspace>",
                    "reason": "too_many_changed_paths",
                    "count": len(paths),
                    "limit": max_changed_paths,
                }
            )

        for rel_path in paths[: max_changed_paths + 1]:
            rel = Path(rel_path)
            if rel.is_absolute() or ".." in rel.parts:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "unsafe_changed_path",
                    }
                )
                continue

            candidate = worktree / rel
            if candidate.is_symlink():
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "symlink_change",
                    }
                )
                continue

            if not candidate.exists():
                # Deletions are represented completely by Git diff metadata/content.
                continue

            resolved = candidate.resolve()
            try:
                resolved.relative_to(worktree)
            except ValueError:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "path_escaped_worktree",
                    }
                )
                continue

            if not resolved.is_file():
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "non_regular_file",
                    }
                )
                continue

            try:
                size = resolved.stat().st_size
            except OSError as exc:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "stat_failed",
                        "error": str(exc),
                    }
                )
                continue

            existing_file_bytes += size
            if existing_file_bytes > max_total_bytes:
                issues.append(
                    {
                        "path": "<workspace>",
                        "reason": "changed_content_too_large",
                        "bytes": existing_file_bytes,
                        "limit": max_total_bytes,
                    }
                )
                break

            if size > self.settings.max_file_bytes:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "file_too_large",
                        "bytes": size,
                        "limit": self.settings.max_file_bytes,
                    }
                )
                continue

            try:
                resolved.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "binary_or_non_utf8",
                        "bytes": size,
                    }
                )
            except OSError as exc:
                issues.append(
                    {
                        "path": rel_path,
                        "reason": "read_failed",
                        "error": str(exc),
                    }
                )

        return {
            "ok": not issues,
            "changed_path_count": len(paths),
            "existing_file_bytes": existing_file_bytes,
            "max_changed_paths": max_changed_paths,
            "max_total_bytes": max_total_bytes,
            "issues": issues,
        }

    def security_diff(self, workspace_id: str) -> str:
        """Return the complete base-to-working-tree patch used for secret scanning."""

        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        tracked = self._run_git(
            [
                "diff",
                "--no-ext-diff",
                "--unified=0",
                state.base_sha,
                "--",
                ".",
            ],
            cwd=worktree,
        ).stdout
        return tracked + "\n" + self._untracked_diff(worktree)

    def latest_commit_subject(self, workspace_id: str) -> str:
        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        return self._run_git(
            ["log", "-1", "--pretty=%s"],
            cwd=worktree,
        ).stdout.strip()

    def diff(self, workspace_id: str) -> dict[str, object]:
        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        working = self._run_git(
            ["diff", "--no-ext-diff", "--", "."],
            cwd=worktree,
        ).stdout
        staged = self._run_git(
            ["diff", "--cached", "--no-ext-diff", "--", "."],
            cwd=worktree,
        ).stdout
        committed = self._run_git(
            ["diff", "--no-ext-diff", f"{state.base_sha}..HEAD", "--", "."],
            cwd=worktree,
        ).stdout
        untracked = self._untracked_diff(worktree)

        budget = self.settings.max_output_bytes
        combined = (
            "### COMMITTED SINCE BASE\n"
            + committed
            + "\n### STAGED\n"
            + staged
            + "\n### UNSTAGED\n"
            + working
            + "\n### UNTRACKED\n"
            + untracked
        )
        clipped, truncated, original_bytes = _clip(combined, budget)
        return {
            "workspace_id": workspace_id,
            "base_sha": state.base_sha,
            "truncated": truncated,
            "original_bytes": original_bytes,
            "diff": clipped,
        }

    @_locked_workspace_mutation
    def commit(self, workspace_id: str, message: str) -> dict[str, object]:
        if not message.strip():
            raise ValueError("Commit message must not be empty")
        state = self.get_state(workspace_id)
        worktree = self._worktree(state)
        self._run_git(["add", "-A"], cwd=worktree)
        staged_check = self._run_git(["diff", "--cached", "--quiet"], cwd=worktree, check=False)
        if staged_check.returncode == 0:
            raise RuntimeError("Nothing to commit")
        self._run_git(["commit", "-m", message.strip()], cwd=worktree)
        head = self._run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        state.last_commit = head
        self._save_state(state)
        return self.status(workspace_id)

    def _assert_pushable(self, state: WorkspaceState) -> tuple[Path, int]:
        worktree = self._worktree(state)
        if not state.branch.startswith(self.settings.branch_prefix):
            raise RuntimeError("Refusing to push a branch outside configured branch prefix")
        if state.branch == state.base_ref:
            raise RuntimeError("Refusing to push directly to the base branch")

        status = self.status(state.workspace_id)
        if status["dirty"]:
            raise RuntimeError("Workspace has uncommitted changes; commit before pushing")
        ahead = int(status["commits_ahead_of_base"])
        if ahead <= 0:
            raise RuntimeError("Workspace has no commits ahead of its base")
        return worktree, ahead

    @_locked_workspace_mutation
    def push(self, workspace_id: str) -> dict[str, object]:
        state = self.get_state(workspace_id)
        was_already_pushed = state.pushed
        existing_mr_url = state.merge_request_url
        worktree, _ = self._assert_pushable(state)
        remote_url = self._run_git(
            ["remote", "get-url", "origin"],
            cwd=worktree,
        ).stdout.strip()
        auth = self._remote_needs_auth(remote_url)

        self._progress(
            f"{'updating' if was_already_pushed else 'pushing'} remote branch {state.branch} ..."
        )
        proc = self._run_git(
            ["push", "--set-upstream", "origin", state.branch],
            cwd=worktree,
            auth=auth,
        )
        state.pushed = True
        state.remote_branch = state.branch
        state.last_commit = self._run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        self._save_state(state)
        return {
            "workspace": self.status(workspace_id),
            "updated_existing_branch": was_already_pushed,
            "merge_request_url": existing_mr_url,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    @_locked_workspace_mutation
    def push_and_create_mr(
        self,
        workspace_id: str,
        *,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> dict[str, object]:
        state = self.get_state(workspace_id)
        if state.pushed:
            raise RuntimeError(
                "This workspace branch is already marked as pushed. "
                "For the managed no-broad-API flow, create the MR on the first push "
                "with push-mr rather than calling push first."
            )
        worktree, _ = self._assert_pushable(state)
        if not target_branch.strip():
            raise ValueError("target_branch must not be empty")
        if not title.strip():
            raise ValueError("MR title must not be empty")

        remote_url = self._run_git(
            ["remote", "get-url", "origin"],
            cwd=worktree,
        ).stdout.strip()
        auth = self._remote_needs_auth(remote_url)

        # Git push options cannot contain LF/NUL. Keep the initial MR description
        # intentionally simple; richer editing can be added later with a narrower API permission.
        safe_title = " ".join(title.replace("\x00", "").splitlines()).strip()
        safe_description = " ".join(description.replace("\x00", "").splitlines()).strip()
        if len(safe_title) > 240:
            raise ValueError("MR title is too long")
        if len(safe_description) > 4000:
            raise ValueError("MR description is too long")

        args = [
            "push",
            "--set-upstream",
            "-o",
            "merge_request.create",
            "-o",
            f"merge_request.target={target_branch.strip()}",
            "-o",
            f"merge_request.title={safe_title}",
        ]
        if safe_description:
            args.extend(["-o", f"merge_request.description={safe_description}"])
        args.extend(["origin", state.branch])

        proc = self._run_git(args, cwd=worktree, auth=auth)
        combined = proc.stdout + "\n" + proc.stderr
        match = _MR_URL_RE.search(combined)

        state.pushed = True
        state.remote_branch = state.branch
        state.last_commit = self._run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        state.merge_request_url = match.group(0).rstrip(".,)") if match else None
        self._save_state(state)
        return {
            "workspace": self.status(workspace_id),
            "merge_request_url": state.merge_request_url,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    @_locked_workspace_mutation
    def cleanup(self, workspace_id: str, *, force: bool = False) -> dict[str, object]:
        state = self.get_state(workspace_id)
        repo_path, worktree = self._cleanup_paths(state)
        status = self.status(workspace_id)
        if status["branch"] != state.branch:
            raise RuntimeError("Workspace branch changed; preserving local work")
        head = str(status["head"])

        if not force:
            if status["dirty"]:
                raise RuntimeError("Workspace has uncommitted changes; use force to discard")
            if int(status["commits_ahead_of_base"]) > 0:
                # `pushed` records a past event, not publication of the current HEAD.
                # This also reconciles a successful push followed by a state-save failure.
                published_tip = self._fetch_published_tip(state, repo_path)
                if not self._is_ancestor(repo_path, head, published_tip):
                    raise RuntimeError(
                        "Workspace has unpublished commits; push them before cleanup, "
                        "or use --force only to intentionally discard local work"
                    )

        # Remote verification can take time. Refuse if a writer changed the reviewed
        # state in the meantime. This is not a replacement for process locking.
        self._cleanup_paths(state)
        current = self.status(workspace_id)
        if (
            self.get_state(workspace_id) != state
            or any(current[key] != status[key] for key in ("head", "branch", "status_porcelain"))
        ):
            raise RuntimeError("Workspace changed during cleanup checks; preserving local work")

        args = ["--git-dir", str(repo_path), "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(worktree))
        self._run_git(args)

        # Compare-and-delete prevents removing a branch advanced after our check.
        # On failure keep the branch and metadata for explicit recovery.
        self._run_git(
            ["--git-dir", str(repo_path), "update-ref", "-d", f"refs/heads/{state.branch}", head]
        )
        self._state_path(workspace_id).unlink(missing_ok=True)
        return {"workspace_id": workspace_id, "removed": True}
