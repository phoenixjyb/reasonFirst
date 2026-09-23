#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/home"
cat > "$TMP/bin/gh" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "api" ] && [ "$2" = "user" ]; then
  echo example-user
  exit 0
fi
exit 1
EOF
chmod +x "$TMP/bin/gh"
PATH="$TMP/bin:$PATH" HOME="$TMP/home" RF_BRIDGE_CONFIG="$TMP/home/bridge.yaml" "$ROOT/configure_v3.sh" --defaults >/dev/null
grep -q 'repo: "example-user/reasonfirst-control"' "$TMP/home/bridge.yaml"
grep -q 'issue: 1' "$TMP/home/bridge.yaml"
grep -q 'author: "example-user"' "$TMP/home/bridge.yaml"
grep -q 'codex_backend: "desktop-preferred"' "$TMP/home/bridge.yaml"
echo "first-run config: OK"
