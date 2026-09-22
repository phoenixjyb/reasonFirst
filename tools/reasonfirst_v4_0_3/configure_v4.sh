#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
"$HERE/install_v4_runtime.sh"
PY="$HOME/.local/share/reasonfirst/v4-runtime/.venv/bin/python"
"$PY" "$HERE/configure_v4.py" --bridge-dir "$HERE" "$@"
"$PY" "$HERE/install_chatgpt_desktop_plugin.py" \
  --source "$HERE/chatgpt_desktop_plugin" --url "http://127.0.0.1:8765/mcp"
codex plugin add reasonfirst-v4@personal
"$HERE/set_local_no_proxy.sh"
"$HERE/stage_v4_runtime.sh"
"$HERE/install_launch_agent.sh"
if gh auth status >/dev/null 2>&1; then
  "$HERE/install_web_relay_agent.sh"
else
  echo "Web relay pending: run gh auth login to restore the existing ChatGPT Web connector route"
fi
if [ -n "${RF_TUNNEL_ID:-}" ]; then
  "$HERE/configure_v4_tunnel.sh" "$RF_TUNNEL_ID"
else
  echo "Optional Web tunnel not configured; the private GitHub connector route remains available"
fi
