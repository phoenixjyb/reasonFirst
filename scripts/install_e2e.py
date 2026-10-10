#!/usr/bin/env python3
"""Exercise packaged-tool and source/developer install routes on a clean CI host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EXECUTABLES = (
    "reasonfirst",
    "reasonfirst-gitlab-mcp",
    "reasonfirst-bridge-mcp",
    "reasonfirst-bridge-http",
    "reasonfirst-runtime",
    "actual-coder",
    "gitlab-agent",
)

MANAGED_HTTP_CASES = frozenset({
    "test_async_turn_survives_http_reply_and_interrupt_ack",
    "test_bound_full_chat_policy_and_runtime",
    "test_bound_read_only_policy_and_runtime",
    "test_bound_full_chat_helpers_keep_settings_and_launch_inputs",
    "test_bound_read_only_helpers_keep_settings_and_launch_inputs",
    "test_bound_read_only_api_trust_survives_helper_transfer_and_fails_on_drift",
    "test_bound_full_chat_api_trust_survives_helper_transfer_and_fails_on_drift",
    "test_disconnect_keeps_work_counted_and_then_unknown",
    "test_full_chat_catalog_and_maintenance_cycle",
    "test_http_and_controller_work_block_maintenance_until_reply",
    "test_http_body_is_reserved_before_controller_dispatch",
    "test_import_origins_match_selected_route",
    "test_invalid_configuration_cleans_failed_startup",
    "test_occupied_listener_survives_failed_startup",
    "test_protocol_and_method_guards_on_real_listener",
    "test_read_only_catalog_and_maintenance_cycle",
})
MANAGED_HTTP_REPORT_LIMIT = 8192


def _managed_http_report(raw: bytes, *, route: str, returncode: int) -> dict[str, object]:
    """Accept only the bounded fixture schema; never echo process output."""
    failure = "managed HTTP native fixture returned an invalid report; raw output withheld"
    if type(raw) is not bytes or not 0 < len(raw) <= MANAGED_HTTP_REPORT_LIMIT:
        raise RuntimeError(failure)
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate_key")
            obj[key] = value
        return obj

    try:
        payload = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise RuntimeError(failure) from None
    boolean_fields = {
        "ok", "module_origins_verified", "real_mcp_calls_exercised",
        "all_tools_denied_during_maintenance", "working_service_touched", "activation_tested",
        "selected_policy_binding_exercised", "runtime_observation_exercised",
        "child_launch_binding_exercised",
        "api_trust_binding_exercised",
    }
    count_fields = {"tests_run", "expected_tests", "failures", "errors", "skipped"}
    if (type(payload) is not dict
            or set(payload) != boolean_fields | count_fields | {
                "operation", "route", "failed_cases", "modes",
            }
            or payload["operation"] != "managed-http-native-acceptance"
            or route not in {"packaged", "source"} or payload["route"] != route
            or payload["modes"] != ["read-only", "full-chat"]
            or any(type(payload[key]) is not bool for key in boolean_fields)
            or any(type(payload[key]) is not int or not 0 <= payload[key] <= 2 * len(MANAGED_HTTP_CASES)
                   for key in count_fields)
            or payload["expected_tests"] != len(MANAGED_HTTP_CASES)
            or payload["tests_run"] > payload["expected_tests"]
            or payload["working_service_touched"] is not False
            or payload["activation_tested"] is not False):
        raise RuntimeError(failure)
    cases = payload["failed_cases"]
    if (type(cases) is not list or len(cases) > len(MANAGED_HTTP_CASES) + 1
            or any(type(name) is not str or name not in MANAGED_HTTP_CASES | {"fixture_failed"}
                   for name in cases)
            or cases != sorted(set(cases))):
        raise RuntimeError(failure)
    ok = payload["ok"]
    if ((returncode == 0) is not ok
            or any(payload[key] is not ok for key in (
                "module_origins_verified", "real_mcp_calls_exercised",
                "all_tools_denied_during_maintenance",
                "selected_policy_binding_exercised", "runtime_observation_exercised",
                "child_launch_binding_exercised",
                "api_trust_binding_exercised",
            ))
            or (ok and (payload["tests_run"] != payload["expected_tests"]
                        or any(payload[key] != 0 for key in ("failures", "errors", "skipped"))
                        or cases))):
        raise RuntimeError(failure)
    return payload


def _verify_managed_http(
    *, python: Path, source_root: Path, outside: Path, env: dict[str, str],
    route: str, expected_root: Path,
) -> None:
    try:
        result = subprocess.run(
            [str(python), "-I", "-B", str(source_root / "tests/test_managed_http_integration.py"),
             "--native-route", route, "--expected-root", str(expected_root)],
            cwd=outside, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False, timeout=180,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("managed HTTP native fixture timed out; raw output withheld") from None
    report = _managed_http_report(result.stdout, route=route, returncode=result.returncode)
    print(json.dumps(report, sort_keys=True), flush=True)
    if report["ok"] is not True:
        raise RuntimeError(f"{route}: managed HTTP native acceptance failed; raw output withheld")


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {argv!r}\n"
            f"stdout={proc.stdout[-4000:]}\nstderr={proc.stderr[-4000:]}"
        )
    return proc


def _exe(bin_dir: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    path = bin_dir / f"{name}{suffix}"
    if not path.is_file():
        raise RuntimeError(f"missing installed executable: {path}")
    return path


def _clean_child_env(root: Path, bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if (
            key.startswith("GITLAB_")
            or key.startswith("RF_")
            or key.startswith("REASONFIRST_")
            or key in {"CONTROL_PLANE_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"}
        ):
            env.pop(key, None)

    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["UV_TOOL_BIN_DIR"] = str(bin_dir)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


def _assert_status(payload: dict[str, object], *, route: str) -> None:
    if payload.get("ok") is not True:
        raise RuntimeError(f"{route}: status did not report ok=true")
    if payload.get("mutating") is not False:
        raise RuntimeError(f"{route}: status must be non-mutating")
    readiness = payload.get("readiness")
    if not isinstance(readiness, dict):
        raise RuntimeError(f"{route}: missing readiness object")
    if readiness.get("ready") is not False:
        raise RuntimeError(f"{route}: clean-machine status must not claim READY")
    if readiness.get("chatgpt_connection") != "not_verified":
        raise RuntimeError(f"{route}: ChatGPT readiness must remain unverified")


def verify_packaged_route(*, wheel: Path, root: Path, source_root: Path) -> None:
    tool_root = root / "uv-tools"
    bin_dir = root / "bin"
    cache = root / "uv-cache"
    outside = root / "outside-source-tree"
    outside.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["UV_TOOL_DIR"] = str(tool_root)
    env["UV_TOOL_BIN_DIR"] = str(bin_dir)
    env["UV_CACHE_DIR"] = str(cache)

    _run(
        ["uv", "tool", "install", "--force", str(wheel.resolve())],
        cwd=outside,
        env=env,
    )

    for name in EXECUTABLES:
        _exe(bin_dir, name)

    child_env = _clean_child_env(root / "packaged-child", bin_dir)
    _run([str(_exe(bin_dir, "reasonfirst")), "--version"], cwd=outside, env=child_env)
    status = _run(
        [
            str(_exe(bin_dir, "reasonfirst")),
            "setup",
            "--status",
            "--json",
            "--state-file",
            str(root / "packaged-state.yaml"),
        ],
        cwd=outside,
        env=child_env,
    )
    payload = json.loads(status.stdout)
    _assert_status(payload, route="packaged")

    inventory = _run(
        [str(_exe(bin_dir, "reasonfirst")), "bridge", "inventory", "--json"],
        cwd=outside,
        env=child_env,
    )
    inventory_payload = json.loads(inventory.stdout)
    if inventory_payload.get("ok") is not True:
        raise RuntimeError("packaged: Bridge inventory failed")

    _run([str(_exe(bin_dir, "reasonfirst-runtime")), "--help"], cwd=outside, env=child_env)
    _run([str(_exe(bin_dir, "reasonfirst-bridge-http")), "--help"], cwd=outside, env=child_env)
    inspection = _run(
        [str(_exe(bin_dir, "reasonfirst-bridge-http")), "--inspect",
         "--host", "127.0.0.1", "--port", "8765", "--path", "/mcp",
         "--mode", "read-only", "--control-policy", "disabled"],
        cwd=outside, env=child_env,
    )
    described = json.loads(inspection.stdout)
    if described.get("mutating") is not False or described.get("server_started") is not False:
        raise RuntimeError("packaged: HTTP inspection is not static")

    # Execute the native wire-level tests with the actual clean wheel runtime,
    # outside the checkout and with isolated Python imports. This verifies the
    # uv environment selected by this harness; not an arbitrary user's layout.
    runtime = tool_root / "chatgpt-selfhosted-gitlab-mcp"
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError("packaged: installed tool interpreter not found")
    child_env["RF_HTTP_TEST_WHEEL_ROOT"] = str(runtime)
    result = _run(
        [str(python), "-I", "-B", str(source_root / "tests/test_bridge_http_integration.py")],
        cwd=outside, env=child_env,
    )
    print(result.stdout)
    print(result.stderr)

    _verify_managed_http(
        python=python, source_root=source_root, outside=outside, env=child_env,
        route="packaged", expected_root=runtime,
    )

    staging = _run(
        [str(python), "-I", "-B", str(source_root / "scripts/runtime_e2e.py"),
         "--wheel", str(wheel.resolve()), "--source-root", str(source_root)],
        cwd=outside, env=child_env,
    )
    print(staging.stdout)
    print(staging.stderr)


def verify_source_route(*, source_root: Path, root: Path) -> None:
    env = _clean_child_env(root / "source-child", Path(shutil.which("uv") or "").parent)
    # Source/developer use stays intentionally separate from uv-tool installation.
    _run(["uv", "sync", "--python", "3.12"], cwd=source_root, env=env)
    _run(["uv", "run", "reasonfirst", "--version"], cwd=source_root, env=env)
    status = _run(
        [
            "uv",
            "run",
            "reasonfirst",
            "setup",
            "--status",
            "--json",
            "--state-file",
            str(root / "source-state.yaml"),
        ],
        cwd=source_root,
        env=env,
    )
    payload = json.loads(status.stdout)
    _assert_status(payload, route="source")

    inventory = _run(
        ["uv", "run", "reasonfirst", "bridge", "inventory", "--json"],
        cwd=source_root,
        env=env,
    )
    inventory_payload = json.loads(inventory.stdout)
    if inventory_payload.get("ok") is not True:
        raise RuntimeError("source: Bridge inventory failed")

    selected = _run(
        ["uv", "run", "python", "-I", "-B", "-c", "import sys; print(sys.executable)"],
        cwd=source_root, env=env,
    ).stdout.strip()
    python = Path(selected)
    if "\n" in selected or "\r" in selected or not python.is_absolute() or not python.is_file():
        raise RuntimeError("source: developer interpreter could not be selected")
    outside = root / "outside-source-tree"
    outside.mkdir(parents=True, exist_ok=True)
    _verify_managed_http(
        python=python, source_root=source_root, outside=outside, env=env,
        route="source", expected_root=source_root / "src",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", default="dist")
    parser.add_argument("--source-root", default=".")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    wheels = sorted(Path(args.dist_dir).resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one wheel, found {len(wheels)}")

    with tempfile.TemporaryDirectory(prefix="reasonfirst-install-e2e-") as td:
        root = Path(td)
        verify_packaged_route(wheel=wheels[0], root=root, source_root=source_root)
        verify_source_route(source_root=source_root, root=root)

    print("ReasonFirst install E2E: OK (packaged + source routes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
