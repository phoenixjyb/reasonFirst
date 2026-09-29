from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import __version__
from .config import AgentSettings, resolve_env_file
from .setup_actions import (
    CANONICAL_WORKERS,
    apply_project_add,
    apply_worker_use,
    candidate_settings,
    detect_workers,
    list_projects,
    plan_project_add,
    plan_worker_use,
    preflight_project,
)
from .setup_config import (
    apply_env_updates,
    assert_no_effective_env_override,
    ensure_persistent_config_target,
    selected_user_config_path,
)
from .setup_state import (
    DEFAULT_SETUP_STATE_PATH,
    SETUP_MODES,
    SetupState,
    load_setup_state,
    save_setup_state,
)
from .setup_status import build_setup_status
from .setup_tunnel import (
    DEFAULT_TUNNEL_ALIAS,
    RUNTIME_KEY_ENV,
    build_chatgpt_handoff,
    connect_runtime,
    open_handoff_pages,
    persist_tunnel_state,
    resolve_read_mcp,
    resolve_tunnel_client,
    runtime_status,
    stop_runtime,
)
from .tunnel_install import install_official_tunnel_client


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _mark(value: bool) -> str:
    return "✓" if value else "·"


def _load_settings_clean() -> AgentSettings:
    """Load legacy/effective config without leaking file values into this process."""
    before = dict(os.environ)
    try:
        return AgentSettings.load()
    finally:
        os.environ.clear()
        os.environ.update(before)


def _prompt(
    label: str,
    *,
    default: str | None = None,
    input_fn: Callable[[str], str] = input,
) -> str:
    suffix = f" [{default}]" if default else ""
    value = input_fn(f"{label}{suffix}: ").strip()
    return value or (default or "")


