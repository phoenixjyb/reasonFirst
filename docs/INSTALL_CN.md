# 安装 ReasonFirst

ReasonFirst v0.5.1 明确支持**两条一等安装路径**。两条路径最终进入同一个 `reasonfirst` CLI，并共用私有 GitLab 配置、非秘密 setup state、Tunnel ID 与 review/publication policy。

## 路径 A——打包安装（普通用户推荐）

v0.5.1 发布后，安装 GitHub Release 中经过验证的 wheel：

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup
```

这条路径**不要求长期保留 ReasonFirst 源码检出目录**。wheel 已包含：

- `reasonfirst`；
- `reasonfirst-gitlab-mcp`；
- `reasonfirst-bridge-mcp`；
- `actual-coder` 及兼容/专家 CLI。

### 安装 uv

使用 uv 官方支持的安装方式。例如：

- macOS：`brew install uv`；
- Windows：`winget install --id=astral-sh.uv -e`；
- Linux/macOS/Windows：使用 uv 官方 installer 或其他受支持包管理器。

ReasonFirst 本身不把 `curl | sh` 作为规范安装入口。

虽然 wheel 安装不需要 ReasonFirst 源码目录，但后续受管 workspace / coding workflow 仍需要 Git。

### 然后只进入一个 wizard

```bash
reasonfirst setup
```

先做完全不修改系统的检查：

```bash
reasonfirst setup --status
```

具备 write-capable custom MCP 资格的 ChatGPT workspace 可选：

```bash
reasonfirst setup --mode full-chat
```

full-chat 始终是可选能力；通用默认路径仍是只读 GitLab app + 终端 ActualCoder。

## 路径 B——从源码构建/使用（持续正式支持）

适合贡献、审计实现、维护私有 patch、测试 PR，或者明确希望命令跟随本地源码目录的用户。

从正式 tag：

```bash
git clone --branch v0.5.1 --depth 1 https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run reasonfirst setup --status
uv run reasonfirst setup
```

如果希望把这个检出目录作为长期 editable 用户工具：

```bash
bash scripts/install_user.sh
```

Windows PowerShell：

```powershell
.\scripts\install_user.ps1
```

editable 安装会跟随该源码目录。长期目录应保持在已审阅 branch/tag；PR 实验使用独立 worktree。

## 两条路径共用状态

两条路径有意共用：

```text
~/.config/gitlab-agent/.env
~/.config/reasonfirst/setup.yaml
~/.local/share/chatgpt-gitlab-mcp/
~/.local/share/reasonfirst/
```

切换安装方式**不需要**重新创建已批准项目、OpenAI tunnel ID 或 ChatGPT app。

不要让两个不同 ReasonFirst 安装同时修改同一个受管 workspace。

切换版本/安装方式后运行：

```bash
reasonfirst setup --repair
reasonfirst setup --status
```

`--repair` 只修复**已经记录的本地 runtime**，不会创建 GitLab 项目、OpenAI tunnel 或 ChatGPT app。需要 runtime credential 时，只从 `CONTROL_PLANE_API_KEY` 或 masked prompt 获取，不由 ReasonFirst 持久化。

## 更新打包安装

安装目标 release 的新 wheel：

```bash
uv tool install --force https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup --repair
```

后续版本把 URL 中的两个 `0.5.1` 换成目标已审阅版本。

## 更新源码安装

保留本地改动，把源码切到目标已审阅 tag/commit，再执行：

```bash
uv sync --python 3.12
bash scripts/install_user.sh
reasonfirst setup --repair
```

Windows 使用 `scripts/install_user.ps1`。

## Homebrew formula

v0.5.1 release 流程会基于准确受保护 tag、源码 archive 与 SHA256 生成固定版本的 `reasonfirst.rb`，并作为 GitHub Release artifact。后续可把它发布到维护中的 Homebrew tap。

Homebrew formula **不是** ReasonFirst 配置的 source of truth；它安装相同 Python package/entry points，真正 onboarding 入口依然是 `reasonfirst setup`。

在 tap 尚未真实创建并发布已审公式前，不应宣传 `brew install phoenixjyb/tap/reasonfirst`。

## “安装完成”不等于 READY

包/源码安装成功，只证明本地 executable 可用；不证明：

- GitLab 身份/项目访问；
- coding worker 已认证；
- OpenAI tunnel 已正确关联；
- ChatGPT app 已连接；
- full-chat workspace 具备 write-capable custom MCP 资格。

这些仍由 ReasonFirst 独立的 setup/readiness gate 验证，而不会因为“命令存在”就宣称 READY。

详细手工运维路径见[首次完整接入](GETTING_STARTED_CN.md)；架构与信任边界见[架构说明](ARCHITECTURE_CN.md)和[安全边界](../SECURITY_CN.md)。
