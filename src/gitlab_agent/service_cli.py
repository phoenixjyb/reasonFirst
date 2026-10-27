"""CLI adapter for static service inspection. Unsupported operations fail closed."""
from __future__ import annotations

import argparse
import json

from .service_planning import build_service_plan, collect_service_evidence, service_status, unchanged


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes untrusted command-line values.
        raise ValueError("invalid_service_arguments")


def add_service_parser(subparsers):
    service = subparsers.add_parser("service", help="Static inventory and non-mutating login plans only")
    actions = service.add_subparsers(dest="service_command", required=True)
    for action in ("status", "plan", "repair"):
        command = actions.add_parser(action)
        command.add_argument("--json", action="store_true")
        if action == "status":
            command.add_argument("--native", action="store_true",
                                 help="Opt-in bounded read-only OS service-manager queries")
        if action == "plan":
            command.add_argument("--autostart", choices=("login",), required=True)
        if action == "repair":
            command.add_argument("--dry-run", action="store_true")
    return service


def main(argv: list[str]) -> int:
    json_requested = any(arg.partition("=")[0] == "--json" for arg in argv)
    parser = _Parser(prog="reasonfirst")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_service_parser(subparsers)
    try:
        args = parser.parse_args(["service", *argv])
    except ValueError:
        payload = {"ok": False, **unchanged(), "error": "invalid_service_arguments"}
        code = 2
    else:
        if args.service_command == "repair" and not args.dry_run:
            payload = {"ok": False, **unchanged(), "error": "repair_requires_dry_run"}
            code = 2
        else:
            try:
                evidence = collect_service_evidence()
                if args.service_command == "status":
                    payload = service_status(evidence)
                    if args.native:
                        # No native subprocess in ordinary status/plan/repair.
                        from .native_service_observation import observe_native_services
                        payload["native_services"] = observe_native_services()
                        payload["scope"] = "slice2a_opt_in_native_evidence"
                        payload["target"]["native_observation"] = "bounded_named_query"
                    code = 0
                else:
                    payload = build_service_plan(evidence)
                    payload["evidence"] = service_status(evidence)
                    if args.service_command == "repair":
                        payload["command"] = "service-repair"
                        payload["dry_run"] = True
                    code = 1 if payload["blockers"] else 0
            except Exception:
                # Collection and planning failures are operational errors, even
                # when a dependency happens to raise ValueError.
                payload = {"ok": False, **unchanged(), "error": "service_inspection_failed"}
                code = 1
    if json_requested:
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
    else:
        print("ReasonFirst service inspection: no changes; plans cannot execute.")
        if "error" in payload:
            print(payload["error"])
        else:
            print("Collection complete; runtime ownership and readiness not checked.")
            for blocker in payload.get("blockers", []):
                print("  " + blocker)
    return code
