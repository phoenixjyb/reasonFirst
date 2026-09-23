# ReasonFirst / ActualCoder quickstart

This is the current source-checkout guide. [中文上手](QUICKSTART_CN.md) · [Documentation index](README.md) · [Security boundaries](../SECURITY.md).

Use a trusted personal development machine and repositories you are authorized to access. Worktrees and executable allowlists are not a sandbox. Stop on errors; do not turn off TLS verification or broaden permissions to make a check pass.

## 1. Try the code before adding credentials

Prerequisites: Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Python. Use Python 3.12 to match CI; metadata permits 3.10+, which is not the same as a tested interpreter matrix.

```bash
git clone https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run actual-coder --help
uv run actual-coder-migrate-https --help
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
```

No production account is required for these tests. See [local PR review](LOCAL_PR_REVIEW.md) to use an existing checkout instead of downloading archives. Use `uv run` inside this source directory while testing; a global command may point at a different installation.

## 2. Create a user-owned configuration

Choose the stable user path `~/.config/gitlab-agent/.env`, outside managed repositories. **Never overwrite an existing working config with the example.** Existing HTTP installations should follow the [migration guide](HTTPS_MIGRATION.md), not start again.

On macOS/Linux, from the source checkout, this creates a private example only when the file does not exist:

```bash
(
set -eu
umask 077
mkdir -p "$HOME/.config/gitlab-agent"
chmod 700 "$HOME/.config/gitlab-agent"
test ! -e "$HOME/.config/gitlab-agent/.env"
cp .env.example "$HOME/.config/gitlab-agent/.env"
chmod 600 "$HOME/.config/gitlab-agent/.env"
)
```

If the file already exists, the block stops. Edit that existing file deliberately rather than deleting it. Use a local editor to set your real endpoint and tokens; never paste tokens into a shell command, URL, issue, or chat transcript.

On Windows PowerShell, create and restrict an empty file before adding secrets. Run from the source checkout:

```powershell
$ConfigDir = Join-Path $HOME ".config\gitlab-agent"
$ConfigFile = Join-Path $ConfigDir ".env"
New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
if (Test-Path $ConfigFile) { throw "Config already exists; inspect it instead of overwriting it." }
New-Item -ItemType File -Path $ConfigFile -ErrorAction Stop | Out-Null
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Acl = New-Object System.Security.AccessControl.FileSecurity
$Acl.SetAccessRuleProtection($true, $false)
$Rule = New-Object System.Security.AccessControl.FileSystemAccessRule($Identity, "FullControl", "Allow")
$Acl.AddAccessRule($Rule)
Set-Acl -Path $ConfigFile -AclObject $Acl -ErrorAction Stop
Get-Content -LiteralPath .env.example -Raw | Set-Content -LiteralPath $ConfigFile -Encoding utf8
notepad $ConfigFile
```

Use a local personal filesystem. If ACL setup fails, stop before entering secrets; do not assume Windows `chmod` is equivalent.

Essential settings, using **illustrative** values only:

```dotenv
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_TOKEN=REPLACE_ME
GITLAB_GIT_TOKEN=
GITLAB_ALLOWED_PROJECTS=team/project-a
GITLAB_VERIFY_SSL=true
GITLAB_TRUST_ENV=false
GITLAB_GIT_TRUST_ENV=false
GITLAB_REQUIRE_WRITE_ALLOWLIST=true
GITLAB_WORKSPACE_ROOT=~/.local/share/chatgpt-gitlab-mcp
```

Use a dedicated read API credential (`read_api` and, where needed, `read_repository`). For Git writes, prefer a separate `GITLAB_GIT_TOKEN` with `write_repository`, not a broad `api` token. Empty Git-token configuration falls back to the API token; that does not magically give it push rights. Restrict the project allowlist to exact intended `path_with_namespace` values. Install/authenticate Codex CLI or Copilot CLI separately using the provider's supported flow; for `codex-desktop`, start/enable the managed Codex Desktop App Server. ReasonFirst does not manage provider accounts.

### Coding-worker policy

ReasonFirst passes a user-owned worker policy explicitly to the selected coding backend instead of relying on whichever interactive model/permission choice happened to be active previously. `codex` / `codex-cli` mean Codex CLI, `copilot` / `copilot-cli` mean GitHub Copilot CLI, and `codex-desktop` means the managed Codex Desktop App Server. The two Codex surfaces share the same Codex WorkerPolicy.

Set a persistent user default once:

```dotenv
REASONFIRST_DEFAULT_BACKEND=codex-cli
```

Accepted values are `auto`, `codex-cli`, `copilot-cli`, and `codex-desktop` (historical `codex` / `copilot` aliases remain accepted). Selection precedence is: an explicit `--agent` value first; when `--agent auto` is used, a non-`auto` user default is authoritative; otherwise ReasonFirst uses repository preference and then installed fallback.

The default Codex policy is:

