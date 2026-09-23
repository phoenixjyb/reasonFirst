#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SERVICE_ROOT="${RF_SERVICE_ROOT:-$HOME/.local/share/reasonfirst/v4-service}"
LABEL="com.reasonfirst.v4-mcp"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$HOME/.local/share/reasonfirst/logs"
UID_NUM="$(id -u)"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

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

echo "Installed and started $LABEL"
echo "Plist: $PLIST"
echo "MCP URL: http://${RF_MCP_HOST:-127.0.0.1}:${RF_MCP_PORT:-8765}${RF_MCP_PATH:-/mcp}"
echo "Logs: $LOG_DIR/v4-mcp.stdout.log and $LOG_DIR/v4-mcp.stderr.log"
echo "Status: launchctl print gui/$UID_NUM/$LABEL"
