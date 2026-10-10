"""Managed API consumers use retained trust across the private helper boundary."""
from __future__ import annotations

import asyncio
import base64
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from gitlab_agent import cli, doctor, gitlab_api, project_access, tls
from gitlab_agent.bridge_preview import controller as controller_module
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_child as child
from gitlab_agent.upgrade import service_children as children
from gitlab_agent.upgrade import service_trust as trust_module
from gitlab_agent.upgrade.service_configuration import capture_service_configuration

from test_service_child import _ChildFixture, ACTUAL, ACCESS
from tls_fixtures import LocalAuthority


class _APIFixture(_ChildFixture):
    def setUp(self):
        super().setUp()
        self.settings = replace(self.settings, api_ca_bundle=None)
        self.authority = None
        self.environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC"}
        }
        self.environment.update({"HOME": str(self.root), "USERPROFILE": str(self.root),
                                 "CODEX_HOME": str(self.root / "unused-codex")})

    def retained(self, settings=None):
        if self.authority is None:
            self.authority = LocalAuthority(self.root)
        with patch.object(trust_module.certifi, "where", return_value=str(self.authority.ca_path)):
            return trust_module.capture_api_trust(self.settings if settings is None else settings)

    def helper(self, *, settings=None, trust=None):
        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(Path, "cwd", return_value=self.root):
            return children.capture_helper_children(
                self.settings if settings is None else settings, api_trust=trust,
            )

    def bound(self, settings=None):
        selected = self.settings if settings is None else settings
        return self.helper(settings=selected, trust=self.retained(selected))

    def fail_without_client(self, call):
        with patch.object(gitlab_api.httpx, "Client") as client:
            with self.assertRaises(children.ServiceChildBindingError):
                call()
        client.assert_not_called()


