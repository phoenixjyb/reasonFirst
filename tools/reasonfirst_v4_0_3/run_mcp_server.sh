#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
"$HERE/set_local_no_proxy.sh" >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
PROJECT_ROOT="$(CDPATH= cd -- "$HERE/../.." && pwd)"
PY="${RF_V4_RUNTIME_DIR:-$HOME/.local/share/reasonfirst/v4-runtime}/.venv/bin/python"
if [ ! -x "$PY" ]; then echo "ReasonFirst runtime missing; run configure_v4.sh" >&2; exit 127; fi
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
cd "$PROJECT_ROOT"
case "${1:-}" in
  --doctor|--stdio) exec "$PY" "$HERE/reasonfirst_mcp_server.py" "$@" ;;
esac
exec "$PY" "$HERE/reasonfirst_mcp_server.py" \
  --host 127.0.0.1 --port "${RF_MCP_PORT:-8765}" --path /mcp "$@"
