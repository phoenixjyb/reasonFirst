#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -z "${CONTROL_PLANE_API_KEY:-}" ]; then
  CONTROL_PLANE_API_KEY="$(security find-generic-password -a "$USER" -s com.reasonfirst.tunnel -w)"
  export CONTROL_PLANE_API_KEY
fi
: "${CONTROL_PLANE_API_KEY:?Set CONTROL_PLANE_API_KEY or save it in the macOS Keychain}"
TUNNEL_BIN="${TUNNEL_CLIENT_BIN:-$(command -v tunnel-client 2>/dev/null || true)}"
if [ -z "$TUNNEL_BIN" ] && [ -x "$HOME/.local/bin/tunnel-client" ]; then
  TUNNEL_BIN="$HOME/.local/bin/tunnel-client"
fi
if [ -z "$TUNNEL_BIN" ] || [ ! -x "$TUNNEL_BIN" ]; then
  echo "tunnel-client not found in configured/standard launchd paths" >&2
  exit 127
fi
exec "$TUNNEL_BIN" run --profile reasonfirst-v4
