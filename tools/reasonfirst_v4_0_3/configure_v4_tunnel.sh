#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 tunnel_xxx" >&2
  echo "CONTROL_PLANE_API_KEY must already be exported in the shell." >&2
  exit 2
fi
TUNNEL_BIN="${TUNNEL_CLIENT_BIN:-$(command -v tunnel-client 2>/dev/null || true)}"
if [ -z "$TUNNEL_BIN" ] && [ -x "$HOME/.local/bin/tunnel-client" ]; then TUNNEL_BIN="$HOME/.local/bin/tunnel-client"; fi
if [ -z "$TUNNEL_BIN" ]; then
  python3 "$HERE/install_tunnel_client.py"
  TUNNEL_BIN="$HOME/.local/bin/tunnel-client"
fi
: "${CONTROL_PLANE_API_KEY:?export CONTROL_PLANE_API_KEY before configuring the tunnel}"
TUNNEL_ID="$1"
"$TUNNEL_BIN" init \
  --profile reasonfirst-v4 \
  --tunnel-id "$TUNNEL_ID" \
  --mcp-server-url "http://127.0.0.1:8765/mcp"
"$TUNNEL_BIN" doctor --profile reasonfirst-v4 --explain
security add-generic-password -U -a "$USER" -s com.reasonfirst.tunnel -w "$CONTROL_PLANE_API_KEY" >/dev/null
"$HERE/install_tunnel_launch_agent.sh"
cat <<EOF
Configured Secure MCP Tunnel profile: reasonfirst-v4
Run it with:
  $HERE/run_v4_tunnel.sh
Then create/select a developer-mode ChatGPT app using Tunnel and tunnel id: $TUNNEL_ID
EOF
