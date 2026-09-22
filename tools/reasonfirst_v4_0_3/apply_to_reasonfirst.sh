#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/reasonFirst" >&2
  exit 2
fi
SRC="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DST="$(cd "$1" && pwd)"
for need in pyproject.toml src/gitlab_agent/actual_coder_cli.py src/gitlab_agent/cli.py; do
  test -f "$DST/$need" || { echo "Not a compatible recent reasonFirst checkout; missing $need" >&2; exit 1; }
done

BACKUP="$HOME/.local/share/reasonfirst/backups"
mkdir -p "$BACKUP"

TARGET="$DST/tools/codex_web_bridge"
mkdir -p "$TARGET"
if [ -d "$TARGET/reasonfirst_codex_bridge" ]; then
  cp -R "$TARGET/reasonfirst_codex_bridge" "$BACKUP/reasonfirst_codex_bridge.$(date +%Y%m%d-%H%M%S).$$"
fi
rm -rf "$TARGET/reasonfirst_codex_bridge"
cp -R "$SRC/reasonfirst_codex_bridge" "$TARGET/"

# v4 primary MCP transport
cp "$SRC/reasonfirst_mcp_server.py" "$SRC/run_mcp_server.sh" "$SRC/run_reasonfirst.sh" \
   "$SRC/configure_v4.py" "$SRC/configure_v4.sh" \
   "$SRC/configure_v4_tunnel.sh" "$SRC/run_v4_tunnel.sh" \
   "$SRC/install_v4_runtime.sh" "$SRC/install_launch_agent.sh" \
   "$SRC/stage_v4_runtime.sh" "$SRC/set_local_no_proxy.sh" \
   "$SRC/install_web_relay_agent.sh" \
   "$SRC/install_tunnel_launch_agent.sh" "$SRC/install_tunnel_client.py" \
   "$SRC/install_chatgpt_desktop_plugin.py" \
   "$SRC/configure_gitlab_password.sh" "$TARGET/"
cp -R "$SRC/chatgpt_desktop_plugin" "$TARGET/"

# The existing Web connector relay forwards into the same MCP service.
cp "$SRC/github_control_relay.py" "$SRC/run_github_relay.sh" "$TARGET/"
cp "$SRC/configure_v3.sh" "$TARGET/configure_v3_legacy.sh"

chmod +x \
  "$TARGET/reasonfirst_mcp_server.py" "$TARGET/run_mcp_server.sh" "$TARGET/run_reasonfirst.sh" \
  "$TARGET/configure_v4.py" "$TARGET/configure_v4.sh" "$TARGET/configure_v4_tunnel.sh" "$TARGET/run_v4_tunnel.sh" \
  "$TARGET/install_v4_runtime.sh" "$TARGET/install_launch_agent.sh" "$TARGET/install_chatgpt_desktop_plugin.py" \
  "$TARGET/stage_v4_runtime.sh" \
  "$TARGET/set_local_no_proxy.sh" \
  "$TARGET/install_web_relay_agent.sh" \
  "$TARGET/install_tunnel_launch_agent.sh" "$TARGET/install_tunnel_client.py" \
  "$TARGET/configure_gitlab_password.sh" "$TARGET/github_control_relay.py" "$TARGET/run_github_relay.sh" \
  "$TARGET/configure_v3_legacy.sh"

cat <<EOF
Installed ReasonFirst v4.0.3 to: $TARGET

Primary execution state: one local MCP process
Unified configuration and startup:
  $TARGET/configure_v4.sh
Doctor:
  $TARGET/run_reasonfirst.sh --doctor
Local MCP URL: http://127.0.0.1:8765/mcp
Web connector: private GitHub control Issue (forwarded to the same MCP process)
Optional Secure MCP Tunnel uses RF_TUNNEL_ID and CONTROL_PLANE_API_KEY.
Codex plugin access does not enable MCP tools in ordinary ChatGPT Web/App chats.
ChatGPT direct access requires an MCP connection registered in ChatGPT.

Manual Web relay command:
  $TARGET/run_github_relay.sh
EOF
"$TARGET/configure_v4.sh"