def _confirm(
    message: str,
    *,
    input_fn: Callable[[str], str] = input,
) -> bool:
    answer = input_fn(f"{message} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _available_worker_names() -> list[str]:
    return [
        str(item["name"])
        for item in detect_workers()
        if bool(item.get("available"))
    ]


def _choose_worker(
    *,
    requested: str | None,
    current: AgentSettings | None,
    input_fn: Callable[[str], str] = input,
) -> str:
    available = _available_worker_names()
    if not available:
        raise RuntimeError(
            "No supported coding worker is available. Install/sign in to Codex CLI, "
            "Copilot CLI, or start Codex Desktop, then rerun setup."
        )

    if requested:
        choice = requested
    else:
        current_choice = current.default_backend if current is not None else "auto"
        default = (
            current_choice
            if current_choice == "auto" or current_choice in available
            else "auto"
        )
        print("Detected workers: " + ", ".join(available))
        choice = _prompt(
            "Worker (auto/codex-cli/copilot-cli/codex-desktop)",
            default=default,
            input_fn=input_fn,
        )

    plan = plan_worker_use(choice)
    if not bool(plan.get("ok")):
        raise RuntimeError(str(plan.get("message") or "Selected worker is unavailable"))
    return choice


def _persist_progress(
    *,
    state_path: Path,
    mode: str,
    worker: str,
    config_file: str,
    phases: set[str],
) -> Path:
    previous = load_setup_state(state_path) or SetupState()
    completed = tuple(
        phase
        for phase in ("system", "gitlab", "worker", "tunnel", "chatgpt-read", "bridge", "ready")
        if phase in (set(previous.completed_phases) | phases)
    )
    state = replace(
        previous,
        mode=mode,
        selected_worker=worker,
        config_file=config_file,
        completed_phases=completed,
        installed_version=__version__,
        last_verified_at=datetime.now(timezone.utc).isoformat(),
    )
    return save_setup_state(state, state_path)


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
    print(
        f"{_mark(bool(readiness.get('tunnel_recorded')))} "
        "managed tunnel recorded"
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
            "Provider/project acceptance still requires the relevant live checks "
            "before reporting READY."
        )
    print()
    print("No changes were made.")


def _print_project_plan(plan: dict[str, object]) -> None:
    print("ReasonFirst project grant plan")
    print(f"Project: {plan.get('project')}")
    print(f"Resolved SHA: {plan.get('resolved_commit_sha')}")
    print(f"Already allowed: {bool(plan.get('already_allowed'))}")
    print(f"Config: {plan.get('config_file')}")
    print("No changes have been made yet.")


def _guided_tunnel_after_local(
    *,
    state_path: Path,
    project: str,
    ref: str,
    input_fn: Callable[[str], str],
    secret_fn: Callable[[str], str],
) -> dict[str, object]:
    if not _confirm("Configure ChatGPT read access now?", input_fn=input_fn):
        return {
            "attempted": False,
            "local_tunnel_ready": False,
            "chatgpt_ready": False,
            "next": "Run 'reasonfirst tunnel connect' when you are ready to configure ChatGPT access.",
        }

    state = load_setup_state(state_path)
    tunnel_client = resolve_tunnel_client(state=state)
    if not tunnel_client:
        if not _confirm(
            "Official tunnel-client is missing. Download and checksum-verify it now?",
            input_fn=input_fn,
        ):
            return {
                "attempted": True,
                "local_tunnel_ready": False,
                "chatgpt_ready": False,
                "next": "Install the official tunnel-client, then run 'reasonfirst tunnel connect'.",
            }
        installed = install_official_tunnel_client()
        tunnel_client = str(installed["path"])
        _save_tunnel_client_path(
            state_path=state_path,
            tunnel_client_path=tunnel_client,
        )

    mcp_executable = resolve_read_mcp()
    if not mcp_executable:
        raise RuntimeError(
            "reasonfirst-gitlab-mcp is missing from this installation"
        )

    # The tunnel itself must already exist in the user's Platform organization.
    # Opening this page is a browser handoff, not automatic admin provisioning.
    browser = open_handoff_pages(open_platform=True, open_chatgpt=False)
    state = load_setup_state(state_path)
    default_tunnel = state.tunnel_id if state is not None else None
    tunnel_id = _prompt(
        "OpenAI tunnel ID",
        default=default_tunnel,
        input_fn=input_fn,
    )
    runtime_key = os.getenv(RUNTIME_KEY_ENV, "").strip()
    if not runtime_key:
        runtime_key = secret_fn(
            "OpenAI tunnel runtime API key (masked; not stored by ReasonFirst): "
        ).strip()
    if not runtime_key:
        raise RuntimeError("OpenAI tunnel runtime API key is required")

    alias = (
        state.tunnel_runtime
        if state is not None and state.tunnel_runtime
        else DEFAULT_TUNNEL_ALIAS
    )
    runtime = connect_runtime(
        tunnel_id=tunnel_id,
        runtime_key=runtime_key,
        alias=alias,
        tunnel_client=tunnel_client,
        mcp_executable=mcp_executable,
    )
    if not bool(runtime.get("ok")) or not bool(runtime.get("ready")):
        return {
            "attempted": True,
            "local_tunnel_ready": False,
            "chatgpt_ready": False,
            "browser": browser,
            "runtime": runtime,
            "next": (
                "Inspect 'reasonfirst tunnel status' and the reported local admin UI, "
                "then rerun 'reasonfirst tunnel connect'."
            ),
        }

    persist_tunnel_state(
        tunnel_id=str(runtime["tunnel_id"]),
        alias=str(runtime["alias"]),
        tunnel_client=tunnel_client,
        state_path=state_path,
    )
    handoff = build_chatgpt_handoff(
        tunnel_id=str(runtime["tunnel_id"]),
        project=project,
        ref=ref,
    )
    if _confirm("Open ChatGPT now to create/test the ReasonFirst app?", input_fn=input_fn):
        browser.update(
            open_handoff_pages(open_platform=False, open_chatgpt=True)
        )

    return {
        "attempted": True,
        "local_tunnel_ready": True,
        "chatgpt_ready": False,
        "browser": browser,
        "runtime": runtime,
        "handoff": handoff,
        "next": handoff["acceptance_prompt"],
    }


def _run_guided_setup(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = getpass.getpass,
) -> tuple[int, dict[str, object]]:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Guided setup requires an interactive TTY because secrets and approval "
            "must not be supplied on argv. Use setup --status for non-interactive inspection."
        )

    assert_no_effective_env_override(
        [
            "GITLAB_BASE_URL",
            "GITLAB_TOKEN",
            "GITLAB_ALLOWED_PROJECTS",
            "GITLAB_VERIFY_SSL",
            "GITLAB_REQUIRE_WRITE_ALLOWLIST",
            "REASONFIRST_DEFAULT_BACKEND",
        ]
    )

    current: AgentSettings | None
    try:
        current = _load_settings_clean()
    except Exception:
        current = None

    effective_config = (
        current.config_file
        if current is not None
        else resolve_env_file().expanduser()
    )
    if effective_config.exists():
        ensure_persistent_config_target(effective_config)

    state = load_setup_state(args.state_file)
    mode = args.mode or (state.mode if state is not None else "standard")
    if mode not in SETUP_MODES:
        raise ValueError("Unsupported setup mode")

    base_url = args.gitlab_url or _prompt(
        "GitLab URL",
        default=current.gitlab_base_url if current is not None else None,
        input_fn=input_fn,
    )
    if not base_url:
        raise ValueError("GitLab URL is required")

    existing_token = current.api_token if current is not None else ""
    token_prompt = (
        "GitLab API token (press Enter to keep existing): "
        if existing_token
        else "GitLab API token: "
    )
    entered_token = secret_fn(token_prompt).strip()
    api_token = entered_token or existing_token
    if not api_token:
        raise ValueError("GitLab API token is required")

    default_project = None
    if current is not None and current.allowed_projects:
        default_project = sorted(current.allowed_projects)[0]
    project = args.project or _prompt(
        "Initial GitLab project (namespace/project)",
        default=default_project,
        input_fn=input_fn,
    )
    if not project:
        raise ValueError("An initial GitLab project is required")

    ref = args.ref or (
        current.default_base_ref if current is not None else "main"
    )
    worker = _choose_worker(
        requested=args.worker,
        current=current,
        input_fn=input_fn,
    )

    candidate = candidate_settings(
        base_url=base_url,
        api_token=api_token,
        project=project,
        default_backend=worker,
        current=current,
    )
    preflight = preflight_project(candidate, project, ref=ref)
    if not bool(preflight.get("ok")):
        return 1, {
            "ok": False,
            "command": "setup",
            "stage": "project-preflight",
            "project": project,
            "ref": ref,
            "preflight": preflight,
            "writes_performed": False,
        }

    existing_projects = set(current.allowed_projects) if current is not None else set()
    after_projects = sorted(existing_projects | {project})
    plan: dict[str, object] = {
        "ok": True,
        "command": "setup",
        "stage": "review",
        "mode": mode,
        "gitlab_base_url": candidate.gitlab_base_url,
        "project": project,
        "ref": preflight.get("ref") or ref,
        "resolved_commit_sha": preflight.get("resolved_commit_sha"),
        "worker": worker,
        "worker_authentication_verified": False,
        "config_file": str(selected_user_config_path()),
        "allowed_projects_after": after_projects,
        "api_token_configured": True,
        "writes_performed": False,
        "chatgpt_connection": "not_verified",
    }

    if not args.json:
        print("ReasonFirst guided setup plan")
        print(f"GitLab: {plan['gitlab_base_url']}")
        print(f"Project: {project}")
        print(f"Resolved SHA: {plan['resolved_commit_sha']}")
        print(f"Worker: {worker} (login not asserted by executable detection)")
        print(f"Mode: {mode}")
        print(f"Config: {plan['config_file']}")
        print("No secret value will be printed or stored in setup progress.")
        print("ChatGPT tunnel setup is optional and follows only after this local plan is approved.")
        print()

    if not _confirm(
        "Write this private config and grant exactly this project?",
        input_fn=input_fn,
    ):
        return 1, {
            **plan,
            "ok": False,
            "cancelled": True,
            "message": "No configuration change was made.",
        }

    config_change = apply_env_updates(
        selected_user_config_path(),
        {
            "GITLAB_BASE_URL": candidate.gitlab_base_url,
            "GITLAB_TOKEN": api_token,
            "GITLAB_ALLOWED_PROJECTS": ",".join(after_projects),
            "GITLAB_VERIFY_SSL": "true",
            "GITLAB_REQUIRE_WRITE_ALLOWLIST": "true",
            "REASONFIRST_DEFAULT_BACKEND": worker,
        },
    )
    state_file = _persist_progress(
        state_path=args.state_file,
        mode=mode,
        worker=worker,
        config_file=str(selected_user_config_path()),
        phases={"system", "gitlab", "worker"},
    )

    tunnel_result: dict[str, object] = {
        "attempted": False,
        "local_tunnel_ready": False,
        "chatgpt_ready": False,
        "next": "ChatGPT tunnel setup was skipped.",
    }
    if mode != "cli-only" and not bool(getattr(args, "skip_chatgpt", False)):
        tunnel_result = _guided_tunnel_after_local(
            state_path=args.state_file,
            project=project,
            ref=str(preflight.get("ref") or ref),
            input_fn=input_fn,
            secret_fn=secret_fn,
        )

    return 0, {
        **plan,
        "stage": "tunnel-ready" if tunnel_result.get("local_tunnel_ready") else "local-configured",
        "writes_performed": bool(config_change["changed"]),
        "config_change": config_change,
        "setup_state": str(state_file),
        "local_control_configured": True,
        "local_tunnel_ready": bool(tunnel_result.get("local_tunnel_ready")),
        "chatgpt_ready": False,
        "ready": False,
        "tunnel": tunnel_result,
        "next": tunnel_result.get("next"),
    }


