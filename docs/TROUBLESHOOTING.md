# Troubleshooting by layer

[Workflow](WORKFLOW.md) · [ChatGPT connection and restart](SETUP_TUTORIAL.md) · [中文](OPENAI_TUNNEL_TEAM_SETUP_CN.md) · [Security](../SECURITY.md)

Identify the failing client before changing configuration. A working CLI, a started tunnel, a localhost Assistant session, and a live normal ChatGPT tool call are different pieces of evidence. Do not reinstall, recreate a tunnel/workspace, disable verification, broaden permissions, or rotate an otherwise working key without a reason. An exposed active credential must still be revoked/rotated regardless of successful tests.

## Localhost Assistant says a tool requires approval

The dashboard's Assistant (`/ui#codex`) is an upstream Codex interface, not normal ChatGPT. In tunnel-client revision `0f870e50a973fa820d4c409000059e181e8d242b`, that panel submits `approval_policy: never`; calls requiring approval can therefore be blocked. This describes that observed version, not every future client.

**The localhost Assistant is optional and is not a ReasonFirst acceptance gate.** Close that tab, keep the tunnel process running, and use the selected GitLab connection in normal ChatGPT. Do not set global approvals to auto-accept, request unrestricted execution, or bypass a blocked MCP call with a token-bearing shell command. If you separately need this upstream panel, investigate its supported approval interface as an independent integration task.

Not using the panel does not uninstall Codex or stop its bundled helper. Keep the coding CLI for implementation. A local dashboard error alone does not establish that the normal ChatGPT connection failed.

## Old source directory in the tunnel profile

Run `tunnel-client profiles list` and inspect the returned file privately. Correct only the approved `main` MCP command to the long-lived checkout, for example:

```yaml
mcp:
  commands:
    - channel: main
      command: "bash /absolute/path/to/reasonFirst/run_mcp.sh"
```