```dotenv
REASONFIRST_CODEX_MODEL=gpt-5.6-sol
REASONFIRST_CODEX_REASONING_EFFORT=high
REASONFIRST_CODEX_EXECUTION_MODE=interactive
REASONFIRST_CODEX_SANDBOX=workspace-write
REASONFIRST_CODEX_APPROVAL_POLICY=on-request
REASONFIRST_CODEX_NETWORK_ACCESS=false
```

Set `REASONFIRST_CODEX_EXECUTION_MODE=exec` for non-interactive `codex exec`. For unattended execution, `REASONFIRST_CODEX_APPROVAL_POLICY=never` keeps the configured sandbox boundary but never pauses for approval; operations that need more privilege must fail instead of silently escaping the policy. ReasonFirst intentionally does not expose Codex full-access/yolo as a supported worker policy.

With `codex-desktop` and `approval_policy=on-request`, ActualCoder CLI displays command/file/permission requests on the terminal and grants only the individual request after an explicit yes. In the bridge/MCP flow, use `reasonfirst_pending_approvals`, then `reasonfirst_approve` or `reasonfirst_decline`. Requests time out to deny; permission grants default to turn scope and are never silently upgraded to session scope.

Copilot remains on its provider-selected model/effort unless explicitly pinned:

```dotenv
REASONFIRST_COPILOT_MODEL=
REASONFIRST_COPILOT_REASONING_EFFORT=
REASONFIRST_COPILOT_EXECUTION_MODE=interactive
REASONFIRST_COPILOT_DISABLE_BUILTIN_MCPS=true
REASONFIRST_COPILOT_ALLOW_TOOLS=
REASONFIRST_COPILOT_DENY_TOOLS=shell(git push)
```

Set `REASONFIRST_COPILOT_EXECUTION_MODE=programmatic` to use `copilot -p`. Built-in Copilot MCPs are disabled by default so the worker cannot bypass ReasonFirst's Git/MR publication path through a remote-write integration. The allow/deny values are comma-separated Copilot CLI permission patterns; deny rules are passed explicitly and win over allow rules. `git push` is denied by default so remote publication stays in the reviewed ReasonFirst `finish` flow.

Policy evidence is explicit about certainty. CLI workers report that policy was encoded into launch arguments; Codex CLI also gets a no-model-use App Server catalog/admin-policy preflight for model/effort compatibility. Codex Desktop validates the live model catalog and the resolved `thread/start` model/effort/sandbox/approval response, and records model-reroute events as policy violations. Current App Server does not expose the provider response-envelope model, so ReasonFirst does not claim independent provider-level model attestation. Use `actual-coder config` to inspect the requested non-secret defaults and launch/status output for verification evidence.

Configuration file selection: `GITLAB_AGENT_ENV_FILE`, then the user config above, then a local `.env`. CLI fallback is relative to its working directory; MCP's fallback is relative to its server source directory. Already-exported variables take precedence over the file. Use simple literal assignments; do not rely on shell interpolation or inline comments in values.

On macOS/Linux, explicitly select the file for this shell:

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
```

On PowerShell:

```powershell
$env:GITLAB_AGENT_ENV_FILE = Join-Path $HOME ".config\gitlab-agent\.env"
```

## 3. Verify API and Git separately

```bash
uv run actual-coder config
uv run actual-coder agents
uv run actual-coder doctor
uv run actual-coder project-config team/project-a --validate
```

Inspect `config` locally; even token-free diagnostics can expose private hostnames and paths. `agents` checks CLI executable presence and the managed Desktop App Server socket; it does not authenticate or consume quota. `doctor` checks API authentication unless `--offline`; it does not test Git push rights. For an intentional Git-only deployment with no `GITLAB_TOKEN`, configure exactly one Git credential source and use `actual-coder doctor --git-only` / `actual-coder start ... --git-only`. `project-config` performs managed Git fetch/read and validates the contract, without creating a worktree or pushing.

`found: false, valid: true` means `.actualcoder.yaml` is absent, not that application tests passed. No project-specific validation commands were loaded. Copy and adapt [the example contract](../.actualcoder.example.yaml) **in the target GitLab project**, use its actual test commands, and review it through that project's normal process. Validate a candidate without fetching it:

```bash
uv run actual-coder project-config team/project-a --file .actualcoder.example.yaml --validate
```

The example uses a Python/pytest command; it is not a universal contract. Repository settings cannot expand your executable allowlist. Finish reads policy from the workspace's original base commit, so a newly approved contract applies to a new task based on that commit, not automatically to old workspaces.

## 4. Prepare, implement, review, finish

Define the goal, non-goals, and acceptance criteria with your reasoning interface first. Prepare without launching a coding model:

```bash
uv run actual-coder start team/project-a \
  --task fix-timeout \
  --goal "Fix the timeout bug" \
  --acceptance "Timeout regression test passes" \
  --acceptance "Existing API remains compatible" \
  --non-goal "No unrelated refactor" \
  --no-launch
