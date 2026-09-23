#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ "${1:-}" = "--plan" ]; then
  cat <<'EOF'
ReasonFirst bridge configuration plan (no changes made):
- create/update the dedicated v4 runtime
- back up and update ~/.config/reasonfirst/bridge.yaml
- back up and update only the ReasonFirst MCP section in ~/.codex/config.toml
- install/update the personal ReasonFirst Codex/Desktop plugin
- add loopback NO_PROXY entries for 127.0.0.1,localhost
- stage the login runtime under ~/.local/share/reasonfirst/v4-service
- install/start the local loopback MCP LaunchAgent

Optional operations are NOT enabled automatically:
- GitHub Issue relay: set RF_ENABLE_WEB_RELAY=true and rerun configure_v4.sh
- Secure MCP Tunnel: set RF_TUNNEL_ID and CONTROL_PLANE_API_KEY explicitly
EOF
  exit 0
fi

"$HERE/install_v4_runtime.sh"
PY="$HOME/.local/share/reasonfirst/v4-runtime/.venv/bin/python"
"$PY" "$HERE/configure_v4.py" --bridge-dir "$HERE" "$@"
"$PY" "$HERE/install_chatgpt_desktop_plugin.py" \
  --source "$HERE/chatgpt_desktop_plugin" --url "http://127.0.0.1:8765/mcp"
codex plugin add reasonfirst-v4@personal
"$HERE/set_local_no_proxy.sh"
"$HERE/stage_v4_runtime.sh"
"$HERE/install_launch_agent.sh"

case "${RF_ENABLE_WEB_RELAY:-false}" in
  1|true|TRUE|yes|YES|on|ON)
    if gh auth status >/dev/null 2>&1; then
      "$HERE/install_web_relay_agent.sh"
    else
      echo "RF_ENABLE_WEB_RELAY requested, but GitHub CLI is not authenticated." >&2
      exit 1
    fi
    ;;
  *)
    echo "GitHub Issue relay not enabled; set RF_ENABLE_WEB_RELAY=true explicitly if desired"
    ;;
esac

if [ -n "${RF_TUNNEL_ID:-}" ]; then
  : "${CONTROL_PLANE_API_KEY:?RF_TUNNEL_ID requires CONTROL_PLANE_API_KEY}"
  "$HERE/configure_v4_tunnel.sh" "$RF_TUNNEL_ID"
else
  echo "Secure MCP Tunnel not configured; set RF_TUNNEL_ID and CONTROL_PLANE_API_KEY explicitly if desired"
fi
