from __future__ import annotations

import errno
import hashlib
import http.client
import io
import json
import os
import platform
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable


LATEST_RELEASE_API = "https://api.github.com/repos/openai/tunnel-client/releases/latest"
DEFAULT_INSTALL_DIR = Path("~/.local/share/reasonfirst/tunnel-client").expanduser()
_USER_AGENT = "ReasonFirst-v0.5.2-installer"
_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_CURL_CONNECT_TIMEOUT = 15
_CURL_TRANSFER_TIMEOUT = 180
_CURL_PROCESS_TIMEOUT = 190


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


def _require_https(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        )
        # Validate an explicitly supplied port, too.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise TunnelInstallError(
            "Refusing a non-HTTPS or credential-bearing download URL"
        )


class _HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request_failure(exc: BaseException) -> str:
    """Describe failures without exposing proxy credentials or response bodies."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, BaseException):
            return _request_failure(exc.reason)
        return "network request failed"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(exc, ssl.SSLError):
        return "TLS connection failed"
    if isinstance(exc, TimeoutError):
        return "request timed out"
    if isinstance(exc, socket.gaierror):
        return "host lookup failed"
    if isinstance(exc, http.client.IncompleteRead):
        return "incomplete response"
    if isinstance(exc, ConnectionError):
        return "connection interrupted"
    return "network request failed"


def _can_retry_with_curl(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, BaseException) and _can_retry_with_curl(exc.reason)
    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, ssl.SSLError) and getattr(exc, "reason", None) == "CERTIFICATE_VERIFY_FAILED":
        return False
    if isinstance(exc, OSError) and exc.errno in {
        errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
        errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
        errno.ETIMEDOUT, errno.ENOTCONN, errno.EPIPE,
    }:
        return True
    return isinstance(
        exc,
        (ConnectionError, TimeoutError, socket.gaierror, ssl.SSLError, http.client.IncompleteRead),
    )


def _curl_request_bytes(url: str, executable: str) -> bytes:
    # --disable must be first: a user's .curlrc must not weaken TLS, enable
    # tracing of proxy credentials, or redirect output. No shell is involved.
    argv = [
        executable, "--disable", "--fail", "--silent", "--location",
        "--disallow-username-in-url",
        "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
        "--connect-timeout", str(_CURL_CONNECT_TIMEOUT),
        "--max-time", str(_CURL_TRANSFER_TIMEOUT),
        "--max-filesize", str(_MAX_DOWNLOAD_BYTES),
        "--user-agent", _USER_AGENT, "--url", url,
    ]
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise TunnelInstallError("curl recovery could not be started") from None

    timed_out = threading.Event()

    def terminate() -> None:
        timed_out.set()
        try:
            process.kill()
        except OSError:
            pass

    # Bound elapsed time independently of curl's own timeout. Limit the pipe
    # read as well: older curl versions cannot cap chunked bodies themselves.
    watchdog = threading.Timer(_CURL_PROCESS_TIMEOUT, terminate)
    watchdog.daemon = True
    watchdog.start()
    try:
        assert process.stdout is not None
        raw = process.stdout.read(_MAX_DOWNLOAD_BYTES + 1)
        if timed_out.is_set():
            raise TunnelInstallError("curl recovery timed out")
        if len(raw) > _MAX_DOWNLOAD_BYTES:
            raise TunnelInstallError("curl download exceeds the 256 MiB response limit")
        returncode = process.wait(timeout=5)
        if timed_out.is_set():
            raise TunnelInstallError("curl recovery timed out")
        if returncode != 0:
            raise TunnelInstallError(f"curl recovery failed (exit code {returncode})")
        return raw
    except subprocess.TimeoutExpired:
        raise TunnelInstallError("curl recovery timed out") from None
    except OSError:
        raise TunnelInstallError("curl recovery could not read the response") from None
    finally:
        watchdog.cancel()
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        if process.stdout is not None:
            process.stdout.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _request_bytes(url: str) -> bytes:
    _require_https(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
    )
    try:
        opener = urllib.request.build_opener(_HTTPSOnlyRedirectHandler())
        with opener.open(request, timeout=60) as response:
            raw = response.read(_MAX_DOWNLOAD_BYTES + 1)
            # HTTPResponse.read(amt) can return a truncated fixed-length body
            # without raising IncompleteRead (unlike read() without a limit).
            remaining = getattr(response, "length", None)
            if len(raw) <= _MAX_DOWNLOAD_BYTES and isinstance(remaining, int) and remaining > 0:
                raise http.client.IncompleteRead(b"", remaining)
        if len(raw) > _MAX_DOWNLOAD_BYTES:
            raise TunnelInstallError("HTTPS download exceeds the 256 MiB response limit")
        return raw
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        failure = _request_failure(exc)
        if not _can_retry_with_curl(exc):
            raise TunnelInstallError(f"HTTPS request failed ({failure})") from None

    curl = shutil.which("curl")
    if curl is None:
        raise TunnelInstallError(
            f"HTTPS request failed ({failure}); curl is unavailable for automatic recovery. "
            "Install curl or restore HTTPS connectivity"
        ) from None
    try:
        return _curl_request_bytes(url, curl)
    except TunnelInstallError as exc:
        raise TunnelInstallError(f"HTTPS request failed ({failure}); {exc}") from None


def _fetch_download(fetch: Callable[[str], bytes], url: str, phase: str) -> bytes:
    try:
        return fetch(url)
    except (TunnelInstallError, urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        try:
            parsed = urllib.parse.urlsplit(url)
            # Omit user info, query strings, and fragments from diagnostics.
            safe_url = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.hostname or "", parsed.path, "", "")
            )
        except ValueError:
            safe_url = "<invalid URL>"
        reason = str(exc) if isinstance(exc, TunnelInstallError) else _request_failure(exc)
        raise TunnelInstallError(
            f"tunnel-client {phase} download failed ({safe_url!r}): {reason}. "
            "Check HTTPS access, proxy settings, and certificate trust for this URL, "
            "then retry the same install command."
        ) from None


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
    expected_files: dict[str, bytes],
    client_name: str,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    if bundle_dir.is_symlink():
        raise TunnelInstallError(f"Refusing symlinked tunnel-client bundle: {bundle_dir}")
    for name, expected in expected_files.items():
        path = bundle_dir / name
        if not path.is_file() or path.is_symlink():
            raise TunnelInstallError(
                f"Existing tunnel-client bundle is incomplete: {bundle_dir}"
            )
        if path.read_bytes() != expected:
            raise TunnelInstallError(
                f"Existing tunnel-client bundle differs from the verified official archive: {path}"
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
    release_raw = _fetch_download(fetch, LATEST_RELEASE_API, "release metadata")
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

    checksums = _fetch_download(fetch, checksums_url, "checksum file")
    expected = _checksum_for(checksums, asset_name)
    archive_bytes = _fetch_download(fetch, asset_url, "archive")
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

    expected_files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        for name in (client_name, companion_name, "cloudflared-manifest.json"):
            member = _member_by_basename(archive, name)
            assert member is not None
            expected_files[name] = archive.read(member)
        for optional in ("LICENSE", "NOTICE"):
            member = _member_by_basename(archive, optional, required=False)
            if member is not None:
                expected_files[optional] = archive.read(member)

    if bundle_dir.exists():
        version = _validate_existing_bundle(
            bundle_dir,
            expected_files=expected_files,
            client_name=client_name,
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
        for name, raw in expected_files.items():
            _write_bundle_member(
                stage / name,
                raw,
                executable=name in {client_name, companion_name},
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
