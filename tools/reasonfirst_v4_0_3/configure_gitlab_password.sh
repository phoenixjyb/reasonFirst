#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [/path/to/reasonFirst]" >&2
  exit 2
fi

RF_DIR="${1:-$PWD}"
RF_DIR="$(cd "$RF_DIR" && pwd)"
[ -f "$RF_DIR/pyproject.toml" ] || { echo "Not a reasonFirst checkout: $RF_DIR" >&2; exit 1; }

CFG_DIR="$HOME/.config/gitlab-agent"
CFG="$CFG_DIR/.env"
mkdir -p "$CFG_DIR"
chmod 700 "$CFG_DIR"

if [ -e "$CFG" ]; then
  echo "Existing config found: $CFG" >&2
  echo "Refusing to overwrite it automatically. Back it up/remove it intentionally, then rerun." >&2
  exit 1
fi

printf 'GitLab base URL: '
IFS= read -r BASE_URL
printf 'GitLab username/email: '
IFS= read -r USERNAME
printf 'GitLab password: '
stty -echo
IFS= read -r PASSWORD
stty echo
printf '\n'
printf 'Allowed project (group/project): '
IFS= read -r PROJECT

case "$BASE_URL" in
  https://*) ;;
  *) echo "GitLab URL must start with https://" >&2; exit 1 ;;
esac
[ -n "$USERNAME" ] || { echo "Username is required" >&2; exit 1; }
[ -n "$PASSWORD" ] || { echo "Password is required" >&2; exit 1; }
case "$PASSWORD" in
  *$'\n'*|*$'\r'*) echo "Password must not contain newlines" >&2; exit 1 ;;
esac
[ -n "$PROJECT" ] || { echo "An explicit allowed project is required" >&2; exit 1; }
case "$PROJECT" in
  */*) ;;
  *) echo "Project must be group/project" >&2; exit 1 ;;
esac
REQUIRE_ALLOWLIST=true

umask 077
cat > "$CFG" <<EOF
GITLAB_BASE_URL=${BASE_URL%/}
GITLAB_TOKEN=
GITLAB_GIT_PASSWORD=$PASSWORD
GITLAB_GIT_TOKEN=
GITLAB_GIT_USERNAME=$USERNAME
GITLAB_ALLOWED_PROJECTS=$PROJECT
GITLAB_VERIFY_SSL=true
GITLAB_TRUST_ENV=false
GITLAB_GIT_TRUST_ENV=false
GITLAB_REQUIRE_WRITE_ALLOWLIST=$REQUIRE_ALLOWLIST
GITLAB_WORKSPACE_ROOT=~/.local/share/chatgpt-gitlab-mcp
GITLAB_BRANCH_PREFIX=chatgpt/
GITLAB_DEFAULT_BASE_REF=main
GITLAB_ALLOWED_EXECUTABLES=python,python3,pytest,uv,node,npm,pnpm,yarn,make,cmake,ninja,cargo,go,mvn,gradle
GITLAB_COMMAND_TIMEOUT_SECONDS=300
GITLAB_COMMAND_MAX_OUTPUT_BYTES=120000
GITLAB_MAX_WRITE_FILE_BYTES=1000000
EOF
chmod 600 "$CFG"
unset PASSWORD

export GITLAB_AGENT_ENV_FILE="$CFG"
export RF_GITLAB_AUTH_MODE=git-only
cd "$RF_DIR"

echo "== ReasonFirst offline doctor =="
uv run actual-coder doctor --offline --git-only

echo "== Git-only project check =="
uv run actual-coder project-config "$PROJECT" --validate
echo "Git-only mode ready for project: $PROJECT"

echo "Configuration created: $CFG"
