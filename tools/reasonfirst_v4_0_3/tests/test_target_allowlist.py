from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.bridge_config import (
    BridgeConfigError,
    resolve_configured_target,
)


def main() -> None:
    cfg = {
        "version": 4,
        "defaults": {"target": "local", "codex_backend": "global-config-local"},
        "targets": {
            "local": {"type": "local", "codex_backend": "global-config-local"},
            "gpu-a": {
                "type": "ssh",
                "host": "gpu-a",
                "repo": "/srv/reasonfirst/project",
                "codex_backend": "desktop-proxy",
            },
        },
    }

    target = resolve_configured_target("gpu-a", config=cfg)
    assert target.type == "ssh"
    assert target.host == "gpu-a"
    assert target.repo == "/srv/reasonfirst/project"

    for unsafe in (
        {"type": "ssh", "host": "other", "repo": "/tmp/repo"},
        "other:/tmp/repo",
        "unknown-target",
    ):
        try:
            resolve_configured_target(unsafe, config=cfg)
        except BridgeConfigError:
            pass
        else:
            raise AssertionError(f"unconfigured target should be rejected: {unsafe!r}")

    print("configured execution-target trust boundary: OK")


if __name__ == "__main__":
    main()
