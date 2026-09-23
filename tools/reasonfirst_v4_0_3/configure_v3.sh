#!/usr/bin/env bash
set -euo pipefail

CONFIG="${RF_BRIDGE_CONFIG:-$HOME/.config/reasonfirst/bridge.yaml}"
mkdir -p "$(dirname "$CONFIG")"

MODE="interactive"
if [ "${1:-}" = "--defaults" ]; then
  MODE="defaults"
elif [ $# -gt 0 ]; then
  echo "usage: $0 [--defaults]" >&2
  exit 2
fi

detect_user=""
if command -v gh >/dev/null 2>&1; then
  detect_user="$(gh api user --jq .login 2>/dev/null || true)"
fi

if [ "$MODE" = "defaults" ]; then
  if [ -z "$detect_user" ]; then
    echo "Cannot auto-configure: GitHub CLI is not authenticated. Run 'gh auth login' or run configure_v3.sh interactively." >&2
    exit 2
  fi
  repo="${RF_CONTROL_REPO:-$detect_user/reasonfirst-control}"
  issue="${RF_CONTROL_ISSUE:-1}"
  author="${RF_CONTROL_ALLOWED_AUTHOR:-$detect_user}"
  backend="${RF_CODEX_BACKEND:-desktop-preferred}"
else
  printf 'GitHub control repository [%s/reasonfirst-control]: ' "${detect_user:-OWNER}"
  IFS= read -r repo
  repo="${repo:-${detect_user:+$detect_user/reasonfirst-control}}"
  if [ -z "$repo" ]; then
    echo "A private GitHub control repository is required." >&2
    exit 2
  fi

  printf 'Control issue [1]: '
  IFS= read -r issue
  issue="${issue:-1}"

  printf 'Authorized GitHub author [%s]: ' "${detect_user:-YOUR_LOGIN}"
  IFS= read -r author
  author="${author:-$detect_user}"
  if [ -z "$author" ]; then
    echo "Authorized author is required." >&2
    exit 2
  fi

  printf 'Local Codex backend [desktop-preferred] (desktop-preferred/desktop-required/standalone-local): '
  IFS= read -r backend
  backend="${backend:-desktop-preferred}"
fi

case "$backend" in
  desktop-preferred|desktop-required|standalone-local) ;;
  *) echo "Unsupported backend: $backend" >&2; exit 2 ;;
esac

case "$issue" in
  ''|*[!0-9]*) echo "Control issue must be a positive integer" >&2; exit 2 ;;
esac
if [ "$issue" -le 0 ]; then
  echo "Control issue must be a positive integer" >&2
  exit 2
fi

cat > "$CONFIG" <<EOF
version: 3
control:
  repo: "$repo"
  issue: $issue
  author: "$author"
  poll_seconds: 5
defaults:
  target: local
  codex_backend: "$backend"
targets:
  local:
    type: local
    codex_backend: "$backend"
EOF
chmod 600 "$CONFIG"

cat <<EOF
Saved: $CONFIG
Control repo: $repo
Control issue: $issue
Authorized author: $author
Local backend: $backend
EOF
