# 首次接入：从你的 Mac 到 ChatGPT 中的 GitLab 对话

[English](GETTING_STARTED.md) · [中文文档索引](README_CN.md) · [架构说明](ARCHITECTURE_CN.md) · [日常隧道操作](TUNNEL_LIFECYCLE_CN.md)

## 先选择你真正需要的路径

并不是每个任务都需要全部 ReasonFirst 接入。

| 目标 | 从哪里开始 | 是否需要 Tunnel |
| --- | --- | --- |
| 本地使用 ActualCoder + Codex CLI / Copilot CLI / Codex Desktop | [CLI 快速上手](QUICKSTART_CN.md) | 不需要 |
| 让普通 ChatGPT 读取获批 GitLab 仓库/MR/CI | 继续阅读本指南 | 这条连接路径需要 |
| 使用 Bridge Preview 控制 App Server、审批、SSH workspace 或 finish preview | [架构说明](ARCHITECTURE_CN.md) + Bridge Preview README | 本身不强制；取决于如何连接/暴露 |

**只读 GitLab MCP** 与 **Bridge Preview 编排 MCP** 的信任边界不同。仅仅为了读取仓库，不要顺手暴露权限更高的 Bridge Preview。

**按顺序完成本指南，再发送仓库工作提示词。** 验收目标是在普通 ChatGPT 对话中，通过 ReasonFirst MCP 读取你明确批准的 GitLab 项目。安装完 Python 包或本地健康检查通过，都不等于达到这个目标。

