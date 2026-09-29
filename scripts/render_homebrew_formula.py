#!/usr/bin/env python3
"""Render a release-pinned Homebrew formula for the external tap."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlparse


VERSION_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def render(*, template: str, version: str, source_url: str, sha256: str) -> str:
    version = version.strip()
    digest = sha256.strip().lower()
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError("version must be stable semver X.Y.Z")
    if SHA_RE.fullmatch(digest) is None:
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ValueError("source URL must be an https://github.com URL")
    required = ("__VERSION__", "__SOURCE_URL__", "__SHA256__")
    if any(marker not in template for marker in required):
        raise ValueError("formula template is missing a required placeholder")
    return (
        template.replace("__VERSION__", version)
        .replace("__SOURCE_URL__", source_url)
        .replace("__SHA256__", digest)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument(
        "--template",
        default="packaging/homebrew/reasonfirst.rb.in",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    template = Path(args.template).read_text(encoding="utf-8")
    output = render(
        template=template,
        version=args.version,
        source_url=args.source_url,
        sha256=args.sha256,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(output, encoding="utf-8")
    print(f"Rendered Homebrew formula: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