Keep the existing Tunnel ID and credential reference. An absolute command does not change when you `cd` elsewhere. Stop the specific old process and use the [restart procedure](SETUP_TUTORIAL.md#restart-an-existing-profile). Do not recreate the profile or use a temporary PR checkout for a permanent launcher.

## Missing CONTROL_PLANE_API_KEY

`api_key: "env:CONTROL_PLANE_API_KEY"` needs that exported variable in the process that runs the tunnel. It is an OpenAI runtime key for tunnel use, not the Tunnel ID or GitLab token. Retrieve the existing value from approved secret storage; the restart guide includes a hidden prompt for interactive use. An environment export in an exited subshell does not persist in a new shell or service.

A missing variable is not a rejected/expired key. For actual control-plane authentication/permission failures, check the key and the applicable organization/workspace permissions using the official guide. Do not use an admin key for the daemon. Existing `file:` references need their protected file, not a new environment export. An optional Codex-plugin SKIP is unrelated.

## Startup succeeds but normal ChatGPT has no tools

Keep the tunnel running. Check the selected connection in that conversation, intended ChatGPT workspace association, tunnel-use permissions, and actual profile target. Refresh/discover tools through the current provider UI. `started`, metadata fetched, and health/readiness responses are not proof of an identity/file call.

Use [the live read test](SETUP_TUTORIAL.md#accept-the-read-connection). Do not use cached answers, web search, local credential notes, or a separately running localhost Assistant as substitutes. MCP Inspector is an optional isolated debugging tool, not an extra account/login step everyone must complete.

## Stale HTTP notes or a credential appears in command output

A Markdown note is not an effective-configuration report. Check `actual-coder config` and the actual MCP startup configuration rather than assuming a displayed note changed the running server. Locate the note privately using filename-only searches; do not print the matched credential lines into a chat.

Remove credentials and token-bearing commands from reference notes. Never replace an exposed value with the new token in that note. Revoke/rotate active exposed credentials, update the affected private store, clear stale exported overrides, and restart the process. Do not post unredacted screenshots, profile files, `.env`, logs, or migration backups. Do not merely disable context injection and assume stored copies are gone.

## API, native Git, and the migration probe disagree

Use the [runtime TLS guide](HTTPS_API_TLS.md). `GITLAB_CA_BUNDLE` configures the shared Python API/MCP client, not native Git or the migration probe. A credential-free probe can verify TLS and return 401/403 while authenticated access remains untested. A probe returning 404/500 is not complete service readiness.

Configure the final API endpoint: all shared-client API redirects and disabled certificate verification are rejected. Do not follow a login redirect with a PAT or fall back to HTTP. The legacy `smoke_test.py` helper does not use the shared runtime client factory and is not proof of the current request guards.

For existing HTTP caches use the reviewed [migration procedure](HTTPS_MIGRATION.md). A repeat preview with no changes is sufficient local URL convergence evidence; do not apply again solely to restart MCP. A successful Git fetch does not establish push rights.

## Proxy errors or Git HTTP 502

For an approved direct route, the private user config normally keeps:

```dotenv
GITLAB_TRUST_ENV=false
GITLAB_GIT_TRUST_ENV=false
```

Python and Git proxy policy are separate from the tunnel's outbound OpenAI route and from CA trust. A 502 can have other server/network causes; do not assume every 502 is a proxy failure. Inspect proxy settings privately: proxy URLs can contain credentials, so avoid posting `env` or full Git configuration. Do not install SOCKS support or enable proxy inheritance unless that route is actually intended.

## GitLab CLI works but ReasonFirst returns HTTP 401

`glab auth status` and ReasonFirst do not necessarily use the same credential source. `glab` may be authenticated from the operating-system keyring while ReasonFirst reads `GITLAB_TOKEN` from the selected private `~/.config/gitlab-agent/.env`.

If `glab api user --hostname <host>` succeeds but `gitlab_whoami` returns 401, inspect only whether the configured ReasonFirst token is present/current; do not print the value into chat or logs. Refresh the private ReasonFirst credential through your approved secret-management path, then restart the GitLab MCP/tunnel consumer so it reloads the file. The OpenAI tunnel runtime key is unrelated to GitLab authentication.

## Bridge worker fails before or immediately after start

Two Codex App Server compatibility failures were fixed after the original v0.3.0 tag:

- a `thread/start` policy wire-value mismatch with current Codex App Server; and
- a synthetic `mcp_servers.reasonfirst.enabled=false` override that could create an MCP entry with no transport and abort App Server initialization.

If an updated checkout still shows `INVALID_ARGUMENT`, `invalid transport`, or App Server initialization errors, record the ReasonFirst commit and Codex version, update to a reviewed current `main`, restage the Bridge runtime, and restart the MCP service before retrying. Reuse the existing managed workspace when it is still clean; repeated `dispatch` calls create unnecessary workspaces.

## Worker starts but the model stream repeatedly reconnects or times out

A healthy local MCP and a successfully created Codex thread do not prove that the launchd-managed worker can reach its external model service. On macOS, LaunchAgents do not automatically inherit the interactive shell's proxy variables.

If your network requires `http_proxy` / `https_proxy` / `all_proxy`, sync only the current shell's proxy variables into the current launchd user session:

```bash
bash tools/reasonfirst_v4_0_3/sync_launchd_proxy_env.sh sync
launchctl kickstart -k "gui/$(id -u)/com.reasonfirst.v4-mcp"
```

The helper prints variable names/status only and does not write proxy values to a plist or source file. The launchd user-session environment is still visible to processes running as the same user and is cleared at logout/reboot. To remove the propagated values deliberately:

```bash
bash tools/reasonfirst_v4_0_3/sync_launchd_proxy_env.sh clear
```

After restarting, wait for `http://127.0.0.1:8765/healthz` before continuing the same worker thread. ICMP `ping` is not a reliable proxy-path test: an HTTP(S) proxy can work even when the destination drops ICMP.

## ActualCoder installation or effective config differs

From a stable checkout, see [local installation updates](LOCAL_PR_REVIEW.md). Inspect `actual-coder config` and the interpreter/source path locally. A global editable tool and `uv run` in a temporary checkout may use different environments. Package version `0.3.0` alone does not identify the source commit.

Config selection is `GITLAB_AGENT_ENV_FILE`, then the stable user config, then local `.env`; already-exported values take precedence. CLI fallback is relative to its working directory; MCP fallback is relative to its server source. Do not overwrite a working config with `.env.example` or rename managed workspace directories to match a source-repository rename.

## Missing project contract or rejected command

`found: false, valid: true` means no `.actualcoder.yaml` was loaded, not that tests passed. Define real test commands in the target repository through its normal review process. Use exact project paths for managed workspaces; API numeric IDs do not replace the path-based local workflow. Do not broaden the user allowlist to silence a local rejection.

Repository policy cannot add executable permissions. A required executable must be deliberately allowed by the user, and running a permitted interpreter is still not an OS sandbox. Finish uses policy from the original workspace base, so a newly merged contract does not silently change old tasks.

## Backend quota or existing workspace recovery

For an existing task, set `WS` to its real ID and prepare a replacement handoff:

```bash
actual-coder resume "$WS" --agent copilot --goal "Continue the same approved task and acceptance criteria"
```

This returns a handoff; it does not automatically launch the agent. Review it and carry the [manual task requirements](TASK_HANDOFF_TEMPLATE.md), because project context is not yet uniform across all routes. Do not start a second workspace just to switch backend.

Only when local state is unavailable and the branch remains remote, reconstruct the existing MR using the real project and IID:

```bash
actual-coder checkout-mr team/project-a 123 --agent copilot --goal "Continue the existing MR within its reviewed scope"
```

Recovery refuses to overwrite unpublished abandoned branches. After implementing, use reviewed `finish --dry-run` and confirmed `finish`, not lower-level push commands as an equivalent safety gate. `cleanup --force` is an intentional discard operation, not recovery. See [the quickstart](ACTUAL_CODER_QUICKSTART.md) for the full workflow.

## Evidence to share safely

Share the first failing check, tool/source revision, shell/platform, and a small sanitized result. State the client (CLI, tunnel, normal ChatGPT, optional Assistant), what was actually attempted, and whether logs were complete. Do not demand a fresh push or full build just to accept a read connection. No successful diagnostic makes an exposed credential safe.

Primary references: [OpenAI tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [upstream operator guide](https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md), [versioned Assistant implementation](https://github.com/openai/tunnel-client/blob/0f870e50a973fa820d4c409000059e181e8d242b/adminui/src/components/CodexPanel.svelte).