class ManagedAPIConsumerTests(_APIFixture):
    def test_sync_json_text_and_trace_use_selected_tls_roots(self):
        self.retained()
        with self.authority.serve() as (endpoint, requests):
            settings = replace(self.settings, gitlab_base_url=endpoint)
            api = gitlab_api.GitLabAPI(settings, child_context=self.bound(settings))
            # Revalidation may observe selected paths; CA selection must not run
            # again or replace the retained bytes used by SSLContext.
            with patch.object(trust_module.certifi, "where", side_effect=AssertionError("fresh roots")), \
                    patch.object(tls, "ca_bundle_path", side_effect=AssertionError("fresh CA selection")):
                self.assertEqual(api.get_json("/user")["username"], "loopback-test")
                self.assertIn("loopback-test", api.get_text("/user"))
                self.assertEqual(api.job_trace_tail("owned/project", 1, tail_bytes=1000)["content"],
                                 "build completed\n")
            self.assertEqual(len(requests), 3)
            self.assertTrue(all(item["token"] == settings.api_token for item in requests))

    def test_bound_client_options_preserve_verification_and_redirect_policy(self):
        api = gitlab_api.GitLabAPI(self.settings, child_context=self.bound())
        with patch.object(gitlab_api.httpx, "Client") as client:
            api._client()
        options = client.call_args.kwargs
        self.assertIsInstance(options["verify"], ssl.SSLContext)
        self.assertEqual(options["verify"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options["verify"].check_hostname)
        self.assertFalse(options["follow_redirects"])
        self.assertFalse(options["trust_env"])
        self.assertEqual(options["headers"]["PRIVATE-TOKEN"], self.settings.api_token)

    def test_unused_trust_is_not_selected_by_api_construction(self):
        settings = replace(self.settings, api_ca_bundle=self.root / "unused-missing.pem")
        context = self.helper(settings=settings)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("selection")):
            api = gitlab_api.GitLabAPI(settings, child_context=context)
        self.assertIs(api.settings, settings)
        self.assertIsNone(context.api_trust)

    def test_missing_helper_trust_rejects_before_sync_client_and_never_selects(self):
        context = self.helper()
        api = gitlab_api.GitLabAPI(self.settings, child_context=context)
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("selection")):
            self.fail_without_client(lambda: api.get_json("/user"))
        with self.assertRaises(children.ServiceChildBindingError):
            context.revalidate()

    def test_missing_token_refuses_before_tls_without_poisoning_local_context(self):
        settings = replace(self.settings, api_token="", api_ca_bundle=self.root / "unused-missing.pem")
        context = self.helper(settings=settings)
        api = gitlab_api.GitLabAPI(settings, child_context=context)
        with patch.object(gitlab_api.httpx, "Client") as client, \
                patch.object(children.ServiceChildContext, "api_client_options",
                             side_effect=AssertionError("TLS should not be selected")):
            with self.assertRaisesRegex(RuntimeError, "GITLAB_TOKEN is required"):
                api.get_json("/user")
        client.assert_not_called()
        context.revalidate()

    def test_foreign_or_equal_but_distinct_settings_context_rejects(self):
        context = self.bound()
        for settings, foreign in ((self.settings, object()), (replace(self.settings), context)):
            self.fail_without_client(lambda: gitlab_api.GitLabAPI(settings, child_context=foreign))

    def test_changed_api_settings_are_sticky_and_never_fall_back(self):
        api = gitlab_api.GitLabAPI(self.settings, child_context=self.bound())
        api.settings = replace(self.settings)
        self.fail_without_client(lambda: api.get_json("/user"))
        api.settings = self.settings
        self.fail_without_client(lambda: api.get_json("/user"))

    def test_missing_or_substituted_api_context_is_sticky(self):
        for replacement in (None, object()):
            context = self.bound()
            api = gitlab_api.GitLabAPI(self.settings, child_context=context)
            api.child_context = replacement
            self.fail_without_client(lambda: api.get_json("/user"))
            api.child_context = context
            self.fail_without_client(lambda: api.get_json("/user"))

    def test_request_guard_revalidates_after_client_construction(self):
        self.retained()
        with self.authority.serve() as (endpoint, requests):
            settings = replace(self.settings, gitlab_base_url=endpoint)
            context = self.bound(settings)
            api = gitlab_api.GitLabAPI(settings, child_context=context)
            with api._client() as client:
                original = self.authority.ca_path.read_bytes()
                self.authority.ca_path.write_bytes(original + b"\n")
                with self.assertRaises(children.ServiceChildBindingError):
                    client.get(endpoint + "/api/v4/user")
                self.authority.ca_path.write_bytes(original)
                with self.assertRaises(children.ServiceChildBindingError):
                    client.get(endpoint + "/api/v4/user")
            self.assertEqual(requests, [])

    def test_async_project_factory_uses_bound_tls_and_pinned_file_preflight(self):
        context = self.bound()
        actual_client = httpx.AsyncClient
        observed, sent = [], []
        sha = "b" * 40

        def respond(request):
            sent.append(request)
            if "/repository/commits/" in request.url.path:
                body = {"id": sha}
            else:
                body = {"id": 1, "path_with_namespace": "owned/project", "default_branch": "main"}
            return httpx.Response(200, json=body)

        def factory(**options):
            observed.append(options)
            return actual_client(transport=httpx.MockTransport(respond), **options)

        output = io.StringIO()
        with patch.object(project_access.httpx, "AsyncClient", side_effect=factory), \
                patch.object(AgentSettings, "load", side_effect=AssertionError("ambient settings")), \
                patch.object(trust_module.certifi, "where", side_effect=AssertionError("fresh roots")), \
                redirect_stdout(output):
            result = project_access.main(["owned/project", "--ref", "main", "--require-file", "README.md"],
                                         resolved_settings=self.settings, child_context=context)
        report = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(report["resolved_commit_sha"], sha)
        self.assertTrue(report["required_files_complete"])
        self.assertEqual(len(observed), 1)
        self.assertIsInstance(observed[0]["verify"], ssl.SSLContext)
        self.assertEqual(sent[-1].method, "HEAD")
        self.assertEqual(sent[-1].url.params["ref"], sha)
        self.assertTrue(all(request.headers["PRIVATE-TOKEN"] == self.settings.api_token for request in sent))

    def test_async_missing_or_foreign_context_rejects_before_client(self):
        for context in (self.helper(), object()):
            with patch.object(project_access.httpx, "AsyncClient") as client, \
                    redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(children.ServiceChildBindingError):
                    project_access.main(["owned/project"], resolved_settings=self.settings, child_context=context)
            client.assert_not_called()
            self.assertEqual(output.getvalue(), "")

    def test_async_missing_token_and_local_denial_preserve_no_tls_behavior(self):
        for settings, project, expected in (
            (replace(self.settings, api_token=""), "owned/project", "credential_missing"),
            (self.settings, "other/project", "project_not_allowlisted"),
        ):
            context = self.helper(settings=settings)
            with patch.object(project_access.httpx, "AsyncClient") as client, \
                    patch.object(children.ServiceChildContext, "api_client_options",
                                 side_effect=AssertionError("TLS should not be selected")), \
                    redirect_stdout(io.StringIO()) as output:
                code = project_access.main([project], resolved_settings=settings, child_context=context)
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["error"]["code"], expected)
            client.assert_not_called()
            context.revalidate()

    def test_async_inner_and_outer_handlers_preserve_both_binding_error_types(self):
        failures = (children.ServiceChildBindingError("child_trust_failed"),
                    trust_module.ServiceAPITrustError("api_trust_changed"))
        for failure in failures:
            with self.assertRaises(type(failure)):
                asyncio.run(project_access.check_project_access(
                    "owned/project", allowed={"owned/project"}, token_present=True,
                    client_factory=Mock(side_effect=failure), base_url=self.settings.gitlab_base_url,
                ))
            with patch.object(project_access, "check_project_access", new=AsyncMock(side_effect=failure)), \
                    redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(type(failure)):
                    project_access.main(["owned/project"], resolved_settings=self.settings,
                                        child_context=self.helper())
            self.assertEqual(output.getvalue(), "")

    def test_both_cli_doctor_routes_and_central_api_factory_receive_exact_context(self):
        context = self.bound()
        calls = []

        def run_doctor(**options):
            selected = options["settings_loader"]()
            calls.append((selected, options["api_factory"](selected)))
            return {"ok": True}

        with patch.object(cli, "run_doctor", side_effect=run_doctor), \
                patch.object(cli, "WorkspaceManager"), patch.object(cli, "CommandRunner"), \
                patch.object(cli, "_prepare_start", return_value={"workspace": {}}), \
                patch.object(cli, "GitLabAPI", wraps=gitlab_api.GitLabAPI) as factory, \
                patch.object(AgentSettings, "load", side_effect=AssertionError("ambient loader")), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["doctor"], resolved_settings=self.settings, child_context=context), 0)
            self.assertEqual(cli.main(["start", "owned/project", "--goal", "fixture", "--no-launch"],
                                      resolved_settings=self.settings, child_context=context), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(factory.call_count, 3)
        for call in factory.call_args_list:
            self.assertIs(call.args[0], self.settings)
            self.assertEqual(call.kwargs, {"child_context": context})
        for settings, api in calls:
            self.assertIs(settings, self.settings)
            self.assertIs(api.child_context, context)

    def test_doctor_preserves_trust_and_child_binding_failures(self):
        failures = (children.ServiceChildBindingError("child_trust_failed"),
                    trust_module.ServiceAPITrustError("api_trust_changed"))
        for failure in failures:
            with patch.dict(os.environ, self.environment, clear=True), \
                    patch.object(doctor, "managed_app_server_socket", return_value=self.root / "absent.sock"):
                with self.assertRaises(type(failure)):
                    doctor.run_doctor(settings_loader=lambda: self.settings,
                                      which=lambda name: str(self.root / name),
                                      api_factory=Mock(side_effect=failure))
                with self.assertRaises(type(failure)):
                    doctor.run_doctor(settings_loader=Mock(side_effect=failure),
                                      which=lambda name: str(self.root / name))

    def test_cli_preserves_trust_failures_without_printing_private_diagnostics(self):
        failure = trust_module.ServiceAPITrustError("api_trust_changed")
        with patch.object(cli, "WorkspaceManager", side_effect=failure), \
                redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(trust_module.ServiceAPITrustError):
                cli.main(["status", "id"], resolved_settings=self.settings, child_context=self.helper())
        self.assertEqual(output.getvalue(), "")

    def test_ordinary_api_keeps_path_based_options_and_constructor(self):
        marker = {"verify": object()}
        with patch.object(gitlab_api, "api_client_options", return_value=marker) as options, \
                patch.object(gitlab_api.httpx, "Client") as client:
            gitlab_api.GitLabAPI(self.settings)._client()
        options.assert_called_once_with(self.settings.gitlab_base_url,
                                        verify_ssl=True, ca_bundle=None)
        self.assertIs(client.call_args.kwargs["verify"], marker["verify"])


class ManagedAPICapabilityTests(_APIFixture):
    def capability(self, argv, *, module=ACTUAL, settings=None):
        return child.helper_requires_api_trust(
            self.settings if settings is None else settings, module, argv,
        )

    def test_purely_local_commands_do_not_select_trust(self):
        for argv in (["project-config", "owned/project"], ["status", "id"],
                     ["files", "id", "."], ["read", "id", "README.md"], ["diff", "id"],
                     ["evidence", "id"], ["resume", "id"], ["finish", "id", "--dry-r"]):
            with self.subTest(command=argv[0]):
                self.assertFalse(self.capability(argv))

    def test_online_and_ci_api_commands_select_trust(self):
        for argv in (["doctor"], ["start", "owned/project", "--goal", "fixture", "--no-launch"],
                     ["ci", "id"], ["evidence", "id", "--from-ci"], ["resume", "id", "--from-ci"]):
            with self.subTest(command=argv[0]):
                self.assertTrue(self.capability(argv))
        self.assertTrue(self.capability(["owned/project"], module=ACCESS))

    def test_actual_offline_and_git_only_argument_abbreviations_are_respected(self):
        for argv in (["doctor", "--off"], ["doctor", "--git-on"],
                     ["start", "owned/project", "--goal", "fixture", "--no-l", "--offline-d"],
                     ["start", "owned/project", "--goal", "fixture", "--no-l", "--git-on"]):
            self.assertFalse(self.capability(argv))
        self.assertTrue(self.capability(["start", "owned/project", "--goal=--offline", "--no-launch"]))
        self.assertTrue(self.capability(["evidence", "id", "--from-c"]))
        self.assertTrue(self.capability(["resume", "id", "--from-c"]))

    def test_missing_api_token_skips_material_selection_for_every_capability(self):
        settings = replace(self.settings, api_token="", api_ca_bundle=self.root / "missing.pem")
        for module, argv in ((ACCESS, ["owned/project"]), (ACTUAL, ["doctor"]),
                             (ACTUAL, ["start", "owned/project", "--goal", "fixture", "--no-launch"]),
                             (ACTUAL, ["ci", "id"]), (ACTUAL, ["finish", "id", "--dry-run"])):
            self.assertFalse(self.capability(argv, module=module, settings=settings))

    def test_capability_selection_cannot_bypass_existing_command_restrictions(self):
        settings = replace(self.settings, api_token="")
        for argv in (["resume", "id", "--la"], ["start", "owned/project", "--goal", "fixture"],
                     ["finish", "id"], ["project-config", "owned/project", "--fi", "private-file"],
                     ["push", "id"], ["status", "id", "--unknown"]):
            self.assert_code("child_command_rejected", self.capability, argv, settings=settings)


class ManagedAPIWireTests(_APIFixture):
    def trust_request(self, *, argv=None):
        return child.encode_child_request(self.settings, ACTUAL, argv or ["status", "id"],
                                          api_trust=self.retained())

    def test_v2_material_is_bound_to_exact_decoded_settings_without_new_selection(self):
        raw = self.trust_request()
        with patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("new capture")), \
                patch.object(trust_module.certifi, "where", side_effect=AssertionError("new selection")):
            decoded = child._decode_request(raw)
        self.assertEqual(json.loads(raw)["protocol"], "reasonfirst-managed-child-request-v2")
        self.assertIs(type(decoded.api_trust), trust_module.ServiceAPITrust)
        self.assertIs(decoded.api_trust.settings, decoded.settings)
        self.assertIsNot(decoded.settings, self.settings)
        self.assertNotIn(self.settings.api_token, repr(decoded))

    def test_encoder_rejects_foreign_or_equal_settings_trust(self):
        trust = self.retained()
        self.assert_code("child_context_failed", child.encode_child_request,
                         replace(self.settings), ACTUAL, ["status", "id"], api_trust=trust)
        self.assert_code("child_context_failed", child.encode_child_request,
                         self.settings, ACTUAL, ["status", "id"], api_trust=object())

    def test_request_carries_128_bit_file_identity_without_widening_settings_or_response(self):
        value = json.loads(self.trust_request())
        bundle = value["api_trust"]["public"]
        bundle["identity"][1] = 2**127 + 17
        observed = trust_module._FileObservation(
            Path(bundle["resolved_path"]), tuple(bundle["identity"]), bundle["sha256"],
        )
        with patch.object(trust_module, "_observe_file", return_value=observed):
            decoded = child._decode_request(json.dumps(value).encode())
        self.assertIs(decoded.api_trust.settings, decoded.settings)
        value["settings"]["command_timeout_seconds"] = 2**127
        self.assert_code("child_invalid_settings", child._decode_request, json.dumps(value).encode())
        response = {"protocol": child.RESPONSE_PROTOCOL, "exit_code": 0,
                    "result": {"unbounded": 2**127}, "error": None}
        self.assert_code("child_invalid_response", child.decode_child_response,
                         json.dumps(response).encode(), 0)

    def test_null_payload_does_not_allow_helper_tls_fallback(self):
        raw = child.encode_child_request(self.settings, ACTUAL, ["doctor"])
        self.assertIsNone(child._decode_request(raw).api_trust)

        def doctor_call(**options):
            options["api_factory"](options["settings_loader"]()).get_json("/user")
            return {"ok": True}

        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(cli, "run_doctor", side_effect=doctor_call), \
                patch.object(gitlab_api.httpx, "Client") as client, \
                patch.object(trust_module, "capture_api_trust", side_effect=AssertionError("fallback")):
            response, code = child._run_request(raw)
        self.assert_code("child_context_failed", child.decode_child_response, response, code)
        client.assert_not_called()

    def test_transferred_material_reaches_real_helper_api_factory(self):
        self.retained()
        with self.authority.serve() as (endpoint, requests):
            self.settings = replace(self.settings, gitlab_base_url=endpoint)
            raw = self.trust_request(argv=["doctor"])

            def doctor_call(**options):
                data = options["api_factory"](options["settings_loader"]()).get_json("/user")
                return {"ok": True, "summary": {"pass": 1}, "checks": [
                    {"name": "gitlab_api_connectivity", "status": "pass", "message": data["username"]},
                ]}

            with patch.dict(os.environ, self.environment, clear=True), \
                    patch.object(cli, "run_doctor", side_effect=doctor_call), \
                    patch.object(trust_module.certifi, "where", side_effect=AssertionError("new roots")):
                response, code = child._run_request(raw)
            result = child.decode_child_response(response, code)
            self.assertTrue(result["ok"])
            self.assertEqual(result["checks"][0]["message"], "loopback-test")
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["token"], self.settings.api_token)
            self.assertNotIn(self.settings.api_token.encode(), response)
            self.assertNotIn(b"BEGIN CERTIFICATE", response)

    def test_changed_transferred_file_rejects_before_dispatch(self):
        raw = self.trust_request()
        self.authority.ca_path.write_bytes(self.authority.ca_path.read_bytes() + b"\n")
        with patch.object(cli, "main") as dispatch, patch.object(children, "capture_helper_children") as capture:
            response, code = child._run_request(raw)
        self.assert_code("child_context_failed", child.decode_child_response, response, code)
        dispatch.assert_not_called()
        capture.assert_not_called()

    def test_helper_post_command_revalidation_catches_swallowed_drift(self):
        raw = self.trust_request()

        def swallowed(argv, *, resolved_settings, child_context, **kwargs):
            self.authority.ca_path.write_bytes(self.authority.ca_path.read_bytes() + b"\n")
            try:
                child_context.api_client_options(resolved_settings)
            except children.ServiceChildBindingError:
                pass
            print('{"workspace_id":"id"}')
            return 0

        with patch.dict(os.environ, self.environment, clear=True), patch.object(cli, "main", side_effect=swallowed):
            response, code = child._run_request(raw)
        self.assert_code("child_context_failed", child.decode_child_response, response, code)

    def test_base_trust_and_aggregate_wire_budgets_are_independent(self):
        raw = self.trust_request()
        request = json.loads(raw)
        base_size = len(child._encode({key: value for key, value in request.items() if key != "api_trust"},
                                      maximum=child.MAX_REQUEST_BYTES, code="child_request_limit"))
        trust_size = len(child._encode(request["api_trust"], maximum=child.MAX_REQUEST_BYTES,
                                       code="child_request_limit"))
        for constant, size in (("MAX_BASE_REQUEST_BYTES", base_size),
                               ("MAX_TRUST_PAYLOAD_BYTES", trust_size), ("MAX_REQUEST_BYTES", len(raw))):
            with self.subTest(limit=constant), patch.object(child, constant, size):
                self.assertEqual(child._decode_request(raw).argv, ("status", "id"))
                self.assertEqual(self.trust_request(), raw)
            with self.subTest(limit=constant + "-1"), patch.object(child, constant, size - 1):
                self.assert_code("child_request_limit", child._decode_request, raw)
                self.assert_code("child_request_limit", self.trust_request)

    def test_wire_rejects_unknown_trust_version_shapes_duplicates_and_private_keys(self):
        raw = self.trust_request()
        for changed in ([], "private-value", {}, {**json.loads(raw)["api_trust"], "schema_version": 2}):
            value = json.loads(raw)
            value["api_trust"] = changed
            self.assert_code("child_context_failed", child._decode_request, json.dumps(value).encode())
        self.assert_code("child_invalid_request", child._decode_request,
                         raw.replace(b'"api_trust":', b'"api_trust":null,"api_trust":', 1))
        value = json.loads(raw)
        bundle = value["api_trust"]["public"]
        pem = base64.b64decode(bundle["pem_base64"]) + b"\n-----BEGIN " + b"PRIVATE KEY-----\n"
        bundle["pem_base64"] = base64.b64encode(pem).decode("ascii")
        bundle["sha256"] = hashlib.sha256(pem).hexdigest()
        bundle["identity"][3] = len(pem)
        error = self.assert_code("child_context_failed", child._decode_request, json.dumps(value).encode())
        self.assertNotIn(str(self.root), str(error))
        self.assertNotIn("PRIVATE KEY", str(error))


