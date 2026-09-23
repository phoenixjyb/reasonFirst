#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SERVICE_ROOT="${RF_SERVICE_ROOT:-$HOME/.local/share/reasonfirst/v4-service}"
LABEL="com.reasonfirst.web-bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.local/share/reasonfirst/logs"
mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$LABEL</string>
<key>ProgramArguments</key><array><string>$SERVICE_ROOT/tools/codex_web_bridge/run_github_relay.sh</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$LOG_DIR/web-relay.stdout.log</string>
<key>StandardErrorPath</key><string>$LOG_DIR/web-relay.stderr.log</string>
</dict></plist>
EOF
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
loaded=false
for attempt in 1 2 3 4 5; do
  if launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1; then loaded=true; break; fi
  sleep 1
done
if [ "$loaded" != true ]; then echo "Failed to load $LABEL" >&2; exit 1; fi
launchctl enable "gui/$(id -u)/$LABEL"
echo "Installed and started $LABEL"
