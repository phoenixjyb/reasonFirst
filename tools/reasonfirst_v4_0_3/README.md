# ReasonFirst Bridge Preview

> Compatibility path: `tools/reasonfirst_v4_0_3/`. This directory name is retained for migration compatibility and **does not represent the ReasonFirst product version**. The product version is defined by the core package (`src/gitlab_agent/__init__.py` / `pyproject.toml`).

This preview adds an optional local MCP / Codex Desktop App Server surface around the normal ReasonFirst core. ChatGPT remains the planner/reviewer; coding execution remains a user-selected backend.

## Worker backends

ReasonFirst supports three explicit worker surfaces:

```text
--agent codex-cli      # Codex CLI
--agent copilot-cli    # GitHub Copilot CLI
--agent codex-desktop  # managed Codex Desktop App Server
```

`codex-cli` and `codex-desktop` consume the same user-owned Codex `WorkerPolicy` (model, reasoning effort, sandbox, approval and network policy). Historical `codex` and `copilot` names remain compatibility aliases for `codex-cli` and `copilot-cli`. The bridge does not replace the CLI backends.

## Safe installation flow

Stage the compatibility bridge files without changing user/global configuration:

```bash
cd tools/reasonfirst_v4_0_3
./apply_to_reasonfirst.sh /path/to/reasonFirst
```

The default staging operation does **not** modify Codex config, plugin marketplaces, LaunchAgents, proxy environment, GitHub relay or Tunnel configuration.

Review the configuration plan:

```bash
/path/to/reasonFirst/tools/codex_web_bridge/configure_v4.sh --plan
```

Then apply it explicitly:

```bash
/path/to/reasonFirst/tools/codex_web_bridge/configure_v4.sh
```

For an intentional one-command stage + configure flow:

```bash
./apply_to_reasonfirst.sh --configure /path/to/reasonFirst
```

Configuration backs up files it changes and installs the loopback MCP service. Optional external transports are not enabled merely because credentials happen to exist.

## Optional transports

The private GitHub Issue relay requires explicit opt-in:

```bash
export RF_ENABLE_WEB_RELAY=true
/path/to/reasonFirst/tools/codex_web_bridge/configure_v4.sh
```

The Secure MCP Tunnel requires both values explicitly:

```bash
export RF_TUNNEL_ID=tunnel_xxx
export CONTROL_PLANE_API_KEY=...
/path/to/reasonFirst/tools/codex_web_bridge/configure_v4.sh
```

Tunnel credentials are handled by the dedicated tunnel setup; they are not stored in this source tree.

## Local MCP and Desktop checks

The configured loopback MCP URL is:

```text
http://127.0.0.1:8765/mcp
```

Useful checks:

```bash
curl -fsS http://127.0.0.1:8765/healthz
uv run actual-coder agents
uv run actual-coder config
codex mcp list
launchctl print gui/$(id -u)/com.reasonfirst.v4-mcp
```

The health endpoint reports the **core ReasonFirst version** plus bridge/config-schema metadata.

## Git-only authentication

Git-only operation is a core ReasonFirst capability; this preview no longer patches core source code at install time.

For self-managed GitLab HTTPS authentication, use exactly one Git credential source:

- preferred: `GITLAB_GIT_TOKEN`
- optional self-managed fallback: `GITLAB_GIT_PASSWORD`
- API-token fallback remains last resort when neither separate Git credential is set.

Do not configure `GITLAB_GIT_TOKEN` and `GITLAB_GIT_PASSWORD` simultaneously.

Git-only diagnostics/start:

```bash
uv run actual-coder doctor --offline --git-only
uv run actual-coder start group/project --goal "..." --git-only --no-launch
```

The password bootstrap requires an explicit `group/project` allowlist entry; it does not silently enable writes to every accessible project.

## SSH execution targets

SSH targets must be defined by the user in `~/.config/reasonfirst/bridge.yaml`. MCP callers may select a configured target name but cannot introduce an arbitrary host/repository trust destination inline.

Example:

```yaml
targets:
  gpu-a:
    type: ssh
    host: gpu-a
    repo: /srv/reasonfirst/project
    codex_backend: desktop-proxy
    network_access: false
```

The default remote dynamic-tool surface supports managed workspace/status/file/read/write/apply-patch/diff operations. **Arbitrary remote shell execution is intentionally not exposed** until a real sandboxed runner is available.

## Remote publication

Remote SSH publication is experimental and **disabled by default**.

Without:

```bash
export RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH=true
```

the MCP server does not register `reasonfirst_authorize_push`, and the Codex remote dynamic-tool namespace does not advertise `commit_push`.

When explicitly enabled, approval is bound to the target, project, base SHA, HEAD, branch, origin URL, push URL and exact candidate Git tree, and the tree is rechecked immediately before publication. This experimental path still does not claim full parity with every local ActualCoder controlled-finish policy invariant.

The normal local `actual-coder finish` flow remains the default publication path.

## Web / ChatGPT availability

Installing a local plugin or MCP service does not automatically make ReasonFirst tools appear in every ChatGPT Web/App conversation. Direct ChatGPT access requires a supported registered connection/tool surface. The private GitHub relay remains an optional compatibility transport where appropriate.

## Strict read-only MCP compatibility mode

For connector compatibility testing, especially on ChatGPT surfaces that accept read-only custom MCP tools but reject write-capable tool sets, ReasonFirst can expose a strict read-only MCP surface:

```bash
RF_MCP_READ_ONLY=true bash tools/reasonfirst_v4_0_3/install_launch_agent.sh
```

The LaunchAgent persists that selection and restarts the local MCP service. In this mode the server exposes only inspection/status tools such as doctor, target probe, workspace/file/diff reads, worker status/events, CI, review bundle and artifacts. It does **not** register dispatch, worker-control, approvals, finish/publication, evidence generation, experimental remote push, or the `/control` POST route. `reasonfirst_finish_preview` is also intentionally omitted from this strict surface.

Check the active mode with:

```bash
curl -fsS http://127.0.0.1:8765/healthz
```

The response includes `"read_only_mode": true` when enabled.

Return to the normal full ReasonFirst MCP surface by reinstalling the LaunchAgent with:

```bash
RF_MCP_READ_ONLY=false bash tools/reasonfirst_v4_0_3/install_launch_agent.sh
```

The Secure MCP Tunnel profile and tunnel ID do not change when switching modes; only the local MCP tool surface is restarted.

## Runtime and rollback

The bridge runtime is staged under:

```text
~/.local/share/reasonfirst/
```

Configuration backups are stored under the ReasonFirst backup directory. Runtime dependency versions used only by this preview are pinned in `install_v4_runtime.sh`; the core ReasonFirst package remains the source of shared MCP/PyYAML dependencies.

Automatic-start logs are under:

```text
~/.local/share/reasonfirst/logs
```

Keep this preview subordinate to the core ReasonFirst architecture: one workspace model, one `WorkerPolicy`, one controlled publication policy, and user-authoritative backend/target selection.
