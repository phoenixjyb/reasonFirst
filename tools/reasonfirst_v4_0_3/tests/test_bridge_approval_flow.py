from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reasonfirst_codex_bridge.controller import BridgeController


def wait_for_pending(ctrl: BridgeController, thread_id: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = ctrl.pending_approvals(thread_id=thread_id)
        if data["count"]:
            return data
        time.sleep(0.02)
    raise AssertionError("approval request never became pending")


def main() -> None:
    old = dict(os.environ)
    with tempfile.TemporaryDirectory() as td:
        os.environ["RF_CODEX_BRIDGE_STATE_DIR"] = str(Path(td) / "state")
        os.environ["RF_APPROVAL_TIMEOUT_SECONDS"] = "30"
        try:
            ctrl = BridgeController()
            thread_id = "thr_approval"
            app_key = "local:test"
            ctrl._state["sessions"][thread_id] = {
                "thread_id": thread_id,
                "workspace_id": "ws_test",
                "app_key": app_key,
                "pending_approvals": {},
                "approval_history": [],
                "updated_at": int(time.time()),
            }
            ctrl._save_state()

            result_box: list[dict] = []
            msg = {
                "id": 77,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": thread_id,
                    "turnId": "turn_1",
                    "itemId": "cmd_1",
                    "command": "pytest -q",
                    "cwd": "/tmp/worktree",
                    "reason": "run tests",
                    "availableDecisions": ["accept", "decline"],
                },
            }

            worker = threading.Thread(
                target=lambda: result_box.append(
                    ctrl._handle_app_approval_request(app_key, msg)
                ),
                daemon=True,
            )
            worker.start()
            pending = wait_for_pending(ctrl, thread_id)
            assert pending["count"] == 1
            assert pending["pending"][0]["request_id"] == 77
            assert pending["pending"][0]["method"] == msg["method"]

            decision = ctrl.resolve_approval(
                thread_id=thread_id,
                request_id=77,
                approve=True,
            )
            assert decision["approved"] is True
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert result_box == [{"decision": "accept"}]
            assert ctrl.pending_approvals(thread_id=thread_id)["count"] == 0

            history = ctrl._session(thread_id)["approval_history"]
            assert history[-1]["status"] == "resolved"
            assert history[-1]["result"] == {"decision": "accept"}

            # Permission grants return only the exact requested profile and
            # default to turn scope, never a silent session-wide grant.
            permission_box: list[dict] = []
            pmsg = {
                "id": 78,
                "method": "item/permissions/requestApproval",
                "params": {
                    "threadId": thread_id,
                    "turnId": "turn_2",
                    "itemId": "perm_1",
                    "reason": "need extra workspace path",
                    "permissions": {
                        "fileSystem": {"write": ["/tmp/worktree/generated"]}
                    },
                },
            }
            pworker = threading.Thread(
                target=lambda: permission_box.append(
                    ctrl._handle_app_approval_request(app_key, pmsg)
                ),
                daemon=True,
            )
            pworker.start()
            wait_for_pending(ctrl, thread_id)
            ctrl.resolve_approval(
                thread_id=thread_id,
                request_id=78,
                approve=True,
                for_session=False,
            )
            pworker.join(timeout=2)
            assert permission_box == [{
                "scope": "turn",
                "permissions": {
                    "fileSystem": {"write": ["/tmp/worktree/generated"]}
                },
            }]
            ctrl.close()
        finally:
            os.environ.clear()
            os.environ.update(old)

    print("App Server bridge approval lifecycle: OK")


if __name__ == "__main__":
    main()
