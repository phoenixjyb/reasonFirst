#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SERVICE_ROOT="${RF_SERVICE_ROOT:-$HOME/.local/share/reasonfirst/v4-service}"
LABEL="com.reasonfirst.v4-mcp"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$HOME/.local/share/reasonfirst/logs"
UID_NUM="$(id -u)"
READ_ONLY_RAW="${RF_MCP_READ_ONLY:-false}"
PROXY_SYNC_RAW="${RF_LAUNCHD_PROXY_FROM_SHELL:-false}"
HEALTH_TIMEOUT="${RF_MCP_HEALTH_TIMEOUT_SECONDS:-15}"
case "$READ_ONLY_RAW" in
  1|true|TRUE|yes|YES|on|ON) READ_ONLY_VALUE=true ;;
  0|false|FALSE|no|NO|off|OFF|"") READ_ONLY_VALUE=false ;;
  *) echo "RF_MCP_READ_ONLY must be a boolean value" >&2; exit 2 ;;
esac
case "$PROXY_SYNC_RAW" in
  1|true|TRUE|yes|YES|on|ON) PROXY_SYNC_VALUE=true ;;
  0|false|FALSE|no|NO|off|OFF|"") PROXY_SYNC_VALUE=false ;;
  *) echo "RF_LAUNCHD_PROXY_FROM_SHELL must be a boolean value" >&2; exit 2 ;;
esac
case "$HEALTH_TIMEOUT" in
  ''|*[!0-9]*) echo "RF_MCP_HEALTH_TIMEOUT_SECONDS must be a nonnegative integer" >&2; exit 2 ;;
esac

mkdir -p "$PLIST_DIR" "$LOG_DIR"

if [ "$PROXY_SYNC_VALUE" = true ]; then
  bash "$HERE/sync_launchd_proxy_env.sh" sync
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$SERVICE_ROOT/tools/codex_web_bridge/run_reasonfirst.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$SERVICE_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>RF_MCP_READ_ONLY</key>
    <string>$READ_ONLY_VALUE</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/v4-mcp.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/v4-mcp.stderr.log</string>
</dict>
</plist>
EOF
chmod 600 "$PLIST"

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
loaded=false
for attempt in 1 2 3 4 5; do
  if launchctl bootstrap "gui/$UID_NUM" "$PLIST" >/dev/null 2>&1; then loaded=true; break; fi
  sleep 1
done
if [ "$loaded" != true ]; then echo "Failed to load $LABEL" >&2; exit 1; fi
launchctl enable "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

HEALTH_HOST="${RF_MCP_HOST:-127.0.0.1}"
HEALTH_PORT="${RF_MCP_PORT:-8765}"
HEALTH_URL="http://$HEALTH_HOST:$HEALTH_PORT/healthz"
HEALTH_READY=false
if [ "$HEALTH_TIMEOUT" -gt 0 ]; then
  for ((attempt=0; attempt<HEALTH_TIMEOUT; attempt++)); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      HEALTH_READY=true
      break
    fi
    sleep 1
  done
fi

echo "Installed and started $LABEL"
echo "Plist: $PLIST"
echo "MCP URL: http://${RF_MCP_HOST:-127.0.0.1}:${RF_MCP_PORT:-8765}${RF_MCP_PATH:-/mcp}"
echo "Read-only MCP mode: $READ_ONLY_VALUE"
echo "Launchd proxy sync from current shell: $PROXY_SYNC_VALUE"
if [ "$HEALTH_READY" = true ]; then
  echo "MCP health: ready"
elif [ "$HEALTH_TIMEOUT" -gt 0 ]; then
  echo "MCP health: not ready after ${HEALTH_TIMEOUT}s; inspect the logs below before retrying" >&2
else
  echo "MCP health: check skipped"
fi
echo "Logs: $LOG_DIR/v4-mcp.stdout.log and $LOG_DIR/v4-mcp.stderr.log"
echo "Status: launchctl print gui/$UID_NUM/$LABEL"
