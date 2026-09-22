#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PLAN=false
WORKER_BACKEND=""
INSTALL_PLUGIN=false
ENABLE_LOGIN_SERVICE=false
ENABLE_WEB_RELAY=false
TUNNEL_ID=""

usage() {
  cat <<'EOF'
Usage: configure_v4.sh [options]

Core configuration is the only default action. Persistent/global integrations
must be explicitly requested.

Options:
  --plan                         Show planned actions without changing files/services
  --worker-backend BACKEND       codex-cli | copilot-cli | codex-desktop
  --install-codex-plugin         Install/update the personal ReasonFirst plugin
  --enable-login-service         Install/start the local MCP LaunchAgent
  --enable-web-relay             Install/start the private GitHub Issue relay
  --configure-tunnel TUNNEL_ID   Configure/install the optional Secure MCP Tunnel
  -h, --help                     Show this help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --plan)
      PLAN=true
      shift
      ;;
    --worker-backend)
      [ "$#" -ge 2 ] || { echo "--worker-backend requires a value" >&2; exit 2; }
      WORKER_BACKEND="$2"
      shift 2
      ;;
    --install-codex-plugin)
      INSTALL_PLUGIN=true
      shift
      ;;
    --enable-login-service)
      ENABLE_LOGIN_SERVICE=true
      shift
      ;;
    --enable-web-relay)
      ENABLE_WEB_RELAY=true
      shift
      ;;
    --configure-tunnel)
      [ "$#" -ge 2 ] || { echo "--configure-tunnel requires a tunnel id" >&2; exit 2; }
      TUNNEL_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$WORKER_BACKEND" in
  ""|codex-cli|copilot-cli|codex-desktop) ;;
  *) echo "Unsupported worker backend: $WORKER_BACKEND" >&2; exit 2 ;;
esac

if [ "$ENABLE_WEB_RELAY" = true ] && [ "$ENABLE_LOGIN_SERVICE" != true ]; then
  echo "--enable-web-relay requires --enable-login-service" >&2
  exit 2
fi

if [ -n "$TUNNEL_ID" ] && [ "$ENABLE_LOGIN_SERVICE" != true ]; then
  echo "--configure-tunnel requires --enable-login-service" >&2
  exit 2
fi

if [ "$PLAN" = true ]; then
  cat <<EOF
ReasonFirst v4 setup plan (no changes will be made)
- install/update isolated v4 runtime: yes
- migrate bridge config and ReasonFirst Codex MCP entry: yes
- worker backend: ${WORKER_BACKEND:-preserve existing or codex-desktop}
- install/update personal Codex/Desktop plugin: $INSTALL_PLUGIN
- install/start local MCP login service: $ENABLE_LOGIN_SERVICE
- install/start GitHub Issue web relay: $ENABLE_WEB_RELAY
- configure Secure MCP Tunnel: ${TUNNEL_ID:-no}
EOF
  exit 0
fi

"$HERE/install_v4_runtime.sh"
PY="${RF_V4_RUNTIME_DIR:-$HOME/.local/share/reasonfirst/v4-runtime}/.venv/bin/python"

CONFIG_ARGS=(--bridge-dir "$HERE")
if [ -n "$WORKER_BACKEND" ]; then
  CONFIG_ARGS+=(--worker-backend "$WORKER_BACKEND")
fi
"$PY" "$HERE/configure_v4.py" "${CONFIG_ARGS[@]}"

if [ "$INSTALL_PLUGIN" = true ]; then
  "$PY" "$HERE/install_chatgpt_desktop_plugin.py" \
    --source "$HERE/chatgpt_desktop_plugin" --url "http://127.0.0.1:8765/mcp"
  codex plugin add reasonfirst-v4@personal
fi

if [ "$ENABLE_LOGIN_SERVICE" = true ]; then
  "$HERE/set_local_no_proxy.sh"
  "$HERE/stage_v4_runtime.sh"
  "$HERE/install_launch_agent.sh"
fi

if [ "$ENABLE_WEB_RELAY" = true ]; then
  if ! gh auth status >/dev/null 2>&1; then
    echo "GitHub CLI is not authenticated; refusing to enable the web relay" >&2
    exit 1
  fi
  "$HERE/install_web_relay_agent.sh"
fi

if [ -n "$TUNNEL_ID" ]; then
  "$HERE/configure_v4_tunnel.sh" "$TUNNEL_ID"
fi

cat <<EOF
ReasonFirst core configuration updated.

Optional integrations applied:
  personal plugin: $INSTALL_PLUGIN
  login MCP service: $ENABLE_LOGIN_SERVICE
  GitHub web relay: $ENABLE_WEB_RELAY
  Secure MCP Tunnel: ${TUNNEL_ID:-no}

Use '$0 --plan ...' before enabling additional persistent integrations.
EOF
