#!/usr/bin/env bash
set -euo pipefail
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# v4 entrypoint: one loopback MCP daemon. This does not start the GitHub relay.
exec "$HERE/run_mcp_server.sh" "$@"