def _save_tunnel_client_path(
    *,
    state_path: Path,
    tunnel_client_path: str,
) -> SetupState:
    previous = load_setup_state(state_path) or SetupState()
    state = replace(
        previous,
        tunnel_client_path=tunnel_client_path,
        installed_version=__version__,
        last_verified_at=datetime.now(timezone.utc).isoformat(),
    )
    save_setup_state(state, state_path)
    return state


def _select_project_for_handoff(
    settings: AgentSettings,
    explicit: str | None,
) -> str:
    if explicit:
        if explicit not in settings.allowed_projects:
            raise RuntimeError(
                "Requested handoff project is not in the local ReasonFirst allowlist"
            )
        return explicit
    projects = sorted(settings.allowed_projects)
    if len(projects) == 1:
        return projects[0]
    if not projects:
        raise RuntimeError("No project is locally allowlisted for a ChatGPT acceptance probe")
    raise RuntimeError(
        "More than one project is allowlisted; pass --project explicitly for the acceptance probe"
    )


def _resolve_or_install_tunnel_client(
    *,
    state_path: Path,
    install: bool,
    interactive: bool,
) -> str:
    state = load_setup_state(state_path)
    existing = resolve_tunnel_client(state=state)
    if existing:
        return existing

    should_install = install
    if not should_install and interactive:
        should_install = _confirm(
            "Official tunnel-client is missing. Download and verify the latest OpenAI release now?"
        )
    if not should_install:
        raise RuntimeError(
            "tunnel-client is not available. Re-run with --install or install the official client first."
        )

    installed = install_official_tunnel_client()
    path = str(installed["path"])
    _save_tunnel_client_path(state_path=state_path, tunnel_client_path=path)
    return path


