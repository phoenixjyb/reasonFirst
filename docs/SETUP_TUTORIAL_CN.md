# ReasonFirst 中文教程：让 ChatGPT 直接读取私有 / 自建 GitLab

> 本文只讲 ReasonFirst **Reasoning Plane 的 ChatGPT 只读 GitLab MCP bridge**。v0.5.0 日常 coding（ActualCoder + `codex-cli` / `copilot-cli` / `codex-desktop` + GitLab MR）请优先阅读[快速上手](QUICKSTART_CN.md)；权限更高的 Bridge Preview 是独立可选编排面，不属于本文只读接入流程。整体设计理念见 [ReasonFirst Design Philosophy](DESIGN_PHILOSOPHY.md)。

这套方案适合 **GitLab 部署在公司内网、VPN 或本机网络中，无法直接被公网访问** 的场景。

整体结构：

```text
ChatGPT 普通对话
      │
      │ OpenAI Secure MCP Tunnel
      ▼
OpenAI Tunnel Service
      ▲
      │ 仅出站 HTTPS
      │
Mac / Linux
├── tunnel-client
└── GitLab MCP Server (stdio)
      │
      │ GitLab REST API
      ▼
自建 GitLab（HTTP 或 HTTPS）
```

这套实现默认只读，不要求把 GitLab 或开发机开放公网入站端口。

## 1. 准备环境

你需要：

- 一台能够访问自建 GitLab 的 Mac 或 Linux；
- 同一台机器能够通过 HTTPS 出站访问 OpenAI；
- Python 3.10+；
- `uv`；
- GitLab 只读 Access Token；
- ChatGPT Developer Mode / Custom MCP 能力；
- OpenAI `tunnel-client`。

Mac 安装 `uv`：

```bash
brew install uv
```

## 2. 克隆项目

```bash
git clone https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync
```

## 3. 创建 GitLab 只读 Token

推荐专门为 MCP 创建低权限账号、Project Token 或 Group Token，只授予：

```text
read_api
read_repository
```

不要为了方便直接给 `api` 或 `write_repository`。

## 4. 配置 `.env`

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

```bash
GITLAB_BASE_URL=http://gitlab.example.internal
GITLAB_TOKEN=glpat_xxxxxxxxxxxxx
GITLAB_TRUST_ENV=false
```

强烈推荐再限制允许 ChatGPT 查看哪些项目：

```bash
GITLAB_ALLOWED_PROJECTS=team/project-a,team/project-b
```

`GITLAB_ALLOWED_PROJECTS` 可以填写精确的 `path_with_namespace`，也可以填写项目数字 ID。

## 5. 先测试 GitLab API

```bash
uv run python smoke_test.py
```

正常输出类似：

```text
GET /user -> 200
Authenticated as: your-user (Your Name)
GET /projects -> 200
- 1: team/project-a
```

如果这里失败，先不要继续 Tunnel。优先检查 GitLab 网络、VPN、Token、权限 scope 和代理。

## 6. 本地测试 MCP

```bash
uv run mcp dev server.py
```

浏览器会打开 MCP Inspector。建议依次测试：

```text
gitlab_whoami
list_projects
get_repository_tree
get_file
search_code
get_merge_request
get_merge_request_diff
get_pipelines
get_pipeline_jobs
get_job_log
```

先确认 `gitlab_whoami` 和 `get_file` 能正常工作，再进行 Tunnel 配置。

## 7. 创建 OpenAI Secure MCP Tunnel

打开：

https://platform.openai.com/settings/organization/tunnels

创建 Tunnel，并记录 `tunnel_...` ID。

然后创建供 `tunnel-client` 使用的 OpenAI Platform API Key：

https://platform.openai.com/settings/organization/api-keys

测试阶段在当前 Shell 中加载：

```bash
export CONTROL_PLANE_API_KEY="sk-..."
```

不要把这个 OpenAI Key 放进 GitHub。

