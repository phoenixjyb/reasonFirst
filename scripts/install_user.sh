#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it using the supported method for your platform, then rerun." >&2
  exit 1
fi

echo "[ReasonFirst] installing editable source tool from: $ROOT"
uv tool install --editable "$ROOT" --force

echo
echo "Source/developer installation complete."
echo "The editable tool follows this checkout; keep the checkout on a reviewed ref."
echo
echo "Verify from any directory:"
echo "  reasonfirst --version"
echo "  reasonfirst setup --status"
echo
echo "Start/resume the same guided setup used by packaged installs:"
echo "  reasonfirst setup"
echo
echo "Compatibility/expert commands remain available:"
echo "  actual-coder --help"
echo "  codingagent --help"
echo "  gitlab-agent --help"
