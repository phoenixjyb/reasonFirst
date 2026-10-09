#!/usr/bin/env python3
"""Disposable installed-artifact managed-startup acceptance for runtime_e2e.

Run with an isolated installed interpreter, outside the source checkout. The
outer harness owns the freshly prepared runtime and every fixture home. This
script never prepares, modifies, activates or installs a runtime, and exercises
MCP catalog discovery only. Shared tool-admission guards have separate unit tests.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from unittest.mock import patch

import gitlab_agent
from gitlab_agent import bridge_http, bridge_mcp
from gitlab_agent.bridge_preview import controller
from gitlab_agent.upgrade import runtime, startup_channel, startup_managed, startup_state


_FALSE_FLAGS = (
    "peer_identity_verified", "running_code_verified", "effective_configuration_verified",
    "managed_startup_confirmation_verified", "compatibility_verified",
    "activation_authorized", "ready_for_activation", "service_changed",
)
_SUCCESS_FLAGS = (
    "cleanup_confirmed", "child_reaped", "private_channel_owned",
    "fresh_reply_claims_match", "pid_claim_matches", "listener_reserved",
    "listener_startup_observed", "parent_socket_listening_observed",
    "endpoint_catalog_verified", "selected_runtime_revalidated",
    "configuration_projection_matches", "child_import_origins_verified",
    "child_controller_cleanup_confirmed",
    "catalog_helper_reaped", "catalog_helper_cleanup_confirmed",
)
_ERRORS = frozenset({
    "fixture_context_required", "supervisor_origin_mismatch", "runtime_not_prepared",
    "unexpected_probe_result", "probe_cleanup_unconfirmed", "runtime_changed",
    "listener_fixture_failed", "fixture_cleanup_failed", "unsupported_boundary_failed",
    "fixture_interrupted", "fixture_failed",
})


def _probe_code(value):
    if value is None:
        return None
    return value if type(value) is str and value in startup_managed._CODES else "probe_failed"


class FixtureFailure(RuntimeError):
    def __init__(self, code, *, probe=None):
        self.code = code if type(code) is str and code in _ERRORS else "fixture_failed"
        # Retain only the trusted adapter's fixed codes, never the probe object.
        source = probe if type(probe) is dict else {}
        self.probe_error_code = _probe_code(source.get("error_code"))
        self.probe_cleanup_error_code = _probe_code(source.get("cleanup_error_code"))
        super().__init__(self.code)


def _require(value, code, *, probe=None):
    if not value:
        raise FixtureFailure(code, probe=probe) from None


def _report(route):
    return {
        "operation": "managed-startup-native-acceptance", "supervisor_route": route,
        "ok": False, "stage": "context", "error_code": None, "cleanup_error_code": None,
        "probe_error_code": None, "probe_cleanup_error_code": None,
        "supervisor_import_origins_verified": False, "child_import_origins_verified": False,
        "read_only_catalog_verified": False, "full_chat_catalog_verified": False,
        "occupied_listener_preserved": False, "decoy_listener_preserved": False,
        "attempt_cleanup_confirmed": False, "runtime_unchanged": False,
        "unsupported_before_side_effects": False, "tool_catalog_only": True,
        "tool_calls_exercised": False, "working_service_touched": False,
        "activation_tested": False,
    }


def _check_probe(report, *, expected_error=None):
    _require(type(report) is dict and report.get("operation") == "disposable-managed-startup",
             "unexpected_probe_result", probe=report)
    _require(all(report.get(name) is False for name in _FALSE_FLAGS),
             "unexpected_probe_result", probe=report)
    _require(report.get("cleanup_confirmed") is True and report.get("cleanup_error_code") is None,
             "probe_cleanup_unconfirmed", probe=report)
    if expected_error is None:
        _require(report.get("ok") is True and report.get("error_code") is None
                 and all(report.get(name) is True for name in _SUCCESS_FLAGS),
                 "unexpected_probe_result", probe=report)
        codec = report.get("codec_result")
        _require(type(codec) is dict and codec.get("fresh_reply_claims_match") is True
                 and codec.get("pid_claim_matches") is True
                 and all(codec.get(name) is False for name in _FALSE_FLAGS),
                 "unexpected_probe_result", probe=report)
    else:
        _require(report.get("ok") is False and report.get("error_code") == expected_error
                 and report.get("listener_startup_observed") is False
                 and report.get("endpoint_catalog_verified") is False,
                 "unexpected_probe_result", probe=report)


def _launch(port, mode="read-only"):
    return bridge_http.HTTPLaunch("127.0.0.1", port, "/fixture/managed", mode, "disabled")


def _free_port():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return reserved.getsockname()[1]


def _check_origins(expected_root):
    _require(sys.flags.isolated == 1 and sys.dont_write_bytecode is True,
             "fixture_context_required")
    _require(Path(sys.prefix).resolve() == expected_root
             and expected_root != Path(sys.base_prefix).resolve(), "supervisor_origin_mismatch")
    modules = (gitlab_agent, bridge_http, bridge_mcp, controller,
               startup_managed, startup_channel, startup_state)
    _require(all(Path(module.__file__).resolve().is_relative_to(expected_root) for module in modules),
             "supervisor_origin_mismatch")


def _verify_unsupported(report):
    # Construct arguments/import standard helpers before instrumenting every
    # relevant side-effect boundary. The adapter itself must return first.
    launch = _launch(8765)
    uninspected_home = Path("uninspected-startup-fixture")
    with ExitStack() as stack:
        observed = [stack.enter_context(patch(target, side_effect=AssertionError("forbidden fixture I/O")))
                    for target in ("builtins.open", "os.open", "os.stat", "os.pipe",
                                   "socket.socket", "subprocess.Popen", "tempfile.TemporaryDirectory")]
        observed.append(stack.enter_context(patch.object(runtime, "status",
                                side_effect=AssertionError("forbidden fixture status"))))
        result = startup_managed.probe_disposable(runtime_id="a" * 64,
                                                  home=uninspected_home, launch=launch)
        _require(result.get("ok") is False
                 and result.get("error_code") == "unsupported_managed_startup_platform"
                 and all(result.get(name) is False for name in _FALSE_FLAGS),
                 "unsupported_boundary_failed", probe=result)
        _require(all(operation.call_count == 0 for operation in observed), "unsupported_boundary_failed")
    report.update(ok=True, stage="complete", unsupported_before_side_effects=True)


def _verify(args, report):
    fixture = Path(args.fixture_root).resolve()
    source = Path(args.source_root).resolve()
    home = Path(args.runtime_home).resolve()
    prepared = Path(args.prepared_root).resolve()
    supervisor = Path(args.supervisor_root).resolve()
    cwd = Path.cwd().resolve()
    _require(fixture.name.startswith("rf-runtime-native-")
             and home.parent == fixture and cwd.is_relative_to(fixture)
             and not cwd.is_relative_to(source)
             and prepared == home.joinpath(*runtime.PARTS, args.runtime_id, "venv")
             and ((supervisor == prepared) == (args.route == "prepared-runtime")),
             "fixture_context_required")
    _check_origins(supervisor)
    report["supervisor_import_origins_verified"] = True
    report["stage"] = "initial_status"
    before = runtime.status(runtime_id=args.runtime_id, home=home)
    _require(before.get("prepared") is True and before.get("runtime_status") == "prepared_matches_record"
             and Path(before["runtime_path"]).resolve() / "venv" == prepared
             and not (home / ".config").exists(), "runtime_not_prepared")

    for mode, flag in (("read-only", "read_only_catalog_verified"),
                       ("full-chat", "full_chat_catalog_verified")):
        report["stage"] = mode
        result = startup_managed.probe_disposable(runtime_id=args.runtime_id, home=home,
                                                  launch=_launch(_free_port(), mode))
        _check_probe(result)
        report[flag] = True
        report["child_import_origins_verified"] = True

    report["stage"] = "occupied_listener"
    with socket.socket() as listener:
        listener.settimeout(2)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = startup_managed.probe_disposable(runtime_id=args.runtime_id, home=home, launch=_launch(port))
        _check_probe(result, expected_error="listener_bind_failed")
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            accepted, _ = listener.accept()
            accepted.close()
        report["occupied_listener_preserved"] = True

    class Decoy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"service":"reasonfirst","read_only_mode":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *values):
            pass

    report["stage"] = "decoy_listener"
    decoy = http.server.HTTPServer(("127.0.0.1", 0), Decoy)
    worker = threading.Thread(target=lambda: decoy.serve_forever(poll_interval=0.02), daemon=True)
    try:
        worker.start()
        port = decoy.server_address[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def check_health():
            with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=1) as response:
                body = response.read(1025)
            _require(len(body) <= 1024 and json.loads(body) == {
                "service": "reasonfirst", "read_only_mode": True}, "listener_fixture_failed")

        check_health()
        result = startup_managed.probe_disposable(runtime_id=args.runtime_id, home=home, launch=_launch(port))
        _check_probe(result, expected_error="listener_bind_failed")
        check_health()
        report["decoy_listener_preserved"] = True
    finally:
        try:
            if worker.ident is not None:
                decoy.shutdown()
        except Exception:
            report["cleanup_error_code"] = "fixture_cleanup_failed"
        finally:
            try:
                decoy.server_close()
                if worker.ident is not None:
                    worker.join(timeout=2)
                if worker.is_alive():
                    report["cleanup_error_code"] = "fixture_cleanup_failed"
            except Exception:
                report["cleanup_error_code"] = "fixture_cleanup_failed"
    _require(report["cleanup_error_code"] is None, "fixture_cleanup_failed")

    report["attempt_cleanup_confirmed"] = True
    report["stage"] = "final_status"
    after = runtime.status(runtime_id=args.runtime_id, home=home)
    _require(after == before and not (home / ".config").exists(), "runtime_changed")
    report.update(ok=True, stage="complete", runtime_unchanged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("unsupported", allow_abbrev=False)
    verify = commands.add_parser("verify", allow_abbrev=False)
    for option in ("runtime-id", "runtime-home", "prepared-root", "supervisor-root",
                   "source-root", "fixture-root"):
        verify.add_argument("--" + option, required=True)
    verify.add_argument("--route", choices=("clean-wheel", "prepared-runtime"), required=True)
    args = parser.parse_args()
    report = _report("unsupported" if args.action == "unsupported" else args.route)
    try:
        if args.action == "unsupported":
            _verify_unsupported(report)
        else:
            _verify(args, report)
    except FixtureFailure as error:
        report.update(error_code=error.code, probe_error_code=error.probe_error_code,
                      probe_cleanup_error_code=error.probe_cleanup_error_code)
    except KeyboardInterrupt:
        report["error_code"] = "fixture_interrupted"
    except Exception:
        report["error_code"] = "fixture_failed"
    if report["cleanup_error_code"] is not None:
        report["ok"] = False
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