def _connect_tunnel_from_args(args: argparse.Namespace) -> dict[str, object]:
    interactive = sys.stdin.isatty()
    state = load_setup_state(args.state_file)
    tunnel_client = _resolve_or_install_tunnel_client(
        state_path=args.state_file,
        install=bool(args.install),
        interactive=interactive,
    )
    mcp_executable = resolve_read_mcp()
    if not mcp_executable:
        raise RuntimeError(
            "reasonfirst-gitlab-mcp is not available from this installation"
        )

    tunnel_id = args.tunnel_id or (state.tunnel_id if state is not None else None)
    if not tunnel_id:
        if not interactive:
            raise RuntimeError(
                "Tunnel ID is required in non-interactive mode; pass --tunnel-id"
            )
        if args.open_platform:
            open_handoff_pages(open_platform=True, open_chatgpt=False)
        tunnel_id = _prompt("OpenAI tunnel ID")

    runtime_key = os.getenv(RUNTIME_KEY_ENV, "").strip()
    if not runtime_key:
        if not interactive:
            raise RuntimeError(
                f"{RUNTIME_KEY_ENV} is not set; runtime keys are never accepted on argv"
            )
        runtime_key = getpass.getpass(
            "OpenAI tunnel runtime API key (masked; not stored by ReasonFirst): "
        ).strip()
    if not runtime_key:
        raise RuntimeError("OpenAI tunnel runtime API key is required")

    alias = args.alias or (
        state.tunnel_runtime if state is not None and state.tunnel_runtime else DEFAULT_TUNNEL_ALIAS
    )
    result = connect_runtime(
        tunnel_id=tunnel_id,
        runtime_key=runtime_key,
        alias=alias,
        tunnel_client=tunnel_client,
        mcp_executable=mcp_executable,
    )
    if bool(result.get("ok")) and bool(result.get("ready")):
        persist_tunnel_state(
            tunnel_id=str(result["tunnel_id"]),
            alias=str(result["alias"]),
            tunnel_client=tunnel_client,
            state_path=args.state_file,
        )
    return {
        **result,
        "tunnel_client": tunnel_client,
        "mcp_executable": mcp_executable,
        "runtime_key_stored": False,
        "local_tunnel_ready": bool(result.get("ok")) and bool(result.get("ready")),
        "chatgpt_ready": False,
    }


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
    setup.add_argument(
        "--mode",
        choices=sorted(SETUP_MODES),
        default=None,
        help="Select standard, full-chat, or cli-only setup mode",
    )
    setup.add_argument("--gitlab-url", default=None, help="Prefill the GitLab URL")
    setup.add_argument("--project", default=None, help="Prefill namespace/project")
    setup.add_argument("--ref", default=None, help="Project ref (default: configured/main)")
    setup.add_argument(
        "--worker",
        choices=CANONICAL_WORKERS,
        default=None,
        help="Prefill the worker preference; secrets are never accepted on argv",
    )
    setup.add_argument(
        "--skip-chatgpt",
        action="store_true",
        help="Configure only the local GitLab/worker control plane in this run",
    )

    project = sub.add_parser("project", help="Manage explicit GitLab project grants")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_sub.add_parser("list", help="List locally allowlisted projects")
    project_add = project_sub.add_parser(
        "add",
        help="Verify a GitLab project/ref, then explicitly grant it locally",
    )
    project_add.add_argument("project")
    project_add.add_argument("--ref", default="")
    project_add.add_argument("--require-file", action="append", default=[])
    project_add.add_argument("--yes", action="store_true", help="Apply after successful preflight")
    project_add.add_argument("--json", action="store_true")

    worker = sub.add_parser("worker", help="Inspect or select the coding worker")
    worker_sub = worker.add_subparsers(dest="worker_command", required=True)
    worker_sub.add_parser("list", help="List detected coding workers")
    worker_use = worker_sub.add_parser("use", help="Persist a worker preference")
    worker_use.add_argument("backend", choices=CANONICAL_WORKERS)
    worker_use.add_argument("--json", action="store_true")
    worker_use.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
        help="Override the non-secret setup progress file",
    )
    tunnel = sub.add_parser("tunnel", help="Manage the private OpenAI MCP tunnel runtime")
    tunnel_sub = tunnel.add_subparsers(dest="tunnel_command", required=True)

    tunnel_install = tunnel_sub.add_parser(
        "install",
        help="Download and checksum-verify the latest official tunnel-client",
    )
    tunnel_install.add_argument("--json", action="store_true")
    tunnel_install.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
    )

    tunnel_connect = tunnel_sub.add_parser(
        "connect",
        help="Attach the packaged read MCP to an existing OpenAI tunnel",
    )
    tunnel_connect.add_argument("--tunnel-id", default=None)
    tunnel_connect.add_argument("--alias", default=None)
    tunnel_connect.add_argument(
        "--install",
        action="store_true",
        help="Install the official tunnel-client if it is missing",
    )
    tunnel_connect.add_argument(
        "--open-platform",
        action="store_true",
        help="Open Platform tunnel settings before prompting for a tunnel ID",
    )
    tunnel_connect.add_argument("--json", action="store_true")
    tunnel_connect.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
    )

    tunnel_status = tunnel_sub.add_parser("status", help="Inspect managed tunnel runtime health")
    tunnel_status.add_argument("--alias", default=None)
    tunnel_status.add_argument("--json", action="store_true")
    tunnel_status.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
    )

    tunnel_stop = tunnel_sub.add_parser("stop", help="Stop the managed tunnel runtime")
    tunnel_stop.add_argument("--alias", default=None)
    tunnel_stop.add_argument("--json", action="store_true")
    tunnel_stop.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
    )

    chatgpt = sub.add_parser("chatgpt", help="Prepare the ChatGPT-side acceptance handoff")
    chatgpt_sub = chatgpt.add_subparsers(dest="chatgpt_command", required=True)
    handoff = chatgpt_sub.add_parser(
        "handoff",
        help="Show the exact browser steps and live-read acceptance prompt",
    )
    handoff.add_argument("--project", default=None)
    handoff.add_argument("--ref", default="main")
    handoff.add_argument(
        "--open",
        action="store_true",
        help="Open Platform tunnel settings and ChatGPT in the default browser",
    )
    handoff.add_argument("--json", action="store_true")
    handoff.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_SETUP_STATE_PATH,
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "setup":
            if args.status:
                payload = build_setup_status(state_path=args.state_file)
                if args.json:
                    _print_json(payload)
                else:
                    _print_human_status(payload)
                return 0

            code, payload = _run_guided_setup(args)
            if args.json:
                _print_json(payload)
            elif code == 0:
                print("ReasonFirst local control setup complete.")
                print(str(payload.get("next") or ""))
            elif payload.get("cancelled"):
                print(str(payload.get("message") or "Cancelled."), file=sys.stderr)
            else:
                print("GitLab project preflight failed; no config was changed.", file=sys.stderr)
                _print_json(payload)
            return code

        if args.command == "project":
            settings = _load_settings_clean()
            if args.project_command == "list":
                _print_json(list_projects(settings))
                return 0

            plan = plan_project_add(
                settings,
                args.project,
                ref=args.ref,
                required_files=args.require_file,
            )
            if args.json and not args.yes:
                _print_json(plan)
                return 0 if bool(plan.get("ok")) else 1
            if not args.json:
                _print_project_plan(plan)
            if not bool(plan.get("ok")):
                if args.json:
                    _print_json(plan)
                return 1
            if bool(plan.get("already_allowed")):
                if args.json:
                    _print_json(plan)
                return 0

            approved = bool(args.yes)
            if not approved:
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "project add requires interactive confirmation on a TTY "
                        "or --yes after reviewing the successful preflight"
                    )
                approved = _confirm(
                    "Grant exactly this project in the local ReasonFirst allowlist?"
                )
            if not approved:
                return 1

            result = apply_project_add(settings, plan)
            if args.json:
                _print_json(result)
            else:
                print(f"Granted: {result['project']}")
                print(f"Config: {result['config_change']['config_file']}")
                if result.get("reload_required"):
                    print(
                        "The running read MCP may still have old configuration; "
                        "service reload is automated in Slice 3."
                    )
            return 0

        if args.command == "worker":
            if args.worker_command == "list":
                _print_json(
                    {
                        "ok": True,
                        "workers": detect_workers(),
                        "writes_performed": False,
                    }
                )
                return 0

            try:
                current_settings = _load_settings_clean()
            except Exception:
                current_settings = None
            effective_config = (
                current_settings.config_file
                if current_settings is not None
                else resolve_env_file().expanduser()
            )
            if effective_config.exists():
                ensure_persistent_config_target(effective_config)

            plan = plan_worker_use(args.backend)
            if not bool(plan.get("ok")):
                if args.json:
                    _print_json(plan)
                else:
                    print(str(plan.get("message") or "Worker unavailable"), file=sys.stderr)
                return 1
            result = apply_worker_use(plan)
            previous = load_setup_state(args.state_file) or SetupState()
            state = replace(
                previous,
                selected_worker=args.backend,
                completed_phases=tuple(
                    phase
                    for phase in ("system", "gitlab", "worker", "chatgpt-read", "bridge", "ready")
                    if phase in (set(previous.completed_phases) | {"worker"})
                ),
                installed_version=__version__,
                last_verified_at=datetime.now(timezone.utc).isoformat(),
            )
            save_setup_state(state, args.state_file)
            if args.json:
                _print_json(result)
            else:
                print(f"Worker preference saved: {args.backend}")
                print("Provider authentication remains a separate verification step.")
            return 0

        if args.command == "tunnel":
            if args.tunnel_command == "install":
                installed = install_official_tunnel_client()
                _save_tunnel_client_path(
                    state_path=args.state_file,
                    tunnel_client_path=str(installed["path"]),
                )
                if args.json:
                    _print_json(installed)
                else:
                    print(f"Installed tunnel-client: {installed['path']}")
                    print(f"Verified SHA256: {installed['sha256']}")
                return 0

            state = load_setup_state(args.state_file)
            tunnel_client = resolve_tunnel_client(state=state)
            if args.tunnel_command == "connect":
                result = _connect_tunnel_from_args(args)
                if args.json:
                    _print_json(result)
                else:
                    if result.get("local_tunnel_ready"):
                        print("ReasonFirst tunnel runtime is healthy and ready.")
                        print(f"Alias: {result.get('alias')}")
                        if result.get("ui_url"):
                            print(f"Local admin UI: {result.get('ui_url')}")
                        print(
                            "Runtime key was used only through the child environment and was not stored by ReasonFirst."
                        )
                        print("Next: reasonfirst chatgpt handoff --open")
                    else:
                        print("Tunnel runtime did not become ready.", file=sys.stderr)
                        _print_json(result)
                return 0 if result.get("local_tunnel_ready") else 1

            if not tunnel_client:
                raise RuntimeError(
                    "tunnel-client is not available; run 'reasonfirst tunnel install'"
                )
            alias = args.alias or (
                state.tunnel_runtime if state is not None and state.tunnel_runtime else DEFAULT_TUNNEL_ALIAS
            )
            if args.tunnel_command == "status":
                result = runtime_status(alias=alias, tunnel_client=tunnel_client)
            else:
                result = stop_runtime(alias=alias, tunnel_client=tunnel_client)
            if args.json:
                _print_json(result)
            else:
                if args.tunnel_command == "status":
                    print(f"Alias: {result.get('alias')}")
                    print(f"Process running: {result.get('process_running')}")
                    print(f"Healthy: {result.get('healthy')}")
                    print(f"Ready: {result.get('native_ready')}")
                    if result.get("ui_url"):
                        print(f"Local admin UI: {result.get('ui_url')}")
                else:
                    print(f"Stopped: {result.get('stopped')}")
            return 0 if result.get("ok") else 1

        if args.command == "chatgpt":
            state = load_setup_state(args.state_file)
            if state is None or not state.tunnel_id:
                raise RuntimeError(
                    "No verified tunnel is recorded; run 'reasonfirst tunnel connect' first"
                )
            settings = _load_settings_clean()
            project = _select_project_for_handoff(settings, args.project)
            payload = build_chatgpt_handoff(
                tunnel_id=state.tunnel_id,
                project=project,
                ref=args.ref,
            )
            if args.open:
                payload["browser"] = open_handoff_pages(
                    open_platform=True,
                    open_chatgpt=True,
                )
            if args.json:
                _print_json(payload)
            else:
                print("ChatGPT handoff")
                print(f"Tunnel ID: {payload['tunnel_id']}")
                for index, step in enumerate(payload["chatgpt_steps"], 1):
                    print(f"  {index}. {step}")
                print()
                print("Acceptance prompt:")
                print(payload["acceptance_prompt"])
                print()
                print(
                    "CHATGPT_READY remains false until that live ChatGPT probe succeeds."
                )
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
            print(f"ReasonFirst command failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
