# First-time setup: from your Mac to a GitLab conversation in ChatGPT

[简体中文](GETTING_STARTED_CN.md) · [Documentation index](README.md) · [Architecture](ARCHITECTURE.md) · [Daily tunnel operations](TUNNEL_LIFECYCLE.md)

## Choose your path first

You do **not** need every ReasonFirst integration for every task.

| Goal | Start here | Tunnel required? |
| --- | --- | --- |
| Use ActualCoder locally with Codex CLI, Copilot CLI, or Codex Desktop | [CLI quickstart](ACTUAL_CODER_QUICKSTART.md) | No |
| Let normal ChatGPT read an approved GitLab repository/MR/CI | Continue with this guide | Yes, for this connection path |
| Use the local Bridge Preview for App Server control, approvals, SSH workspaces or finish preview | [Architecture](ARCHITECTURE.md) and the Bridge Preview README | Not inherently; depends on how you expose/connect it |

The **read-only GitLab MCP** and the **Bridge Preview orchestration MCP** have different trust boundaries. Do not expose the more privileged Bridge Preview merely because you need repository reads.

**Finish this guide in order before sending a repository-work prompt.** The result is a normal ChatGPT conversation that can read your explicitly approved GitLab project through ReasonFirst MCP. Installing Python packages or seeing a local health check pass is not that result.

