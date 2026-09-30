from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .setup_state import DEFAULT_SETUP_STATE_PATH
from .setup_status import build_setup_status


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _mark(value: bool) -> str:
    return "✓" if value else "·"


def _print_human_status(payload: dict[str, object]) -> None:
    system = payload["system"]
    assert isinstance(system, dict)
    readiness = payload["readiness"]
    assert isinstance(readiness, dict)
    config = payload["config"]
    assert isinstance(config, dict)
    workers = payload["workers"]
    assert isinstance(workers, list)
    plan = payload["plan"]
    assert isinstance(plan, dict)

    print("ReasonFirst setup status")
    print(
        f"Platform: {system.get('os')} {system.get('architecture')}  "
        f"| ReasonFirst {payload.get('reasonfirst_version')}"
    )
    print(f"Mode: {payload.get('mode')}")
    print()
    print(
        f"{_mark(bool(readiness.get('machine_prerequisites')))} "
        "machine prerequisites"
    )
    print(
        f"{_mark(bool(config.get('valid')))} "
        f"GitLab configuration: {config.get('path')}"
    )

    available_workers = [
        str(item.get("name"))
        for item in workers
        if isinstance(item, dict) and bool(item.get("available"))
    ]
    print(
        f"{_mark(bool(available_workers))} workers detected: "
        + (", ".join(available_workers) if available_workers else "none")
    )
    print(
        f"{_mark(bool(readiness.get('tunnel_client_available')))} "
        "tunnel-client available"
    )
    print()
    print("Readiness:")
    print(
        "  machine: "
        + ("ready" if readiness.get("machine_prerequisites") else "needs action")
    )
    print(
        "  control plane: "
        + (
            "prerequisites present"
            if readiness.get("control_plane_prerequisites")
            else "needs action"
        )
    )
    print("  ChatGPT connection: not verified (local status never guesses)")
    print()

    actions = plan.get("actions")
    if isinstance(actions, list) and actions:
        print("Next actions:")
        for index, action in enumerate(actions, 1):
            if isinstance(action, dict):
                print(f"  {index}. {action.get('message')}")
    else:
        print("No local prerequisite action detected.")
        print(
            "A future guided setup phase must still verify external/project "
            "authorization before reporting READY."
        )
    print()
    print("No changes were made.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reasonfirst",
        description="ReasonFirst guided setup and orchestration entry point",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser(
        "setup",
        help="Inspect or configure the ReasonFirst installation",
    )
    setup.add_argument(
        "--status",
        action="store_true",
        help="Detect local setup state without making changes",
    )
    setup.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )
    setup.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
        help="Override the non-secret setup progress file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            if not args.status:
                payload: dict[str, object] = {
                    "ok": False,
                    "command": "setup",
                    "error": (
                        "Guided setup apply is delivered in the next v0.5.1 slice; "
                        "use 'reasonfirst setup --status' for the non-mutating "
                        "cross-platform inventory implemented in Slice 1."
                    ),
                    "mutating": False,
                }
                if args.json:
                    _print_json(payload)
                else:
                    print(payload["error"], file=sys.stderr)
                return 2

            payload = build_setup_status(state_path=args.state_file)
            if args.json:
                _print_json(payload)
            else:
                _print_human_status(payload)
            return 0

        parser.error(f"Unhandled command {args.command}")
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "type": type(exc).__name__,
        }
        if getattr(args, "json", False):
            _print_json(payload)
        else:
            print(f"ReasonFirst setup status failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
