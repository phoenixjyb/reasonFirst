from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_launch_agent.sh"

with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    bin_dir = home / "bin"
    bin_dir.mkdir()
    calls = home / "launchctl.log"

    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$RF_TEST_LAUNCHCTL_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    launchctl.chmod(0o755)

    curl = bin_dir / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["RF_TEST_LAUNCHCTL_LOG"] = str(calls)
    env["RF_SERVICE_ROOT"] = str(home / "service")
    env["RF_MCP_HEALTH_TIMEOUT_SECONDS"] = "2"
    env["RF_MCP_READ_ONLY"] = "false"

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "MCP health: ready" in result.stdout
    assert "Read-only MCP mode: false" in result.stdout
    assert "Launchd proxy sync from current shell: false" in result.stdout

    plist = home / "Library" / "LaunchAgents" / "com.reasonfirst.v4-mcp.plist"
    text = plist.read_text(encoding="utf-8")
    assert "<key>RF_MCP_READ_ONLY</key>" in text
    assert "<string>false</string>" in text

print("MCP LaunchAgent installer: OK")
