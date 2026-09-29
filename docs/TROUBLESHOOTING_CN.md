# 按层排查故障

[English](TROUBLESHOOTING.md) · [工作流程](WORKFLOW_CN.md) · [ChatGPT 接入与重启](OPENAI_TUNNEL_TEAM_SETUP_CN.md) · [安全边界](../SECURITY_CN.md)

<!-- Translation source: docs/TROUBLESHOOTING.md @ a3e33c72c55efef6a0dc3808fb9853c62ad15f9d -->

修改配置前，先确认哪个客户端失败。CLI 可用、Tunnel 已启动、localhost Assistant 会话，以及普通 ChatGPT 的实时工具调用，是不同层次的证据。没有明确原因时，不要重新安装、重建 Tunnel/工作区、关闭验证、扩大权限或轮换原本可用的密钥。仍有效的已泄露凭证，无论测试是否成功，都必须撤销或轮换。

## 本地 Assistant 提示工具需要审批

仪表盘 Assistant（`/ui#codex`）是上游 Codex 界面，不是普通 ChatGPT。Tunnel-client 的 `0f870e50a973fa820d4c409000059e181e8d242b` 修订版中，该面板提交 `approval_policy: never`，因此需要审批的调用可能被阻断。这里描述的是该已检查版本，不是所有未来版本。

**本地 Assistant 可选，不是 ReasonFirst 的验收门槛。** 关闭该标签页，保持 Tunnel 进程运行，并在普通 ChatGPT 中使用选定的 GitLab 连接。不要将全局审批改为自动接受，不要要求无限制执行，也不要用带令牌的 shell 命令绕过被阻断的 MCP 调用。如果另外确实需要上游面板，应将其支持的审批界面作为独立集成任务调查。

不使用该面板不会卸载 Codex 或停止其附带的辅助进程。保留编程 CLI 用于实现。仅本地仪表盘报错，不能说明普通 ChatGPT 连接也失败。

## Tunnel profile 中仍是旧源码目录

运行 `tunnel-client profiles list`，私下检查返回的文件。仅将获准的 `main` MCP 命令改为长期使用的检出目录，例如：

```yaml
mcp:
  commands:
    - channel: main
      command: "bash /absolute/path/to/reasonFirst/run_mcp.sh"
```

保留现有 Tunnel ID 和凭证引用。绝对命令路径不会因你 `cd` 到其他地方而改变。停止对应旧进程，并按[重启流程](OPENAI_TUNNEL_TEAM_SETUP_CN.md)操作。不要重建 profile，也不要把临时 PR 检出目录用作永久启动器。

## 缺少 CONTROL_PLANE_API_KEY

`api_key: "env:CONTROL_PLANE_API_KEY"` 要求运行 Tunnel 的进程中存在这个已导出的变量。它是用于 Tunnel 的 OpenAI runtime key，不是 Tunnel ID，也不是 GitLab token。从获准的秘密存储中取回已有值；重启指南提供交互使用的隐藏输入提示。已经退出的子 shell 中导出的变量，不会保留到新 shell 或服务中。

变量缺失不等于密钥被拒绝或过期。如果是实际控制平面认证或权限错误，应按官方指南检查密钥和对应组织/工作区权限。不要为守护进程使用 admin key。现有 `file:` 引用需要对应的受保护文件，不需要新增环境变量。可选 Codex 插件的 SKIP 与此无关。

## 启动成功，但普通 ChatGPT 没有工具

保持 Tunnel 运行。检查该对话中选择的连接、目标 ChatGPT 工作区关联、Tunnel 使用权限和实际 profile 目标。通过当前供应商界面刷新或发现工具。`started`、元数据已获取和 health/readiness 响应，均不能证明身份或文件调用成功。

使用[实时读取测试](OPENAI_TUNNEL_TEAM_SETUP_CN.md)。不要用缓存回答、网页搜索、本地凭证笔记或独立运行的 localhost Assistant 代替。MCP Inspector 是可选的独立调试工具，不是所有人必须完成的额外账号或登录步骤。

## 旧 HTTP 笔记或命令输出中出现凭证

