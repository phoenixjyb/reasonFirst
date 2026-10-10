"""Private resolved-settings transport for owned ReasonFirst helper children.

The request contains credentials and is for a private stdin pipe only. It is
never a public projection, a settings file, or authority to launch a worker or
publish. Supplying these inputs does not attest child code or provider policy.
Ordinary CLI entrypoints do not consume this protocol.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, fields
import io
import json
import math
from pathlib import Path
import sys
from typing import Any

from ..config import AgentSettings
from ..secret_scan import redact_sensitive_text
from .service_configuration import _SETTING_FIELDS, _settings as _validate_settings
from .service_trust import MAX_TRUST_PAYLOAD_BYTES


REQUEST_PROTOCOL = "reasonfirst-managed-child-request-v2"
RESPONSE_PROTOCOL = "reasonfirst-managed-child-response-v1"
MAX_BASE_REQUEST_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = MAX_BASE_REQUEST_BYTES + MAX_TRUST_PAYLOAD_BYTES
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
_MAX_ARGUMENTS = 256
_MAX_ARGUMENT_BYTES = 64 * 1024
_MAX_JSON_DEPTH = 64
_MAX_JSON_NODES = 200_000
_ACTUAL_CODER = "gitlab_agent.actual_coder_cli"
_PROJECT_ACCESS = "gitlab_agent.project_access"
_COMMANDS = frozenset({
    "doctor", "project-config", "start", "status", "files", "read", "diff",
    "resume", "ci", "evidence", "finish",
})
_PATH_FIELDS = frozenset({"config_file", "workspace_root", "api_ca_bundle"})
_SET_FIELDS = frozenset({"allowed_projects", "allowed_executables"})
_TUPLE_FIELDS = frozenset({"copilot_allow_tools", "copilot_deny_tools"})
_CREDENTIAL_FIELDS = frozenset({"api_token", "git_token"})
_CODES = frozenset({
    "child_invalid_request", "child_request_limit", "child_invalid_settings",
    "child_command_rejected", "child_context_failed", "child_command_failed",
    "child_output_limit", "child_invalid_response",
})


class ServiceChildError(RuntimeError):
    """Finite errors never retain a request, settings, argv, or child output."""

    def __init__(self, code: str):
        self.code = code if type(code) is str and code in _CODES else "child_invalid_response"
        super().__init__(self.code)


def _fail(code: str) -> None:
    raise ServiceChildError(code) from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _integer_text(value: str, *, maximum: int = 24) -> int:
    if len(value) > maximum:
        raise ValueError()
    return int(value)


def _constant(value: str):
    raise ValueError()


def _json_tree(value: Any, code: str) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
            _fail(code)
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                _fail(code)
            pending.extend((value, depth + 1) for value in item.values())
        elif type(item) is list:
            pending.extend((value, depth + 1) for value in item)
        elif type(item) is float:
            if not math.isfinite(item):
                _fail(code)
        elif item is not None and type(item) not in (str, int, bool):
            _fail(code)


def _load_json(raw: bytes, *, maximum: int, code: str, integer_chars: int = 24) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        _fail(code)
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_int=lambda text: _integer_text(text, maximum=integer_chars),
                            parse_constant=_constant)
        _json_tree(result, code)
        return result
    except ServiceChildError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        _fail(code)


def _encode(value: dict, *, maximum: int, code: str) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        _fail(code)
    if len(raw) > maximum:
        _fail(code)
    return raw


class _ManagedParser(argparse.ArgumentParser):
    """Use the real CLI grammar without printing caller-controlled parse errors."""

    def error(self, message):
        _fail("child_command_rejected")

    def exit(self, status=0, message=None):
        _fail("child_command_rejected")

    def _print_message(self, message, file=None):
        pass


def _parsed_command(module: str, argv: list[str]) -> tuple[str, argparse.Namespace]:
    if type(module) is not str or module not in {_ACTUAL_CODER, _PROJECT_ACCESS}:
        _fail("child_command_rejected")
    if type(argv) is not list or not 1 <= len(argv) <= _MAX_ARGUMENTS:
        _fail("child_command_rejected")
    for item in argv:
        if type(item) is not str or "\x00" in item:
            _fail("child_command_rejected")
        try:
            if len(item) > _MAX_ARGUMENT_BYTES or len(item.encode("utf-8")) > _MAX_ARGUMENT_BYTES:
                _fail("child_request_limit")
        except UnicodeError:
            _fail("child_command_rejected")
    try:
        if module == _PROJECT_ACCESS:
            from ..project_access import _build_parser
            args = _build_parser(parser_class=_ManagedParser).parse_args(argv)
            return "project-access", args
        from ..cli import _build_parser
        args = _build_parser(prog="actual-coder", parser_class=_ManagedParser).parse_args(argv)
        if args.command not in _COMMANDS:
            _fail("child_command_rejected")
        # Inspect parsed values: --la and other accepted abbreviations have the
        # same semantics as their full spellings and cannot bypass this guard.
        if args.command == "start" and not args.no_launch:
            _fail("child_command_rejected")
        if args.command == "resume" and args.launch:
            _fail("child_command_rejected")
        if args.command == "finish" and (not args.dry_run or args.description_file is not None):
            _fail("child_command_rejected")
        if args.command == "project-config" and args.file is not None:
            _fail("child_command_rejected")
        return args.command, args
    except ServiceChildError:
        raise
    except (Exception, SystemExit):
        _fail("child_command_rejected")


def _command(module: str, argv: list[str]) -> str:
    return _parsed_command(module, argv)[0]


def helper_requires_api_trust(settings: AgentSettings, module: str, argv: list[str]) -> bool:
    """Select trust before an admitted helper that can use the GitLab API.

    Use the real parser for both capability selection and the existing launch
    restrictions. Missing-token and explicitly offline/Git-only routes keep
    their existing behavior without forcing an unused CA file to be available.
    """
    _settings_wire(settings)
    command, args = _parsed_command(module, argv)
    if not settings.api_token:
        return False
    if command == "doctor":
        return not args.offline and not args.git_only
    if command == "start":
        return not args.offline_doctor and not args.git_only
    if command in {"evidence", "resume"}:
        return args.from_ci
    return command in {"project-access", "ci"}


def _settings_wire(settings: AgentSettings) -> dict:
    try:
        _validate_settings(settings)
        result = {}
        for field in fields(AgentSettings):
            value = getattr(settings, field.name)
            if field.name in _PATH_FIELDS:
                value = str(value) if value is not None else None
            elif field.name in _SET_FIELDS:
                value = sorted(value)
            elif field.name in _TUPLE_FIELDS:
                value = list(value)
            result[field.name] = value
        return result
    except Exception:
        _fail("child_invalid_settings")


def _settings_value(raw: Any) -> AgentSettings:
    if type(raw) is not dict or set(raw) != _SETTING_FIELDS:
        _fail("child_invalid_settings")
    try:
        values = dict(raw)
        for name in _PATH_FIELDS:
            value = values[name]
            if value is None and name == "api_ca_bundle":
                continue
            if type(value) is not str:
                _fail("child_invalid_settings")
            values[name] = Path(value)
        for name in _SET_FIELDS | _TUPLE_FIELDS:
            value = values[name]
            if type(value) is not list or any(type(item) is not str for item in value):
                _fail("child_invalid_settings")
            if name in _SET_FIELDS:
                if len(set(value)) != len(value):
                    _fail("child_invalid_settings")
                values[name] = frozenset(value)
            else:
                values[name] = tuple(value)
        settings = AgentSettings(**values)
        _validate_settings(settings)
        return settings
    except Exception:
        _fail("child_invalid_settings")


@dataclass(frozen=True, repr=False)
class _ChildRequest:
    settings: AgentSettings
    module: str
    argv: tuple[str, ...]
    command: str
    api_trust: object | None


def encode_child_request(
    settings: AgentSettings, module: str, argv: list[str], *, api_trust=None,
) -> bytes:
    """Return private pipe bytes; do not log, persist, or place them in argv/env."""
    values = _settings_wire(settings)
    _command(module, argv)
    base = {"protocol": REQUEST_PROTOCOL, "settings": values,
            "module": module, "argv": list(argv)}
    _encode(base, maximum=MAX_BASE_REQUEST_BYTES, code="child_request_limit")
    payload = None
    if api_trust is not None:
        from .service_trust import ServiceAPITrust, ServiceAPITrustError, encode_api_trust
        try:
            if type(api_trust) is not ServiceAPITrust or api_trust.settings is not settings:
                _fail("child_context_failed")
            payload = encode_api_trust(api_trust)
        except ServiceAPITrustError:
            _fail("child_context_failed")
        _encode(payload, maximum=MAX_TRUST_PAYLOAD_BYTES, code="child_request_limit")
    return _encode({**base, "api_trust": payload},
                   maximum=MAX_REQUEST_BYTES, code="child_request_limit")


def _decode_request(raw: bytes) -> _ChildRequest:
    if type(raw) is bytes and len(raw) > MAX_REQUEST_BYTES:
        _fail("child_request_limit")
    # The transferred native file observations allow 128-bit identity values.
    # Keep response parsing and every settings field's own bounds unchanged.
    value = _load_json(raw, maximum=MAX_REQUEST_BYTES, code="child_invalid_request", integer_chars=40)
    if (type(value) is not dict or set(value) != {"protocol", "settings", "module", "argv", "api_trust"}
            or type(value["protocol"]) is not str or value["protocol"] != REQUEST_PROTOCOL):
        _fail("child_invalid_request")
    base = {key: item for key, item in value.items() if key != "api_trust"}
    _encode(base, maximum=MAX_BASE_REQUEST_BYTES, code="child_request_limit")
    settings = _settings_value(value["settings"])
    command = _command(value["module"], value["argv"])
    api_trust = None
    if value["api_trust"] is not None:
        _encode(value["api_trust"], maximum=MAX_TRUST_PAYLOAD_BYTES, code="child_request_limit")
        from .service_trust import ServiceAPITrustError, decode_api_trust
        try:
            api_trust = decode_api_trust(settings, value["api_trust"])
        except ServiceAPITrustError:
            _fail("child_context_failed")
    return _ChildRequest(settings, value["module"], tuple(value["argv"]), command, api_trust)


class _BoundedText(io.TextIOBase):
    """Bound UTF-8 capture even when a CLI catches the first write exception."""

    def __init__(self, maximum: int):
        self.maximum = maximum
        self.data = bytearray()
        self.exceeded = False

    def writable(self):
        return True

    def write(self, text):
        if type(text) is not str:
            _fail("child_invalid_response")
        remaining = self.maximum - len(self.data)
        if len(text) > remaining:
            self.exceeded = True
            _fail("child_output_limit")
        try:
            raw = text.encode("utf-8")
        except UnicodeError:
            _fail("child_invalid_response")
        if len(raw) > remaining:
            self.exceeded = True
            _fail("child_output_limit")
        self.data.extend(raw)
        return len(text)


def _doctor_result(value: Any) -> dict:
    if (type(value) is not dict or type(value.get("ok")) is not bool
            or type(value.get("checks")) is not list or type(value.get("summary")) is not dict):
        _fail("child_command_failed")
    result = dict(value)
    checks = []
    for item in value["checks"]:
        if (type(item) is not dict or type(item.get("name")) is not str
                or item.get("status") not in {"pass", "warn", "fail", "skip"}):
            _fail("child_command_failed")
        if item["status"] == "fail" or (item["name"] == "disk_space" and item["status"] == "warn"):
            # Doctor catches transport/configuration exceptions. Preserve its
            # structured diagnosis without reflecting their arbitrary messages.
            item = {"name": item["name"], "status": item["status"],
                    "message": "Managed child diagnostic did not pass."}
        checks.append(item)
    result["checks"] = checks
    return result


def _redact_result(value: Any, credentials: tuple[str, ...]) -> Any:
    if type(value) is str:
        if any(secret in value for secret in credentials):
            _fail("child_command_failed")
        return redact_sensitive_text(value)[0]
    if type(value) is dict:
        # A username or author can be as short as "a" or "git". Neither these
        # metadata values nor redaction patterns may rewrite result schema keys.
        if any(secret in key for key in value for secret in credentials):
            _fail("child_command_failed")
        return {key: _redact_result(item, credentials) for key, item in value.items()}
    if type(value) is list:
        return [_redact_result(item, credentials) for item in value]
    return value


def _command_result(request: _ChildRequest, raw: bytes, exit_code: int) -> dict:
    result = _load_json(raw, maximum=MAX_RESPONSE_BYTES, code="child_invalid_response")
    if type(result) is not dict or ("error" in result and type(result["error"]) is not dict):
        _fail("child_command_failed")
    if request.command == "doctor":
        result = _doctor_result(result)
    elif request.command == "start" and result.get("stage") == "doctor":
        result = {**result, "preflight": _doctor_result(result.get("preflight"))}
    elif request.module == _PROJECT_ACCESS and "error" in result:
        from ..project_access import ProjectAccessError, _ERRORS
        error = result["error"]
        code, stage, status = error.get("code"), error.get("stage"), error.get("http_status")
        if (type(code) is not str or code not in _ERRORS or type(stage) is not str
                or stage not in {"configuration", "input", "local_policy", "credential", "project", "ref", "required_files"}
                or (status is not None and (type(status) is not int or not 100 <= status <= 599))):
            _fail("child_command_failed")
        result = {**result, "error": ProjectAccessError(code, stage=stage, http_status=status).result()["error"]}
    elif exit_code != 0:
        structured_failure = (
            request.command == "project-config" and result.get("valid") is False
            and type(result.get("errors")) is list
        ) or (
            request.command == "finish" and result.get("dry_run") is True
            and result.get("ok") is False and type(result.get("snapshot")) is dict
        )
        if not structured_failure:
            _fail("child_command_failed")
    credentials = tuple({getattr(request.settings, name) for name in _CREDENTIAL_FIELDS
                         if getattr(request.settings, name)})
    return _redact_result(result, credentials)


def _response(result: dict | None, exit_code: int, error: str | None = None) -> bytes:
    return _encode({"protocol": RESPONSE_PROTOCOL, "exit_code": exit_code,
                    "result": result, "error": error},
                   maximum=MAX_RESPONSE_BYTES - 1, code="child_output_limit")


def decode_child_response(raw: bytes, returncode: int) -> dict:
    """Decode one child's result; transport status cannot be overridden by JSON."""
    value = _load_json(raw, maximum=MAX_RESPONSE_BYTES, code="child_invalid_response")
    if (type(returncode) is not int or not 0 <= returncode <= 255
            or type(value) is not dict or set(value) != {"protocol", "exit_code", "result", "error"}
            or type(value["protocol"]) is not str or value["protocol"] != RESPONSE_PROTOCOL
            or type(value["exit_code"]) is not int or value["exit_code"] != returncode):
        _fail("child_invalid_response")
    if value["error"] is not None:
        if (type(value["error"]) is not str or value["error"] not in _CODES
                or value["result"] is not None or returncode != 1):
            _fail("child_invalid_response")
        _fail(value["error"])
    if type(value["result"]) is not dict:
        _fail("child_invalid_response")
    return {**value["result"], "_returncode": returncode}


