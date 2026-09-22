#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from datetime import datetime


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    ap.add_argument("--install-root", default="")
    ap.add_argument("--marketplace", default="~/.agents/plugins/marketplace.json")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve()
    marketplace = Path(args.marketplace).expanduser().resolve()
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    install_root = (
        Path(args.install_root).expanduser().resolve()
        if args.install_root
        else marketplace.parent / "plugins"
    )
    dst = install_root / "reasonfirst-v4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        backup = dst.with_name(dst.name + ".backup." + datetime.now().strftime("%Y%m%d%H%M%S%f"))
        shutil.copytree(dst, backup)
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    template = (dst / "mcp.json.template").read_text(encoding="utf-8")
    (dst / "mcp.json").write_text(template.replace("__RF_MCP_URL__", args.url), encoding="utf-8")
    (dst / "mcp.json.template").unlink()

    data = load_json(
        marketplace,
        {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []},
    )
    if not isinstance(data, dict):
        data = {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}
    data.setdefault("name", "personal")
    interface = data.get("interface")
    if not isinstance(interface, dict):
        interface = {}
    interface.setdefault("displayName", "Personal")
    data["interface"] = interface
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        plugins = []
    plugins = [p for p in plugins if not (isinstance(p, dict) and p.get("name") == "reasonfirst-v4")]
    # Personal marketplace sources resolve from the home directory, not .agents/plugins.
    rel = os.path.relpath(dst, marketplace.parent.parent.parent)
    if not rel.startswith("./"):
        rel = "./" + rel
    plugins.append({
        "name": "reasonfirst-v4",
        "source": {"source": "local", "path": rel},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Developer Tools",
    })
    data["plugins"] = plugins
    if marketplace.exists():
        shutil.copy2(marketplace, marketplace.with_name(marketplace.name + ".backup." + datetime.now().strftime("%Y%m%d%H%M%S%f")))
    marketplace.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        marketplace.chmod(0o600)
    except OSError:
        pass
    print(f"Installed ChatGPT Desktop plugin: {dst}")
    print(f"Updated personal plugin marketplace: {marketplace}")
    print("Plugin source registered; configure_v4.sh installs and enables it with Codex CLI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