Markdown 笔记不是有效配置报告。应检查 `actual-coder config` 和实际 MCP 启动配置，而不是假设显示出来的笔记改变了运行中的服务。通过只返回文件名的搜索私下定位笔记，不要将命中的凭证行打印到聊天中。

从参考笔记中移除凭证及含令牌的命令。绝不能把泄露的值替换为新令牌后继续留在该笔记。撤销或轮换有效的泄露凭证，更新对应私有存储，清除过期环境覆盖并重启进程。不要发布未脱敏截图、profile 文件、`.env`、日志或迁移备份。不要仅关闭上下文注入，就认为已保存的副本已经消失。

## API、原生 Git 与迁移探测结果不一致

参阅[运行时 TLS 指南](HTTPS_API_TLS_CN.md)。`GITLAB_CA_BUNDLE` 配置共享的 Python API/MCP 客户端，不配置原生 Git 或迁移探测。无凭证探测可能验证 TLS 后返回 401/403，而认证访问仍未测试。探测返回 404/500，不代表服务已完整就绪。

配置最终 API 端点：共享客户端拒绝所有 API 重定向和关闭证书验证。不要携带 PAT 跟随登录重定向，也不要回退 HTTP。旧 `smoke_test.py` 不使用共享运行时客户端工厂，不能证明当前请求保护生效。

已有 HTTP 缓存应使用经过审阅的[迁移流程](HTTPS_MIGRATION_CN.md)。重复预览无变化，足以证明检查范围内本地 URL 已统一；不要只为重启 MCP 再次 apply。Git fetch 成功不能证明推送权限。

## 代理错误或 Git HTTP 502

经批准采用直连路径时，私有用户配置通常保留：

```dotenv
GITLAB_TRUST_ENV=false
GITLAB_GIT_TRUST_ENV=false
```

Python/Git 的代理策略与 Tunnel 出站访问 OpenAI 的路径，以及 CA 信任相互独立。502 也可能由其他服务端或网络问题导致，不要将所有 502 都归因于代理。私下检查代理设置：代理 URL 可能含凭证，因此避免公开 `env` 或完整 Git 配置。只有确实打算使用该路径时，才安装 SOCKS 支持或启用代理继承。

## glab 可用，但 ReasonFirst 返回 HTTP 401

`glab auth status` 与 ReasonFirst 不一定使用同一份凭证。前者可能从操作系统 Keychain 读取认证，而 ReasonFirst 仍从选定的私有 `~/.config/gitlab-agent/.env` 中读取 `GITLAB_TOKEN`。

如果 `glab api user --hostname <host>` 成功，但 `gitlab_whoami` 返回 401，只检查 ReasonFirst 私有配置中的 token 是否存在/是否已更新，不要把 token 值打印到聊天或日志。通过你认可的秘密管理方式更新 ReasonFirst 凭证后，重启 GitLab MCP/Tunnel 消费端，使其重新加载该文件。OpenAI Tunnel runtime key 与 GitLab 认证无关。

## Bridge worker 在启动前或刚启动时失败

原始 v0.3.0 tag 之后已经修复两类 Codex App Server 兼容问题：

- 当前 Codex App Server 的 `thread/start` policy wire value 不兼容；
- 无条件注入 `mcp_servers.reasonfirst.enabled=false` 可能生成没有 transport 的 MCP 配置，使 App Server 初始化直接失败。

如果更新后的环境仍出现 `INVALID_ARGUMENT`、`invalid transport` 或 App Server 初始化错误，请记录 ReasonFirst commit 和 Codex 版本，更新到已审阅的当前 `main`，重新 stage Bridge runtime，并重启 MCP 服务后再试。若现有 managed workspace 仍然干净，应继续复用它；不要为了重试反复 dispatch 新 workspace。

## worker 已创建，但模型流反复 reconnect / timeout

本地 MCP 健康、Codex thread 已成功创建，并不代表 launchd 管理的 worker 一定能访问外部模型服务。macOS LaunchAgent 默认不会自动继承交互式 shell 中的代理变量。