def _run_request(raw: bytes) -> tuple[bytes, int]:
    stdout, stderr = _BoundedText(MAX_RESPONSE_BYTES), _BoundedText(MAX_STDERR_BYTES)
    binding_error_type = ()
    try:
        # Redirection is confined to this dedicated helper process, never used
        # by the parent encoder/decoder or a concurrent HTTP handler.
        with redirect_stdout(stdout), redirect_stderr(stderr):
            from .service_children import capture_helper_children, ServiceChildBindingError
            from .service_trust import ServiceAPITrustError
            binding_error_type = (ServiceChildBindingError, ServiceAPITrustError)
            request = _decode_request(raw)
            try:
                child_context = capture_helper_children(request.settings, api_trust=request.api_trust)
            except Exception:
                _fail("child_context_failed")
            if request.module == _ACTUAL_CODER:
                from ..cli import main as cli_main
                exit_code = cli_main(list(request.argv), prog="actual-coder",
                                     resolved_settings=request.settings, child_context=child_context)
            else:
                from ..project_access import main as access_main
                exit_code = access_main(list(request.argv), resolved_settings=request.settings,
                                        child_context=child_context)
            # A CLI diagnostic may catch an operation failure. Invalidating the
            # retained context must still reach the parent admission gate.
            child_context.revalidate()
        if stdout.exceeded or stderr.exceeded:
            _fail("child_output_limit")
        if type(exit_code) is not int or not 0 <= exit_code <= 255:
            _fail("child_invalid_response")
        result = _command_result(request, bytes(stdout.data), exit_code)
        return _response(result, exit_code), exit_code
    except ServiceChildError as exc:
        code = "child_output_limit" if stdout.exceeded or stderr.exceeded else exc.code
        return _response(None, 1, code), 1
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        code = "child_context_failed" if isinstance(exc, binding_error_type) else "child_command_failed"
        if stdout.exceeded or stderr.exceeded:
            code = "child_output_limit"
        return _response(None, 1, code), 1


def main() -> int:
    """Read one private request, emit one bounded response, and leave no files."""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        response, exit_code = _run_request(raw)
    except (Exception, SystemExit, KeyboardInterrupt):
        response, exit_code = _response(None, 1, "child_invalid_request"), 1
    try:
        sys.stdout.buffer.write(response + b"\n")
        sys.stdout.buffer.flush()
    except (OSError, ValueError):
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
