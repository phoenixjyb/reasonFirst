#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home"
out="$(
  HOME="$TMP/home"   RF_V4_RUNTIME_DIR="$TMP/runtime"   "$ROOT/configure_v4.sh"     --plan     --worker-backend codex-desktop     --install-codex-plugin     --enable-login-service
)"
printf '%s\n' "$out" | grep -q 'no changes will be made'
printf '%s\n' "$out" | grep -q 'worker backend: codex-desktop'
printf '%s\n' "$out" | grep -q 'personal Codex/Desktop plugin: true'
printf '%s\n' "$out" | grep -q 'local MCP login service: true'
test ! -e "$TMP/runtime"
test ! -e "$TMP/home/.config/reasonfirst/bridge.yaml"
echo "v4 setup plan is side-effect free: OK"
