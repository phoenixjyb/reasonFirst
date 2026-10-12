from __future__ import annotations

import errno
import hashlib
import http.client
import io
import json
import os
import socket
import ssl
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from gitlab_agent import tunnel_install
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


def fake_curl_process(raw: bytes, *, returncode: int = 0) -> Mock:
    process = Mock()
    process.stdout = io.BytesIO(raw)
    process.wait.return_value = returncode
    process.poll.return_value = returncode
    return process


class TunnelDownloadTests(unittest.TestCase):
    def test_successful_https_request_does_not_start_curl(self) -> None:
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"download")
        with patch.object(urllib.request, "build_opener", return_value=opener), \
             patch.object(tunnel_install.shutil, "which") as which, \
             patch.object(subprocess, "Popen") as popen:
            self.assertEqual(tunnel_install._request_bytes(LATEST_RELEASE_API), b"download")
        which.assert_not_called()
        popen.assert_not_called()

    def test_transport_failures_recover_with_exactly_one_secure_curl_attempt(self) -> None:
        failures = [
            urllib.error.URLError(ssl.SSLEOFError(8, "TLS EOF")),
            ConnectionResetError("connection reset"),
            TimeoutError("timeout"),
            urllib.error.URLError(OSError(errno.ENETUNREACH, "network unreachable")),
            urllib.error.URLError(socket.gaierror(-3, "temporary failure")),
            http.client.IncompleteRead(b"partial", 10),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = Mock()
                opener.open.side_effect = failure
                process = fake_curl_process(b"recovered")
                with patch.object(urllib.request, "build_opener", return_value=opener), \
                     patch.object(tunnel_install.shutil, "which", return_value="/bin/curl"), \
                     patch.object(subprocess, "Popen", return_value=process) as popen:
                    self.assertEqual(tunnel_install._request_bytes(LATEST_RELEASE_API), b"recovered")
                self.assertEqual(opener.open.call_count, 1)
                popen.assert_called_once()
                argv = popen.call_args.args[0]
                self.assertEqual(argv[:2], ["/bin/curl", "--disable"])
                self.assertIn("--disallow-username-in-url", argv)
                for flag, value in (
                    ("--proto", "=https"), ("--proto-redir", "=https"),
                    ("--connect-timeout", "15"), ("--max-time", "180"),
                    ("--max-redirs", "5"), ("--url", LATEST_RELEASE_API),
                    ("--max-filesize", str(tunnel_install._MAX_DOWNLOAD_BYTES)),
                ):
                    self.assertEqual(argv[argv.index(flag) + 1], value)
                self.assertNotIn("--insecure", argv)
                self.assertNotIn("--retry", argv)
                self.assertFalse(popen.call_args.kwargs.get("shell", False))
                self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
                self.assertTrue(process.stdout.closed)

    def test_certificate_and_http_failures_do_not_try_another_transport(self) -> None:
        certificate_error = ssl.SSLCertVerificationError(1, "untrusted certificate")
        failures = [
            certificate_error,
            urllib.error.URLError(certificate_error),
            *[
                urllib.error.HTTPError(LATEST_RELEASE_API, code, "HTTP error", {}, None)
                for code in (403, 429, 500)
            ],
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, code=getattr(failure, "code", None)):
                opener = Mock()
                opener.open.side_effect = failure
                with patch.object(urllib.request, "build_opener", return_value=opener), \
                     patch.object(tunnel_install.shutil, "which") as which, \
                     patch.object(subprocess, "Popen") as popen:
                    with self.assertRaises(TunnelInstallError):
                        tunnel_install._request_bytes(LATEST_RELEASE_API)
                which.assert_not_called()
                popen.assert_not_called()

    def test_truncated_fixed_length_response_uses_transport_recovery(self) -> None:
        response = io.BytesIO(b"partial")
        response.length = 10  # HTTPResponse retains the unread Content-Length.
        opener = Mock()
        opener.open.return_value = response
        with patch.object(urllib.request, "build_opener", return_value=opener), \
             patch.object(tunnel_install.shutil, "which", return_value="/bin/curl"), \
             patch.object(subprocess, "Popen", return_value=fake_curl_process(b"complete")) as popen:
            self.assertEqual(tunnel_install._request_bytes(LATEST_RELEASE_API), b"complete")
        popen.assert_called_once()

    def test_missing_curl_reports_phase_url_and_recovery_without_raw_exception(self) -> None:
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(
            ssl.SSLEOFError(8, "proxy-user:private-proxy-password")
        )
        with patch.object(urllib.request, "build_opener", return_value=opener), \
             patch.object(tunnel_install.shutil, "which", return_value=None), \
             patch.object(subprocess, "Popen") as popen:
            with self.assertRaises(TunnelInstallError) as captured:
                install_official_tunnel_client(system_name="Linux", machine="amd64")
        message = str(captured.exception)
        self.assertIn("release metadata download failed", message)
        self.assertIn(LATEST_RELEASE_API, message)
        self.assertIn("curl is unavailable", message)
        self.assertIn("Install curl", message)
        self.assertIn("retry the same install command", message)
        self.assertNotIn("private-proxy-password", message)
        popen.assert_not_called()

    def test_curl_failure_is_sanitized_and_not_retried(self) -> None:
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(
            ssl.SSLEOFError(8, "sensitive proxy error")
        )
        # 35 is a TLS failure; 67 is curl's rejection of URL user information.
        for returncode in (35, 67):
            with self.subTest(returncode=returncode):
                process = fake_curl_process(b"partial download", returncode=returncode)
                with patch.object(urllib.request, "build_opener", return_value=opener), \
                     patch.object(tunnel_install.shutil, "which", return_value="/bin/curl"), \
                     patch.object(subprocess, "Popen", return_value=process) as popen:
                    with self.assertRaises(TunnelInstallError) as captured:
                        install_official_tunnel_client(system_name="Linux", machine="amd64")
                message = str(captured.exception)
                self.assertIn("release metadata download failed", message)
                self.assertIn("TLS connection failed", message)
                self.assertIn(f"curl recovery failed (exit code {returncode})", message)
                self.assertNotIn("sensitive", message)
                popen.assert_called_once()

    def test_unsafe_initial_urls_and_redirects_are_rejected(self) -> None:
        synthetic_username = "user"
        synthetic_password = "password"
        synthetic_credential_url = urllib.parse.urlunsplit(
            ("https", f"{synthetic_username}:{synthetic_password}@example.test", "/file", "", "")
        )
        for url in ("http://example.test/file", synthetic_credential_url):
            with self.subTest(url=url), patch.object(urllib.request, "build_opener") as opener, \
                 patch.object(subprocess, "Popen") as popen:
                with self.assertRaisesRegex(TunnelInstallError, "Refusing"):
                    tunnel_install._request_bytes(url)
                opener.assert_not_called()
                popen.assert_not_called()
                handler = tunnel_install._HTTPSOnlyRedirectHandler()
                with self.assertRaisesRegex(TunnelInstallError, "Refusing"):
                    handler.redirect_request(
                        urllib.request.Request(LATEST_RELEASE_API), None, 302, "", {}, url,
                    )

    def test_https_redirect_is_preserved(self) -> None:
        target = "https://release-assets.githubusercontent.com/fixture"
        request = tunnel_install._HTTPSOnlyRedirectHandler().redirect_request(
            urllib.request.Request(LATEST_RELEASE_API), None, 302, "", {}, target,
        )
        self.assertEqual(request.full_url, target)

    def test_oversized_response_is_rejected_without_transport_retry(self) -> None:
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"123456789")
        with patch.object(tunnel_install, "_MAX_DOWNLOAD_BYTES", 8), \
             patch.object(urllib.request, "build_opener", return_value=opener), \
             patch.object(subprocess, "Popen") as popen:
            with self.assertRaisesRegex(TunnelInstallError, "response limit"):
                tunnel_install._request_bytes(LATEST_RELEASE_API)
        popen.assert_not_called()

    def test_curl_response_is_limited_even_if_curl_ignores_max_filesize(self) -> None:
        process = fake_curl_process(b"123456789")
        process.poll.return_value = None
        with patch.object(tunnel_install, "_MAX_DOWNLOAD_BYTES", 8), \
             patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(TunnelInstallError, "response limit"):
                tunnel_install._curl_request_bytes(LATEST_RELEASE_API, "/bin/curl")
        process.kill.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_curl_watchdog_kills_process_and_reports_timeout(self) -> None:
        process = fake_curl_process(b"", returncode=-9)
        timer = Mock()

        def expired_timer(seconds, callback):
            self.assertEqual(seconds, 190)
            timer.start.side_effect = callback
            return timer

        with patch.object(subprocess, "Popen", return_value=process), \
             patch.object(tunnel_install.threading, "Timer", side_effect=expired_timer):
            with self.assertRaisesRegex(TunnelInstallError, "curl recovery timed out"):
                tunnel_install._curl_request_bytes(LATEST_RELEASE_API, "/bin/curl")
        process.kill.assert_called_once()
        timer.cancel.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_invalid_metadata_does_not_trigger_transport_recovery(self) -> None:
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"not JSON")
        with patch.object(urllib.request, "build_opener", return_value=opener), \
             patch.object(subprocess, "Popen") as popen:
            with self.assertRaisesRegex(TunnelInstallError, "Could not parse"):
                install_official_tunnel_client(system_name="Linux", machine="amd64")
        popen.assert_not_called()

    def test_checksum_and_archive_errors_name_phase_and_omit_url_secrets(self) -> None:
        asset = "tunnel-client-v1-linux-amd64.zip"
        checksum_url = "https://example.test/SHA256SUMS.txt?access=private-token"
        archive_url = "https://example.test/client.zip?access=private-token"
        release = json.dumps({
            "tag_name": "v1",
            "assets": [
                {"name": asset, "browser_download_url": archive_url},
                {"name": "SHA256SUMS.txt", "browser_download_url": checksum_url},
            ],
        }).encode()
        for failed_url, phase in ((checksum_url, "checksum file"), (archive_url, "archive")):
            def fetch(url):
                if url == failed_url:
                    raise urllib.error.URLError(ConnectionResetError("private-proxy-password"))
                if url == LATEST_RELEASE_API:
                    return release
                return f"{'0' * 64}  {asset}\n".encode()

            with self.subTest(phase=phase), self.assertRaises(TunnelInstallError) as captured:
                install_official_tunnel_client(
                    fetch=fetch, system_name="Linux", machine="amd64",
                )
            message = str(captured.exception)
            self.assertIn(f"{phase} download failed", message)
            self.assertIn(failed_url.split("?")[0], message)
            self.assertNotIn("private-token", message)
            self.assertNotIn("private-proxy-password", message)

    def test_recovered_download_still_requires_checksum_and_complete_bundle(self) -> None:
        valid_files = {
            "tunnel-client": b"client",
            "cloudflared": b"companion",
            "cloudflared-manifest.json": b"{}",
        }
        valid_archive = zip_bundle(valid_files)
        asset = "tunnel-client-v1-linux-amd64.zip"
        checksum_url = "https://example.test/SHA256SUMS.txt"
        archive_url = "https://example.test/client.zip"
        release = json.dumps({
            "tag_name": "v1",
            "assets": [
                {"name": asset, "browser_download_url": archive_url},
                {"name": "SHA256SUMS.txt", "browser_download_url": checksum_url},
            ],
        }).encode()
        cases = [
            ("valid", valid_archive, None),
            ("tampered", b"tampered response", TunnelInstallError),
            ("missing companion", zip_bundle({"tunnel-client": b"client"}), TunnelInstallError),
            ("malformed archive", b"not a zip", zipfile.BadZipFile),
        ]
        for case, archive, expected_error in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                digest = hashlib.sha256(valid_archive if case == "tampered" else archive).hexdigest()
                payloads = {
                    LATEST_RELEASE_API: release,
                    checksum_url: f"{digest}  {asset}\n".encode(),
                    archive_url: archive,
                }
                opener = Mock()
                opener.open.side_effect = urllib.error.URLError(ssl.SSLEOFError(8, "EOF"))
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="v1", stderr=""))
                with patch.object(urllib.request, "build_opener", return_value=opener), \
                     patch.object(tunnel_install.shutil, "which", return_value="/bin/curl"), \
                     patch.object(subprocess, "Popen", side_effect=lambda argv, **_: fake_curl_process(payloads[argv[-1]])) as popen:
                    if expected_error is not None:
                        with self.assertRaises(expected_error):
                            install_official_tunnel_client(
                                install_dir=Path(td), runner=runner,
                                system_name="Linux", machine="amd64",
                            )
                        runner.assert_not_called()
                        self.assertEqual(list(Path(td).iterdir()), [])
                    else:
                        result = install_official_tunnel_client(
                            install_dir=Path(td), runner=runner,
                            system_name="Linux", machine="amd64",
                        )
                        self.assertEqual(result["sha256"], digest)
                        for name, content in valid_files.items():
                            self.assertEqual((Path(result["bundle_dir"]) / name).read_bytes(), content)
                        runner.assert_called_once()
                # Each URL gets a single recovery attempt. Integrity failures
                # never cause another download or a partial install.
                self.assertEqual(popen.call_count, 3)
                self.assertEqual(opener.open.call_count, 3)


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

    def test_tampered_existing_bundle_is_not_reused(self) -> None:
        archive = zip_bundle(
            {
                "tunnel-client": b"client",
                "cloudflared": b"companion",
                "cloudflared-manifest.json": b"{}",
            }
        )
        digest = hashlib.sha256(archive).hexdigest()
        tag = "v2.1.0"
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
            Path(str(first["companion_path"])).write_bytes(b"tampered")
            with self.assertRaisesRegex(
                TunnelInstallError,
                "differs from the verified official archive",
            ):
                install_official_tunnel_client(
                    install_dir=Path(td),
                    fetch=lambda url: payloads[url],
                    runner=runner,
                    system_name="Linux",
                    machine="amd64",
                )

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
