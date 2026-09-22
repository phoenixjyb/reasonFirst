from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.controller import BridgeController


def tool_names() -> set[str]:
    namespaces = BridgeController._remote_dynamic_tools()
    tools = namespaces[0]["tools"]
    return {
        str(item.get("name"))
        for item in tools
        if isinstance(item, dict) and item.get("name")
    }


def main() -> None:
    old = os.environ.pop("RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", None)
    try:
        assert "commit_push" not in tool_names()
        os.environ["RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"] = "true"
        assert "commit_push" in tool_names()
    finally:
        if old is None:
            os.environ.pop("RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH", None)
        else:
            os.environ["RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH"] = old

    print("remote push dynamic-tool visibility: OK")


if __name__ == "__main__":
    main()
