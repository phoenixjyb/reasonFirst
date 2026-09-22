from __future__ import annotations

import os
from pathlib import Path
import tempfile

import yaml

from reasonfirst_codex_bridge.controller import BridgeController, BridgeError


def main() -> None:
    old = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "bridge.yaml"
            config.write_text(
                yaml.safe_dump(
                    {
                        "version": 4,
                        "defaults": {
                            "target": "local",
                            "worker_backend": "codex-desktop",
                            "codex_backend": "global-config-local",
                        },
                        "targets": {
                            "local": {
                                "type": "local",
                                "worker_backend": "codex-desktop",
                                "codex_backend": "global-config-local",
                            },
                            "gpu-a": {
                                "type": "ssh",
                                "host": "gpu-a",
                                "repo": "/work/project",
                                "worker_backend": "codex-desktop",
                                "codex_backend": "desktop-proxy",
                            },
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            os.environ["RF_BRIDGE_CONFIG"] = str(config)
            os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(root / "state")
            ctrl = BridgeController()
            try:
                target = ctrl._resolve_requested_target("gpu-a")
                assert target.type == "ssh"
                assert target.host == "gpu-a"
                assert target.repo == "/work/project"

                for bad in (
                    {"type": "ssh", "host": "evil", "repo": "/tmp/repo"},
                    "evil:/tmp/repo",
                    "unknown-target",
                ):
                    try:
                        ctrl._resolve_requested_target(bad)
                    except BridgeError:
                        pass
                    else:
                        raise AssertionError(f"unconfigured execution target accepted: {bad!r}")
            finally:
                ctrl.close()
    finally:
        os.environ.clear()
        os.environ.update(old)

    print("configured execution target allowlist: OK")


if __name__ == "__main__":
    main()
