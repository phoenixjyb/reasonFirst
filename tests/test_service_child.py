from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import fields, replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gitlab_agent import cli, config, project_access
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade import service_child as child
from gitlab_agent.upgrade import service_children as children


ACTUAL = "gitlab_agent.actual_coder_cli"
ACCESS = "gitlab_agent.project_access"


class _ChildFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rf-child-codec-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.settings = AgentSettings(
            config_file=self.root / "absent.env", gitlab_base_url="https://gitlab.example.invalid",
            api_token="synthetic-private-api-value", api_verify_ssl=True, api_trust_env=False,
            git_token="synthetic-private-git-value", git_username="synthetic-private-user",
            git_trust_env=False, allowed_projects={"owned/project"}, require_write_allowlist=True,
            workspace_root=self.root / "workspaces", branch_prefix="chatgpt/", default_base_ref="main",
            allowed_executables={"python", "pytest"}, command_timeout_seconds=90,
            max_output_bytes=120000, max_file_bytes=1000000,
            git_author_name="synthetic-private-author", git_author_email="synthetic-private-email",
            api_ca_bundle=self.root / "unread-ca.pem", copilot_model="fixture-model",
            copilot_reasoning_effort="high", copilot_allow_tools=("read", "shell(git status)"),
        )

    def request(self, argv=None, *, settings=None, module=ACTUAL):
        return child.encode_child_request(
            self.settings if settings is None else settings, module,
            ["status", "abcdef123456"] if argv is None else argv,
        )

    def changed_request(self, change):
        value = json.loads(self.request())
        change(value)
        return json.dumps(value).encode("utf-8")

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(child.ServiceChildError) as failure:
            function(*args, **kwargs)
        self.assertEqual(failure.exception.code, code)
        self.assertEqual(str(failure.exception), code)
        return failure.exception

    def invoke(self, *, argv=None, module=ACTUAL, settings=None, result=None, code=0, effect=None, raw=None):
        request = self.request(argv, settings=settings, module=module) if raw is None else raw
        self.context = SimpleNamespace(revalidate=Mock())
        self.calls = []

        def execute(arguments, **kwargs):
            self.calls.append((arguments, kwargs))
            if effect is not None:
                return effect(arguments, **kwargs)
            print(json.dumps({"workspace_id": "abcdef123456"} if result is None else result))
            return code

        with (patch.object(children, "capture_helper_children", return_value=self.context) as capture,
              patch.object(cli, "main", side_effect=execute),
              patch.object(project_access, "main", side_effect=execute)):
            response, returned = child._run_request(request)
        self.capture_calls = capture.call_args_list
        return response, returned


