#!/usr/bin/env bash
set -euo pipefail
VERSION="4.0.3-r1"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$HERE/../.." && pwd)"
RUNTIME_DIR="${RF_V4_RUNTIME_DIR:-$HOME/.local/share/reasonfirst/v4-runtime}"
VENV="$RUNTIME_DIR/.venv"
MARKER="$RUNTIME_DIR/version"
UV_BIN="${UV_BIN:-$(command -v uv 2>/dev/null || true)}"
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then UV_BIN="$HOME/.local/bin/uv"; fi
if [ -z "$UV_BIN" ]; then echo "uv not found" >&2; exit 127; fi
mkdir -p "$RUNTIME_DIR"
ready=0
if [ -x "$VENV/bin/python" ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$VERSION" ]; then
  if "$VENV/bin/python" - <<'PY' >/dev/null 2>&1
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
import yaml, websockets, PIL, pypdf, pymupdf
PY
  then ready=1; fi
fi
if [ "$ready" -eq 1 ]; then
  echo "ReasonFirst v4 runtime already ready: $VENV"
  exit 0
fi
if [ ! -x "$VENV/bin/python" ]; then
  "$UV_BIN" venv --python 3.12 "$VENV" 2>/dev/null || "$UV_BIN" venv "$VENV"
fi
"$UV_BIN" pip install --python "$VENV/bin/python" \
  -e "$PROJECT_ROOT" \
  'websockets==16.1.1' \
  'pillow==12.3.0' \
  'pypdf==6.19.0' \
  'pymupdf==1.28.2'
printf '%s\n' "$VERSION" > "$MARKER"
chmod 700 "$RUNTIME_DIR" "$VENV" 2>/dev/null || true
chmod 600 "$MARKER" 2>/dev/null || true
"$VENV/bin/python" - <<'PY'
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
import yaml, websockets, PIL, pypdf, pymupdf
print("ReasonFirst v4 runtime imports: OK")
PY
echo "Installed ReasonFirst v4 runtime: $VENV"
