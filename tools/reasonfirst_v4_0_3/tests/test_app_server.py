from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reasonfirst_codex_bridge.app_server import AppServerClient


def test_callback_failure_does_not_kill_reader():
    fake = str(ROOT / "tests" / "fake_codex.py")
    seen = {"raised": False}
    def flaky(event):
        if not seen["raised"]:
            seen["raised"] = True
            raise RuntimeError("synthetic callback failure")
    client = AppServerClient(codex_bin=fake, event_handler=flaky)
    tid = client.start_thread(cwd=str(ROOT))
    assert tid == "thr_fake"
    client.close()



def test_dynamic_tool_roundtrip():
    fake = str(ROOT / "tests" / "fake_codex.py")
    events = []
    seen = []
    def handle(msg):
        seen.append(msg)
        return {
            "contentItems": [{"type": "inputText", "text": '{"ok":true,"head":"abc"}'}],
            "success": True,
        }
    client = AppServerClient(
        codex_bin=fake,
        event_handler=events.append,
        server_request_handler=handle,
    )
    tools = [{
        "type": "namespace",
        "name": "reasonfirst_remote",
        "description": "remote",
        "tools": [{
            "type": "function",
            "name": "status",
            "description": "status",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        }],
    }]
    tid = client.start_thread(cwd=str(ROOT), dynamic_tools=tools, sandbox_mode="read-only")
    client.start_turn(thread_id=tid, cwd=str(ROOT), prompt="use tool", sandbox_mode="read-only")
    time.sleep(0.15)
    assert seen and seen[0].get("method") == "item/tool/call"
    assert any(e.get("method") == "turn/completed" for e in events)
    client.close()

def main():
    test_callback_failure_does_not_kill_reader()
    test_dynamic_tool_roundtrip()
    events = []
    fake = str(ROOT / "tests" / "fake_codex.py")
    client = AppServerClient(codex_bin=fake, event_handler=events.append)
    tid = client.start_thread(cwd=str(ROOT))
    turn = client.start_turn(thread_id=tid, cwd=str(ROOT), prompt="test")
    client.set_thread_name(tid, "[ReasonFirst] group/project - optimize-src")
    client.set_thread_goal(tid, "Keep tests green")
    client.update_thread_metadata(tid, branch="chatgpt/task", sha="abc123", origin_url="https://gitlab.example/group/project.git")
    time.sleep(0.05)
    assert tid == "thr_fake"
    assert turn == "turn_fake"
    assert any(e.get("method") == "turn/completed" for e in events)
    thread = client.read_thread(tid)["thread"]
    assert thread["name"].startswith("[ReasonFirst]")
    assert thread["isPinned"] is True
    assert thread["gitInfo"]["branch"] == "chatgpt/task"
    listed = client.list_threads(search_term="ReasonFirst")
    assert listed["data"][0]["id"] == tid
    client.steer(thread_id=tid, turn_id=turn, prompt="focus")
    client.interrupt(thread_id=tid, turn_id=turn)
    client.close()
    print("app-server metadata smoke test: OK")


if __name__ == "__main__":
    main()