class ServiceChildTests(_ChildFixture):
    def test_private_codec_preserves_every_resolved_field_without_files_or_environment(self):
        settings = replace(self.settings, git_author_name="", git_author_email=None,
                           api_token="", copilot_model=None, copilot_reasoning_effort=None,
                           copilot_allow_tools=("read,with,commas", " read ", "read,with,commas"))
        with (patch.object(AgentSettings, "load", side_effect=AssertionError("ambient loader")),
              patch.object(config, "load_env_file", side_effect=AssertionError("ambient file")),
              patch.object(config, "resolve_env_file", side_effect=AssertionError("ambient path")),
              patch.object(os, "getenv", side_effect=AssertionError("ambient environment")),
              patch.object(Path, "open", side_effect=AssertionError("file access"))):
            raw = self.request(settings=settings)
            decoded = child._decode_request(raw)
        self.assertEqual(set(json.loads(raw)["settings"]), {field.name for field in fields(AgentSettings)})
        for field in fields(AgentSettings):
            self.assertEqual(getattr(decoded.settings, field.name), getattr(settings, field.name), field.name)
        self.assertIs(type(decoded.settings.allowed_projects), frozenset)
        self.assertIs(type(decoded.settings.copilot_allow_tools), tuple)
        self.assertNotIn(self.settings.git_token, repr(decoded))

    def test_request_detaches_mutable_settings_collections_and_arguments(self):
        commands, tools = {"python"}, ["read"]
        settings = replace(self.settings, allowed_executables=commands, copilot_allow_tools=tools)
        argv = ["files", "abcdef123456", "."]
        raw = self.request(argv, settings=settings)
        commands.add("unexpected")
        tools.append("write")
        argv[:] = ["push", "abcdef123456"]
        decoded = child._decode_request(raw)
        self.assertEqual(decoded.argv, ("files", "abcdef123456", "."))
        self.assertEqual(decoded.settings.allowed_executables, {"python"})
        self.assertEqual(decoded.settings.copilot_allow_tools, ("read",))

    def test_request_rejects_duplicate_keys_and_nonfinite_json(self):
        raw = self.request()
        duplicate_top = raw.replace(b'"protocol":', b'"protocol":"duplicate", "protocol":', 1)
        duplicate_setting = raw.replace(b'"api_token":', b'"api_token":"duplicate", "api_token":', 1)
        nonfinite = self.changed_request(lambda value: value["settings"].update(command_timeout_seconds=float("nan")))
        for index, value in enumerate((duplicate_top, duplicate_setting, nonfinite)):
            with self.subTest(case=index):
                self.assert_code("child_invalid_request", child._decode_request, value)

    def test_request_protocol_shape_and_byte_limits_are_strict(self):
        malformed = [b"", b"\xff", b"{}", b"[]", b"null", b"{} {}"]
        for raw in malformed:
            self.assert_code("child_invalid_request", child._decode_request, raw)
        self.assert_code("child_invalid_request", child._decode_request, bytearray(self.request()))
        self.assert_code("child_request_limit", child._decode_request, b"x" * (child.MAX_REQUEST_BYTES + 1))
        for change in (
            lambda value: value.update(protocol="reasonfirst-selected-service-policy-v1"),
            lambda value: value.update(protocol="reasonfirst-managed-child-request-v1"),
            lambda value: value.update(extra="private"),
            lambda value: value.pop("settings"),
            lambda value: value.pop("api_trust"),
        ):
            self.assert_code("child_invalid_request", child._decode_request, self.changed_request(change))

    def test_settings_schema_scalars_paths_and_collections_fail_closed(self):
        changes = [
            ("api_verify_ssl", 1), ("command_timeout_seconds", True),
            ("command_timeout_seconds", 0), ("max_output_bytes", 2**31),
            ("api_token", {"secret": "synthetic-private-api-value"}),
            ("git_token", "x" * (16 * 1024 + 1)), ("git_author_email", 1),
            ("workspace_root", "relative/path"), ("config_file", None),
            ("api_ca_bundle", 1), ("allowed_projects", "owned/project"),
            ("allowed_projects", ["owned/project", "owned/project"]),
            ("allowed_executables", ["python", True]),
            ("copilot_allow_tools", ["read"] * 513),
            ("codex_execution_mode", "unexpected"),
        ]
        for name, value in changes:
            raw = self.changed_request(lambda request: request["settings"].update({name: value}))
            with self.subTest(field=name):
                self.assert_code("child_invalid_settings", child._decode_request, raw)
        for change in (lambda value: value["settings"].pop("api_token"),
                       lambda value: value["settings"].update(unknown="private")):
            self.assert_code("child_invalid_settings", child._decode_request, self.changed_request(change))
        self.assert_code("child_invalid_settings", child.encode_child_request, object(), ACTUAL, ["status", "id"])

    def test_private_request_limit_counts_utf8_bytes(self):
        settings = replace(self.settings, allowed_projects={str(index) + "界" * 4000 for index in range(100)})
        self.assert_code("child_request_limit", child.encode_child_request, settings, ACTUAL, ["status", "id"])
        self.assert_code("child_request_limit", self.request, ["resume", "id", "--goal", "界" * 30000])

    def test_real_parser_allows_only_nonlaunch_helper_semantics(self):
        accepted = [
            ["doctor", "--offline"], ["project-config", "owned/project", "--validate"],
            ["start", "owned/project", "--goal", "fixture", "--no-launch"],
            ["start", "owned/project", "--goal", "fixture", "--no-l"],
            ["status", "id"], ["files", "id", ".", "--recursive"],
            ["read", "id", "notes.txt"], ["diff", "id"], ["resume", "id"],
            ["ci", "id"], ["evidence", "id"], ["finish", "id", "--dry-r"],
        ]
        for argv in accepted:
            with self.subTest(command=argv[0]):
                self.assertEqual(child._decode_request(self.request(argv)).argv, tuple(argv))
        self.assertEqual(child._decode_request(self.request(["owned/project", "--ref", "main"], module=ACCESS)).module, ACCESS)

    def test_launch_abbreviations_and_file_inputs_are_rejected_before_context_or_dispatch(self):
        rejected = [
            ["start", "owned/project", "--goal", "fixture"],
            ["resume", "id", "--launch"], ["resume", "id", "--la"],
            ["continue", "id", "--no-launch"], ["finish", "id"],
            ["finish", "id", "--dry-run", "--description-f", "-"],
            ["project-config", "owned/project", "--fi", "/private/file"],
            ["config"], ["push", "id"], ["run", "id", "python"],
            ["--help"], ["read", "id"], ["status", "id", "--unexpected"],
        ]
        for argv in rejected:
            raw = self.changed_request(lambda value: value.update(argv=argv))
            with self.subTest(command=argv[0]):
                response, code = self.invoke(raw=raw)
                self.assert_code("child_command_rejected", child.decode_child_response, response, code)
                self.assertFalse(self.capture_calls)
                self.assertFalse(self.calls)

    def test_modules_and_argv_types_cannot_invoke_other_code(self):
        for module, argv in [("os", ["system"]), (ACTUAL, "status"), (ACTUAL, []),
                             (ACTUAL, ["status", 1]), (ACTUAL, ["status", "bad\x00id"]),
                             (ACTUAL, ["status"] * 257)]:
            raw = self.changed_request(lambda value: value.update(module=module, argv=argv))
            self.assert_code("child_command_rejected", child._decode_request, raw)

    def test_entrypoint_delivers_decoded_settings_and_exact_helper_context(self):
        for module, argv in [(ACTUAL, ["status", "abcdef123456"]), (ACCESS, ["owned/project"])]:
            with self.subTest(module=module):
                raw, code = self.invoke(module=module, argv=argv)
                data = child.decode_child_response(raw, code)
                self.assertEqual(data, {"workspace_id": "abcdef123456", "_returncode": 0})
                arguments, kwargs = self.calls[0]
                self.assertEqual(arguments, argv)
                self.assertEqual(kwargs["resolved_settings"], self.settings)
                self.assertIs(kwargs["child_context"], self.context)
                self.assertIs(self.capture_calls[0].args[0], kwargs["resolved_settings"])
                self.assertEqual(self.capture_calls[0].kwargs, {"api_trust": None})
                self.context.revalidate.assert_called_once_with()
                self.assertNotIn(self.settings.api_token.encode(), raw)

    def test_short_username_and_author_values_preserve_successful_result_schema(self):
        settings = replace(self.settings, git_username="git", git_author_name="a", git_author_email="a")
        fixtures = [
            (["status", "id"], {"workspace_id": "abcdef123456", "worktree_path": "/workspace/git", "changed_paths": ["a.py"]}),
            (["files", "id", "."], {"workspace_id": "abcdef123456", "entries": [{"path": "a.py", "kind": "file"}]}),
            (["finish", "id", "--dry-run"], {"dry_run": True, "ok": True, "snapshot": {"digest": "a" * 64}, "plan": {"commit_message": "a git change"}}),
        ]
        for argv, result in fixtures:
            raw, code = self.invoke(settings=settings, argv=argv, result=result)
            self.assertEqual(child.decode_child_response(raw, code), {**result, "_returncode": 0})

    def test_private_credentials_in_result_values_or_keys_fail_without_reflection(self):
        for result in ({"content": "prefix " + self.settings.api_token},
                       {self.settings.git_token: "value"},
                       {"nested": [{"content": self.settings.git_token}]}):
            raw, code = self.invoke(result=result)
            self.assert_code("child_command_failed", child.decode_child_response, raw, code)
            self.assertNotIn(self.settings.api_token.encode(), raw)
            self.assertNotIn(self.settings.git_token.encode(), raw)

    def test_unstructured_and_reflective_cli_errors_are_non_reflective(self):
        def exception(*args, **kwargs):
            raise RuntimeError(self.settings.api_token)

        def raw_text(*args, **kwargs):
            print(self.settings.git_token)
            return 1

        for effect, result in [(exception, None), (raw_text, None),
                               (None, {"ok": False, "error": self.settings.api_token, "type": "RuntimeError"})]:
            raw, code = self.invoke(effect=effect, result=result, code=1)
            with self.assertRaises(child.ServiceChildError):
                child.decode_child_response(raw, code)
            self.assertNotIn(self.settings.api_token.encode(), raw)
            self.assertNotIn(self.settings.git_token.encode(), raw)

    def test_doctor_and_start_preflight_keep_structured_failures_without_exception_text(self):
        doctor = {"ok": False, "overall": "fail", "offline": True,
                  "summary": {"pass": 1, "fail": 1, "warn": 0, "skip": 0},
                  "checks": [{"name": "python", "status": "pass", "message": "available"},
                             {"name": "gitlab_api_connectivity", "status": "fail",
                              "message": "unexpected " + self.settings.api_token,
                              "details": {"private": self.settings.git_token}}]}
        for argv, result in [(["doctor", "--offline"], doctor),
                             (["start", "owned/project", "--goal", "fixture", "--no-launch"],
                              {"ok": False, "stage": "doctor", "preflight": doctor})]:
            raw, code = self.invoke(argv=argv, result=result, code=1)
            data = child.decode_child_response(raw, code)
            report = data["preflight"] if "preflight" in data else data
            self.assertFalse(report["ok"])
            self.assertEqual(report["checks"][0], doctor["checks"][0])
            self.assertEqual(report["checks"][1], {"name": "gitlab_api_connectivity", "status": "fail",
                                                    "message": "Managed child diagnostic did not pass."})
            self.assertEqual(data["_returncode"], 1)
            self.assertNotIn(self.settings.api_token.encode(), raw)

    def test_project_access_reconstructs_finite_errors_instead_of_reflecting_messages(self):
        result = project_access.ProjectAccessError("credential_missing", stage="credential").result()
        result["error"]["message"] = self.settings.api_token
        result["error"]["next_steps"] = [self.settings.git_token]
        raw, code = self.invoke(module=ACCESS, argv=["owned/project"], result=result, code=1)
        data = child.decode_child_response(raw, code)
        self.assertEqual(data["error"], project_access.ProjectAccessError("credential_missing", stage="credential").result()["error"])
        self.assertNotIn(self.settings.api_token.encode(), raw)

    def test_project_contract_and_finish_plan_can_return_structured_blockers(self):
        fixtures = [(["project-config", "owned/project", "--validate"],
                     {"valid": False, "errors": ["validation executable is not allowed"], "effective": {}}),
                    (["finish", "id", "--dry-run"],
                     {"ok": False, "dry_run": True, "snapshot": {"digest": "a" * 64}, "blockers": ["validation failed"]})]
        for argv, result in fixtures:
            raw, code = self.invoke(argv=argv, result=result, code=1)
            self.assertEqual(child.decode_child_response(raw, code), {**result, "_returncode": 1})

    def test_stdout_and_stderr_limits_latch_even_if_cli_catches_write_failure(self):
        for target in ("stdout", "stderr"):
            def noisy(*args, **kwargs):
                try:
                    getattr(sys, target).write("界" * 400)
                except child.ServiceChildError:
                    pass
                print('{"workspace_id":"abcdef123456"}')
                return 0

            with patch.object(child, "MAX_RESPONSE_BYTES", 1024), patch.object(child, "MAX_STDERR_BYTES", 1024):
                raw, code = self.invoke(effect=noisy)
                self.assertLessEqual(len(raw) + 1, child.MAX_RESPONSE_BYTES)
                self.assert_code("child_output_limit", child.decode_child_response, raw, code)

    def test_unexpected_stderr_is_captured_and_never_returned(self):
        def noisy(*args, **kwargs):
            print(self.settings.git_token, file=sys.stderr)
            print('{"workspace_id":"abcdef123456"}')
            return 0

        real_stderr = io.StringIO()
        with patch.object(sys, "stderr", real_stderr):
            raw, code = self.invoke(effect=noisy)
        self.assertEqual(real_stderr.getvalue(), "")
        self.assertEqual(child.decode_child_response(raw, code)["_returncode"], 0)
        self.assertNotIn(self.settings.git_token.encode(), raw)

    def test_binding_failures_remain_distinct_from_command_failures(self):
        def drift(*args, **kwargs):
            raise children.ServiceChildBindingError("child_wrong_process")

        raw, code = self.invoke(effect=drift)
        self.assert_code("child_context_failed", child.decode_child_response, raw, code)
        with patch.object(children, "capture_helper_children", side_effect=RuntimeError(self.settings.api_token)):
            raw, code = child._run_request(self.request())
        self.assert_code("child_context_failed", child.decode_child_response, raw, code)
        self.assertNotIn(self.settings.api_token.encode(), raw)

    def test_response_protocol_status_and_shapes_are_strict(self):
        response = {"protocol": child.RESPONSE_PROTOCOL, "exit_code": 0,
                    "result": {"workspace_id": "id", "_returncode": 99}, "error": None}
        raw = json.dumps(response).encode()
        self.assertEqual(child.decode_child_response(raw, 0)["_returncode"], 0)
        malformed = [b"", b"\xff", b"{}", raw + raw, b"x" * (child.MAX_RESPONSE_BYTES + 1)]
        for value in malformed:
            self.assert_code("child_invalid_response", child.decode_child_response, value, 0)
        for status in (True, -1, 1, 256):
            self.assert_code("child_invalid_response", child.decode_child_response, raw, status)
        for change in ({"protocol": child.REQUEST_PROTOCOL}, {"exit_code": False}, {"result": []},
                       {"extra": "private"}, {"error": "unknown"}):
            value = {**response, **change}
            self.assert_code("child_invalid_response", child.decode_child_response, json.dumps(value).encode(), 0)
        duplicate = raw.replace(b'"exit_code":', b'"exit_code":0,"exit_code":', 1)
        self.assert_code("child_invalid_response", child.decode_child_response, duplicate, 0)
        self.assert_code("child_invalid_response", child.decode_child_response,
                         raw.replace(b'"_returncode": 99', b'"_returncode": NaN'), 0)

    def test_main_reads_only_the_bounded_private_input_and_emits_one_envelope(self):
        incoming = io.BytesIO(b"x" * (child.MAX_REQUEST_BYTES + 20))
        outgoing = io.BytesIO()
        with (patch.object(sys, "stdin", SimpleNamespace(buffer=incoming)),
              patch.object(sys, "stdout", SimpleNamespace(buffer=outgoing)),
              patch.object(children, "capture_helper_children") as capture):
            code = child.main()
        self.assertEqual(incoming.tell(), child.MAX_REQUEST_BYTES + 1)
        capture.assert_not_called()
        self.assert_code("child_request_limit", child.decode_child_response, outgoing.getvalue(), code)

    def test_error_objects_have_only_finite_non_reflective_messages(self):
        for code in (self.settings.api_token, None, 12, {}):
            error = child.ServiceChildError(code)
            self.assertEqual(error.code, "child_invalid_response")
            self.assertNotIn(self.settings.api_token, repr(error))


