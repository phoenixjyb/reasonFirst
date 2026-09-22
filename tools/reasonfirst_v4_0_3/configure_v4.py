#!/usr/bin/env python3
"""Migrate ReasonFirst and register the single local MCP endpoint in Codex."""
from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib

import yaml


def strip_reasonfirst_mcp(value: str) -> str:
    out, skip = [], False
    header = re.compile(r"^\s*\[\[?([^]]+)\]\]?\s*(?:#.*)?$")
    for line in value.splitlines(keepends=True):
        if line.strip() in {"# BEGIN REASONFIRST V4 MANAGED", "# END REASONFIRST V4 MANAGED"}:
            continue
        match = header.match(line)
        if match:
            name = match.group(1).strip()
            skip = name == "mcp_servers.reasonfirst" or name.startswith("mcp_servers.reasonfirst.")
        if not skip:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")


def backup(path: Path, directory: Path) -> None:
    if path.exists():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(path, directory / f"{path.name}.{stamp}.bak")


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as f:
        tmp = Path(f.name)
        f.write(value)
        f.flush()
    try:
        tmp.chmod(0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge-dir", required=True)
    ap.add_argument("--bridge-config", default="~/.config/reasonfirst/bridge.yaml")
    ap.add_argument("--codex-config", default="~/.codex/config.toml")
    ap.add_argument("--backup-dir", default="~/.local/share/reasonfirst/backups")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--worker-backend",
        choices=("codex-cli", "copilot-cli", "codex-desktop"),
        default=os.getenv("RF_WORKER_BACKEND", "").strip() or None,
        help="Explicit coding backend; default preserves existing config or uses codex-desktop",
    )
    args = ap.parse_args()
    root = Path(args.bridge_dir).expanduser().resolve()
    if not (root / "run_mcp_server.sh").is_file():
        ap.error(f"Missing MCP launcher in {root}")
    if not 1 <= args.port <= 65535:
        ap.error("--port must be between 1 and 65535")
    bridge = Path(args.bridge_config).expanduser().resolve()
    codex = Path(args.codex_config).expanduser().resolve()
    backups = Path(args.backup_dir).expanduser().resolve()
    data = yaml.safe_load(bridge.read_text(encoding="utf-8")) if bridge.exists() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        ap.error("bridge.yaml must contain a YAML object")
    try:
        version = int(data.get("version", 3))
    except (ValueError, TypeError):
        ap.error("bridge.yaml version must be 3 or 4")
    if version not in {3, 4}:
        ap.error(f"Unsupported bridge config version: {version}")
    url = f"http://127.0.0.1:{args.port}/mcp"
    for section in ("control", "defaults", "targets", "mcp", "audit"):
        if section in data and not isinstance(data[section], dict):
            ap.error(f"bridge.yaml {section} must be an object")
    data["version"] = 4
    data.setdefault("control", {})["mode"] = "mcp"
    defaults = data.setdefault("defaults", {})
    defaults["target"] = defaults.get("target") or "local"
    worker_backend = (
        args.worker_backend
        or str(defaults.get("worker_backend") or "").strip()
        or "codex-desktop"
    )
    if worker_backend not in {"codex-cli", "copilot-cli", "codex-desktop"}:
        ap.error(
            "defaults.worker_backend must be codex-cli, copilot-cli, or codex-desktop"
        )
    defaults["worker_backend"] = worker_backend
    defaults["codex_backend"] = "global-config-local"
    targets = data.setdefault("targets", {})
    local = targets.setdefault("local", {})
    if not isinstance(local, dict):
        ap.error("bridge.yaml targets.local must be an object")
    local.update(
        type="local",
        worker_backend=worker_backend,
        codex_backend="global-config-local",
    )
    data.setdefault("mcp", {}).update(transport="streamable-http", host="127.0.0.1",
                                      port=args.port, path="/mcp")
    data.setdefault("audit", {}).setdefault("github_enabled", False)
    new_bridge = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if not bridge.exists() or bridge.read_text(encoding="utf-8") != new_bridge:
        backup(bridge, backups)
        atomic_write(bridge, new_bridge)
    codex_mcp_status = "not modified"
    if worker_backend == "codex-desktop":
        old = codex.read_text(encoding="utf-8") if codex.exists() else ""
        tomllib.loads(old)  # Never rewrite a malformed global config.
        base = strip_reasonfirst_mcp(old)
        managed = (
            "# BEGIN REASONFIRST V4 MANAGED\n"
            "[mcp_servers.reasonfirst]\n"
            f'url = "{url}"\n'
            "enabled = true\n"
            "# END REASONFIRST V4 MANAGED\n"
        )
        new_codex = base + ("\n" if base else "") + managed
        tomllib.loads(new_codex)
        if old != new_codex:
            backup(codex, backups)
            atomic_write(codex, new_codex)
            codex_mcp_status = "updated"
        else:
            codex_mcp_status = "already configured"
    print(f"ReasonFirst bridge: {bridge}")
    print(f"Worker backend: {worker_backend}")
    print(
        f"Codex global MCP: {url} ({codex_mcp_status})"
        if worker_backend == "codex-desktop"
        else f"Codex global MCP: not modified for {worker_backend}"
    )
    print(f"Backups: {backups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
