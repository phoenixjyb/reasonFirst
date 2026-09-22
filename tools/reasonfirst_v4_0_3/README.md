# ReasonFirst v4.0.3

ChatGPT 负责分析、规划和审查；Mac 本地 Codex 负责改代码、构建、测试和经审查的提交。SSH 目标机不需要安装 Codex。

## 一键安装

```bash
cd reasonfirst_v4_0_3
./apply_to_reasonfirst.sh /path/to/reasonFirst
```

默认安装只更新 ReasonFirst 核心运行时与 bridge/Codex MCP 配置；不会因为本机已有登录态就自动安装插件、启动 LaunchAgent、开启 GitHub relay 或配置 Tunnel。先用 `./configure_v4.sh --plan ...` 检查计划，再显式选择持久化集成。

例如：

```bash
./configure_v4.sh --plan --worker-backend codex-desktop --install-codex-plugin --enable-login-service
./configure_v4.sh --worker-backend codex-desktop --install-codex-plugin --enable-login-service
./configure_v4.sh --enable-login-service --enable-web-relay
./configure_v4.sh --enable-login-service --configure-tunnel tunnel_xxx
```

只有 `--enable-login-service` 才会修改登录环境中的本地代理例外、stage 登录运行时并安装 MCP LaunchAgent；Web relay/Tunnel 也必须显式开启。

## 选择 coding backend

ReasonFirst 保留用户对实际 coding worker 的选择，不会把 Codex Desktop/App Server 设为唯一后端。

```bash
./configure_v4.sh --worker-backend codex-cli
./configure_v4.sh --worker-backend copilot-cli
./configure_v4.sh --worker-backend codex-desktop
```

也可以设置 `RF_WORKER_BACKEND`。配置会写入 `bridge.yaml` 的 `defaults.worker_backend`。三种后端共享上游 `WorkerPolicy` 的模型、reasoning effort、sandbox/network/permission 语义；若某一后端无法表达请求的策略，应明确失败，不得静默降级。

当前本 PR 中的 App Server 专用 MCP 启动/线程工具只用于 `codex-desktop`。选择 `codex-cli` 或 `copilot-cli` 时，继续使用 ActualCoder 的 CLI worker 路径，不能被 App Server 路径悄悄替换。

## 启动与检查

若用户显式执行 `--enable-login-service`，本地 MCP 才会由 `com.reasonfirst.v4-mcp` 在登录后自动启动，地址为 `http://127.0.0.1:8765/mcp`。只有显式安装的 Codex 插件和 Web relay 才会连接该 MCP 进程及 `~/.local/share/reasonfirst/codex-web-bridge/state.json`。普通 ChatGPT Desktop 聊天不会因本地安装而自动获得该 MCP 工具。
启用登录服务时会把运行代码复制到 `~/.local/share/reasonfirst/v4-service`；源目录仍是更新入口。

```bash
cd /path/to/reasonFirst
curl -fsS http://127.0.0.1:8765/healthz
codex mcp list
./tools/codex_web_bridge/run_reasonfirst.sh --doctor
launchctl print gui/$(id -u)/com.reasonfirst.v4-mcp
launchctl print gui/$(id -u)/com.reasonfirst.web-bridge
```

只有显式使用 `--install-codex-plugin` 时才会通过 Codex CLI 安装并启用个人插件。安装后重启 Codex，以重新读取插件与 MCP 配置。`codex plugin list` 和 Codex 中的实际工具调用只验证 Codex 接入，不代表普通 ChatGPT Web/App 会话已接入。

## 网页端

当前账号不能使用 ChatGPT 开发者模式，网页端沿用此前确定的**私人 GitHub Issue + GitHub connector**入口。relay 将命令转发给同一本地 MCP 进程；GitHub 仅负责网页消息进出，不保存另一份任务 state。`gh auth status` 和私人控制仓库必须可用，网页端才能实测。若网络或凭据失效，本地 MCP 与 Desktop 仍可使用。

普通 ChatGPT Web/App 会话目前没有直接可调用的 ReasonFirst 工具，因此这里的 GitHub connector 是当前可用的网页控制入口，并非已经切换到直连。只有在该会话工具列表中出现 ReasonFirst MCP 工具，并实际成功调用后，才能宣称直连可用。不能通过本机配置让未注册的工具自动出现在普通聊天中。

若以后开通开发者模式，可设置 `RF_TUNNEL_ID` 与 `CONTROL_PLANE_API_KEY` 后重跑 `configure_v4.sh`，并在 ChatGPT 中注册连接，通过 Secure MCP Tunnel 将同一 MCP URL 接入 Web。Tunnel 客户端从 OpenAI 官方发布页下载安装并校验 SHA256；密钥保存在 macOS Keychain，不写入包内。未注册连接前，安装 Tunnel 本身也不会给聊天会话添加工具。

## 兼容与回退

`run_reasonfirst.sh` 启动 MCP，不再启动旧 GitHub relay；`run_github_relay.sh` 是显式的网页入口。配置版本 3/4 都可读取，安装会迁移到 4。原有配置备份位于 `~/.local/share/reasonfirst/backups`。

自动启动日志位于 `~/.local/share/reasonfirst/logs`。本地服务故障时先查看 `v4-mcp.stderr.log`；网页故障时查看 `web-relay.stderr.log` 与 `gh auth status`。
