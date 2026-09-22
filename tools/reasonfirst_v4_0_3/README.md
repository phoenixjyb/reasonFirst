# ReasonFirst v4.0.3

ChatGPT 负责分析、规划和审查；Mac 本地 Codex 负责改代码、构建、测试和经审查的提交。SSH 目标机不需要安装 Codex。

## 一键安装

```bash
cd reasonfirst_v4_0_3
./apply_to_reasonfirst.sh /path/to/reasonFirst
```

安装程序会备份并迁移 v3/v4 `bridge.yaml`，仅新增或更新 `~/.codex/config.toml` 的 ReasonFirst MCP 与插件条目，保留其他全局模型、profile、MCP、skills、rules 和登录态。它还会安装 ChatGPT Desktop 插件、启动本地 MCP LaunchAgent，并在现有 GitHub CLI 登录有效时启动网页控制 relay。重复运行可安全更新 ReasonFirst 配置。
为兼容 macOS 系统代理，安装程序只给本机登录环境追加 `127.0.0.1,localhost` 的 `NO_PROXY` 例外；外网代理保持开启。新例外在重启 ChatGPT Desktop/Codex 后生效。

## 启动与检查

本地 MCP 在用户登录后由 `com.reasonfirst.v4-mcp` 自动启动，地址为 `http://127.0.0.1:8765/mcp`。Codex 插件和网页控制 relay 共用这个 MCP 进程及 `~/.local/share/reasonfirst/codex-web-bridge/state.json`。普通 ChatGPT Desktop 聊天不会因本地安装而自动获得该 MCP 工具。
安装时会把运行代码复制到 `~/.local/share/reasonfirst/v4-service`，供 macOS 登录项读取；源目录仍是更新入口。

```bash
cd /path/to/reasonFirst
curl -fsS http://127.0.0.1:8765/healthz
codex mcp list
./tools/codex_web_bridge/run_reasonfirst.sh --doctor
launchctl print gui/$(id -u)/com.reasonfirst.v4-mcp
launchctl print gui/$(id -u)/com.reasonfirst.web-bridge
```

安装程序会通过 Codex CLI 安装并启用个人插件。安装后重启 Codex，以重新读取插件与 MCP 配置。`codex plugin list` 和 Codex 中的实际工具调用只验证 Codex 接入，不代表普通 ChatGPT Web/App 会话已接入。

## 网页端

当前账号不能使用 ChatGPT 开发者模式，网页端沿用此前确定的**私人 GitHub Issue + GitHub connector**入口。relay 将命令转发给同一本地 MCP 进程；GitHub 仅负责网页消息进出，不保存另一份任务 state。`gh auth status` 和私人控制仓库必须可用，网页端才能实测。若网络或凭据失效，本地 MCP 与 Desktop 仍可使用。

普通 ChatGPT Web/App 会话目前没有直接可调用的 ReasonFirst 工具，因此这里的 GitHub connector 是当前可用的网页控制入口，并非已经切换到直连。只有在该会话工具列表中出现 ReasonFirst MCP 工具，并实际成功调用后，才能宣称直连可用。不能通过本机配置让未注册的工具自动出现在普通聊天中。

若以后开通开发者模式，可设置 `RF_TUNNEL_ID` 与 `CONTROL_PLANE_API_KEY` 后重跑 `configure_v4.sh`，并在 ChatGPT 中注册连接，通过 Secure MCP Tunnel 将同一 MCP URL 接入 Web。Tunnel 客户端从 OpenAI 官方发布页下载安装并校验 SHA256；密钥保存在 macOS Keychain，不写入包内。未注册连接前，安装 Tunnel 本身也不会给聊天会话添加工具。

## 兼容与回退

`run_reasonfirst.sh` 启动 MCP，不再启动旧 GitHub relay；`run_github_relay.sh` 是显式的网页入口。配置版本 3/4 都可读取，安装会迁移到 4。原有配置备份位于 `~/.local/share/reasonfirst/backups`。

自动启动日志位于 `~/.local/share/reasonfirst/logs`。本地服务故障时先查看 `v4-mcp.stderr.log`；网页故障时查看 `web-relay.stderr.log` 与 `gh auth status`。