class ResolvedCLIInjectionTests(_ChildFixture):
    def test_cli_resolved_settings_are_used_by_all_factories_without_loading(self):
        manager = Mock()
        manager.status.return_value = {"workspace_id": "abcdef123456"}
        context = object()
        with (patch.object(AgentSettings, "load", side_effect=AssertionError("ambient loader")),
              patch.object(cli, "WorkspaceManager", return_value=manager) as workspace_factory,
              patch.object(cli, "CommandRunner") as runner_factory,
              patch.object(cli, "GitLabAPI") as api_factory,
              redirect_stdout(io.StringIO())):
            code = cli.main(["status", "abcdef123456"], resolved_settings=self.settings, child_context=context)
        self.assertEqual(code, 0)
        workspace_factory.assert_called_once_with(self.settings, child_context=context)
        runner_factory.assert_called_once_with(self.settings, manager)
        api_factory.assert_called_once_with(self.settings, child_context=context)

    def test_both_managed_doctor_paths_receive_the_exact_settings_loader(self):
        observed = []
        def doctor(**kwargs):
            observed.append(kwargs["settings_loader"]())
            return {"ok": True}

        with (patch.object(AgentSettings, "load", side_effect=AssertionError("ambient loader")),
              patch.object(cli, "run_doctor", side_effect=doctor),
              patch.object(cli, "WorkspaceManager"), patch.object(cli, "CommandRunner"),
              patch.object(cli, "GitLabAPI"), patch.object(cli, "_prepare_start", return_value={"workspace": {}}),
              redirect_stdout(io.StringIO())):
            self.assertEqual(cli.main(["doctor", "--offline"], resolved_settings=self.settings), 0)
            self.assertEqual(cli.main(["start", "owned/project", "--goal", "fixture", "--no-launch"],
                                      resolved_settings=self.settings), 0)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(value is self.settings for value in observed))

    def test_ordinary_cli_factories_and_doctor_calls_keep_their_existing_arguments(self):
        with (patch.object(AgentSettings, "load", return_value=self.settings) as load,
              patch.object(cli, "WorkspaceManager") as manager,
              patch.object(cli, "CommandRunner"), patch.object(cli, "GitLabAPI"),
              patch.object(cli, "run_doctor", return_value={"ok": True}) as doctor,
              patch.object(cli, "_prepare_start", return_value={"workspace": {}}),
              redirect_stdout(io.StringIO())):
            self.assertEqual(cli.main(["doctor", "--offline"]), 0)
            load.assert_not_called()
            doctor.assert_called_once_with(offline=True, git_only=False)
            doctor.reset_mock()
            self.assertEqual(cli.main(["start", "owned/project", "--goal", "fixture", "--no-launch"]), 0)
            load.assert_called_once_with()
            manager.assert_called_once_with(self.settings)
            doctor.assert_called_once_with(offline=False, git_only=False)

    def test_project_access_injection_denies_before_client_without_loading(self):
        output = io.StringIO()
        with (patch.object(AgentSettings, "load", side_effect=AssertionError("ambient loader")),
              patch.object(project_access.httpx, "AsyncClient", side_effect=AssertionError("network client")) as client,
              redirect_stdout(output)):
            code = project_access.main(["other/project"], resolved_settings=self.settings)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"]["code"], "project_not_allowlisted")
        client.assert_not_called()

    def test_ordinary_project_access_keeps_its_loader(self):
        with (patch.object(AgentSettings, "load", return_value=self.settings) as load,
              patch.object(project_access.httpx, "AsyncClient", side_effect=AssertionError("network client")),
              redirect_stdout(io.StringIO())):
            self.assertEqual(project_access.main(["other/project"]), 1)
        load.assert_called_once_with()

    def test_managed_cli_and_project_access_do_not_swallow_binding_drift(self):
        failure = children.ServiceChildBindingError("child_wrong_process")
        with (patch.object(cli, "WorkspaceManager", side_effect=failure),
              redirect_stdout(io.StringIO()) as output):
            with self.assertRaises(children.ServiceChildBindingError):
                cli.main(["status", "id"], resolved_settings=self.settings, child_context=object())
        self.assertEqual(output.getvalue(), "")
        with (patch.object(project_access, "check_project_access", new=AsyncMock(side_effect=failure)),
              redirect_stdout(io.StringIO()) as output):
            with self.assertRaises(children.ServiceChildBindingError):
                project_access.main(["owned/project"], resolved_settings=self.settings, child_context=object())
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
