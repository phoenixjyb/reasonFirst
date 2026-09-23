# ReasonFirst v4.0.2 历史设计记录

v4.0.3 的实际安装与网页接入说明以包根目录 [README.md](../README.md) 为准。

这是 v4.0.2 的设计目标，不代表普通 ChatGPT 会话已获得本地 MCP 工具。v4.0.3 的当前网页入口仍是 GitHub Issue connector。

## Responsibility boundary

```text
ChatGPT Web or ChatGPT Desktop chat
        │
        │ analysis / plan / review / approval
        ▼
ReasonFirst MCP on Mac
        │
        ├── workspace / SSH / credentials / evidence / safety gates
        │
        └── dedicated local Codex app-server
                │
                │ implementation / build / test / reviewed push only
                ▼
        local workspace or SSH target workspace
```

**ChatGPT is the planner/reviewer. Codex is the executor.** Codex is not asked to choose architecture or redefine the task.

## What changed from v3

- Codex 可直接调用本地 MCP；普通 ChatGPT Web/App 在没有 MCP 连接时仍使用 GitHub relay。
- `run_reasonfirst.sh` starts the MCP STDIO server, **not** `github_control_relay.py`.
- 普通 ChatGPT Desktop 聊天不会自动读取 Codex 的 MCP 配置或个人插件。
- ChatGPT Web 只有在开发者模式下注册连接后，才能通过 Secure MCP Tunnel 使用本地服务。
- 在该账号无法使用开发者模式期间，GitHub relay 是普通聊天的可用控制通道。
- Codex runs locally and inherits the normal global Codex configuration/login state.
- SSH targets do **not** need Codex installed.
- Remote commit/push is performed by Codex only after ChatGPT approves the exact reviewed snapshot.

## Codex global configuration

ReasonFirst launches a dedicated local `codex app-server` so ChatGPT chat remains independent from the execution backend. The child process reads the normal Codex config stack, including:

- `~/.codex/config.toml`
- trusted project `.codex/config.toml`
- model/provider settings
- rules and skills
- all other configured MCP servers
- normal Codex authentication state

The only per-process override is:

```text
mcp_servers.reasonfirst.enabled=false
```

This prevents the executor Codex from recursively invoking the ReasonFirst MCP server. It does **not** modify `~/.codex/config.toml` and does not disable other global configuration.

## Install / upgrade

```bash
cd ~/Downloads/reasonfirst_codex_web_bridge_v4_0_2

./apply_to_reasonfirst.sh \
  /path/to/reasonFirst
```

Then configure once:

```bash
/path/to/reasonFirst/tools/codex_web_bridge/configure_v4.sh
```

`configure_v4.sh`:

1. migrates `~/.config/reasonfirst/bridge.yaml` to v4;
2. preserves named SSH targets;
3. backs up `~/.codex/config.toml`;
4. replaces only `[mcp_servers.reasonfirst]`;
5. preserves all other Codex global configuration.

Restart ChatGPT Desktop / Codex after changing MCP configuration.

## Doctor

```bash
/path/to/reasonFirst/tools/codex_web_bridge/run_reasonfirst.sh --doctor
```

## ChatGPT Desktop

After `configure_v4.sh`, restart Codex. ReasonFirst is configured for Codex as a local MCP server. This does not add tools to ordinary ChatGPT Desktop chat.

The MCP command is:

```bash
/path/to/reasonFirst/tools/codex_web_bridge/run_mcp_server.sh
```

Do not expect a normal log stream when launching `run_reasonfirst.sh` manually without `--doctor`; an MCP STDIO server normally waits for JSON-RPC on stdin.

## ChatGPT Web via Secure MCP Tunnel

Requirements: a Platform MCP tunnel id, tunnel permissions, ChatGPT developer-mode/app access, `tunnel-client`, and a runtime API key exported locally.

```bash
export CONTROL_PLANE_API_KEY='...'

/path/to/reasonFirst/tools/codex_web_bridge/configure_v4_tunnel.sh \
  tunnel_xxx

/path/to/reasonFirst/tools/codex_web_bridge/run_v4_tunnel.sh
```

The tunnel is outbound-only. The Mac does not need an inbound public port.

## ChatGPT-facing MCP tools

Read / analysis:

- `reasonfirst_doctor`
- `reasonfirst_target_probe`
- `reasonfirst_workspace_status`
- `reasonfirst_files`
- `reasonfirst_read`
- `reasonfirst_diff`
- `reasonfirst_codex_status`
- `reasonfirst_codex_events`
- `reasonfirst_review_bundle`
- `reasonfirst_artifacts`

Task / execution:

- `reasonfirst_dispatch`
- `reasonfirst_codex_start`
- `reasonfirst_codex_continue`
- `reasonfirst_codex_steer`
- `reasonfirst_codex_interrupt`
- `reasonfirst_authorize_push`

## Normal workflow

```text
user request
  ↓
ChatGPT dispatches/prepares workspace
  ↓
ChatGPT reads real source and makes the plan
  ↓
ChatGPT starts Codex with the reviewed plan + acceptance criteria
  ↓
Codex edits and runs build/tests
  ↓
ChatGPT reads review_bundle + real diff + artifacts
  ↓
(optional iterations)
  ↓
ChatGPT calls reasonfirst_authorize_push
  ↓
ReasonFirst pins the exact snapshot digest
  ↓
ChatGPT instructs existing Codex thread to push
  ↓
Codex calls reasonfirst_remote.commit_push
```

If the source changes after push approval, the digest changes and push is rejected. ChatGPT must review and authorize again.

## Push safety

Automatic remote push:

- only `chatgpt/*` feature branches;
- never force-push;
- does not push `main` directly;
- runs `git diff --check`;
- blocks common secret/private-key paths;
- scans obvious private-key / GitLab token / OpenAI-key patterns;
- uses existing ReasonFirst GitLab credentials only through a temporary `GIT_ASKPASS` on the SSH channel;
- does not persist GitLab credentials on the target;
- requires an exact ChatGPT-reviewed snapshot digest.

## Legacy GitHub relay

Still available explicitly for rollback/audit only:

```bash
tools/codex_web_bridge/run_github_relay.sh
```

`run_reasonfirst.sh` never launches it in v4.

## v4.0.1 startup bug fixed

v4.0.1 could write a v4 `bridge.yaml` but still launch the v3 GitHub relay, producing:

```text
BridgeConfigError: Unsupported bridge config version
```

v4.0.2 fixes both sides:

- `bridge_config.py` accepts/migrates v3 and v4 config;
- `run_reasonfirst.sh` launches MCP only.