```

The goal plus repeatable `--acceptance` and `--non-goal` values are persisted as a bounded TaskSpec tied to the workspace's project/base SHA. Subsequent resume/continue handoffs reuse the original goal when `--goal` is omitted and carry the acceptance criteria/non-goals back into the worker prompt. Each handoff records bounded non-secret attempt metadata (backend, CI-context presence and WorkerPolicy), not full model transcripts.

This fetches project context and creates a worktree. Save the returned workspace ID. Inspect the handoff and use its returned backend command/prompt. On a new task, omit `--no-launch` to launch the selected worker; use `--agent codex-cli`, `--agent copilot-cli`, or `--agent codex-desktop` for the clearest explicit surface names. Historical `codex` / `copilot` remain supported aliases. No existing-workspace ID is accepted by `start`.

After edits, set `WS` to the returned ID, not the illustrative value below:

```bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
```

**Dry-run executes configured validation commands and may therefore change local files.** It does not commit or push. Inspect tests, diff, protected-path findings, secret coverage, and the proposed MR target. Then, only for an unblocked intentional change:

```bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
```

Finish asks for human confirmation. First controlled publication creates an MR; later finishes update its recorded branch/MR. `--yes` is explicit scripted confirmation, not a way around validation. `--allow-secret-match` is only for reviewed false positives and cannot override incomplete history coverage. A real secret must be revoked and removed from unpublished history, not merely deleted in a new commit.

If CI truly fails, either inspect the resumed handoff first:

```bash
uv run actual-coder resume "$WS" --agent auto --from-ci --goal "Repair the matching-head CI failure without unrelated changes"
```

or launch the selected worker immediately after the handoff is emitted:

```bash
uv run actual-coder resume "$WS" --agent auto --from-ci --launch --goal "Repair the matching-head CI failure without unrelated changes"
```

The convenience alias `continue` uses `--agent auto` and launches by default:

```bash
uv run actual-coder continue "$WS" --from-ci --goal "Repair the matching-head CI failure without unrelated changes"
```

Use `continue ... --no-launch` to inspect without starting a worker. Resume/continue reload the repository contract from the workspace's pinned base SHA, so instructions, protected paths, validation commands, and worker selection remain tied to the reviewed base policy rather than whatever is currently on the remote branch. Matching-head CI freshness checks still apply.

Build a bounded, redacted, read-only evidence bundle at any point:

```bash
uv run actual-coder evidence "$WS"
uv run actual-coder evidence "$WS" --from-ci
```

EvidencePack includes workspace identity/status, persistent TaskSpec/attempt metadata, pinned-base project policy, changed paths, reviewability and a bounded/redacted diff. `--from-ci` attaches sanitized CI evidence and reports stale/incomplete pipelines as warnings instead of treating them as fresh proof. Building evidence does not run repository code, mutate task state, commit, or push.

Review and merge the MR in GitLab outside ReasonFirst; the controller has no merge operation.

## 5. Recovery and low-level commands

Prefer `status`/`resume` for an existing workspace. Only when local state is unavailable and the feature branch remains on GitLab, use the existing MR:

```bash
uv run actual-coder checkout-mr team/project-a 123 --agent copilot-cli --goal "Continue this MR and address reviewed feedback"
```

Replace the project/IID and review the returned handoff. Recovery refuses to overwrite an abandoned local branch with unpublished commits. Normal `cleanup` checks dirty state and fresh publication evidence; offline/deleted-remote cases can deliberately block it. `cleanup --force` discards local work and is not an installation repair command.

The `task`, `commit`, `push`, `push-update`, and `push-mr` commands remain compatibility/manual routes. **They do not collectively enforce the full finish gate.** Some generated prompts still mention these commands; review their proposed writes rather than assuming every worker action is intercepted. See [security boundaries](../SECURITY.md).

## 6. Use global commands from a stable checkout

Optional macOS/Linux installation, from your long-lived source checkout:

```bash
bash scripts/install_user.sh
actual-coder config
actual-coder-migrate-https --help
```

On PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_user.ps1
actual-coder config
actual-coder-migrate-https --help
```

The installers use editable `uv tool install` and may replace existing command entry points. They do not overwrite an existing user config; Windows config ACLs must be established separately. A global editable installation follows this checkout's code, not an immutable released wheel. Keep the source directory and interpreter; do not install globally from a temporary PR checkout that you will remove. Use the [update/PR guide](LOCAL_PR_REVIEW.md).

## 7. Optional reasoning-client integration

The local CLI does not require an MCP tunnel. The read-only MCP server can expose GitLab repository, MR, and CI reads to a compatible reasoning client. It is not a local-task execution API. Starting `run_mcp.sh` alone is not a complete tunnel setup. The [MCP setup tutorial](SETUP_TUTORIAL.md) and [team tunnel guide](OPENAI_TUNNEL_TEAM_SETUP_CN.md) describe an optional deployment; verify current provider eligibility and installation instructions before following provider-specific steps. Never share tunnel runtime credentials.
