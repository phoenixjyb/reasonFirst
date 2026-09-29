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


def zip_bundle(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TunnelInstallTests(unittest.TestCase):
    def test_linux_install_verifies_checksum_and_complete_bundle(self) -> None:
        archive = zip_bundle(
            {
                "bundle/tunnel-client": b"fake-linux-binary",
                "bundle/cloudflared": b"fake-cloudflared",
                "bundle/cloudflared-manifest.json": b'{"version":"fixture"}\n',
                "bundle/LICENSE": b"license",
                "bundle/NOTICE": b"notice",
            }
        )
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
            return subprocess.CompletedProcess(
                argv, 0, stdout="tunnel-client v9.9.9\n", stderr=""
            )

        with tempfile.TemporaryDirectory() as td:
            result = install_official_tunnel_client(
                install_dir=Path(td),
                fetch=fetch,
                runner=runner,
                system_name="Linux",
                machine="x86_64",
            )
            path = Path(str(result["path"]))
            bundle = Path(str(result["bundle_dir"]))
            self.assertEqual(path.read_bytes(), b"fake-linux-binary")
            self.assertEqual(
                Path(str(result["companion_path"])).read_bytes(),
                b"fake-cloudflared",
            )
            self.assertEqual(
                Path(str(result["manifest_path"])).read_bytes(),
                b'{"version":"fixture"}\n',
            )
            self.assertEqual((bundle / "LICENSE").read_bytes(), b"license")
            self.assertEqual((bundle / "NOTICE").read_bytes(), b"notice")
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(result["platform"], "linux-amd64")
            self.assertEqual(calls[0][1], "--version")
            if os.name != "nt":
                self.assertTrue(path.stat().st_mode & 0o100)
                self.assertTrue(
                    Path(str(result["companion_path"])).stat().st_mode & 0o100
                )

    def test_existing_verified_bundle_is_reused(self) -> None:
        archive = zip_bundle(
            {
                "tunnel-client": b"client",
                "cloudflared": b"companion",
                "cloudflared-manifest.json": b"{}",
            }
        )
        digest = hashlib.sha256(archive).hexdigest()
        tag = "v2.0.0"
        asset = f"tunnel-client-{tag}-linux-amd64.zip"
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
            "https://example.test/sums": f"{digest}  {asset}\n".encode(),
        }

        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout="v2", stderr="")

        with tempfile.TemporaryDirectory() as td:
            first = install_official_tunnel_client(
                install_dir=Path(td),
                fetch=lambda url: payloads[url],
                runner=runner,
                system_name="Linux",
                machine="amd64",
            )
            second = install_official_tunnel_client(
                install_dir=Path(td),
                fetch=lambda url: payloads[url],
                runner=runner,
                system_name="Linux",
                machine="amd64",
            )
            self.assertTrue(first["installed"])
            self.assertFalse(second["installed"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["path"], second["path"])

    def test_windows_asset_uses_exe_and_adjacent_companion(self) -> None:
        archive = zip_bundle(
            {
                "tunnel-client.exe": b"fake-win-binary",
                "cloudflared.exe": b"fake-win-cloudflared",
                "cloudflared-manifest.json": b"{}",
            }
        )
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
            self.assertEqual(
                Path(str(result["path"])).read_bytes(),
                b"fake-win-binary",
            )
            self.assertEqual(
                Path(str(result["companion_path"])).read_bytes(),
                b"fake-win-cloudflared",
            )

    def test_missing_companion_is_rejected(self) -> None:
        archive = zip_bundle(
            {
                "tunnel-client": b"client",
                "cloudflared-manifest.json": b"{}",
            }
        )
        digest = hashlib.sha256(archive).hexdigest()
        tag = "v1.0.1"
        asset = f"tunnel-client-{tag}-linux-amd64.zip"
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
            "https://example.test/sums": f"{digest}  {asset}\n".encode(),
        }
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(TunnelInstallError, "cloudflared"):
                install_official_tunnel_client(
                    install_dir=Path(td),
                    fetch=lambda url: payloads[url],
                    runner=lambda *args, **kwargs: None,
                    system_name="Linux",
                    machine="amd64",
                )

    def test_checksum_mismatch_fails_before_install(self) -> None:
        archive = zip_bundle({"tunnel-client": b"bad"})
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
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_unsupported_platform_fails_closed(self) -> None:
        with self.assertRaisesRegex(TunnelInstallError, "Unsupported platform"):
            install_official_tunnel_client(
                fetch=lambda _: b"{}",
                system_name="Haiku",
                machine="x86_64",
            )


if __name__ == "__main__":
    unittest.main()
