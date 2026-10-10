from __future__ import annotations

import asyncio
import base64
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization

from gitlab_agent.config import AgentSettings
from gitlab_agent.tls import api_client_options, verified_context
from gitlab_agent.upgrade import service_trust as t
from tls_fixtures import LocalAuthority


class ServiceAPITrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material_root = tempfile.TemporaryDirectory(prefix="rf-trust-material-")
        root = Path(cls.material_root.name)
        roots = [root / name for name in ("public", "custom", "other")]
        for path in roots:
            path.mkdir()
        cls.public_authority, cls.custom_authority, cls.other_authority = [LocalAuthority(path) for path in roots]
        cls.public_pem = cls.public_authority.ca_path.read_bytes()
        cls.custom_pem = cls.custom_authority.ca_path.read_bytes()
        cls.other_pem = cls.other_authority.ca_path.read_bytes()
        cls.private_pkcs8_pem = cls.custom_authority.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.private_rsa_pem = cls.custom_authority.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

    @classmethod
    def tearDownClass(cls):
        cls.material_root.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-private-trust-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.public = self.root / "private-public-root.pem"
        self.custom = self.root / "private-custom-root.pem"
        self.public.write_bytes(self.public_pem)
        self.custom.write_bytes(self.custom_pem)
        self.settings = AgentSettings(
            config_file=self.root / "selected.env", gitlab_base_url="https://gitlab.example.invalid",
            api_token="private-credential-marker", api_verify_ssl=True, api_trust_env=False,
            git_token="", git_username="oauth2", git_trust_env=False,
            allowed_projects={"owned/project"}, require_write_allowlist=True,
            workspace_root=self.root / "workspaces", branch_prefix="chatgpt/", default_base_ref="main",
            allowed_executables={"git", "python"}, command_timeout_seconds=30,
            max_output_bytes=10000, max_file_bytes=10000, git_author_name=None,
            git_author_email=None, api_ca_bundle=self.custom,
        )

    def capture(self, settings=None):
        with patch.object(t.certifi, "where", return_value=str(self.public)):
            return t.capture_api_trust(self.settings if settings is None else settings)

    def assert_code(self, code, action):
        with self.assertRaises(t.ServiceAPITrustError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        self.assertNotIn(str(self.root), repr(raised.exception))
        self.assertNotIn("private-credential-marker", repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        return raised.exception

    def options(self, trust, *, asynchronous=False):
        return api_client_options(self.settings.gitlab_base_url, ca_bundle=self.custom,
                                  retained_trust=trust, asynchronous=asynchronous)

    def credential_url(self):
        return str(httpx.URL(self.settings.gitlab_base_url).copy_with(
            username="synthetic-user", password=self.settings.api_token,
        ))

    def test_factory_requires_exact_settings_before_selection(self):
        with patch.object(t.certifi, "where", side_effect=AssertionError("must not select")):
            for value in (None, {}, object()):
                self.assert_code("invalid_api_trust", lambda: t.capture_api_trust(value))
            self.assert_code("invalid_api_trust", lambda: t.ServiceAPITrust())

    def test_capture_keeps_exact_settings_and_private_immutable_material(self):
        trust = self.capture()
        self.assertIs(trust.settings, self.settings)
        self.assertEqual(repr(trust), "ServiceAPITrust(retained_api_trust=private)")
        with self.assertRaises(FrozenInstanceError):
            trust._settings = replace(self.settings)
        self.assertIsNone(trust.revalidate())
        self.assertEqual(trust.summary()["selected_bundle_count"], 2)

    def test_summary_is_cached_detached_and_has_exact_finite_schema(self):
        trust = self.capture()
        with patch.object(t, "_observe_file", side_effect=AssertionError("no read")), \
                patch.object(Path, "resolve", side_effect=AssertionError("no path read")):
            summary = trust.summary()
        self.assertEqual(summary, {
            "schema_version": 1, "scope": "selected-gitlab-api-trust-v1",
            "material_retained": True, "selected_bundle_count": 2,
            "current_process_only": True, "tls_peer_verified": False,
            "provider_configuration_verified": False, "native_git_trust_verified": False,
            "native_ssh_trust_verified": False, "activation_authorized": False,
        })
        summary["selected_bundle_count"] = 999
        self.assertEqual(trust.summary()["selected_bundle_count"], 2)
        public = json.dumps(trust.summary()) + repr(trust)
        for marker in (str(self.root), "PRIVATE", "BEGIN CERTIFICATE", "private-credential-marker",
                       hashlib.sha256(self.custom_pem).hexdigest()):
            self.assertNotIn(marker, public)

    def test_public_only_context_does_not_consult_unselected_custom_file(self):
        settings = replace(self.settings, api_ca_bundle=None)
        self.custom.unlink()
        trust = self.capture(settings)
        self.assertEqual(trust.summary()["selected_bundle_count"], 1)
        self.assertIsNone(t.encode_api_trust(trust)["custom"])
        self.assertEqual(len(trust.ssl_context().get_ca_certs(binary_form=True)), 1)

    def test_fresh_contexts_use_only_retained_public_and_custom_bytes(self):
        trust = self.capture()
        with patch.object(t.certifi, "where", side_effect=AssertionError("no reselection")), \
                patch.dict(os.environ, {"SSL_CERT_FILE": str(self.root / "absent"),
                                        "SSL_CERT_DIR": str(self.root / "absent-dir")}):
            first, second = trust.ssl_context(), trust.ssl_context()
        self.assertIsNot(first, second)
        self.assertEqual(len(first.get_ca_certs(binary_form=True)), 2)
        first.check_hostname = False
        first.verify_mode = ssl.CERT_NONE
        self.assertTrue(second.check_hostname)
        self.assertEqual(second.verify_mode, ssl.CERT_REQUIRED)

    def test_option_validation_rejects_different_destination_verification_and_ca(self):
        trust = self.capture()
        alternatives = [
            ("https://other.example.invalid", True, self.custom),
            (self.settings.gitlab_base_url, False, self.custom),
            (self.settings.gitlab_base_url, 1, self.custom),
            (self.settings.gitlab_base_url, True, None),
            (self.settings.gitlab_base_url, True, self.public),
            (self.credential_url(), True, self.custom),
        ]
        for args in alternatives:
            with self.subTest(args=args[:2]):
                self.assert_code("api_trust_options_mismatch", lambda: trust.assert_options(*args))
        self.assertIsNone(trust.assert_options(self.settings.gitlab_base_url + "/", True, str(self.custom)))

    def test_invalid_selected_options_fail_before_any_file_is_read(self):
        cases = [replace(self.settings, api_verify_ssl=False),
                 replace(self.settings, api_ca_bundle=Path("relative.pem")),
                 replace(self.settings, gitlab_base_url=self.credential_url())]
        with patch.object(t, "_read_bundle", side_effect=AssertionError("no file read")):
            for settings in cases:
                self.assert_code("api_trust_options_mismatch", lambda: self.capture(settings))

    def test_missing_selected_public_or_custom_bundle_has_no_fallback(self):
        self.public.unlink()
        self.assert_code("api_trust_unavailable", self.capture)
        self.public.write_bytes(self.public_pem)
        self.custom.unlink()
        self.assert_code("api_trust_unavailable", self.capture)

    def test_failed_capture_can_be_retried_without_refreshing_a_prior_object(self):
        self.custom.write_text("invalid-private-marker")
        self.assert_code("api_trust_invalid_material", self.capture)
        self.custom.write_bytes(self.custom_pem)
        self.assertEqual(self.capture().summary()["selected_bundle_count"], 2)

    def test_empty_nonascii_and_private_key_material_rejected_in_either_bundle(self):
        values = (b"", b"not certificates", b"\xff", self.private_pkcs8_pem,
                  self.public_pem + self.private_rsa_pem)
        for destination in (self.public, self.custom):
            original = destination.read_bytes()
            for raw in values:
                with self.subTest(destination=destination.name, size=len(raw)):
                    destination.write_bytes(raw)
                    self.assert_code("api_trust_invalid_material", self.capture)
            destination.write_bytes(original)

    def test_oversized_selected_bundle_rejects_before_read(self):
        self.custom.write_bytes(b"x" * (t.MAX_CA_BUNDLE_BYTES + 1))
        actual_read = os.read
        def reject_large_read(fd, size):
            self.assertLessEqual(os.fstat(fd).st_size, t.MAX_CA_BUNDLE_BYTES)
            return actual_read(fd, size)
        with patch.object(t.os, "read", side_effect=reject_large_read):
            self.assert_code("api_trust_limit", self.capture)

    def test_maximum_sized_public_and_custom_bundles_fit_private_transfer_budget(self):
        self.public.write_bytes(self.public_pem + b"\n" * (t.MAX_CA_BUNDLE_BYTES - len(self.public_pem)))
        self.custom.write_bytes(self.custom_pem + b"\n" * (t.MAX_CA_BUNDLE_BYTES - len(self.custom_pem)))
        payload = t.encode_api_trust(self.capture())
        self.assertLessEqual(len(json.dumps(payload).encode("utf-8")), t.MAX_TRUST_PAYLOAD_BYTES)
        decoded = t.decode_api_trust(replace(self.settings), payload)
        self.assertEqual(len(decoded.ssl_context().get_ca_certs(binary_form=True)), 2)

    def test_directory_is_not_a_trust_bundle(self):
        self.custom.unlink()
        self.custom.mkdir()
        self.assert_code("api_trust_unavailable", self.capture)

    def test_descriptor_capture_returns_bytes_and_digest_from_one_read(self):
        real_read = os.read
        chunks = []
        def record(fd, size):
            result = real_read(fd, size)
            chunks.append(result)
            return result
        with patch.object(t.os, "read", side_effect=record):
            trust = self.capture(replace(self.settings, api_ca_bundle=None))
        payload = t.encode_api_trust(trust)["public"]
        raw = base64.b64decode(payload["pem_base64"])
        self.assertEqual(raw, b"".join(chunks))
        self.assertEqual(payload["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(payload["identity"][3], len(raw))

    def test_mutation_during_descriptor_read_fails_capture(self):
        real_read = os.read
        changed = False
        def mutate(fd, size):
            nonlocal changed
            result = real_read(fd, size)
            if not changed:
                changed = True
                self.public.write_bytes(self.other_pem)
            return result
        with patch.object(t.os, "read", side_effect=mutate):
            self.assert_code("api_trust_changed", self.capture)

    def test_selected_material_change_is_sticky_even_after_restoration(self):
        trust = self.capture()
        self.custom.write_bytes(self.other_pem)
        self.assert_code("api_trust_changed", trust.revalidate)
        self.custom.write_bytes(self.custom_pem)
        for action in (trust.revalidate, trust.ssl_context, trust.summary,
                       lambda: t.encode_api_trust(trust)):
            self.assert_code("api_trust_changed", action)

    def test_public_root_change_cannot_be_reselected(self):
        trust = self.capture()
        self.public.write_bytes(self.other_pem)
        with patch.object(t.certifi, "where", return_value=str(self.custom)) as where:
            self.assert_code("api_trust_changed", trust.ssl_context)
        where.assert_not_called()

    def test_selected_deletion_is_sticky(self):
        trust = self.capture()
        self.custom.unlink()
        self.assert_code("api_trust_changed", trust.revalidate)
        self.custom.write_bytes(self.custom_pem)
        self.assert_code("api_trust_changed", trust.revalidate)

    def test_same_bytes_replacement_changes_selected_file_identity(self):
        trust = self.capture()
        replacement = self.root / "replacement.pem"
        replacement.write_bytes(self.custom_pem)
        replacement.replace(self.custom)
        self.assert_code("api_trust_changed", trust.revalidate)

    def test_invocation_alias_drift_is_detected_before_ssl_context(self):
        trust = self.capture()
        payload = t.encode_api_trust(trust)
        self.assertEqual(payload["custom"]["path"], str(self.custom))
        other = self.root / "other.pem"
        other.write_bytes(self.custom_pem)
        actual_resolve = Path.resolve
        def redirected(path, *args, **kwargs):
            return actual_resolve(other if path == self.custom else path, *args, **kwargs)
        with patch.object(Path, "resolve", redirected):
            self.assert_code("api_trust_changed", trust.ssl_context)

    def test_wrong_pid_rejects_before_lock_and_remains_sticky(self):
        trust = self.capture()
        class NoLock:
            def __enter__(self):
                raise AssertionError("inherited lock was acquired")
        lock = trust._lock
        object.__setattr__(trust, "_lock", NoLock())
        with patch.object(t.os, "getpid", return_value=os.getpid() + 1000):
            self.assert_code("api_trust_wrong_process", trust.revalidate)
        object.__setattr__(trust, "_lock", lock)
        self.assert_code("api_trust_wrong_process", trust.summary)

    def test_encode_returns_detached_material_and_observation(self):
        trust = self.capture()
        payload = t.encode_api_trust(trust)
        self.assertEqual(set(payload), {"schema_version", "scope", "public", "custom"})
        self.assertEqual(set(payload["public"]), {"path", "resolved_path", "identity", "sha256", "pem_base64"})
        self.assertEqual(base64.b64decode(payload["custom"]["pem_base64"]), self.custom_pem)
        payload["custom"]["identity"][3] = 0
        payload["public"]["pem_base64"] = "modified"
        refreshed = t.encode_api_trust(trust)
        self.assertEqual(refreshed["custom"]["identity"][3], len(self.custom_pem))
        self.assertEqual(base64.b64decode(refreshed["public"]["pem_base64"]), self.public_pem)

    def test_decode_uses_transferred_material_and_exact_child_settings(self):
        original = self.capture()
        payload = t.encode_api_trust(original)
        child_settings = replace(self.settings)
        with patch.object(t.certifi, "where", side_effect=AssertionError("no selection")), \
                patch.object(t, "_read_bundle", side_effect=AssertionError("no recapture")):
            child = t.decode_api_trust(child_settings, payload)
            context = child.ssl_context()
        self.assertIs(child.settings, child_settings)
        self.assertIsNot(child, original)
        self.assertEqual(context.get_ca_certs(binary_form=True), original.ssl_context().get_ca_certs(binary_form=True))

    def test_decode_does_not_accept_changed_transferred_file(self):
        payload = t.encode_api_trust(self.capture())
        self.custom.write_bytes(self.other_pem)
        self.assert_code("api_trust_changed", lambda: t.decode_api_trust(self.settings, payload))

    def test_decode_rejects_missing_custom_material_and_wrong_selected_path(self):
        payload = t.encode_api_trust(self.capture())
        no_custom = json.loads(json.dumps(payload))
        no_custom["custom"] = None
        self.assert_code("api_trust_invalid_payload", lambda: t.decode_api_trust(self.settings, no_custom))
        self.assert_code("api_trust_invalid_payload", lambda: t.decode_api_trust(
            replace(self.settings, api_ca_bundle=None), payload))
        self.assert_code("api_trust_invalid_payload", lambda: t.decode_api_trust(
            replace(self.settings, api_ca_bundle=self.public), payload))

    def test_decode_rejects_invalid_outer_schema_before_observing_files(self):
        baseline = t.encode_api_trust(self.capture())
        cases = [None, [], {}, {**baseline, "extra": True},
                 {**baseline, "schema_version": True}, {**baseline, "schema_version": 2},
                 {**baseline, "scope": "unrecognized"}, {**baseline, "public": None}]
        with patch.object(t, "_observe_file", side_effect=AssertionError("no observation")):
            for payload in cases:
                self.assert_code("api_trust_invalid_payload", lambda: t.decode_api_trust(self.settings, payload))

    def test_decode_rejects_invalid_bundle_metadata_and_base64(self):
        baseline = t.encode_api_trust(self.capture())
        bad = {
            "path": ["relative.pem", 7, ""],
            "resolved_path": ["relative.pem", None],
            "identity": [[1, 2, 3], [1, 2, stat.S_IFREG, True, 0, 0],
                         [1, 2, stat.S_IFDIR, 5, 0, 0], [1, 2, stat.S_IFREG, -1, 0, 0],
                         [1, 2, stat.S_IFREG, 5, 2**200, 0]],
            "sha256": ["not-a-digest", "0" * 64, True],
            "pem_base64": ["", "!bad!", "Zg===", "é", "x" * (t._MAX_BASE64_CHARS + 1)],
        }
        with patch.object(t, "_observe_file", side_effect=AssertionError("no observation")):
            for field, values in bad.items():
                for value in values:
                    payload = json.loads(json.dumps(baseline))
                    payload["public"][field] = value
                    with self.subTest(field=field):
                        self.assert_code("api_trust_invalid_payload", lambda: t.decode_api_trust(self.settings, payload))

    def test_decode_material_digest_size_and_certificate_must_correspond(self):
        baseline = t.encode_api_trust(self.capture())
        for raw in (b"invalid", self.public_pem + self.private_pkcs8_pem):
            payload = json.loads(json.dumps(baseline))
            bundle = payload["public"]
            bundle.update(pem_base64=base64.b64encode(raw).decode(), sha256=hashlib.sha256(raw).hexdigest())
            bundle["identity"][3] = len(raw)
            self.assert_code("api_trust_invalid_material", lambda: t.decode_api_trust(self.settings, payload))

    def test_decode_observation_metadata_must_match_current_file(self):
        payload = t.encode_api_trust(self.capture())
        payload["public"]["identity"][1] += 1
        self.assert_code("api_trust_changed", lambda: t.decode_api_trust(self.settings, payload))

    def test_tls_entrypoints_reject_foreign_trust_objects(self):
        for value in ({}, object()):
            self.assert_code("invalid_api_trust", lambda: verified_context(
                self.settings.gitlab_base_url, ca_bundle=self.custom, retained_trust=value))
            self.assert_code("invalid_api_trust", lambda: api_client_options(
                self.settings.gitlab_base_url, ca_bundle=self.custom, retained_trust=value))

    def test_tls_entrypoint_enforces_selected_options(self):
        trust = self.capture()
        self.assert_code("api_trust_options_mismatch", lambda: verified_context(
            self.settings.gitlab_base_url, ca_bundle=None, retained_trust=trust))
        self.assert_code("api_trust_options_mismatch", lambda: api_client_options(
            self.settings.gitlab_base_url, verify_ssl=False, ca_bundle=self.custom, retained_trust=trust))

    def test_sync_pre_request_drift_guard_runs_before_transport(self):
        trust = self.capture()
        sent = []
        with httpx.Client(transport=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200)),
                          trust_env=False, **self.options(trust)) as client:
            self.custom.write_bytes(self.other_pem)
            self.assert_code("api_trust_changed", lambda: client.get(self.settings.gitlab_base_url + "/api/v4/user"))
        self.assertEqual(sent, [])

    def test_async_pre_request_drift_guard_runs_before_transport(self):
        trust = self.capture()
        sent = []
        async def check():
            async with httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200)),
                    trust_env=False, **self.options(trust, asynchronous=True)) as client:
                self.public.write_bytes(self.other_pem)
                with self.assertRaises(t.ServiceAPITrustError) as raised:
                    await client.get(self.settings.gitlab_base_url + "/api/v4/user")
                self.assertEqual(raised.exception.code, "api_trust_changed")
        asyncio.run(check())
        self.assertEqual(sent, [])

    def test_retained_trust_keeps_destination_and_redirect_guards(self):
        trust = self.capture()
        sent = []
        def respond(request):
            sent.append(request)
            return httpx.Response(302, headers={"Location": "https://other.example.invalid/api/v4/user"})
        with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False, **self.options(trust)) as client:
            with self.assertRaisesRegex(httpx.RequestError, "destination rejected"):
                client.get("https://other.example.invalid/api/v4/user")
            self.assertEqual(sent, [])
            with self.assertRaisesRegex(httpx.RequestError, "redirect refused"):
                client.get(self.settings.gitlab_base_url + "/api/v4/user", follow_redirects=True)
        self.assertEqual(len(sent), 1)

    def test_real_loopback_tls_uses_selected_private_ca(self):
        with self.custom_authority.serve() as (url, seen):
            settings = replace(self.settings, gitlab_base_url=url)
            trust = self.capture(settings)
            with httpx.Client(trust_env=False, headers={"PRIVATE-TOKEN": "dummy-session"},
                              **api_client_options(url, ca_bundle=self.custom, retained_trust=trust)) as client:
                self.assertEqual(client.get(url + "/api/v4/user").json()["username"], "loopback-test")
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["token"], "dummy-session")

    def test_real_loopback_other_ca_and_invalid_leaf_send_no_token(self):
        for authority, options in ((self.other_authority, {}),
                                   (self.custom_authority, {"expired": True}),
                                   (self.custom_authority, {"wrong_host": True})):
            with authority.serve(**options) as (url, seen):
                settings = replace(self.settings, gitlab_base_url=url)
                trust = self.capture(settings)
                with httpx.Client(trust_env=False, headers={"PRIVATE-TOKEN": "dummy-session"},
                                  **api_client_options(url, ca_bundle=self.custom, retained_trust=trust)) as client:
                    with self.assertRaises(httpx.HTTPError):
                        client.get(url + "/api/v4/user")
                self.assertEqual(seen, [])

    def test_ordinary_context_still_loads_current_selected_ca_path(self):
        with patch.object(t.certifi, "where", return_value=str(self.public)):
            first = verified_context(self.settings.gitlab_base_url, ca_bundle=self.custom)
            self.custom.write_bytes(self.other_pem)
            second = verified_context(self.settings.gitlab_base_url, ca_bundle=self.custom)
        self.assertNotEqual(first.get_ca_certs(binary_form=True), second.get_ca_certs(binary_form=True))
        self.assertEqual(first.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(second.check_hostname)


if __name__ == "__main__":
    unittest.main()