This is the recommended **macOS + Keychain + existing-profile/stdio** path. Linux users can use the same lifecycle helper with an explicit environment/file credential source; see [manual alternatives](SETUP_TUTORIAL.md). Windows lifecycle management is not supported by this helper; use the [Windows manual procedure](OPENAI_TUNNEL_TEAM_SETUP_CN.md#windows-powershell). Do not run macOS commands there.

Already connected successfully? Go directly to [daily use](#daily-use). Already have some prerequisites? Reuse them; do not recreate a working tunnel, overwrite configuration, or reinstall a working client merely to follow every example.

```text
One-time preparation: permissions -> software -> GitLab config -> tunnel/key/profile
Every stopped session: start service -> check local status -> select app in ChatGPT
Each new project: live identity -> project access preflight -> pinned source reads
Approved implementation: ChatGPT plan -> human handoff -> Codex/ActualCoder -> MR/CI -> review
```

<a id="prerequisites"></a>
## 0. Prerequisites and the three permission layers

| Requirement | What to prepare / who provides it | Checkpoint |
| --- | --- | --- |
| Trusted host and network | A Mac allowed to run the MCP service, reach the intended GitLab, and make outbound HTTPS requests to OpenAI. Obtain approval to send the selected source/CI data to ChatGPT. | Do not expose the local admin port to the internet or disable TLS verification. |
| Software | Git, uv, Python 3.12 environment, ReasonFirst, and the separate upstream `tunnel-client` binary. | Step 1 and Step 2 commands succeed. |
| GitLab access | Exact instance URL, existing `namespace/project`, actual branch/ref, a known text file, and a valid least-privilege GitLab API token. | Step 3 preflight returns `ok: true`. |
| OpenAI Platform access | Correct Platform organization; tunnel manager with **Tunnels Read + Manage** and runtime principal with **Tunnels Read + Use**. | An authorized tunnel ID and usable runtime key are available. |
| ChatGPT access | The intended ChatGPT workspace permits custom MCP apps/developer mode and the tunnel is associated with it. Workspace-admin permission is separate from Platform permission. | Step 7 can select the intended tunnel/app. |
| Persistent key loading | An existing, exact Keychain password item for this runtime key, or another deliberately selected supported secret source. | `start` retrieves it without needing yesterday's shell export. |
| Coding backend and Git writes | **Only needed later for implementation:** a signed-in `codex-cli`, `copilot-cli`, or available `codex-desktop` App Server backend, approved repository-write access, real project tests and a usable GitLab runner. | Not a prerequisite to the read-only connection. |

Provider access and UI labels can change. Check [OpenAI's tunnel permissions and workspace guidance](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and [current developer-mode policy](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt); no subscription name in this guide guarantees access. A tunnel cannot override GitLab permissions or ReasonFirst's local allowlist.

**Credential map — do not substitute one value for another:**

| Value | Consumer | Where it belongs |
| --- | --- | --- |
| `GITLAB_TOKEN` | ReasonFirst -> GitLab API | Private `~/.config/gitlab-agent/.env`; never chat or Markdown notes. |
| `GITLAB_GIT_TOKEN` | ActualCoder's native Git fetch/push | Optional separate credential in that private config; defer writes until approved. |
| `CONTROL_PLANE_API_KEY` | `tunnel-client` -> OpenAI tunnel service | Keychain (recommended here), or approved env/file reference. Not the GitLab token or an admin key. |
| `tunnel_...` ID | Identifies the existing OpenAI tunnel | Tunnel profile and ChatGPT's Tunnel connection field. It is not the runtime credential. |
| Coding-backend login/session | `codex-cli`, `copilot-cli`, or `codex-desktop` | That backend's own supported authentication/session. A tunnel key does not log the worker in. |

ReasonFirst does not directly call a model-inference API. The tunnel still needs its runtime API key. This is not a promise of free tunnel service, unlimited coding quota, or transferable subscriptions. Do not test setup by making a paid model API request.

<a id="install-tools"></a>
## 1. Install the tools, then verify them

On a Mac with [Homebrew](https://brew.sh/) already installed, install missing tools:

```bash
brew install git uv
brew install openai/tools/tunnel-client
```

Without Homebrew, follow its official installation instructions first, or use [uv's installer](https://docs.astral.sh/uv/getting-started/installation/) and the supported binary linked from [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels) / [upstream latest release](https://github.com/openai/tunnel-client/releases/latest). Check release integrity using the upstream instructions. Do not download a similarly named third-party tunnel program.

```bash
git --version
uv --version
tunnel-client --version
tunnel-client help quickstart
tunnel-client profiles list
```

**Checkpoint:** all executables are found. Record the existing profile name, if any. `actual-coder-tunnel` is the ReasonFirst supervisor; it does not install or replace `tunnel-client`. The [upstream installation guide](https://github.com/openai/tunnel-client#install-with-homebrew) documents the official tap.

## 2. Select a permanent source checkout and install ReasonFirst

In **Terminal A**, set these non-secret working values. Replace example project/ref/file values only after confirming them on your own GitLab. `gitlab-work` is a suggested new local profile name, not a claim that a tunnel/profile exists. Existing users must use their actual profile and checkout paths. Keep this Terminal for setup; these variables are not persistent settings.

```bash
RF_SOURCE="$HOME/Projects/reasonFirst"
RF_PROFILE="gitlab-work"
RF_PROJECT="team/project-a"
RF_REF="main"
RF_FILE="README.md"
```

For a **new checkout only**, this refuses to overwrite an existing destination:

<!-- example: clone -->
```bash
(
set -eu
if [ -e "$RF_SOURCE" ] || [ -L "$RF_SOURCE" ]; then
  printf 'Source already exists. Use the source-update guide; nothing was overwritten.\n'
  exit 1
fi
mkdir -p "$(dirname "$RF_SOURCE")"
git clone https://github.com/phoenixjyb/reasonFirst.git "$RF_SOURCE"
cd "$RF_SOURCE"
uv sync --python 3.12
bash scripts/install_user.sh
actual-coder-tunnel --help
actual-coder-check-project --help
git rev-parse HEAD
)
```

For an existing checkout, preserve local changes and use [source updates](LOCAL_PR_REVIEW.md), then run the sync/installer/help checks above from it, without cloning again. Stop a running tunnel in its original Terminal before updating its source. Global tools are installed editable: keep this checkout on a reviewed ref, not a temporary PR worktree.

**Checkpoint:** both helper commands exist and you have recorded the installed commit. `uv sync` may create a local `uv.lock`; at the onboarding baseline it is not committed. An untracked `?? uv.lock` is different from an edited tracked file. Preserve it; do not ignore every lockfile, reset unrelated edits, or use `--locked` before a reviewed shared lockfile exists. No lockfile policy change is made by this guide.

<a id="gitlab-access"></a>
## 3. Confirm the target and configure GitLab privately

First confirm **the exact remote project exists and your GitLab identity can access it**, with its owner or in the correct GitLab instance. A GitHub starter kit or local folder is not a GitLab project. A proposed practice project must be created and seeded by an authorized user before using the [practice guide](PRACTICE_LAB.md); do not move the exercise into a production repository to avoid this step.

For API reads, use the appropriate read-only scope (`read_api`); the repository example recommends `read_api` plus `read_repository` when the same credential also supplies Git reads. Later Git pushes can use a separate `write_repository` token. Scopes do not grant membership in an otherwise inaccessible project. See [GitLab token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/). Replace any exposed token before authenticated use; do not paste tokens into commands, URLs, Git remotes, screenshots, or this conversation.

From Terminal A, create the private configuration only if absent. Existing contents are preserved; symlinks are refused:

<!-- example: private-config -->
```bash
(
set -eu
umask 077
CFG_DIR="$HOME/.config/gitlab-agent"
CFG="$CFG_DIR/.env"
test ! -L "$CFG_DIR"
mkdir -p "$CFG_DIR"
chmod 700 "$CFG_DIR"
test ! -L "$CFG"
if [ ! -e "$CFG" ]; then
  (set -C; cat "$RF_SOURCE/.env.example" > "$CFG")
fi
test -f "$CFG"
chmod 600 "$CFG"
printf 'Edit privately: %s\n' "$CFG"
)
```

Then open it **locally**, without printing its contents:

```bash
nano "$HOME/.config/gitlab-agent/.env"
```

In the editor, replace the template's `GITLAB_BASE_URL` with the final GitLab HTTPS endpoint, `GITLAB_TOKEN` with the valid API token, and `GITLAB_ALLOWED_PROJECTS` with the explicitly approved exact project paths, comma-separated and without leading `/`. Preserve existing approved entries and keep TLS verification enabled. Save with Control+O, Enter; exit with Control+X.

The **local MCP project grant is `GITLAB_ALLOWED_PROJECTS`, not OpenAI tunnel settings**. Do not clear it to bypass a denial: an empty list permits all token-accessible projects for MCP reads. The helper does not grant access automatically. `.env` is plaintext protected by permissions; `GITLAB_TOKEN=keychain:...` is not resolved by the GitLab loader. The Keychain integration below is specifically for the tunnel runtime key.

Replace `RF_PROJECT`, `RF_REF` and `RF_FILE` in Terminal A with the confirmed values, then check the API path without starting a workspace or a coding agent:

<!-- example: project-check -->
```bash
(
set -eu
if [ "$RF_PROJECT" = "team/project-a" ]; then
  printf 'Replace RF_PROJECT with your confirmed, explicitly approved GitLab project first.\n'
  exit 1
fi
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
unset GITLAB_BASE_URL GITLAB_TOKEN GITLAB_GIT_TOKEN GITLAB_ALLOWED_PROJECTS
actual-coder-check-project "$RF_PROJECT" --ref "$RF_REF" --require-file "$RF_FILE"
)
```

The unset removes those stale overrides only in this subshell; put intended values in the selected private file. Other intentional environment overrides still need operator review. **Checkpoint:** `ok: true`, the intended project, and a `resolved_commit_sha`. This checks local API access, not the running ChatGPT connection. On failure, stop: `project_not_allowlisted` needs explicit local approval; `project_missing_or_inaccessible` does not prove nonexistence; ref/file errors need the actual ref/file; authentication or TLS errors need their own correction. See [project access](PROJECT_ACCESS.md). Do not test read readiness by requiring `actual-coder doctor` to find a coding backend.

<a id="openai-tunnel"></a>
## 4. Obtain the OpenAI tunnel ID and runtime key

Open [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels) under the intended **Platform organization**. Reuse an authorized tunnel, or have an authorized manager create one. Associate it with the intended **ChatGPT workspace**, not only the Platform organization. Record the returned ID; do not invent one or create a second tunnel when the missing piece is workspace association.

Under [Runtime API keys](https://platform.openai.com/settings/organization/api-keys), obtain the runtime credential from a principal allowed to use this tunnel. Creation/management permission and runtime-use permission are different. Do not use an admin key for the foreground runtime. A key's `sk-`/other prefix does not prove permissions. If the page reports insufficient access, ask the appropriate organization/workspace administrator and wait; the guide cannot grant that access. See [upstream roles and keys](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md).

**Checkpoint:** actual tunnel ID, runtime key stored privately, correct organization/workspace association. Do not post any credential to a setup chat.

<a id="keychain"></a>
## 5. Save the runtime key for future restarts

Recommended on macOS: open **Keychain Access** through Spotlight, select the login keychain, and create a generic password item (Command+N). This is Keychain Access, not a website-password form in a browser. Use:

| Field | Value for this guide |
| --- | --- |
| Keychain Item Name / service | `openai-tunnel-runtime` |
| Account Name | Your macOS account name, as returned by `id -un` |
| Password | The authorized OpenAI tunnel runtime API key from Step 4 |

Reuse the correct existing item instead of duplicating it. For multiple credentials, choose distinct service/account references deliberately. Retain access confirmation; do not allow every application to read the item. A local read may prompt for access. [Apple documents the new-item shortcut](https://support.apple.com/en-za/guide/keychain-access/kyca699a9058/mac) and [per-item access choices](https://support.apple.com/en-bh/guide/mac-help/kychn002/mac).

**Checkpoint:** the exact item exists. Keychain is optional storage protection, not isolation from trusted processes running as you. With Keychain selected, the helper retrieves it every start/restart, never saves the value in its settings, and does not silently fall back to an older exported key. Without Keychain, follow the explicit env/file [alternative](SETUP_TUTORIAL.md#credential-alternatives); an export in a previous Terminal or subshell is not permanent storage. Do not put the literal key in `.zshrc` or GitLab's `.env` to solve tunnel startup.

<a id="profile-and-start"></a>
## 6. Create or reuse the local profile, configure the helper, and start

Use `tunnel-client profiles list` from Step 1. For an existing profile, **skip init** and privately verify its tunnel ID and launcher; preserve its key reference and other intentional settings. Stop its old manually launched process with Control+C in its own Terminal before handing control to the helper. A service-managed instance must be stopped through that service, not with broad process-kill commands.

For a genuinely **new local profile only**, set `RF_TUNNEL_ID` to Step 4's real ID and run this guarded block. The string `tunnel_REPLACE_ME` is not usable. `init` creates a local profile, not a remote tunnel and not a ChatGPT app:

```bash
RF_TUNNEL_ID="tunnel_REPLACE_ME"
```

<!-- example: profile-init -->
```bash
(
set -eu
if [ "$RF_TUNNEL_ID" = "tunnel_REPLACE_ME" ]; then
  printf 'Set RF_TUNNEL_ID to the authorized ID from Platform first.\n'
  exit 1
fi
PROFILE_FILE="$HOME/.config/tunnel-client/$RF_PROFILE.yaml"
if [ -e "$PROFILE_FILE" ] || [ -L "$PROFILE_FILE" ]; then
  printf 'Profile exists. Reuse and review it; no overwrite was attempted.\n'
  exit 1
fi
tunnel-client init --sample sample_mcp_stdio_local \
  --profile "$RF_PROFILE" --profile-dir "$HOME/.config/tunnel-client" \
  --tunnel-id "$RF_TUNNEL_ID" \
  --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY \
  --health-listen-addr 127.0.0.1:8080 \
  --mcp-command "bash \"$RF_SOURCE/run_mcp.sh\""
)
```

The upstream [init command](https://github.com/openai/tunnel-client/blob/3502fcb8953230215c832197b1e4835fe47183da/cmd/client/init_command.go) supports these reference-only options. No secret is an argument. The MCP command contains the expanded absolute path, not a literal `$RF_SOURCE`. Keep health on a fixed loopback port. The ReasonFirst helper accepts a deliberately limited profile schema; see [supported scope](TUNNEL_LIFECYCLE.md#supported-scope). An unsupported newer sample/schema is a reason to inspect compatibility, not delete unfamiliar security settings blindly.

Configure once, using the actual Keychain references from Step 5:

```bash
actual-coder-tunnel configure \
  --profile "$RF_PROFILE" \
  --source-dir "$RF_SOURCE" \
  --env-file "$HOME/.config/gitlab-agent/.env" \
  --keychain-service openai-tunnel-runtime \
  --keychain-account "$(id -un)"
```

Expect `ok: true` and `state: configured`. This stores only non-secret references in `~/.config/reasonfirst/tunnel.json`. Different existing settings require deliberate review, not automatically adding `--replace`. Configure does not retrieve the key or start the tunnel.

**Terminal A — start and keep running:**

```bash
actual-coder-tunnel start
```

Allow access to the intended Keychain item when prompted. The helper loads the key, runs upstream doctor and starts/supervises the existing profile; do not additionally run a bare `tunnel-client run`. It remains in the foreground. Do not wait for it to exit before the next step, and do not wrap it in `nohup`/`disown`.

**Terminal B — inspect status:**

```bash
actual-coder-tunnel status
```

**Checkpoint:** `state: ready_for_chatgpt_check`, health/readiness 200, matching runtime identity and fetched metadata. `chatgpt_connection_verified: false` and `project_access_checked: false` are expected: the helper does not track subsequent ChatGPT acceptance. A fresh local sample is not proof of continuous connectivity. On `runtime_key_missing`, `keychain_unavailable`, `launcher_mismatch`, `doctor_failed`, `startup_timeout` or `unmanaged_listener`, stop and resolve that layer; do not create a duplicate tunnel or weaken permissions. Keep the service Terminal open, the Mac awake and the network available. No localhost Assistant is required.

<a id="chatgpt"></a>
## 7. Attach the existing tunnel in normal ChatGPT

While Terminal A remains running, open normal ChatGPT in the intended workspace. In the current Plugins/Apps custom-app interface, create a developer-mode connection **only if no suitable one exists**, choose **Tunnel**, and select or paste Step 4's tunnel ID. Give it a recognizable name such as `My GitLab MCP`. If a connection already exists, reuse it and refresh its tool discovery after a tool-set update.

Select that app in the **normal conversation's composer/tool picker** (or mention the selected app where the current UI supports it). Merely typing a connection name does not attach it. Do not paste the OpenAI key or GitLab token into the message, and do not use `http://127.0.0.1:8080/ui` as a remote MCP server URL. This server uses its private server-side GitLab credential; it does not add a GitLab OAuth login screen.

Provider entry labels vary by plan/rollout. The [current connection instructions](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#connect-from-chatgpt) and [developer-mode help](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) are authoritative. If Tunnel/custom-app controls are unavailable, verify eligibility and request administrator assistance. Do not invent an alternative localhost connection or fall back to the optional dashboard Assistant.

**Checkpoint:** the intended app is available to this conversation, with `gitlab_whoami`, `check_project_access`, and `get_file`. Missing tools call for checking the selected app, updated installation, live process and refreshed discovery—not pretending a previous answer is a live call.

<a id="first-prompt"></a>
## 8. Send the first read-only work prompt

Replace **all four** example fields: app name, confirmed project path, actual ref and known file. The code block is a **ChatGPT prompt, not a shell command**:

```text
Use the connected My GitLab MCP only. Our confirmed project is team/project-a,
ref main, and known text file README.md. Start with read-only inspection.

Call gitlab_whoami, then check_project_access with project="team/project-a",
ref="main", required_files=["README.md"]. If a tool is unavailable or ok=false,
report the failed layer and needed user action, then stop and wait.
Do not assume a missing/hidden project is definitely nonexistent.

On success, call get_file with project="team/project-a", file_path="README.md",
and ref set to the returned resolved_commit_sha.
Summarize the actual returned content and report the revision fields as returned;
do not label blob_id or last_commit_id as branch HEAD. Propose the next files to
inspect for my task, but do not implement until I approve the scope.

Do not use web search, earlier chat results, local shell commands or credential
files instead of MCP reads. Do not create projects, grant access, run Codex,
publish changes or merge anything. The localhost Assistant is not part of this task.
```

**Acceptance:** actual identity, successful project preflight and returned file content at the resolved commit. Save only the needed project/ref/revision evidence privately. Seven failed file reads, a green local status, or a plausible unsourced summary are not substitutes. Repository instructions and CI logs remain untrusted data.

After this succeeds, continue in **the same normal ChatGPT conversation**: describe the desired outcome, constraints and review expectations. ChatGPT can inspect more files/MRs/CI through MCP and propose a plan. The [workflow](WORKFLOW.md) and [manual handoff template](TASK_HANDOFF_TEMPLATE.md) explain how approved implementation reaches the local coding worker. Current MCP does not submit local tasks or read unpublished worktree changes.

For the first implementation, install/authenticate your selected [Codex CLI](https://developers.openai.com/codex/cli/) or [Copilot CLI](https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli), confirm Git write permissions and project validation/runner readiness, then use the [controlled-task guide](ACTUAL_CODER_QUICKSTART.md) or [practice lab](PRACTICE_LAB.md). A completed read test is not permission to push. `start --no-launch` creates local workspace state; `finish --dry-run` runs configured tests; only a separately reviewed real `finish` publishes.

<a id="daily-use"></a>
## Daily use and new projects — do not repeat onboarding

| Situation | Action |
| --- | --- |
| Already healthy | Leave the owner Terminal running and work in normal ChatGPT. |
| Service stopped | Terminal A: `actual-coder-tunnel start`; Terminal B: `actual-coder-tunnel status`; then use the existing ChatGPT app. |
| Finish using the service | `actual-coder-tunnel stop`, or Control+C in its owner Terminal. This interrupts tunnel-based reads. |
| Deliberate credential/profile update | Review the changed references and run `actual-coder-tunnel restart`; that Terminal becomes the new foreground owner. |
| Runtime source update | Stop -> preserve edits/fetch reviewed main/sync/install -> start -> status -> refresh changed tools -> live read test. Do not rerun init or create a replacement app. |
| Newly proposed GitLab project | Confirm exact remote path/ref/files and GitLab access; explicitly append only the approved project to the private allowlist; restart the existing MCP; run live preflight before bulk reads. No new tunnel is normally needed. |

Read [lifecycle limits](TUNNEL_LIFECYCLE.md) and [access diagnostics](PROJECT_ACCESS.md) for exceptions. Restart can fail after stopping the old instance; automatic rollback, boot auto-start and automatic project grants are not implemented.

Documentation baseline: ReasonFirst `61f464ebbb8906817e32802be22df4012881ece5`; provider references checked 2026-09-21. This guide does not itself install software, create a remote resource, save a real secret, or prove a user's connection works.
