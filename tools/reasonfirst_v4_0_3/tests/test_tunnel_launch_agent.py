from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install_tunnel_launch_agent.sh"

with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)

    tunnel_bin = bin_dir / "tunnel-client"
    tunnel_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    tunnel_bin.chmod(0o755)

    launchctl = bin_dir / "launchctl"
    launchctl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launchctl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("TUNNEL_CLIENT_BIN", None)

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)

    plist = home / "Library" / "LaunchAgents" / "com.reasonfirst.v4-tunnel.plist"
    text = plist.read_text(encoding="utf-8")
    assert "<key>TUNNEL_CLIENT_BIN</key>" in text
    assert f"<string>{tunnel_bin}</string>" in text
    assert "Installed and started com.reasonfirst.v4-tunnel" in result.stdout

with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    launchctl = bin_dir / "launchctl"
    launchctl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launchctl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TUNNEL_CLIENT_BIN"] = str(home / "missing-tunnel-client")

    result = subprocess.run(
        ["bash", str(INSTALLER)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 127, (result.stdout, result.stderr)
    assert "tunnel-client not found" in result.stderr

print("tunnel LaunchAgent installer: OK")