class ManagedAPIParentCompletionTests(_APIFixture):
    def assert_completion_drift_closes_before_decode(self, stdout, returncode):
        self.retained()
        configuration = capture_service_configuration(
            self.settings,
            {"version": 4,
             "defaults": {"target": "local", "codex_backend": "global-config-local"},
             "targets": {"local": {"type": "local", "codex_backend": "global-config-local"}}},
            bridge_config_path=self.root / "bridge.yaml", state_dir=self.root / "bridge-state",
        )
        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(Path, "cwd", return_value=self.root):
            context = children.capture_service_children(configuration)
        with patch.object(trust_module.certifi, "where", return_value=str(self.authority.ca_path)):
            context.select_api_trust(configuration.settings)
        admission = ControllerAdmission()
        controller = controller_module.BridgeController(
            admission=admission, service_configuration=configuration, child_context=context,
        )
        self.addCleanup(controller.close)
        command = controller._module_command(ACTUAL, "status", "id")
        original = self.authority.ca_path.read_bytes()

        def completed_after_drift(argv, **kwargs):
            self.assertIsNotNone(json.loads(kwargs["input"])["api_trust"])
            self.authority.ca_path.write_bytes(original + b"\n")
            return subprocess.CompletedProcess(argv, returncode, stdout, b"")

        with patch.object(controller_module.subprocess, "run", side_effect=completed_after_drift) as runner, \
                patch.object(child, "decode_child_response", wraps=child.decode_child_response) as decode:
            with self.assertRaisesRegex(controller_module.BridgeError, "^child_binding_failed$"):
                controller._run_json(command, allow_failure_json=True)
            self.assertFalse(admission.snapshot()["admission_open"])
            decode.assert_not_called()
            self.authority.ca_path.write_bytes(original)
            with self.assertRaisesRegex(controller_module.BridgeError, "^child_binding_failed$"):
                controller._run_json(command, allow_failure_json=True)
            runner.assert_called_once()
            decode.assert_not_called()
            self.assertFalse(admission.snapshot()["admission_open"])

    def test_malformed_helper_response_cannot_hide_parent_trust_drift(self):
        self.assert_completion_drift_closes_before_decode(b"malformed helper output", 0)

    def test_ordinary_child_error_cannot_hide_parent_trust_drift(self):
        response = json.dumps({"protocol": child.RESPONSE_PROTOCOL, "exit_code": 1,
                               "result": None, "error": "child_command_failed"}).encode()
        self.assert_completion_drift_closes_before_decode(response, 1)


if __name__ == "__main__":
    unittest.main()