如果你的网络依赖 `http_proxy` / `https_proxy` / `all_proxy`，可将当前 shell 的代理变量仅同步到当前 launchd user session：

```bash
bash tools/reasonfirst_v4_0_3/sync_launchd_proxy_env.sh sync
launchctl kickstart -k "gui/$(id -u)/com.reasonfirst.v4-mcp"
```

该 helper 只输出变量名和状态，不把代理值写入 plist 或源码文件。launchd user-session 环境仍可被同一用户权限下的进程读取，并会在退出登录/重启后消失。需要主动清除时：

```bash
bash tools/reasonfirst_v4_0_3/sync_launchd_proxy_env.sh clear
```

重启后先等待 `http://127.0.0.1:8765/healthz` 变为健康，再继续同一个 worker thread。ICMP `ping` 不能可靠验证代理链路：HTTP(S) 代理可能正常工作，而目标站点同时拒绝 ICMP。

## ActualCoder 安装或有效配置与预期不同

从稳定检出目录按[安装更新指南](LOCAL_PR_REVIEW_CN.md)操作。私下检查 `actual-coder config` 及解释器/源码路径。全局 editable 工具与临时检出目录内的 `uv run` 可能使用不同环境。仅凭包版本不能确定持续前进的源码检出；对 v0.5.0 及之后的源码构建，应同时记录准确 commit SHA 与包版本。

配置选择顺序是 `GITLAB_AGENT_ENV_FILE`、稳定用户配置、本地 `.env`；已导出的值优先。CLI 的本地回退相对工作目录，MCP 的回退相对 server 源码目录。不要用 `.env.example` 覆盖工作配置，也不要为匹配源码仓库新名称而重命名受管工作区目录。

## 缺少项目约定或命令被拒绝

`found: false, valid: true` 表示没有加载 `.actualcoder.yaml`，不是测试已通过。通过目标仓库的正常审查流程定义真实测试命令。受管工作区使用精确项目路径；API 数字 ID 不能替代本地基于路径的流程。不要为了消除本地拒绝而扩大用户允许列表。

仓库策略不能增加可执行程序权限。必需程序须由用户有意识地允许；运行获准解释器仍不等于操作系统沙箱。Finish 使用原始工作区 base 的策略，因此新合并的项目约定不会悄悄改变旧任务。

## 后端额度或现有工作区恢复

对于已有任务，将 `WS` 设置为真实 ID，并准备替换后端的交接：

```bash
actual-coder resume "$WS" --agent copilot --goal "Continue the same approved task and acceptance criteria"
```

它返回交接内容，不会自动启动代理。审查返回内容，并保留[人工任务要求](TASK_HANDOFF_TEMPLATE_CN.md)，因为目前并非所有入口都有一致的项目上下文。不要仅为了切换后端而创建第二个工作区。

只有在本地状态不可用、远端分支仍存在时，才用真实项目与 IID 重建现有 MR：

```bash
actual-coder checkout-mr team/project-a 123 --agent copilot --goal "Continue the existing MR within its reviewed scope"
```

恢复会拒绝覆盖含未发布提交的遗留本地分支。实现之后，使用经审阅的 `finish --dry-run` 和经确认的 `finish`，不要把低层 push 当作等效安全门槛。`cleanup --force` 是主动丢弃，不是恢复。完整流程见[快速上手](QUICKSTART_CN.md)。

## 可以安全分享的证据

分享首个失败检查、工具/源码修订、shell/平台以及简短脱敏结果。说明客户端是哪一种（CLI、Tunnel、普通 ChatGPT、可选 Assistant），实际尝试了什么，日志是否完整。不要为了验收读取连接而要求新推送或完整构建。任何成功诊断都不会让泄露凭证重新变得安全。

主要参考：[OpenAI Tunnel 指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)、[上游操作指南](https://github.com/openai/tunnel-client/blob/master/docs/end-user-guide.md)、[指定版本的 Assistant 实现](https://github.com/openai/tunnel-client/blob/0f870e50a973fa820d4c409000059e181e8d242b/adminui/src/components/CodexPanel.svelte)。
