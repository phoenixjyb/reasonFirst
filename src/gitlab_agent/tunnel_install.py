from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable


LATEST_RELEASE_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
DEFAULT_INSTALL_DIR = Path("~/.local/share/reasonfirst/bin").expanduser()


class TunnelInstallError(RuntimeError):
    pass


def _platform_tag(
    *,
    system_name: str | None = None,
    machine: str | None = None,
) -> tuple[str, str]:
    system = (system_name or platform.system()).strip().lower()
    arch = (machine or platform.machine()).strip().lower()

    goos = {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "windows",
    }.get(system)
    if goos is None:
        raise TunnelInstallError(f"Unsupported platform for tunnel-client: {system}")

    goarch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "x64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(arch)
    if goarch is None:
        raise TunnelInstallError(f"Unsupported architecture for tunnel-client: {arch}")
    return goos, goarch


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ReasonFirst-v0.5.1-installer"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _asset_url(release: dict[str, object], name: str) -> str:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise TunnelInstallError("Latest tunnel-client release has no asset list")
    for item in assets:
        if isinstance(item, dict) and item.get("name") == name:
            url = item.get("browser_download_url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
    raise TunnelInstallError(f"Official tunnel-client release asset is missing: {name}")


def _checksum_for(checksums: bytes, asset_name: str) -> str:
    try:
        text = checksums.decode("utf-8")
    except UnicodeError as exc:
        raise TunnelInstallError("SHA256SUMS.txt is not valid UTF-8") from exc
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        digest, filename = parts
        filename = filename.lstrip("*")
        if filename == asset_name:
            if len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
                raise TunnelInstallError("Invalid SHA256 digest in release checksum file")
            return digest.lower()
    raise TunnelInstallError(f"Checksum missing for release asset: {asset_name}")


def _binary_member(archive: zipfile.ZipFile, *, windows: bool) -> zipfile.ZipInfo:
    expected = "tunnel-client.exe" if windows else "tunnel-client"
    candidates = [
        item
        for item in archive.infolist()
        if not item.is_dir() and Path(item.filename).name == expected
    ]
    if len(candidates) != 1:
        raise TunnelInstallError(
            f"Expected exactly one {expected} in official release archive"
        )
    return candidates[0]


def _verify_binary(path: Path, *, runner: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    try:
        proc = runner(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except OSError as exc:
        raise TunnelInstallError("Installed tunnel-client could not be executed") from exc
    if proc.returncode != 0:
        raise TunnelInstallError(
            "Installed tunnel-client failed --version verification"
        )
    return (proc.stdout or proc.stderr or "").strip()


def install_official_tunnel_client(
    *,
    install_dir: Path = DEFAULT_INSTALL_DIR,
    fetch: Callable[[str], bytes] = _request_bytes,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    system_name: str | None = None,
    machine: str | None = None,
) -> dict[str, object]:
    """Download the latest official tunnel-client and verify its SHA256 before install."""
    goos, goarch = _platform_tag(system_name=system_name, machine=machine)
    release_raw = fetch(LATEST_RELEASE_API)
    try:
        release = json.loads(release_raw)
    except (TypeError, ValueError) as exc:
        raise TunnelInstallError("Could not parse latest tunnel-client release metadata") from exc
    if not isinstance(release, dict):
        raise TunnelInstallError("Latest tunnel-client release metadata is invalid")

    tag = str(release.get("tag_name") or "").strip()
    if not tag:
        raise TunnelInstallError("Latest tunnel-client release has no tag_name")

    asset_name = f"tunnel-client-{tag}-{goos}-{goarch}.zip"
    checksums_name = "SHA256SUMS.txt"
    asset_url = _asset_url(release, asset_name)
    checksums_url = _asset_url(release, checksums_name)

    checksums = fetch(checksums_url)
    expected = _checksum_for(checksums, asset_name)
    archive_bytes = fetch(asset_url)
    actual = hashlib.sha256(archive_bytes).hexdigest()
    if actual != expected:
        raise TunnelInstallError(
            f"tunnel-client checksum mismatch for {asset_name}"
        )

    target_dir = install_dir.expanduser()
    if target_dir.exists() and target_dir.is_symlink():
        raise TunnelInstallError(f"Refusing symlinked install directory: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        target_dir.chmod(0o700)

    target = target_dir / ("tunnel-client.exe" if goos == "windows" else "tunnel-client")
    if target.exists() and target.is_symlink():
        raise TunnelInstallError(f"Refusing symlinked tunnel-client target: {target}")

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        member = _binary_member(archive, windows=goos == "windows")
        binary = archive.read(member)

    suffix = ".exe" if goos == "windows" else ".bin"
    fd, temp_name = tempfile.mkstemp(prefix=".tunnel-client.", suffix=suffix, dir=target_dir)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(binary)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temp.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        version = _verify_binary(temp, runner=runner)
        os.replace(temp, target)
        if os.name != "nt":
            target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    finally:
        if temp.exists():
            temp.unlink()

    return {
        "ok": True,
        "installed": True,
        "version": version,
        "release_tag": tag,
        "platform": f"{goos}-{goarch}",
        "path": str(target),
        "asset": asset_name,
        "sha256": actual,
        "source_url": asset_url,
        "checksums_url": checksums_url,
    }
