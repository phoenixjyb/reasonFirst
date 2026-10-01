"""Operator-only Python selection and pinned-contract validation commands."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .finish import _load_base_contract, run_project_validations
from .python_runtime import PythonBindings, discover_python


def register_commands(sub: Any) -> None:
    parser = sub.add_parser("python", help="Approve or inspect a workspace-scoped Python interpreter")
    actions = parser.add_subparsers(dest="python_action", required=True)
    bind = actions.add_parser("bind", help="Review, probe, and save one explicitly approved Python mapping")
    bind.add_argument("workspace_id")
    bind.add_argument("--command", dest="python_command", choices=("python", "python3"), default="python3")
    selector = bind.add_mutually_exclusive_group(required=True)
    selector.add_argument("--executable", help="Absolute path to the intended project Python executable")
    selector.add_argument("--python", help="Find an already installed system Python version, e.g. 3.12")
    bind.add_argument("--yes", action="store_true", help="Explicitly approve the displayed selection and probe")
    bind.add_argument("--replace", action="store_true", help="Explicitly replace a previously approved mapping")
    status = actions.add_parser("status", help="Verify approved bindings and check local command availability")
    status.add_argument("workspace_id")
    validate = sub.add_parser("validate", help="Run pinned project validation without commit, push, or worker launch")
    validate.add_argument("workspace_id")
    validate.add_argument("--plan", action="store_true", help="Only check command resolution; do not run project tests")


def handle_command(args: argparse.Namespace, settings: Any, manager: Any, runner: Any) -> tuple[dict[str, Any], int]:
    bindings = PythonBindings(manager)
    if args.command == "python" and args.python_action == "bind":
        # Authorize workspace/alias before performing interpreter discovery.
        bindings._identity(args.workspace_id)
        bindings._allowed(args.python_command)
        executable = args.executable or discover_python(args.python)
        plan = bindings.plan(args.workspace_id, args.python_command, executable)
        if not args.yes:
            if not sys.stdin.isatty():
                return {"ok": False, "approval_required": True, "plan": plan, "saved": False}, 1
            print(json.dumps(plan, indent=2, ensure_ascii=True), file=sys.stderr)
            print(
                "Probe this executable and save this workspace-only Python binding? [y/N] ",
                file=sys.stderr, end="", flush=True,
            )
            if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
                return {"ok": False, "cancelled": True, "saved": False}, 1
        record = bindings.bind(plan, approved=True, replace=args.replace, expected_version=args.python)
        return {"ok": True, "saved": True, "binding": record, "worker_started": False}, 0

    context, metadata = _load_base_contract(settings, manager, args.workspace_id)
    resolution = bindings.validation_plan(args.workspace_id, context)
    if args.command == "python" or args.plan:
        return {"command": "validation-plan", "project_config": metadata,
                **resolution, "tests_executed": False}, 0 if resolution["ok"] else 1
    if not resolution["ok"]:
        return {"ok": False, "command": "validate", "resolution": resolution,
                "tests_executed": False, "message": "Resolve missing commands explicitly; no tests were started."}, 1
    validations, blocked = run_project_validations(settings, runner, args.workspace_id, context)
    ok = bool(validations) and not blocked
    return {
        "ok": ok, "command": "validate", "project_config": metadata,
        "resolution": resolution, "validations": validations,
        "workspace": manager.status(args.workspace_id),
        "publication_attempted": False, "worker_started": False,
        "message": "Pinned validations completed" if validations else "No validation commands configured; not a test pass",
    }, 0 if ok else 1
