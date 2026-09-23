from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from .config import AgentSettings
from .workspace import WorkspaceManager


PRACTICE_KIT_FILES = (
    ".actualcoder.yaml",
    ".gitignore",
    ".gitlab-ci.yml",
    "AGENTS.md",
    "EXERCISE.md",
    "README.md",
    "clip_summary.py",
    "tests/test_clip_summary.py",
)
_SEEDABLE_EXISTING_PATHS = {"README.md"}


def _kit_root():
    return resources.files("gitlab_agent").joinpath("practice_kit")


def _copy_kit(destination: Path) -> None:
    root = _kit_root()
    for relative in PRACTICE_KIT_FILES:
        source = root.joinpath(*relative.split("/"))
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _run_baseline_tests(path: Path) -> dict[str, object]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        cwd=path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    return {
        "argv": [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-v",
        ],
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _list_tree(
    manager: WorkspaceManager,
    repo_path: Path,
    commit_sha: str,
) -> list[str]:
    proc = manager._run_git(  # internal control-plane plumbing; no remote write
        [
            "--git-dir",
            str(repo_path),
            "ls-tree",
            "-r",
            "--name-only",
            commit_sha,
        ]
    )
    return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _resolve_optional_base(
    manager: WorkspaceManager,
    repo_path: Path,
    ref: str,
) -> str | None:
    try:
        return manager._resolve_base_sha(repo_path, ref)
    except RuntimeError:
        return None


def _validate_ref(manager: WorkspaceManager, ref: str) -> None:
    result = manager._run_git(
        ["check-ref-format", f"refs/heads/{ref}"],
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Unsafe or invalid practice target ref: {ref!r}")


def _kit_smoke_test() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="reasonfirst-practice-kit-") as temp:
        root = Path(temp)
        _copy_kit(root)
        return _run_baseline_tests(root)


def build_practice_seed_plan(
    *,
    settings: AgentSettings,
    manager: WorkspaceManager,
    project: str,
    ref: str | None = None,
) -> dict[str, object]:
    """Build a non-writing plan for the one-time synthetic practice seed."""

    settings.assert_project_allowed_for_workspace(project)
    target_ref = (ref or settings.default_base_ref).strip() or settings.default_base_ref
    _validate_ref(manager, target_ref)

    repo_path = manager._ensure_cached_repo(project)
    base_sha = _resolve_optional_base(manager, repo_path, target_ref)
    existing_paths = _list_tree(manager, repo_path, base_sha) if base_sha else []

    already_seeded = any(
        path in existing_paths
        for path in (".actualcoder.yaml", "EXERCISE.md", "AGENTS.md")
    )
    unexpected = sorted(set(existing_paths) - _SEEDABLE_EXISTING_PATHS)
    seedable = not already_seeded and not unexpected

    baseline = _kit_smoke_test()
    baseline_ok = bool(baseline.get("passed"))
    ok = seedable and baseline_ok

    if already_seeded:
        reason = "Project already contains ReasonFirst practice contract/exercise files; use practice-doctor instead of reseeding."
    elif unexpected:
        reason = "Project contains nontrivial tracked files outside the conservative README-only seed boundary."
    elif not baseline_ok:
        reason = "Packaged practice kit baseline tests failed locally; refusing any remote write."
    elif base_sha:
        reason = "README-only starter project is eligible for a one-time fast-forward practice seed."
    else:
        reason = "Empty project/ref is eligible for a one-time initial practice seed."

    return {
        "ok": ok,
        "dry_run": True,
        "project": project,
        "ref": target_ref,
        "remote_url": manager.clone_url(project),
        "base_sha": base_sha,
        "existing_paths": existing_paths,
        "already_seeded": already_seeded,
        "unexpected_existing_paths": unexpected,
        "seedable": seedable,
        "kit_files": list(PRACTICE_KIT_FILES),
        "baseline_validation": baseline,
        "reason": reason,
        "write_performed": False,
        "next": (
            "Re-run with --apply --confirm-synthetic and approve the interactive prompt to seed this synthetic project."
            if ok
            else "Resolve the reported seed-safety blocker; no remote write is permitted."
        ),
    }


def _prepare_seed_checkout(
    *,
    manager: WorkspaceManager,
    repo_path: Path,
    remote_url: str,
    ref: str,
    base_sha: str | None,
    destination: Path,
) -> None:
    if base_sha:
        manager._run_git(
            ["clone", "--no-hardlinks", str(repo_path), str(destination)]
        )
        manager._run_git(
            ["checkout", "-B", ref, base_sha],
            cwd=destination,
        )
        manager._run_git(
            ["rm", "-r", "--ignore-unmatch", "."],
            cwd=destination,
        )
        manager._run_git(
            ["remote", "set-url", "origin", remote_url],
            cwd=destination,
        )
    else:
        destination.mkdir(parents=True, exist_ok=False)
        manager._run_git(["init", "-b", ref], cwd=destination)
        manager._run_git(["remote", "add", "origin", remote_url], cwd=destination)

    _copy_kit(destination)


def execute_practice_seed(
    *,
    settings: AgentSettings,
    manager: WorkspaceManager,
    project: str,
    ref: str | None = None,
) -> dict[str, object]:
    """Seed the synthetic lab through managed Git auth/proxy behavior.

    The caller is responsible for the explicit human/synthetic-project approval
    gate. This function still revalidates the remote immediately before push and
    uses a normal non-force push.
    """

    plan = build_practice_seed_plan(
        settings=settings,
        manager=manager,
        project=project,
        ref=ref,
    )
    if not bool(plan.get("ok")):
        raise RuntimeError(str(plan.get("reason") or "Practice seed plan is blocked"))

    target_ref = str(plan["ref"])
    planned_base = plan.get("base_sha")
    remote_url = str(plan["remote_url"])

    with tempfile.TemporaryDirectory(prefix="reasonfirst-practice-seed-") as temp:
        seed_path = Path(temp) / "repo"
        repo_path = manager._ensure_cached_repo(project)
        current_base = _resolve_optional_base(manager, repo_path, target_ref)
        if current_base != planned_base:
            raise RuntimeError(
                "Practice target changed after planning; refusing seed. "
                f"planned={planned_base!r} current={current_base!r}"
            )

        current_paths = _list_tree(manager, repo_path, current_base) if current_base else []
        unexpected = sorted(set(current_paths) - _SEEDABLE_EXISTING_PATHS)
        if any(path in current_paths for path in (".actualcoder.yaml", "EXERCISE.md", "AGENTS.md")):
            raise RuntimeError("Practice project became seeded before apply; refusing duplicate seed")
        if unexpected:
            raise RuntimeError(
                "Practice project became nontrivial before apply; refusing overwrite: "
                + ", ".join(unexpected)
            )

        _prepare_seed_checkout(
            manager=manager,
            repo_path=repo_path,
            remote_url=remote_url,
            ref=target_ref,
            base_sha=current_base,
            destination=seed_path,
        )

        baseline = _run_baseline_tests(seed_path)
        if not bool(baseline.get("passed")):
            raise RuntimeError("Practice baseline tests failed before commit; refusing remote write")

        manager._run_git(["add", "-A"], cwd=seed_path)
        check = manager._run_git(["diff", "--cached", "--check"], cwd=seed_path, check=False)
        if check.returncode != 0:
            raise RuntimeError("Practice seed diff failed git diff --cached --check")

        changed = manager._run_git(
            ["diff", "--cached", "--name-only"],
            cwd=seed_path,
        ).stdout.splitlines()
        changed_paths = [line.strip() for line in changed if line.strip()]
        if not changed_paths:
            raise RuntimeError("Practice seed produced no changes")

        manager._run_git(
            ["commit", "-m", "chore: seed current ReasonFirst practice baseline"],
            cwd=seed_path,
        )
        seed_commit = manager._run_git(
            ["rev-parse", "HEAD"],
            cwd=seed_path,
        ).stdout.strip()

        # Refresh once more immediately before the remote write. A normal push
        # provides the final non-fast-forward/concurrent-update protection.
        refreshed_repo = manager._ensure_cached_repo(project)
        remote_before = _resolve_optional_base(manager, refreshed_repo, target_ref)
        if remote_before != planned_base:
            raise RuntimeError(
                "Practice target changed before push; refusing seed. "
                f"planned={planned_base!r} current={remote_before!r}"
            )

        manager._run_git(
            ["push", remote_url, f"HEAD:refs/heads/{target_ref}"],
            cwd=seed_path,
            auth=manager._remote_needs_auth(remote_url),
        )

        refreshed_after = manager._ensure_cached_repo(project)
        remote_after = _resolve_optional_base(manager, refreshed_after, target_ref)
        if remote_after != seed_commit:
            raise RuntimeError(
                "Practice seed push returned without the expected remote commit; "
                f"local={seed_commit} remote={remote_after}"
            )

        return {
            "ok": True,
            "dry_run": False,
            "project": project,
            "ref": target_ref,
            "previous_base_sha": planned_base,
            "seed_commit": seed_commit,
            "remote_commit_sha": remote_after,
            "changed_paths": changed_paths,
            "baseline_validation": baseline,
            "write_performed": True,
            "message": (
                "Synthetic practice baseline seeded successfully. Run practice-doctor "
                "before creating a Stage-1 workspace."
            ),
        }
