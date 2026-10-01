# 安装 ReasonFirst

ReasonFirst v0.5.1 明确支持**两条一等安装路径**。两条路径最终进入同一个 `reasonfirst` CLI，并共用私有 GitLab 配置、非秘密 setup state、Tunnel ID 与 review/publication policy。

## 已有安装？先检查，不要重新配置

**安装新包不等于所有后台服务都已升级。** 保留现有配置、工作区、源码检出、旧运行环境
及活动 sidecar 目录。缺少 `setup.yaml` 不等于原有隧道或 Bridge 不存在，不要因此重建连接。
下面的只读检查在已发布 v0.5.1 和本开发分支均可使用，不联网验证、不写文件、不启停服务：

```bash
reasonfirst --version
reasonfirst setup --status --json
```

多套安装共存时明确选择要检查的可执行文件。配置仍按 `GITLAB_AGENT_ENV_FILE`、
`~/.config/gitlab-agent/.env`、当前目录 `.env` 的顺序选择；已导出的变量优先于文件。
不要把 `.env` 内容或密钥粘贴到问题报告中。

### 本开发分支新增行为（不在已发布 v0.5.1 wheel 中）

发现有效既有配置后，普通 `reasonfirst setup` 先询问一次是否复用。接受后不再索取
已有 URL、token、项目或 worker；不缩减完整白名单，不写 `setup.yaml`，不验证登录，
不启动服务或创建连接。非交互的显式只读复用方式为：

```bash
reasonfirst setup --reuse-existing --json
reasonfirst status --json
```

这些新选项和命令别名需本分支构建或未来包含此变更的版本，不能在已发布 v0.5.1 中使用。
`setup --reconfigure` 或显式配置选项（如 `--project`）才进入原来的配置编辑流程，
仍保留项目预检、环境覆盖冲突检查和写入确认。拒绝复用直接取消，不自动进入编辑。
`--reuse-existing` 不能与修改选项组合。

自定义路径、当前目录文件及仅环境变量的配置均可只读复用，不复制密钥到新的高优先级文件。
缺字段时保留配置并列出缺项；格式错误、重复键、过大、不可读或符号链接配置被分类报告，
不当作全新安装覆盖。检查只接受已有 loader 的简单单行赋值格式，不执行 shell 表达式。
该变更不调整其他低层 CLI 的配置加载行为。

`status` 与 `setup --status` 使用同一静态清单，分别报告已安装 CLI 的版本、Python、
模块位置，以及尚未检查的运行中版本。macOS 只检查已知用户级 `com.reasonfirst.v4-mcp`
LaunchAgent，识别旧 staged HTTP 和版本化 HTTP sidecar 路径；不执行其脚本，不扫描无关
服务，不调用 launchctl、HTTP 或 MCP。路径只能表明可能的布局，不能证明实际代码或传输状态。
Windows/Linux 的服务管理器检查明确为 `not_inspected`；`not_found` 仅指已检查的那个位置。

plist 私下解析时可能含环境值，输出不包含环境值或任意启动参数；Bridge 配置只检查存在性。
已识别旧部署可显示 `discovered_legacy`；格式错误、不可读、不支持的布局不等于不存在。
静态文件不被当作 `healthy`/`unhealthy` 证据。既有配置或部署证据存在而向导记录缺失时，
建议审阅，不建议重复建立隧道。默认的 `mode: standard` 与 `ready: false` 不否定旧服务存在。
此步骤不写部署登记、不接管服务。

## 路径 A——打包安装（新用户推荐）

v0.5.1 发布后，安装 GitHub Release 中经过验证的 wheel：

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup
```

公开资源包括 `SHA256SUMS.txt` 和 `RELEASE.json`，详见[下载校验与发布范围](RELEASE_DISTRIBUTION.md#简体中文)。这些文件须等发布资源流水线成功后才可用。

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

切换版本或安装方式之前先检查既有部署。`reasonfirst setup --repair` 只处理有效
向导元数据中**已记录**的 runtime，可能执行重新连接，不是旧部署导入器。没有向导
记录时先审阅现有服务，不要为满足 repair 的前置条件而重新跑向导。明确批准的重新
连接可能需要 runtime credential；这与配置复用不同，ReasonFirst 仍不持久化 runtime API key。

## 更新打包安装

先检查有效配置与后台服务实际使用的运行环境；服务依赖某个 tool 环境时不要直接替换它。
固定版本的 sidecar 可能有意拒绝更改后的包指纹，因此更新 CLI 本身不等于协调服务升级。

确认软件包替换安全并已批准后，再安装已审阅 release 的 wheel。下例仍指向已发布 v0.5.1：

```bash
uv tool install --force https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup --status --json
```

第一条命令替换 tool 安装，不是服务激活事务。以后更新时替换 URL 中的两个版本号。
复用既有配置，服务健康检查与必要的重启另行计划；不要在有任务运行时无条件执行 repair。

## 更新源码或 editable 安装

保留本地修改及现有包管理方式，在不 reset 或丢弃工作的前提下选择已审阅 tag/commit，
按源码安装路径更新该检出的环境，然后检查配置及服务版本。不要擅自把 editable
安装改成 wheel，也不要认为 staged daemon 一定从更新后的检出导入。不能让独立安装
并发修改同一受管工作区。

## 接管或升级旧后台服务

旧 launchd/staged HTTP 部署可以共享 GitLab 配置，却使用不同于 CLI 的 core/runtime。
缺少 `setup.yaml` 不是凭据丢失。保留完整白名单、隧道身份、监听端点及读写策略，不能
把 stdio-only 的 packaged Bridge 命令直接替换进 HTTP 服务。

本次源码变更提供**检查与配置复用**，不提供服务接管器或自动更新器。激活、维护门控、
版本化运行环境、回滚和恢复需要独立审阅及支持证据。不要伪造向导阶段，不要为了消除
状态警告而删除旧目录、活动 launcher、工作目录或私密恢复备份。服务启动与已有客户端
回连必须独立于包安装验收；静态清单不能替代这些检查。

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
