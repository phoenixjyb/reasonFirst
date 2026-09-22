# ReasonFirst v4.0.3 清单

- `apply_to_reasonfirst.sh`：一键安装到已有 ReasonFirst。
- `configure_v4.py`、`configure_v4.sh`：v3/v4 配置迁移、全局 Codex MCP 更新与备份。
- `reasonfirst_mcp_server.py`、`run_mcp_server.sh`、`run_reasonfirst.sh`：共享回环 HTTP MCP 服务。
- `install_launch_agent.sh`、`install_web_relay_agent.sh`：本地服务及网页 relay 登录自启动。
- `set_local_no_proxy.sh`：只为本机 MCP 地址设置代理例外，保留外网代理。
- `github_control_relay.py`、`run_github_relay.sh`：原有网页私人 Issue 入口，转发到同一 MCP 进程。
- `configure_v4_tunnel.sh`、`install_tunnel_client.py`：有开发者模式时的可选 Tunnel 入口。
- `reasonfirst_codex_bridge/`：原有 workspace、Codex app-server、SSH、证据和推送审查逻辑。
- `tests/`：迁移、MCP、relay、Codex 和远端模拟回归。

详细使用方式见 [README.md](README.md)。
