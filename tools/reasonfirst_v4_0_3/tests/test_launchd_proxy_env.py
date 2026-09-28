from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "sync_launchd_proxy_env.sh"

if sys.platform != "darwin":
    result = subprocess.run(
        ["bash", str(HELPER)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "only for macOS" in result.stderr
else:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        log = root / "launchctl.log"

        launchctl = bin_dir / "launchctl"
        launchctl.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$1:$2\" >> \"$RF_TEST_LAUNCHCTL_LOG\"\nexit 0\n",
            encoding="utf-8",
        )
        launchctl.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["RF_TEST_LAUNCHCTL_LOG"] = str(log)
        env["https_proxy"] = "http://proxy.example:8080"
        env["no_proxy"] = "127.0.0.1,localhost"
        env.pop("HTTPS_PROXY", None)

        result = subprocess.run(
            ["bash", str(HELPER), "sync"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "https_proxy=propagated" in result.stdout
        assert "HTTPS_PROXY=unchanged" in result.stdout
        assert "http://proxy.example:8080" not in result.stdout

        calls = log.read_text(encoding="utf-8")
        assert "setenv:https_proxy" in calls
        assert "setenv:no_proxy" in calls
        assert "unsetenv:HTTPS_PROXY" not in calls

        cleared = subprocess.run(
            ["bash", str(HELPER), "clear"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert cleared.returncode == 0, (cleared.stdout, cleared.stderr)
        assert "https_proxy=cleared" in cleared.stdout
        calls = log.read_text(encoding="utf-8")
        assert "unsetenv:https_proxy" in calls

print("launchd proxy environment sync: OK")
