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