本文主路径是 **macOS + Keychain + 已选定的 stdio profile**。Linux 可以使用同一生命周期助手，但须明确选择环境变量/文件凭证来源，见[手工替代方案](OPENAI_TUNNEL_TEAM_SETUP_CN.md)。本助手不支持 Windows 生命周期管理；Windows 应使用[手工操作](OPENAI_TUNNEL_TEAM_SETUP_CN.md#windows-powershell)，不要照搬 macOS 命令。

已经成功接入？直接看[日常使用](#daily-use)。已经具备部分条件？复用它们，不要为了逐条执行示例而重建可用隧道、覆盖配置或重装正常客户端。

```text
一次性准备：权限 -> 软件 -> GitLab 配置 -> 隧道/密钥/profile
每次服务停止后：启动服务 -> 检查本地状态 -> 在 ChatGPT 选择应用
每个新项目：实时身份 -> 项目访问预检 -> 固定提交的源码读取
批准后的实现：ChatGPT 方案 -> 人工交接 -> Codex/ActualCoder -> MR/CI -> 审阅
```

<a id="prerequisites"></a>
## 0. 前置条件与三层权限

| 条件 | 准备什么、由谁提供 | 检查点 |
| --- | --- | --- |
| 可信主机与网络 | 获准运行 MCP 的 Mac，可访问目标 GitLab，并可向 OpenAI 发出 HTTPS 请求。确认允许将选定源码/CI 数据发送给 ChatGPT。 | 不把本地管理端口暴露到互联网，不关闭 TLS 验证。 |
| 软件 | Git、uv、Python 3.12 环境、ReasonFirst，以及单独安装的上游 `tunnel-client`。 | 第 1、2 步命令成功。 |
| GitLab 访问 | 准确实例地址、已有 `namespace/project`、实际分支/ref、已知文本文件，以及有效最小权限 API token。 | 第 3 步预检 `ok: true`。 |
| OpenAI Platform 权限 | 正确 Platform 组织；隧道管理者具有 **Tunnels Read + Manage**，运行身份具有 **Tunnels Read + Use**。 | 有获准使用的真实 Tunnel ID 和 runtime key。 |
| ChatGPT 权限 | 目标 ChatGPT workspace 允许自定义 MCP 应用/developer mode，且隧道关联到该 workspace。它与 Platform 权限分开管理。 | 第 7 步能选择目标隧道/应用。 |
| 持久的密钥加载方式 | 对应 runtime key 的准确 Keychain 密码条目，或另一种明确选定、受支持的秘密来源。 | `start` 可加载密钥，不依赖昨天的 shell export。 |
| 编程后端与 Git 写权限 | **只在后续实现时需要：**已登录的 `codex-cli`、`copilot-cli` 或可用的 `codex-desktop` App Server、批准的仓库写权限、真实项目测试及可用 GitLab runner。 | 不是只读接入的前置条件。 |

服务资格与界面会变化，核对 [OpenAI 隧道权限/workspace 说明](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)和[当前 developer mode 政策](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)。本指南不以某个订阅名称保证访问资格。隧道不能绕过 GitLab 权限或 ReasonFirst 本地允许列表。

**凭证对照表：这些值不能互换。**

| 值 | 使用者 | 保存位置 |
| --- | --- | --- |
| `GITLAB_TOKEN` | ReasonFirst -> GitLab API | 私有 `~/.config/gitlab-agent/.env`；不放进对话或 Markdown 笔记。 |
| `GITLAB_GIT_TOKEN` | ActualCoder 原生 Git fetch/push | 可选的独立凭证，保存在同一私有配置中；写入另行批准。 |
| `CONTROL_PLANE_API_KEY` | `tunnel-client` -> OpenAI 隧道服务 | 本指南推荐 Keychain；也可用批准的 env/file 引用。不是 GitLab token 或 admin key。 |
| `tunnel_...` ID | 标识已有 OpenAI 隧道 | 隧道 profile 和 ChatGPT 的 Tunnel 连接字段；它不是运行凭证。 |
| 编程后端登录/会话 | `codex-cli`、`copilot-cli` 或 `codex-desktop` | 后端自身支持的认证/会话。Tunnel key 不会登录编程后端。 |

ReasonFirst 不直接调用模型推理 API，但隧道仍需要 runtime API key。这不意味着隧道服务免费、编程额度无限或订阅可转移。不要通过付费模型 API 请求来测试接入。

<a id="install-tools"></a>
## 1. 安装工具并检查

Mac 已安装 [Homebrew](https://brew.sh/) 时，安装缺少的工具：

```bash
brew install git uv
brew install openai/tools/tunnel-client
```

尚未安装 Homebrew 时，先按官网安装；也可使用 [uv 官方安装器](https://docs.astral.sh/uv/getting-started/installation/)，并从 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)或[上游最新发布](https://github.com/openai/tunnel-client/releases/latest)获取受支持的客户端二进制。按上游要求检查发布完整性，不下载名称相近的第三方隧道程序。

```bash
git --version
uv --version
tunnel-client --version
tunnel-client help quickstart
tunnel-client profiles list
```

**检查点：**所有命令均可找到；已有 profile 时记录它的真实名称。`actual-coder-tunnel` 是 ReasonFirst 的进程管理助手，不会安装或替代上游 `tunnel-client`。[上游安装说明](https://github.com/openai/tunnel-client#install-with-homebrew)提供官方 Homebrew tap。

## 2. 选择长期源码目录并安装 ReasonFirst

在 **Terminal A** 设置下列非秘密工作变量。项目/ref/文件的示例值必须先在你自己的 GitLab 上确认后替换。`gitlab-work` 只是建议的新本地 profile 名，不代表隧道或 profile 已存在。已有用户使用实际 profile 和源码路径。整个准备阶段保留这个 Terminal；这些变量不是持久配置。

```bash
RF_SOURCE="$HOME/Projects/reasonFirst"
RF_PROFILE="gitlab-work"
RF_PROJECT="team/project-a"
RF_REF="main"
RF_FILE="README.md"
```

仅用于**全新检出目录**；已存在的目标不会被覆盖：

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

已有检出目录时，保留本地修改，按[源码更新指南](LOCAL_PR_REVIEW_CN.md)更新；然后在该目录执行上述 sync、installer 和 help 检查，不再 clone。更新运行中服务的源码之前，在其原 Terminal 停止隧道。全局工具采用 editable 安装：长期目录应停留在已审阅 ref，不使用临时 PR worktree。

**检查点：**两个助手命令存在，并记录安装的 commit。`uv sync` 可能生成本地 `uv.lock`，本指南基线尚未将它纳入版本控制。`?? uv.lock` 表示未跟踪文件，不等于已跟踪文件被修改。保留它，不要忽略所有 lockfile、重置其他修改，或在共享 lockfile 尚未审阅提交时使用 `--locked`。本指南不改变依赖锁定策略。

<a id="gitlab-access"></a>
## 3. 确认目标并私下配置 GitLab

先在正确的 GitLab 实例中或向项目负责人确认：**远端项目确实存在，你的 GitLab 身份有权访问。** GitHub 模板或本地文件夹不等于 GitLab 项目。拟议的练习项目必须先由获准用户创建并发布初始文件，再使用[演练指南](PRACTICE_LAB_CN.md)；不要为了省略这一步而把练习放进生产仓库。

API 读取使用适当的只读 scope（`read_api`）；若同一凭证还用于 Git 读取，仓库模板推荐 `read_api` 加 `read_repository`。后续 Git push 可采用独立 `write_repository` token。scope 不会让身份自动成为无权访问项目的成员，见 [GitLab token scopes](https://docs.gitlab.com/security/tokens/access_token_scopes/)。认证使用前更换泄露的 token；不要把 token 粘贴到命令、URL、Git remote、截图或此对话中。

在 Terminal A，仅当配置不存在时创建它。保留已有内容，拒绝符号链接：

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

随后仅在**本地编辑器**打开，不打印文件内容：

```bash
nano "$HOME/.config/gitlab-agent/.env"
```

在编辑器中，把模板的 `GITLAB_BASE_URL` 换成最终 GitLab HTTPS 地址，`GITLAB_TOKEN` 换成有效 API token，`GITLAB_ALLOWED_PROJECTS` 换成明确批准的准确项目路径（逗号分隔、不加开头 `/`）。保留已有获批项目，保持 TLS 验证开启。Control+O、Enter 保存，Control+X 退出。

**本地 MCP 的逐项目授权是 `GITLAB_ALLOWED_PROJECTS`，不是 OpenAI Tunnel 设置。** 不得清空列表以绕过拒绝：空列表允许 MCP 读取 token 可访问的所有项目。助手不会自动授权。`.env` 是用文件权限保护的明文配置；GitLab loader 不解析 `GITLAB_TOKEN=keychain:...`。下文 Keychain 集成专门用于隧道 runtime key。

在 Terminal A 把 `RF_PROJECT`、`RF_REF`、`RF_FILE` 改为已确认值，然后验证 API 路径，不创建工作区、不启动编程代理：

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

unset 仅在子 shell 中清除这些旧覆盖值；将预期值放在选定私有文件中。其他有意配置的环境覆盖仍须操作者核对。**检查点：**`ok: true`、目标项目正确，并返回 `resolved_commit_sha`。这验证本地 API，不验证运行中的 ChatGPT 连接。失败时停止：`project_not_allowlisted` 需要明确本地批准；`project_missing_or_inaccessible` 不证明项目一定不存在；ref/文件错误需核对真实值；认证和 TLS 错误分别处理。详见[项目访问](PROJECT_ACCESS_CN.md)。不要要求 `actual-coder doctor` 找到编程后端来判断只读接入是否就绪。

<a id="openai-tunnel"></a>
## 4. 获取 OpenAI Tunnel ID 与 runtime key

在正确的 **Platform 组织**下打开 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)。复用获准使用的隧道，或请有权限的管理者创建。将它关联到目标 **ChatGPT workspace**，不只是 Platform 组织。记录真实返回的 ID；不要编造 ID，也不要因缺少 workspace 关联就创建第二条隧道。

在 [Runtime API keys](https://platform.openai.com/settings/organization/api-keys) 页面，使用获准调用该隧道的身份获取运行凭证。创建/管理权限与运行使用权限不同；不要用 admin key 启动长期运行的客户端。`sk-` 或其他前缀不能证明权限。页面提示权限不足时，联系对应组织/workspace 管理员并等待，本指南不能替你授权。参见[上游角色与密钥说明](https://github.com/openai/tunnel-client/blob/master/docs/permissions.md)。

**检查点：**真实 Tunnel ID、私下保存的 runtime key、正确的组织/workspace 关联。不要把任何凭证发送到接入对话。

<a id="keychain"></a>
## 5. 保存 runtime key，支持以后重启

macOS 推荐方式：通过 Spotlight 打开 **Keychain Access（钥匙串访问）**，选择 login 钥匙串，按 Command+N 创建通用密码条目。这里是 Keychain Access，不是浏览器的网站密码表单。填写：

| 字段 | 本指南使用的值 |
| --- | --- |
| Keychain Item Name / service | `openai-tunnel-runtime` |
| Account Name | `id -un` 返回的 macOS 用户名 |
| Password | 第 4 步获准使用的 OpenAI tunnel runtime API key |

已有正确条目时直接复用，不重复创建。多个凭证使用有意区分的 service/account。保留访问确认，不允许所有应用读取；本地读取可能弹出权限确认。[Apple 快捷键说明](https://support.apple.com/en-za/guide/keychain-access/kyca699a9058/mac)与[逐条目访问选项](https://support.apple.com/en-bh/guide/mac-help/kychn002/mac)可供核对。

**检查点：**准确的条目已存在。Keychain 是可选的存储保护，不是对同用户可信进程的隔离。选定 Keychain 后，助手每次 start/restart 都重新读取，不把值保存进配置，也不会静默退回旧的 export。不用 Keychain 时，按明确的 env/file [替代方案](OPENAI_TUNNEL_TEAM_SETUP_CN.md#credential-alternatives)操作；此前 Terminal 或子 shell 中的 export 不是持久存储。不要通过把 key 字面值写进 `.zshrc` 或 GitLab `.env` 来解决隧道启动。

<a id="profile-and-start"></a>
## 6. 创建或复用本地 profile，配置助手并启动

使用第 1 步 `tunnel-client profiles list` 的结果。已有 profile 时**跳过 init**，在本地私下核对 Tunnel ID 与 launcher，保留原凭证引用和其他有意设置。交给新助手管理前，在旧手工启动进程的原 Terminal 按 Control+C。由服务管理器启动的实例应通过原服务停止，不使用广泛的进程名匹配来杀进程。

仅对**全新本地 profile**：把 `RF_TUNNEL_ID` 设为第 4 步的真实 ID，再运行下面有保护的命令。`tunnel_REPLACE_ME` 不可用。`init` 创建的是本地 profile，不是远端隧道，也不是 ChatGPT 应用：

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

上游 [init 命令](https://github.com/openai/tunnel-client/blob/3502fcb8953230215c832197b1e4835fe47183da/cmd/client/init_command.go)支持这些仅含凭证引用的选项，没有秘密作为命令参数。MCP 命令包含展开后的绝对路径，不是字面 `$RF_SOURCE`。健康端口固定绑定回环地址。ReasonFirst 助手有意只接受有限 profile schema，见[支持范围](TUNNEL_LIFECYCLE_CN.md)。新版样例/schema 不兼容时，应检查适配关系，不要盲目删除陌生的安全设置。

使用第 5 步的真实 Keychain 引用，一次性配置：

```bash
actual-coder-tunnel configure \
  --profile "$RF_PROFILE" \
  --source-dir "$RF_SOURCE" \
  --env-file "$HOME/.config/gitlab-agent/.env" \
  --keychain-service openai-tunnel-runtime \
  --keychain-account "$(id -un)"
```

应返回 `ok: true`、`state: configured`。它只将非秘密引用写入 `~/.config/reasonfirst/tunnel.json`。已有不同设置时先明确审阅，不自动加 `--replace`。Configure 不读取密钥，也不启动隧道。

**Terminal A：启动并保持运行。**

```bash
actual-coder-tunnel start
```

出现提示时允许读取预期 Keychain 条目。助手加载 key、运行上游 doctor，并启动/管理所选 profile；不要再额外运行裸 `tunnel-client run`。它保持前台运行，不要等它退出才做下一步，也不要套 `nohup`/`disown`。

**Terminal B：检查状态。**

```bash
actual-coder-tunnel status
```

**检查点：**`state: ready_for_chatgpt_check`，健康/就绪均为 200，runtime 身份一致，metadata 已获取。`chatgpt_connection_verified: false` 与 `project_access_checked: false` 是预期结果：助手不跟踪后续 ChatGPT 验收。新鲜本地样本也不证明连接持续可用。出现 `runtime_key_missing`、`keychain_unavailable`、`launcher_mismatch`、`doctor_failed`、`startup_timeout`、`unmanaged_listener` 时，停止并处理对应层，不建重复隧道、不放宽权限。保持服务 Terminal 打开、Mac 唤醒、网络可用。不需要 localhost Assistant。

<a id="chatgpt"></a>
## 7. 在普通 ChatGPT 中连接已有隧道

保持 Terminal A 运行，打开目标 workspace 中的普通 ChatGPT。在当前 Plugins/Apps 的自定义应用入口，**只有没有适用连接时才创建** developer-mode 连接；Connection 选择 **Tunnel**，选择或粘贴第 4 步的 Tunnel ID。取一个易识别的名字，如 `My GitLab MCP`。已有连接时复用；工具集更新后刷新工具发现。

在**正常对话输入框的应用/工具选择器**中选中它，或在当前界面支持时使用应用提及。只输入连接名称不等于连接已经启用。不要把 OpenAI key 或 GitLab token 粘贴进消息，也不要将 `http://127.0.0.1:8080/ui` 当成远端 MCP server URL。本服务使用私有服务端 GitLab 凭证，不另加 GitLab OAuth 登录页。

不同套餐/发布阶段的界面文字可能变化，以[当前连接说明](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#connect-from-chatgpt)和[developer mode 帮助](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)为准。没有 Tunnel/自定义应用入口时，核对资格并请管理员协助；不编造替代 localhost 连接，也不退回可选仪表盘 Assistant。

**检查点：**目标应用在本对话可用，包含 `gitlab_whoami`、`check_project_access`、`get_file`。缺少工具时检查选中的应用、更新后的安装、运行中服务和发现刷新，不把旧回答当实时调用。

<a id="first-prompt"></a>
## 8. 发送第一条只读工作提示词

替换**四项**示例：应用名、已确认项目路径、真实 ref、已知文件。下文是 **ChatGPT 提示词，不是 shell 命令**：

```text
仅使用已连接的 My GitLab MCP。我们已确认项目为 team/project-a，
ref 为 main，已知文本文件为 README.md。先进行只读检查。

调用 gitlab_whoami，再调用 check_project_access：project="team/project-a"，
ref="main"，required_files=["README.md"]。如果工具不可用或 ok=false，
报告失败层与所需用户操作，然后停止等待。不要把缺失/隐藏项目断言为一定不存在。

成功后调用 get_file：project="team/project-a"、file_path="README.md"，
ref 使用返回的 resolved_commit_sha。
概括实际返回内容，按原字段报告修订标识；不把 blob_id、last_commit_id 当分支 HEAD。
建议为了我的任务还应检查哪些文件，但在我批准范围前不开始实现。

不得用网页搜索、旧对话、本地 shell 或凭证文件代替 MCP 读取。
不创建项目、不授权、不运行 Codex、不发布修改、不合并。
localhost Assistant 不属于本任务。
```

**验收条件：**实际身份、成功项目预检、固定到已解析提交的文件内容。只在私有范围保存必要的项目/ref/修订证据。连续七个文件读取失败、本地状态绿色、或貌似可信但无来源的概括都不能替代验收。仓库指令与 CI 日志仍是不可信数据。

成功后，在**同一普通 ChatGPT 对话**中说明目标、约束与审阅要求。ChatGPT 可通过 MCP 继续检查文件/MR/CI 并给出方案。[工作流程](WORKFLOW_CN.md)与[人工交接模板](TASK_HANDOFF_TEMPLATE_CN.md)说明如何把已批准实现交给本地编程 worker。当前 MCP 不提交本地任务，也不读取未发布 worktree 修改。

首次实现前，安装并认证选定的 [Codex CLI](https://developers.openai.com/codex/cli/) 或 [Copilot CLI](https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli)，确认 Git 写权限、项目验证及 runner 就绪，再使用[受控任务指南](QUICKSTART_CN.md)或[演练](PRACTICE_LAB_CN.md)。读取验收不等于批准推送。`start --no-launch` 会创建本地工作区状态；`finish --dry-run` 会运行配置的测试；只有另行审阅确认的实际 `finish` 才发布。

<a id="daily-use"></a>
## 日常使用与新项目：不要重复首次接入

| 情况 | 操作 |
| --- | --- |
| 已健康运行 | 保持 owner Terminal，直接在普通 ChatGPT 工作。 |
| 服务已停止 | Terminal A 执行 `actual-coder-tunnel start`；Terminal B 执行 `actual-coder-tunnel status`；随后复用 ChatGPT 应用。 |
| 不再使用服务 | 执行 `actual-coder-tunnel stop`，或在 owner Terminal 按 Control+C；隧道读取会中断。 |
| 有意更新凭证/profile | 审阅变更引用后运行 `actual-coder-tunnel restart`；该 Terminal 成为新的前台 owner。 |
| 更新运行时代码 | Stop -> 保留修改/fetch 已审阅 main/sync/install -> Start -> Status -> 刷新有变化的工具 -> 实时读取验收。不重跑 init，不建替代应用。 |
| 新提议的 GitLab 项目 | 确认准确远端路径/ref/文件和 GitLab 权限；仅把获批项目追加到私有 allowlist；重启已有 MCP；批量读取前实时预检。通常不需要新隧道。 |

例外情况见[生命周期边界](TUNNEL_LIFECYCLE_CN.md)和[访问诊断](PROJECT_ACCESS_CN.md)。Restart 可能在停止旧实例后失败；自动回滚、开机自启和自动项目授权均未实现。

文档基线：ReasonFirst `61f464ebbb8906817e32802be22df4012881ece5`；服务商参考资料核对日期为 2026-09-21。本指南本身不会安装软件、创建远端资源、保存真实秘密，也不证明某位用户的连接已成功。
