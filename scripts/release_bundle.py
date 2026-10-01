#!/usr/bin/env python3
"""Stage/verify versioned release assets. No network, upload, tag or Release writes.

A checksum manifest is integrity/provenance data, not a signature or an attestation.
Remote inventory and tag metadata are supplied by the authenticated workflow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
from typing import Any

try:
    from .check_release_tag import stable_tag_version, verify
except ImportError:  # Direct `python scripts/release_bundle.py` entry point.
    from check_release_tag import stable_tag_version, verify

MANIFEST = "RELEASE.json"
SUMS = "SHA256SUMS.txt"
MAX_FILE = 100 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, encoding="utf-8", errors="strict", timeout=20,
    ).strip()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    _regular(path, MAX_JSON)
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object)


def _regular(path: Path, maximum: int = MAX_FILE) -> None:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > maximum:
        raise ValueError("Release inputs must be bounded regular files, not links")


def _digest(path: Path) -> str:
    _regular(path)
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            value.update(chunk)
    return value.hexdigest()


def _names(version: str) -> set[str]:
    return {
        f"chatgpt_selfhosted_gitlab_mcp-{version}-py3-none-any.whl",
        f"chatgpt_selfhosted_gitlab_mcp-{version}.tar.gz", "reasonfirst.rb",
    }


def _sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("An exact lowercase source commit SHA is required")
    return value


def stage_bundle(*, root: Path, dist: Path, output: Path, tag: str,
                 repository: str, source_sha: str, run_id: str, run_attempt: str) -> dict[str, Any]:
    version = verify(tag, root)["version"]
    if tag != "v" + version or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Canonical stable release tag required")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repository must be owner/name")
    if not all(re.fullmatch(r"[1-9][0-9]*", x) for x in (run_id, run_attempt)):
        raise ValueError("Workflow run and attempt must be positive integer strings")
    _sha(source_sha)
    head = _git(root, "rev-parse", "HEAD")
    if head != source_sha or _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}") != head:
        raise ValueError("Release tag, checked-out HEAD and approved source must match")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Tracked source changed during the build")
    names = _names(version)
    if dist.is_symlink() or {p.name for p in dist.iterdir()} != names:
        raise ValueError("Expected exactly the versioned wheel, sdist and formula")
    # Validate every source before creating the staging directory.
    expected = {name: _digest(dist / name) for name in sorted(names)}
    manifest = {
        "schema_version": 1, "artifact_kind": "reasonfirst-release",
        "repository": repository, "release_tag": tag, "package_version": version,
        "source_commit": head, "source_tree": _sha(_git(root, "rev-parse", "HEAD^{tree}")),
        "workflow_run_id": run_id, "workflow_run_attempt": run_attempt,
        "build_python": platform.python_version(),
        "build_uv": subprocess.check_output(["uv", "--version"], encoding="utf-8", timeout=20).strip(),
        "files_sha256": expected,
        "evidence_note": "Build/source metadata and checksums; not a signature or proof of live provider/worker acceptance.",
    }
    output.mkdir(exist_ok=False)  # Never overwrite an earlier staging directory.
    for name in sorted(names):
        shutil.copyfile(dist / name, output / name)
        if _digest(output / name) != expected[name]:
            raise ValueError("Artifact changed while staging; upload refused")
    with (output / MANIFEST).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    sums = {**expected, MANIFEST: _digest(output / MANIFEST)}
    with (output / SUMS).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())))
    verify_bundle(output)
    return manifest


def verify_bundle(directory: Path) -> dict[str, Any]:
    if directory.is_symlink():
        raise ValueError("Release directory must not be a symlink")
    manifest = _read_json(directory / MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or type(manifest.get("schema_version")) is not int:
        raise ValueError("Invalid release manifest schema")
    tag = manifest.get("release_tag", "")
    version = stable_tag_version(tag)
    if (tag != "v" + version or version != manifest.get("package_version")
            or manifest.get("artifact_kind") != "reasonfirst-release"):
        raise ValueError("Release identity mismatch")
    _sha(manifest.get("source_commit"))
    _sha(manifest.get("source_tree"))
    names = _names(version)
    expected = manifest.get("files_sha256")
    if not isinstance(expected, dict) or set(expected) != names:
        raise ValueError("Unexpected release manifest file set")
    if {p.name for p in directory.iterdir()} != names | {MANIFEST, SUMS}:
        raise ValueError("Release directory has missing or unexpected files")
    for name, digest in expected.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or _digest(directory / name) != digest:
            raise ValueError("Release asset checksum mismatch")
    sums = {**expected, MANIFEST: _digest(directory / MANIFEST)}
    _regular(directory / SUMS, MAX_JSON)
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items()))
    if (directory / SUMS).read_bytes() != text.encode("utf-8"):
        raise ValueError("SHA256SUMS does not cover the exact release files")
    return manifest


def check_upload(*, directory: Path, release: dict[str, Any], asset_pages: list[Any],
                 repository: str, release_id: int, remote_sha: str) -> list[str]:
    manifest = verify_bundle(directory)
    tag = manifest["release_tag"]
    if (manifest["repository"] != repository or manifest["source_commit"] != _sha(remote_sha)
            or release.get("id") != release_id or type(release_id) is not int or release_id <= 0
            or release.get("tag_name") != tag or release.get("draft") is not False
            or release.get("prerelease") is not False
            or release.get("immutable") is True
            or release.get("url") != f"https://api.github.com/repos/{repository}/releases/{release_id}"):
        raise ValueError("Release/tag identity or writable stable-release state changed")
    if not isinstance(asset_pages, list) or not asset_pages:
        raise ValueError("Complete paginated asset inventory is required")
    existing: set[str] = set()
    for page in asset_pages:
        if not isinstance(page, list):
            raise ValueError("Expected gh api --paginate --slurp asset pages")
        for asset in page:
            if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
                raise ValueError("Invalid remote asset inventory")
            existing.add(asset["name"])
    names = _names(manifest["package_version"]) | {MANIFEST, SUMS}
    if names & existing:
        # Even identical assets require explicit operator reconciliation. Never
        # delete/replace an existing asset merely because a workflow was rerun.
        raise ValueError("Release already contains a target filename; no overwrite or deletion is permitted")
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--root", type=Path, default=Path("."))
    stage.add_argument("--dist", type=Path, default=Path("dist"))
    stage.add_argument("--output", type=Path, default=Path("release-download"))
    for name in ("tag", "repository", "source-sha", "run-id", "run-attempt"):
        stage.add_argument("--" + name, required=True)
    check = commands.add_parser("check-upload")
    check.add_argument("--directory", type=Path, default=Path("release-download"))
    check.add_argument("--release-json", type=Path, required=True)
    check.add_argument("--assets-json", type=Path, required=True)
    check.add_argument("--repository", required=True)
    check.add_argument("--release-id", type=int, required=True)
    check.add_argument("--remote-sha", required=True)
    args = parser.parse_args()
    if args.command == "stage":
        result = stage_bundle(root=args.root, dist=args.dist, output=args.output,
                              tag=args.tag, repository=args.repository, source_sha=args.source_sha,
                              run_id=args.run_id, run_attempt=args.run_attempt)
    else:
        names = check_upload(directory=args.directory, release=_read_json(args.release_json),
                             asset_pages=_read_json(args.assets_json), repository=args.repository,
                             release_id=args.release_id, remote_sha=args.remote_sha)
        result = {"ok": True, "files_to_upload": names, "publication_attempted": False}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
