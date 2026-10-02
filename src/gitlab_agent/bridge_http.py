"""Explicit foreground HTTP transport for the packaged Bridge core.

Inspection is a static launch description, not deployment discovery or approval.
No service manager, runtime installer, control-token route or remote-push adapter
is used here. Legacy deployments requiring those extensions are not compatible.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping


class HTTPLaunchError(ValueError):
    """A classified error whose message never includes caller-provided values."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse normally echoes unknown arguments and invalid values. They
        # may contain a mistakenly supplied credential; do not reflect them.
        self.exit(2, "reasonfirst-bridge-http: invalid_arguments; run --help\n")


@dataclass(frozen=True)
class HTTPLaunch:
    host: str
    port: int
    path: str
    mode: str
    control_policy: str

    def validate(self, environ: Mapping[str, str]) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise HTTPLaunchError("unsupported_host: explicit literal loopback required")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise HTTPLaunchError("invalid_port: fixed port in 1..65535 required")
        if (
            not isinstance(self.path, str)
            or len(self.path) > 256
            or re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+", self.path) is None
            or self.path.split("/")[1].lower() in {"control", "healthz"}
        ):
            raise HTTPLaunchError("unsupported_path: unambiguous nonreserved MCP path required")
        if self.mode not in {"read-only", "full-chat"}:
            raise HTTPLaunchError("invalid_mode: explicit read-only or full-chat required")
        if self.control_policy != "disabled":
            raise HTTPLaunchError("unsupported_control_policy: legacy control is not packaged")
        for name in ("PYTHONPATH", "PYTHONHOME"):
            if environ.get(name):
                raise HTTPLaunchError("python_environment_override: use a reviewed clean launch environment")
        read_only = _environment_bool(environ, "RF_MCP_READ_ONLY")
        if read_only is not None and read_only != (self.mode == "read-only"):
            raise HTTPLaunchError("policy_conflict: explicit mode differs from RF_MCP_READ_ONLY")
        if _environment_bool(environ, "RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"):
            raise HTTPLaunchError("unsupported_remote_push: experimental extension is not packaged")

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "bridge-http-inspect",
            "ok": True,
            "mutating": False,
            "transport": "streamable-http",
            "endpoint": {"host": self.host, "port": self.port, "path": self.path},
            "mode": self.mode,
            "control_policy": self.control_policy,
            "remote_push_exposed": False,
            "shared_core": "gitlab_agent.bridge_mcp.build_server",
            "configuration_inspected": False,
            "package_integrity_verified": False,
            "server_started": False,
            "live_probe_performed": False,
            "ready_for_activation": False,
            "note": "Static arguments only; no deployment, runtime, authentication or health acceptance.",
        }


def _environment_bool(environ: Mapping[str, str], name: str) -> bool | None:
    if name not in environ:
        return None
    value = environ[name].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise HTTPLaunchError("invalid_policy_environment: explicit boolean required")


def _build_server(*, read_only_mode: bool):
    # Lazy import keeps --help, --inspect and rejected launches independent of
    # controller/configuration loading and of optional SDK import side effects.
    from .bridge_mcp import build_server
    return build_server(read_only_mode=read_only_mode)


def _serve(launch: HTTPLaunch) -> int:
    # Check immediately before constructing any controller. No ambient values
    # are rewritten to turn an inconsistent launch into a successful one.
    launch.validate(os.environ)
    for name in ("CONTROL_PLANE_API_KEY", "OPENAI_ADMIN_KEY"):
        os.environ.pop(name, None)
    server = None
    result = 0
    try:
        server = _build_server(read_only_mode=launch.mode == "read-only")
        server.run(
            transport="streamable-http",
            host=launch.host,
            port=launch.port,
            streamable_http_path=launch.path,
        )
    except KeyboardInterrupt:
        result = 130
    except Exception:
        print("reasonfirst-bridge-http: server_failed; no service-manager action was attempted", file=sys.stderr)
        result = 1
    finally:
        ctrl = getattr(server, "_reasonfirst_controller", None)
        if ctrl is not None:
            try:
                ctrl.close()
            except Exception:
                print("reasonfirst-bridge-http: controller_cleanup_failed", file=sys.stderr)
                result = result or 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(
        description="Development-only packaged Bridge HTTP transport; not a service updater.",
        allow_abbrev=False,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inspect", action="store_true", help="print a static JSON launch description; no filesystem or network inspection")
    action.add_argument("--serve", action="store_true", help="start a foreground HTTP server; loads Bridge configuration/state")
    parser.add_argument("--host", required=True, help="literal 127.0.0.1 or ::1; no hostname or wildcard")
    parser.add_argument("--port", type=int, required=True, help="fixed existing port; 0 is not supported")
    parser.add_argument("--path", required=True, help="exact MCP path; no automatic rewriting")
    parser.add_argument("--mode", choices=("read-only", "full-chat"), required=True)
    parser.add_argument("--control-policy", choices=("disabled", "legacy"), required=True,
                        help="must explicitly be disabled; legacy is rejected, never silently removed")
    args = parser.parse_args(argv)
    launch = HTTPLaunch(args.host, args.port, args.path, args.mode, args.control_policy)
    try:
        launch.validate(os.environ)
        if args.inspect:
            print(json.dumps(launch.describe(), ensure_ascii=False, sort_keys=True))
            return 0
        return _serve(launch)
    except HTTPLaunchError as exc:
        print(f"reasonfirst-bridge-http: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
