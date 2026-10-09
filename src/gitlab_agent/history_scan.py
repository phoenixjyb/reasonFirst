"""Bounded, local-only inspection of added text in base..HEAD commit history."""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .secret_scan import scan_added_diff_for_secrets


MAX_HISTORY_COMMITS = 256
MAX_HISTORY_BYTES = 8 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_SUBMODULE = re.compile(
    r"^(?:(?:old|new|new file|deleted file) mode 160000|"
    r"index [0-9a-f]+\.\.[0-9a-f]+ 160000)$", re.MULTILINE,
)


class HistoryScanError(RuntimeError):
    """Coverage is incomplete; callers must not treat this as a clean scan."""


def _bounded_git(
    worktree: Path, args: list[str], *, deadline: float, max_bytes: int,
    child_context=None,
) -> bytes:
    """Read fixed Git plumbing output without unbounded communicate() buffering."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HistoryScanError("History scan timed out; coverage is incomplete")
    retained = child_context.environment_copy() if child_context is not None else os.environ
    env = {
        k: v for k, v in retained.items()
        if not k.upper().startswith("GIT_")
        and not any(x in k.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    }
    env.update({
        "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    })
    executable = (child_context.resolve_executable("git", cwd=worktree)
                  if child_context is not None else "git")
    try:
        proc = subprocess.Popen(
            [executable, "--no-pager", "--no-replace-objects", "-c", "protocol.allow=never",
             "-c", "core.quotePath=true", *args],
            cwd=worktree, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise HistoryScanError("Cannot start Git history scan; coverage is incomplete") from exc

    data = bytearray()
    exceeded = threading.Event()
    read_failed = threading.Event()

    def read_output() -> None:
        assert proc.stdout is not None
        try:
            with proc.stdout:
                while True:
                    chunk = proc.stdout.read(min(65536, max_bytes - len(data) + 1))
                    if not chunk:
                        break
                    if len(data) + len(chunk) > max_bytes:
                        exceeded.set()
                        proc.kill()
                        break
                    data.extend(chunk)
        except OSError:
            read_failed.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        reader.join(timeout=max(0.001, deadline - time.monotonic()))
        if reader.is_alive():
            raise HistoryScanError("History output read timed out; coverage is incomplete")
    except subprocess.TimeoutExpired as exc:
        raise HistoryScanError("History scan timed out; coverage is incomplete") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        reader.join(timeout=1)

    if exceeded.is_set():
        raise HistoryScanError("History output exceeds the byte limit; coverage is incomplete")
    if read_failed.is_set() or proc.returncode != 0:
        # Git stderr and partial patch output may contain secrets; never export them.
        raise HistoryScanError("Git history inspection failed; coverage is incomplete")
    return bytes(data)


def scan_history_secrets(
    *, worktree: Path, base_sha: str, head_sha: str,
    timeout_seconds: int = 300, max_commits: int = MAX_HISTORY_COMMITS,
    max_bytes: int = MAX_HISTORY_BYTES,
    child_context=None,
) -> dict[str, object]:
    """Scan a conservative superset of outgoing commits, never a cached pushed flag.

    All added patch lines in base..HEAD are scanned, including separate diffs
    against each merge parent. Already-pushed commits in this range are rescanned.
    This is not an audit of base history, commit messages or external LFS objects.
    """
    if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(head_sha):
        raise HistoryScanError("History scan requires full immutable commit SHAs")
    if not (1 <= max_commits <= MAX_HISTORY_COMMITS and 1 <= max_bytes <= MAX_HISTORY_BYTES):
        raise HistoryScanError("Invalid history scan limits")
    if timeout_seconds <= 0:
        raise HistoryScanError("Invalid history scan timeout")
    deadline = time.monotonic() + min(timeout_seconds, 300)

    def git(args: list[str], cap: int = 4096) -> bytes:
        options = {"child_context": child_context} if child_context is not None else {}
        return _bounded_git(worktree, args, deadline=deadline, max_bytes=cap, **options)

    if git(["rev-parse", "--is-shallow-repository"]).strip() != b"false":
        raise HistoryScanError("Shallow history cannot establish complete scan coverage")
    grafts = git(["rev-parse", "--git-path", "info/grafts"]).decode("utf-8").strip()
    if (worktree / grafts).exists():
        raise HistoryScanError("Legacy grafts prevent trustworthy history coverage")
    git(["merge-base", "--is-ancestor", base_sha, head_sha])
    range_spec = f"{base_sha}..{head_sha}"
    commits = git(
        ["rev-list", f"--max-count={max_commits + 1}", range_spec, "--"],
        (max_commits + 1) * 66,
    ).splitlines()
    if len(commits) > max_commits:
        raise HistoryScanError("History exceeds the commit limit; coverage is incomplete")
    if any(not _SHA.fullmatch(x.decode("ascii")) for x in commits):
        raise HistoryScanError("Unexpected history response; coverage is incomplete")

    patch = b""
    if commits:
        patch = git([
            "log", "--full-history", "--root", "--diff-merges=separate", "-p",
            "--format=commit %H", "--no-color", "--no-decorate", "--no-notes",
            "--no-show-signature", "--no-ext-diff", "--no-textconv", "--no-renames",
            "--text", "--full-index", "--unified=0", "--submodule=short",
            "--src-prefix=a/", "--dst-prefix=b/", "--line-prefix=",
            "--output-indicator-new=+", "--output-indicator-old=-",
            "--output-indicator-context= ", range_spec, "--",
        ], max_bytes)
    try:
        text = patch.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoryScanError("Non-UTF-8 history cannot be fully secret-scanned") from exc
    if "\x00" in text or _SUBMODULE.search(text):
        raise HistoryScanError("Binary or submodule history requires separate review")

    return {
        "coverage_complete": True,
        "scope": "added text in base..HEAD (all merge parents; includes already-pushed commits)",
        "base_sha": base_sha, "head_sha": head_sha,
        "commit_count": len(commits), "patch_bytes": len(patch),
        "max_commits": max_commits, "max_bytes": max_bytes,
        "findings": scan_added_diff_for_secrets(text),
    }
