#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$HERE/../.." && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${RF_V4_RUNTIME_DIR:-$HOME/.local/share/reasonfirst/v4-runtime}/.venv/bin/python"
if [ ! -x "$PY" ]; then echo "ReasonFirst runtime missing; run configure_v4.sh" >&2; exit 127; fi
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS --max-time 2 "http://127.0.0.1:${RF_MCP_PORT:-8765}/healthz" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS --max-time 2 "http://127.0.0.1:${RF_MCP_PORT:-8765}/healthz" >/dev/null
exec "$PY" "$HERE/github_control_relay.py" "$@"
