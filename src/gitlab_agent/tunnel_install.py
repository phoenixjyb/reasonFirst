from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable


LATEST_RELEASE_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
DEFAULT_INSTALL_DIR = Path("~/.local/share/reasonfirst/tunnel-client").expanduser()


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
            if len(digest) != 64 or any(
                ch not in "0123456789abcdefABCDEF" for ch in digest
            ):
                raise TunnelInstallError("Invalid SHA256 digest in release checksum file")
            return digest.lower()
    raise TunnelInstallError(f"Checksum missing for release asset: {asset_name}")


def _member_by_basename(
    archive: zipfile.ZipFile,
    expected: str,
    *,
    required: bool = True,
) -> zipfile.ZipInfo | None:
    candidates = [
        item
        for item in archive.infolist()
        if not item.is_dir() and Path(item.filename).name == expected
    ]
    if not candidates and not required:
        return None
    if len(candidates) != 1:
        raise TunnelInstallError(
            f"Expected exactly one {expected} in official release archive"
        )
    return candidates[0]


def _verify_binary(
    path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
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


def _write_bundle_member(path: Path, raw: bytes, *, executable: bool) -> None:
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(
            stat.S_IRUSR
            | stat.S_IWUSR
            | (stat.S_IXUSR if executable else 0)
        )


def _validate_existing_bundle(
    bundle_dir: Path,
    *,
    client_name: str,
    companion_name: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    if bundle_dir.is_symlink():
        raise TunnelInstallError(f"Refusing symlinked tunnel-client bundle: {bundle_dir}")
    expected = [
        bundle_dir / client_name,
        bundle_dir / companion_name,
        bundle_dir / "cloudflared-manifest.json",
    ]
    if not all(path.is_file() and not path.is_symlink() for path in expected):
        raise TunnelInstallError(
            f"Existing tunnel-client bundle is incomplete: {bundle_dir}"
        )
    return _verify_binary(bundle_dir / client_name, runner=runner)


def install_official_tunnel_client(
    *,
    install_dir: Path = DEFAULT_INSTALL_DIR,
    fetch: Callable[[str], bytes] = _request_bytes,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    system_name: str | None = None,
    machine: str | None = None,
) -> dict[str, object]:
    """Install a checksum-verified official client bundle without in-place upgrades.

    The supported upstream ZIP intentionally keeps tunnel-client, its pinned
    cloudflared companion, and cloudflared-manifest.json adjacent. ReasonFirst
    preserves that runtime contract in a versioned bundle directory.
    """

    goos, goarch = _platform_tag(system_name=system_name, machine=machine)
    release_raw = fetch(LATEST_RELEASE_API)
    try:
        release = json.loads(release_raw)
    except (TypeError, ValueError) as exc:
        raise TunnelInstallError(
            "Could not parse latest tunnel-client release metadata"
        ) from exc
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

    root = install_dir.expanduser()
    if root.exists() and root.is_symlink():
        raise TunnelInstallError(f"Refusing symlinked install directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)

    client_name = "tunnel-client.exe" if goos == "windows" else "tunnel-client"
    companion_name = "cloudflared.exe" if goos == "windows" else "cloudflared"
    bundle_name = f"{tag}-{goos}-{goarch}-{actual[:12]}"
    bundle_dir = root / bundle_name
    target = bundle_dir / client_name

    if bundle_dir.exists():
        version = _validate_existing_bundle(
            bundle_dir,
            client_name=client_name,
            companion_name=companion_name,
            runner=runner,
        )
        return {
            "ok": True,
            "installed": False,
            "reused": True,
            "version": version,
            "release_tag": tag,
            "platform": f"{goos}-{goarch}",
            "path": str(target),
            "bundle_dir": str(bundle_dir),
            "companion_path": str(bundle_dir / companion_name),
            "manifest_path": str(bundle_dir / "cloudflared-manifest.json"),
            "asset": asset_name,
            "sha256": actual,
            "source_url": asset_url,
            "checksums_url": checksums_url,
        }

    stage = Path(tempfile.mkdtemp(prefix=".bundle.", dir=root))
    try:
        if os.name != "nt":
            stage.chmod(0o700)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            required = {
                client_name: True,
                companion_name: True,
                "cloudflared-manifest.json": False,
            }
            for name, executable in required.items():
                member = _member_by_basename(archive, name)
                assert member is not None
                _write_bundle_member(
                    stage / name,
                    archive.read(member),
                    executable=executable,
                )
            for optional in ("LICENSE", "NOTICE"):
                member = _member_by_basename(archive, optional, required=False)
                if member is not None:
                    _write_bundle_member(
                        stage / optional,
                        archive.read(member),
                        executable=False,
                    )

        version = _verify_binary(stage / client_name, runner=runner)
        os.replace(stage, bundle_dir)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise

    return {
        "ok": True,
        "installed": True,
        "reused": False,
        "version": version,
        "release_tag": tag,
        "platform": f"{goos}-{goarch}",
        "path": str(target),
        "bundle_dir": str(bundle_dir),
        "companion_path": str(bundle_dir / companion_name),
        "manifest_path": str(bundle_dir / "cloudflared-manifest.json"),
        "asset": asset_name,
        "sha256": actual,
        "source_url": asset_url,
        "checksums_url": checksums_url,
    }
