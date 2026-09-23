#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$HERE/../.." && pwd)"
SERVICE_ROOT="${RF_SERVICE_ROOT:-$HOME/.local/share/reasonfirst/v4-service}"
TEMP="${SERVICE_ROOT}.new.$$"
mkdir -p "$TEMP/tools/codex_web_bridge" "$TEMP/src/gitlab_agent"
rsync -a --exclude '__pycache__' "$HERE/" "$TEMP/tools/codex_web_bridge/"
rsync -a --exclude '__pycache__' "$ROOT/src/gitlab_agent/" "$TEMP/src/gitlab_agent/"
if [ -d "$SERVICE_ROOT" ]; then
  mv "$SERVICE_ROOT" "${SERVICE_ROOT}.backup.$(date +%Y%m%d-%H%M%S).$$"
fi
mv "$TEMP" "$SERVICE_ROOT"
chmod 700 "$SERVICE_ROOT"
echo "Staged ReasonFirst login runtime: $SERVICE_ROOT"
