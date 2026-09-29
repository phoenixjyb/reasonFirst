#!/usr/bin/env python3
"""Verify that a release tag matches package/runtime version metadata."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib


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


def stable_tag_version(tag: str) -> str:
    value = tag.strip()
    if not value.startswith("v"):
        raise ValueError("release tag must use stable semver form vX.Y.Z")
    version = value[1:]
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError("release tag must use stable semver form vX.Y.Z")
    for part in parts:
        if not part.isdigit():
            raise ValueError("release tag must use stable semver form vX.Y.Z")
        if len(part) > 1 and part.startswith("0"):
            raise ValueError("release tag must use stable semver form vX.Y.Z")
    return version


def verify(tag: str, root: Path) -> dict[str, str]:
    requested = stable_tag_version(tag)
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
