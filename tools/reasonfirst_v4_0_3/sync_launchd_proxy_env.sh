#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This helper is only for macOS launchd user sessions." >&2
  exit 2
fi

MODE="${1:-sync}"
case "$MODE" in
  sync|--sync) ;;
  clear|--clear) ;;
  *)
    echo "Usage: $0 [sync|clear]" >&2
    exit 2
    ;;
esac

vars=(
  http_proxy
  https_proxy
  all_proxy
  HTTP_PROXY
  HTTPS_PROXY
  ALL_PROXY
  no_proxy
  NO_PROXY
)

for name in "${vars[@]}"; do
  if [ "$MODE" = "clear" ]; then
    launchctl unsetenv "$name" >/dev/null 2>&1 || true
    echo "$name=cleared"
    continue
  fi

  value="$(printenv "$name" 2>/dev/null || true)"
  if [ -n "$value" ]; then
    launchctl setenv "$name" "$value"
    echo "$name=propagated"
  else
    launchctl unsetenv "$name" >/dev/null 2>&1 || true
    echo "$name=unset"
  fi
done

echo "Proxy values were not written to a plist or source file."
echo "They are scoped to the current macOS launchd user session and disappear at logout/reboot."
