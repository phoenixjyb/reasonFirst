from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.controller import BridgeController


def tool_names(target=None) -> set[str]:
    namespaces = BridgeController._remote_dynamic_tools(target)
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

    validation_target = ExecutionTarget(
        type="ssh",
        name="gpu",
        host="gpu",
        repo="/srv/project",
        codex_backend="desktop-proxy",
        validation_engine="docker",
        validation_image="example/validator:1",
        validation_allowed_executables=("pytest",),
    )
    assert "run" in tool_names(validation_target)
    assert "run" not in tool_names()

    print("remote push/validation dynamic-tool visibility: OK")


if __name__ == "__main__":
    main()
