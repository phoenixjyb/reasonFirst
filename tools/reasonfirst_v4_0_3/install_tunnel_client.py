#!/usr/bin/env python3
"""Install the verified latest official OpenAI tunnel-client for macOS."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
from urllib.request import Request, urlopen
import zipfile


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "ReasonFirst-v4-installer"}), timeout=60) as response:
        return response.read()


def main() -> int:
    existing = shutil.which("tunnel-client")
    if existing:
        print(f"tunnel-client already available: {existing}")
        return 0
    if platform.system() != "Darwin":
        raise SystemExit("Automatic tunnel-client install currently supports macOS only")
    arch = {"arm64": "arm64", "x86_64": "amd64"}.get(platform.machine())
    if not arch:
        raise SystemExit(f"Unsupported Mac architecture: {platform.machine()}")
    release = json.loads(fetch("https://api.github.com/repos/openai/tunnel-client/releases/latest"))
    name = f"tunnel-client-{release['tag_name']}-darwin-{arch}.zip"
    asset = next((a for a in release["assets"] if a["name"] == name), None)
    if not asset or not str(asset.get("digest", "")).startswith("sha256:"):
        raise SystemExit(f"Official release is missing a SHA256 digest for {name}")
    payload = fetch(asset["browser_download_url"])
    expected = asset["digest"].split(":", 1)[1]
    if hashlib.sha256(payload).hexdigest() != expected:
        raise SystemExit("tunnel-client release checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = [i for i in archive.infolist() if Path(i.filename).name == "tunnel-client" and not i.is_dir()]
        if len(candidates) != 1:
            raise SystemExit("Could not identify tunnel-client executable in release archive")
        target_dir = Path.home() / ".local/bin"
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target_dir, prefix=".tunnel-client.", delete=False) as f:
            tmp = Path(f.name)
            f.write(archive.read(candidates[0]))
        try:
            os.chmod(tmp, 0o755)
            tmp.replace(target_dir / "tunnel-client")
        finally:
            tmp.unlink(missing_ok=True)
    print(f"Installed verified {release['tag_name']}: {target_dir / 'tunnel-client'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
