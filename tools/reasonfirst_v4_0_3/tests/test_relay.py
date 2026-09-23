from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import github_control_relay as relay


class FakeCtrl:
    def dispatch_request(self, **kwargs):
        return {"ok": True, "auto_routed": True, **kwargs}

    def artifact_descriptor(self, **kwargs):
        return {
            "workspace_id": "abc123def456",
            "absolute_path": str(self.file),
            "path": "reports/curve.png",
            "name": "curve.png",
            "extension": ".png",
            "size": self.file.stat().st_size,
            "mime_type": "image/png",
        }



def test_gh_retry_behavior():
    class Proc:
        def __init__(self, code, stdout="", stderr=""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr

    original_run = relay.subprocess.run
    original_sleep = relay.time.sleep
    calls = []
    sleeps = []

    def transient_then_ok(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return Proc(1, stderr='Get "https://api.github.com/...": EOF')
        return Proc(0, stdout='{"ok": true}')

    relay.subprocess.run = transient_then_ok
    relay.time.sleep = lambda seconds: sleeps.append(seconds)
    try:
        result = relay.gh_json(["repos/example/private"], max_attempts=3)
    finally:
        relay.subprocess.run = original_run
        relay.time.sleep = original_sleep
    assert result == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [1.0]

    calls.clear()
    def permission_error(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc(1, stderr="gh: Resource not accessible by integration (HTTP 403)")

    relay.subprocess.run = permission_error
    relay.time.sleep = lambda seconds: sleeps.append(seconds)
    try:
        try:
            relay.gh_json(["repos/example/private"], max_attempts=5)
        except relay.BridgeError as exc:
            assert "403" in str(exc)
        else:
            raise AssertionError("expected BridgeError")
    finally:
        relay.subprocess.run = original_run
        relay.time.sleep = original_sleep
    assert len(calls) == 1


def main():
    test_gh_retry_behavior()
    ctrl = FakeCtrl()
    routed = relay.dispatch(ctrl, {
        "op": "dispatch",
        "gitlab_url": "https://gitlab.example/group/project",
        "module": "src/perception",
        "request": "optimize",
    })
    assert routed["auto_routed"] is True

    with tempfile.TemporaryDirectory() as tmp:
        ctrl.file = Path(tmp) / "curve.png"
        ctrl.file.write_bytes(b"fake-png")
        seen = {}
        original = relay.gh_json
        def fake_gh(args, input_obj=None):
            seen["args"] = args
            seen["input"] = input_obj
            return {"content": {"sha": "abc", "html_url": "https://example", "download_url": "https://example/raw"}}
        relay.gh_json = fake_gh
        try:
            result = relay.publish_artifact("example-user/reasonfirst-control", ctrl, {"path": "reports/curve.png"})
        finally:
            relay.gh_json = original
        assert result["ok"] is True
        assert result["published"]["sha"] == "abc"
        assert seen["input"]["content"]
        assert "repos/example-user/reasonfirst-control/contents/artifacts/" in seen["args"][0]
    print("relay dispatch/publish flow: OK")


if __name__ == "__main__":
    main()