## 8. 安装 tunnel-client

Mac 推荐：

```bash
brew install openai/tools/tunnel-client
tunnel-client --version
```

## 9. 初始化 Tunnel Profile

```bash
chmod +x run_mcp.sh
pwd
```

然后使用项目的绝对路径：

```bash
tunnel-client init \
  --sample sample_mcp_stdio_local \
  --profile selfhosted-gitlab \
  --tunnel-id tunnel_YOUR_ACTUAL_ID \
  --mcp-command "/absolute/path/reasonfirst/run_mcp.sh"
```

诊断：

```bash
tunnel-client doctor \
  --profile selfhosted-gitlab \
  --explain
```

对于 stdio MCP，部分网络/OAuth 检查显示 `SKIP` 是正常的。重点看：

```text
RESULT ok
```

## 10. 启动 Tunnel

```bash
tunnel-client run --profile selfhosted-gitlab
```

这个进程需要在 ChatGPT 使用 MCP 时保持运行。

## 11. 在 ChatGPT 创建 Custom MCP Plugin / App

在 ChatGPT Web 中：

1. `Settings → Apps / Plugins`；
2. 开启 Developer Mode；
3. 创建新的 Plugin / App；
4. Connection 选择 **Tunnel**；
5. 选择刚才创建的 Tunnel；
6. 本 MCP 不需要额外用户 OAuth；GitLab Token 始终保存在 MCP 主机端；
7. Scan / Discover Tools；
8. 创建并启用。

如果界面显示 `No tunnels yet`，最常见原因不是 MCP 代码，而是 Tunnel 尚未关联到当前 ChatGPT workspace/account，或者 Tunnel 权限尚未生效。

## 12. 端到端测试

先测试：

```text
使用 My GitLab MCP 告诉我当前认证的是哪个 GitLab 用户。
```

再测试：

```text
列出 MCP 可以访问的 GitLab 项目。
```

然后：

```text
读取 team/project-a 的根目录结构以及 README.md。
```

实际研发场景可以直接问：

```text
读取项目中 perception、PNC 和 SLAM 相关代码，
分析模块边界、接口和潜在架构问题。
```

CI 场景：

```text
查看最近失败的 pipeline，找到失败 job，读取日志并分析根因。
```

## 13. Mac SOCKS / HTTP Proxy 问题

如果遇到：

```text
ImportError: Using SOCKS proxy, but the 'socksio' package is not installed
```

通常是 Mac 环境中设置了 `ALL_PROXY` / `HTTP_PROXY` / `HTTPS_PROXY`。

本项目默认使用：

```bash
GITLAB_TRUST_ENV=false
```

让 `httpx` 访问内网 GitLab 时不继承系统代理变量。对于本地/公司网络 GitLab，这通常比安装 SOCKS support 更合理。

## 14. GitLab 当前是 HTTP 也可以

本方案允许：

```text
MCP Host → http://gitlab.example.internal
```

但是 HTTP 不提供 TLS，所以 GitLab Token 和仓库内容在这一段网络中没有加密保护。

建议至少做到：

- MCP Host 与 GitLab 位于可信内网/VPN；
- Token 最小权限；
- 使用 `GITLAB_ALLOWED_PROJECTS`；
- 条件成熟后迁移 GitLab HTTPS。

未来切 HTTPS 时通常只需要修改：

```bash
GITLAB_BASE_URL=https://gitlab.example.com
```

ChatGPT Plugin、MCP Tool contract 和 Tunnel 架构都不需要改变。

## 15. 长期团队使用

开发和个人测试阶段 MacBook 完全够用。长期多人/7x24 使用，建议将相同代码迁移到 always-on Linux VM、mini PC 或内部服务器，并增加：

- 服务自启动；
- 日志与健康监控；
- Token 轮换；
- Secret Manager；
- HTTPS；
- 保持 MCP 默认只读。

更多常见问题见 [Troubleshooting](TROUBLESHOOTING.md)。
