#!/usr/bin/env python3
"""Inspect built wheel/sdist archives for release hygiene.

This is a filename/path inspection only. It complements, but does not replace,
source/history secret scanning and manual review of public repository surfaces.
"""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import PurePosixPath, Path

FORBIDDEN_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}
FORBIDDEN_SUFFIXES = {".log", ".pem", ".key", ".p12", ".pfx"}
FORBIDDEN_SEGMENTS = {
    ".local",
    "worktrees",
    "runner-home",
    "codex-web-bridge",
    "migration-backups",
}
REQUIRED_WHEEL_PREFIX = "gitlab_agent/"


def _normalized_members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return [name.replace("\\", "/") for name in archive.namelist()]
    if path.name.endswith(".tar.gz") or path.suffix in {".tgz", ".tar"}:
        mode = "r:gz" if path.name.endswith(".gz") or path.suffix == ".tgz" else "r"
        with tarfile.open(path, mode) as archive:
            return [member.name.replace("\\", "/") for member in archive.getmembers()]
    raise ValueError(f"unsupported artifact type: {path}")


def inspect(path: Path) -> list[str]:
    members = _normalized_members(path)
    errors: list[str] = []

    for raw in members:
        p = PurePosixPath(raw)
        parts = [part for part in p.parts if part not in {"", "."}]
        if not parts:
            continue
        basename = parts[-1]
        lower_parts = {part.lower() for part in parts}
        if basename.lower() in FORBIDDEN_BASENAMES:
            errors.append(f"{path.name}: forbidden basename: {raw}")
        if PurePosixPath(basename).suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"{path.name}: forbidden sensitive/runtime suffix: {raw}")
        if lower_parts & FORBIDDEN_SEGMENTS:
            errors.append(f"{path.name}: forbidden runtime path segment: {raw}")

    if path.suffix == ".whl":
        if not any(name.startswith(REQUIRED_WHEEL_PREFIX) for name in members):
            errors.append(f"{path.name}: missing packaged gitlab_agent module")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    args = parser.parse_args()

    problems: list[str] = []
    for item in args.artifacts:
        problems.extend(inspect(Path(item)))

    if problems:
        for problem in problems:
            print(problem)
        return 1

    print(f"release artifact inspection: OK ({len(args.artifacts)} artifact(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
