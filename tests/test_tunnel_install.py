from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from gitlab_agent.tunnel_install import (
    LATEST_RELEASE_API,
    TunnelInstallError,
    install_official_tunnel_client,
)


def zip_bytes(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


class TunnelInstallTests(unittest.TestCase):
    def test_linux_install_verifies_checksum_and_binary(self) -> None:
        archive = zip_bytes("bundle/tunnel-client", b"fake-linux-binary")
        digest = hashlib.sha256(archive).hexdigest()
        tag = "v9.9.9"
        asset = f"tunnel-client-{tag}-linux-amd64.zip"
        release = {
            "tag_name": tag,
            "assets": [
                {"name": asset, "browser_download_url": "https://example.test/client.zip"},
                {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/SHA256SUMS.txt"},
            ],
        }
        payloads = {
            LATEST_RELEASE_API: json.dumps(release).encode(),
            "https://example.test/client.zip": archive,
            "https://example.test/SHA256SUMS.txt": f"{digest}  {asset}\n".encode(),
        }
        calls: list[list[str]] = []

        def fetch(url: str) -> bytes:
            return payloads[url]

        def runner(argv, **kwargs):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, stdout="tunnel-client v9.9.9\n", stderr="")

        with tempfile.TemporaryDirectory() as td:
            result = install_official_tunnel_client(
                install_dir=Path(td),
                fetch=fetch,
                runner=runner,
                system_name="Linux",
                machine="x86_64",
            )
            path = Path(str(result["path"]))
            self.assertEqual(path.read_bytes(), b"fake-linux-binary")
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(result["platform"], "linux-amd64")
            self.assertEqual(calls[0][1], "--version")
            if os.name != "nt":
                self.assertTrue(path.stat().st_mode & 0o100)

    def test_windows_asset_uses_exe(self) -> None:
        archive = zip_bytes("tunnel-client.exe", b"fake-win-binary")
        digest = hashlib.sha256(archive).hexdigest()
        tag = "v1.2.3"
        asset = f"tunnel-client-{tag}-windows-arm64.zip"
        release = {
            "tag_name": tag,
            "assets": [
                {"name": asset, "browser_download_url": "https://example.test/client.zip"},
                {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/sums"},
            ],
        }
        payloads = {
            LATEST_RELEASE_API: json.dumps(release).encode(),
            "https://example.test/client.zip": archive,
            "https://example.test/sums": f"{digest} *{asset}\n".encode(),
        }

        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as td:
            result = install_official_tunnel_client(
                install_dir=Path(td),
                fetch=lambda url: payloads[url],
                runner=runner,
                system_name="Windows",
                machine="arm64",
            )
            self.assertTrue(str(result["path"]).endswith("tunnel-client.exe"))
            self.assertEqual(Path(str(result["path"])).read_bytes(), b"fake-win-binary")

    def test_checksum_mismatch_fails_before_install(self) -> None:
        archive = zip_bytes("tunnel-client", b"bad")
        tag = "v1.0.0"
        asset = f"tunnel-client-{tag}-darwin-arm64.zip"
        release = {
            "tag_name": tag,
            "assets": [
                {"name": asset, "browser_download_url": "https://example.test/client.zip"},
                {"name": "SHA256SUMS.txt", "browser_download_url": "https://example.test/sums"},
            ],
        }
        payloads = {
            LATEST_RELEASE_API: json.dumps(release).encode(),
            "https://example.test/client.zip": archive,
            "https://example.test/sums": f"{'0'*64}  {asset}\n".encode(),
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(TunnelInstallError, "checksum mismatch"):
                install_official_tunnel_client(
                    install_dir=Path(td),
                    fetch=lambda url: payloads[url],
                    runner=lambda *args, **kwargs: None,
                    system_name="Darwin",
                    machine="arm64",
                )
            self.assertFalse((Path(td) / "tunnel-client").exists())

    def test_unsupported_platform_fails_closed(self) -> None:
        with self.assertRaisesRegex(TunnelInstallError, "Unsupported platform"):
            install_official_tunnel_client(
                fetch=lambda _: b"{}",
                system_name="Haiku",
                machine="x86_64",
            )


if __name__ == "__main__":
    unittest.main()
