# Manual and advanced GitLab MCP connection operations

[简体中文 / Windows](OPENAI_TUNNEL_TEAM_SETUP_CN.md) · **[Complete first-time setup](GETTING_STARTED.md)** · [Managed daily lifecycle](TUNNEL_LIFECYCLE.md)

**New users should follow [GETTING_STARTED.md](GETTING_STARTED.md), not assemble a deployment from this fallback guide.** It covers prerequisites, installation, Platform/workspace permissions, runtime key, Keychain, profile creation, the new lifecycle helper, and the first normal ChatGPT request. This page preserves manual/advanced alternatives and earlier section links.

<a id="first-time-connection"></a>
## First-time connection

Use the [ordered first-time guide](GETTING_STARTED.md). Reuse existing GitLab configuration, tunnel ID and local profile; do not run `init --force`, overwrite `.env` or create a replacement tunnel to troubleshoot a restart. Install `tunnel-client` separately from ReasonFirst. It is required for this connection; a coding backend is required only for later implementation.

There are separate permissions for OpenAI Platform tunnel management/use, the ChatGPT workspace's custom-app access, and GitLab/local MCP project authorization. `CONTROL_PLANE_API_KEY` is the OpenAI runtime key, `tunnel_...` is the tunnel ID, and `GITLAB_TOKEN` is the GitLab API credential. The private local `GITLAB_ALLOWED_PROJECTS` controls project grants; it is not an OpenAI tunnel setting.

<a id="restart-an-existing-profile"></a>
## Restart an existing profile

For a configured macOS/Linux helper, use [start/status/stop/restart](TUNNEL_LIFECYCLE.md). Its foreground Terminal must stay open. For a service-managed runtime, use its existing supervisor and secret loader. **Do not mix manual, helper-managed and service-managed processes for one tunnel.**

Only use the manual route below for an intentionally unmanaged or advanced profile. Stop the known old foreground instance with Control+C in its own Terminal. Do not use `pkill`, `killall` or saved PID guesses.

```bash
tunnel-client profiles list
```

Inspect the returned profile in a local editor, not by posting its contents to chat. Its main stdio command must use the permanent reviewed source path. This example is a fragment, not a replacement profile:

```yaml
mcp:
  commands:
    - channel: main
      command: 'bash "/absolute/path/to/reasonFirst/run_mcp.sh"'
```

Changing Terminal's directory does not update that absolute path. `run_mcp.sh` launches the local stdio MCP, not a tunnel by itself. Preserve the existing tunnel ID and secret reference; an intentionally advanced profile may be unsupported by the conservative lifecycle helper without being invalid upstream.

<a id="credential-alternatives"></a>
## Credential alternatives

The first-time Mac path uses an exact existing Keychain item. Keychain is optional, but **some explicit runtime-key source is required**. The helper saves references, not credential values, and does not switch credential sources when loading fails.

**Helper with env/file credentials:** use `actual-coder-tunnel configure` without its two Keychain options, selecting the intended source/profile/private GitLab config. A profile with `env:CONTROL_PLANE_API_KEY` requires that variable in the launching shell on every start; use an approved secret loader or a hidden local prompt. A profile with `file:/absolute/path` uses a regular user-owned, nonsymlink private key file (0600), resolved by upstream. For an existing different helper configuration, review the change before an explicit `configure --replace`; never change it while the owner is running.

**Manual env-reference startup:** first open the actual permanent source checkout. The following explicitly invokes Bash and prompts only when this shell has no runtime key. Replace the example profile name. It does not save the input or put the literal key into shell history:

```bash
bash <<'BASH'
set +x
set +v
set -eu
PROFILE="selfhosted-gitlab"
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
test -f "$GITLAB_AGENT_ENV_FILE"
test -f run_mcp.sh
unset GITLAB_BASE_URL GITLAB_TOKEN GITLAB_GIT_TOKEN GITLAB_ALLOWED_PROJECTS
if [ -z "${CONTROL_PLANE_API_KEY:-}" ]; then
  read -r -s -p 'OpenAI tunnel runtime API key (hidden): ' CONTROL_PLANE_API_KEY </dev/tty
  printf '\n'
fi
if [ -z "${CONTROL_PLANE_API_KEY:-}" ]; then
  printf 'No runtime key supplied. Nothing was started.\n'
  exit 1
fi
export CONTROL_PLANE_API_KEY
tunnel-client doctor --profile "$PROFILE" --explain && tunnel-client run --profile "$PROFILE"
BASH
```

These unsets choose the corresponding values in the selected private GitLab file for this child shell. Other intentional overrides need operator review. An old shell export is not permanent storage; a missing variable does not establish that the key expired. Do not write a literal key into `.zshrc`, repository files or token-bearing curl examples. This is not isolation from same-user processes.

For an existing **file-reference profile**, retain its protected file/loader. Do not replace it with environment loading merely to use the block above. Run upstream doctor/run for the existing profile through the approved service or shell, without an unnecessary key prompt.

The GitLab `.env` loader does not interpret a Keychain/file reference inside `GITLAB_TOKEN`. Its plaintext file permissions and the tunnel key's selected secret source are distinct. Keep API/MCP CA trust, native Git TLS and the separate migration probe distinct; see [runtime TLS](HTTPS_API_TLS.md). Never turn verification off to fix a CA error.

## Diagnose the running service

The upstream localhost Overview/Logs and `/healthz` / `/readyz` are optional diagnostics, not the acceptance test. Use the address actually reported by your profile. Keep the admin interface on loopback. A working dashboard Assistant, Codex tunnel plugin and Inspector session are **not required**.

An error confined to `/ui#codex`, such as `approval policy is never`, does not establish that normal ChatGPT transport failed. Do not weaken global approvals or grant unrestricted shell access for that optional panel. Closing the browser does not stop the tunnel or guarantee its bundled helper is disabled.

<a id="accept-the-read-connection"></a>
## Accept the read connection

Keep the chosen runtime running. In normal ChatGPT, select the intended existing app in the correct workspace; refresh tool discovery after runtime tool changes. Replace the confirmed project/ref/file and app name below:

```text
Use the connected My GitLab MCP only. Call gitlab_whoami, then
check_project_access(project="team/project-a", ref="main", required_files=["README.md"]).
If a tool is unavailable or ok=false, report the diagnostic and needed user action,
then stop and wait. Do not infer definite nonexistence from a hidden/missing project.
On success, get_file with project="team/project-a", file_path="README.md" and
ref=the returned resolved_commit_sha. Summarize actual content and report only
returned revision fields. No web/old-chat/shell/credential-file fallback, project
creation, automatic grants, implementation, publication or merging.
```

Identity success is not project access; an empty filtered listing is not proof that no project exists. Access grants belong to the local MCP allowlist plus the independent GitLab permissions, not a tunnel recreation. Follow [PROJECT_ACCESS.md](PROJECT_ACCESS.md). Do not replace an unprepared practice project with a production application.

A real successful identity/preflight/file read is read-path acceptance only, not a Git push, coding-agent login or full application build. Continue with [the workflow](WORKFLOW.md) and [manual handoff](TASK_HANDOFF_TEMPLATE.md) only after approving the task. The **read-only GitLab MCP** documented on this page has no local task-execution endpoint; the separate Bridge Preview provides optional managed workspace/App Server orchestration when the connected client permits it. The old `smoke_test.py` does not use the shared runtime TLS factory and is not this guide's acceptance gate.

## Primary references

[OpenAI tunnel setup and current ChatGPT connection UI](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [developer-mode eligibility](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt), [upstream configuration](https://github.com/openai/tunnel-client/blob/master/docs/configuration.md), [upstream operator guide](https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md).
