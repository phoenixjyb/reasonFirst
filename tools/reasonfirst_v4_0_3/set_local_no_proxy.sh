#!/usr/bin/env bash
set -euo pipefail
# Keep the system proxy for remote traffic; bypass it only for the local MCP URL.
for name in NO_PROXY no_proxy; do
  value="$(launchctl getenv "$name" 2>/dev/null || true)"
  for host in 127.0.0.1 localhost; do
    case ",$value," in
      *,"$host",*) ;;
      *) value="${value:+$value,}$host" ;;
    esac
  done
  launchctl setenv "$name" "$value"
done
