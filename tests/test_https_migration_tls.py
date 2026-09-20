from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
from pathlib import Path
import ssl
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from gitlab_agent.https_migration import check_tls


class TLSProbeTests(unittest.TestCase):
    """Real loopback TLS; fresh keys/certs exist only in a temporary directory.

    The probe uses the production HTTPX default trust policy. Tests patch its
    certifi path to an ephemeral CA to exercise verification deterministically.
    This does not implement a runtime private-CA setting for ReasonFirst.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        now = datetime.now(timezone.utc)
        self.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Temporary migration test CA")])
        self.ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(self.ca_key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=2))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.ca_key.public_key()), critical=False)
                   .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()), critical=False)
                   .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False,
                                                 content_commitment=False, data_encipherment=False,
                                                 key_agreement=False, key_cert_sign=True, crl_sign=True,
                                                 encipher_only=False, decipher_only=False), critical=True)
                   .sign(self.ca_key, hashes.SHA256()))
        self.ca_path = self.root / "ca.pem"
        self.ca_path.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))

    @contextmanager
    def server(self, *, matching_host=True, expired=False, status=200, location=None):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=2)
        end = now - timedelta(days=1) if expired else now + timedelta(days=1)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test server")])
        san = (x509.IPAddress(ipaddress.ip_address("127.0.0.1")) if matching_host
               else x509.DNSName("wrong.example.invalid"))
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(self.ca.subject)
                       .public_key(key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(start).not_valid_after(end)
                       .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                       .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.ca_key.public_key()), critical=False)
                       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                       .sign(self.ca_key, hashes.SHA256()))
        cert_path, key_path = self.root / "server.pem", self.root / "server.key"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(dict(self.headers))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *args):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        # Arbitrary port is only a test probe fixture, not an accepted migration map.
        plan = SimpleNamespace(mapping=SimpleNamespace(new=f"https://127.0.0.1:{httpd.server_port}"))
        try:
            yield plan, requests
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=3)

    def test_verified_tls_succeeds_without_environment_credentials_or_proxy(self):
        with (self.server() as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path)),
              patch.dict(os.environ, {"GITLAB_TOKEN": "do-not-send-this", "GITLAB_GIT_TOKEN": "also-private",
                                      "HTTPS_PROXY": "http://127.0.0.1:1"})):
            result = check_tls(plan)
        self.assertTrue(result["ok"])
        self.assertTrue(result["certificate_verified"])
        self.assertFalse(result["api_auth_checked"])
        self.assertFalse(result["git_tls_checked"])
        self.assertEqual(len(requests), 1)
        self.assertNotIn("do-not-send-this", str(requests))
        self.assertNotIn("also-private", str(requests))
        self.assertNotIn("Authorization", requests[0])
        self.assertNotIn("PRIVATE-TOKEN", requests[0])

    def test_untrusted_ca_is_rejected_without_http_request(self):
        with self.server() as (plan, requests):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(requests, [])

    def test_expired_certificate_is_rejected(self):
        with (self.server(expired=True) as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(requests, [])

    def test_wrong_hostname_is_rejected(self):
        with (self.server(matching_host=False) as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(requests, [])

    def test_cross_origin_redirect_is_not_followed(self):
        with (self.server(status=302, location="https://rejected.example.invalid/") as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertIn("redirects", result["error"])
        self.assertEqual(len(requests), 1)

    def test_downgrade_redirect_is_not_followed(self):
        with (self.server(status=301, location="http://127.0.0.1:1/") as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(len(requests), 1)

    def test_same_origin_login_redirect_is_not_claimed_ready(self):
        with (self.server(status=302, location="/users/sign_in") as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(len(requests), 1)

    def test_http_error_still_distinguishes_verified_tls_from_api_auth(self):
        with (self.server(status=401) as (plan, requests),
              patch.object(certifi, "where", return_value=str(self.ca_path))):
            result = check_tls(plan)
        self.assertTrue(result["certificate_verified"])
        self.assertEqual(result["status_code"], 401)
        self.assertFalse(result["api_auth_checked"])


if __name__ == "__main__":
    unittest.main()
