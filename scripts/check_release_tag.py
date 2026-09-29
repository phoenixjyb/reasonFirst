#!/usr/bin/env python3
"""Verify that a release tag matches package/runtime version metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


TAG_RE = re.compile(r"v(?P<version>0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
INIT_VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<version>[^"]+)"\s*$', re.MULTILINE)


def package_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml project.version is missing")
    return version


def runtime_version(root: Path) -> str:
    text = (root / "src/gitlab_agent/__init__.py").read_text(encoding="utf-8")
    match = INIT_VERSION_RE.search(text)
    if match is None:
        raise ValueError("gitlab_agent.__version__ is missing")
    return match.group("version")


def verify(tag: str, root: Path) -> dict[str, str]:
    match = TAG_RE.fullmatch(tag.strip())
    if match is None:
        raise ValueError("release tag must use stable semver form vX.Y.Z")
    requested = match.group("version")
    package = package_version(root)
    runtime = runtime_version(root)
    if package != runtime:
        raise ValueError(f"package/runtime version mismatch: {package} != {runtime}")
    if requested != package:
        raise ValueError(f"tag/package version mismatch: {requested} != {package}")
    return {"tag": tag, "version": package}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    result = verify(args.tag, Path(args.root).resolve())
    print(f"release tag/version alignment: OK ({result['tag']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
