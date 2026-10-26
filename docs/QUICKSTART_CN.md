# ReasonFirst 当前版本快速上手

[English](ACTUAL_CODER_QUICKSTART.md) · [文档索引](README.md) · [安全边界](../SECURITY.md)

**在 ReasonFirst 中的定位：**这是一份执行引擎/运维指南，不是另一种主要产品模式。正常主流程从 ChatGPT（或其他强推理界面）开始，在那里完成架构、诊断、范围和验收标准；本指南负责配置和运行已批准任务的 worker 侧。脱离 ChatGPT 的直接 CLI 使用仍适合测试、恢复和自动化。

**首次只读接入：**先按[安装与更新](INSTALL_CN.md)选择打包或源码路径，再完成[首次 ChatGPT 读取](GETTING_STARTED_CN.md)。包含 **Unreleased 源码变更**的检出支持 `uv run reasonfirst setup --mode standard --worker later`；standard 也可在未检测到 worker 且未显式指定时继续只读接入。已发布的 v0.5.3 wheel/tag 不支持 `--worker later`，`cli-only` 和 `full-chat` 仍需要对应 worker。现有配置、worker 偏好和隧道标识按正常复用规则保留，`later` 不会被保存成后端。详见[版本范围](INSTALL_CN.md#read-first-setup)。

ReasonFirst 将“判断该做什么”和“执行编码迭代”分开：用户与推理界面定义目标、约束和验收条件，ActualCoder 准备工作区并将任务交给 Codex CLI、GitHub Copilot CLI 或 Codex Desktop/App Server，再把实际 Git/MR/CI 结果交回审阅。原始 GitLab MCP 仍是只读证据接口；可选的 Bridge Preview 是权限更高的本地编排 MCP，可创建/读取受管 workspace、控制 App Server、审查未发布 diff 并执行受审 finish 流程。

**本项目适合可信个人开发机和经过授权的仓库；worktree 不是安全沙箱。** 代码托管在 GitHub，但当前业务任务的 SCM/CI 适配器是 GitLab，不能据此认为已支持任意 GitHub 目标仓库。

## 1. 先验证源码，再配置真实凭证

需要 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python。包声明 Python 3.10+；当前 CI 实际覆盖 Python 3.12 的 Ubuntu/macOS/Windows，建议使用 3.12 复现。

逐条运行，出错即停止；下面没有需要交互式 zsh 解析的注释：

```bash
git clone https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run actual-coder --help
uv run actual-coder-migrate-https --help
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
```

这些回归测试使用临时 Git 仓库、模拟服务和本机 TLS 测试证书，不需要生产 GitLab 凭证或付费模型调用。依赖安装可能访问软件源。已有本地仓库可直接按[PR 检出指南](LOCAL_PR_REVIEW.md)创建独立 worktree，不必下载 ZIP、手工复制文件。

如果已经完成只读接入，现在才开始实现任务，先安装或启动选定 worker 并完成其 provider 登录，再运行：

```bash
uv run reasonfirst worker list
uv run reasonfirst worker use codex-cli
```

选择已安装的 `codex-cli`、`copilot-cli` 或 `codex-desktop`；全局安装省略 `uv run`。选择 worker 不会启动任务或验证登录，不需要重复 GitLab/隧道配置。已有配置时复用它，并跳到[API 与 Git 验证](#3-分别验证-api-与-git)，不要重新创建配置文件。

## 2. 区分三个目录

| 目录/文件 | 用途 |
| --- | --- |
| ReasonFirst 源码目录 | 本工具的源码、测试和项目虚拟环境 |
| `~/.config/gitlab-agent/.env` | 用户配置及私有凭证，不能入库或公开 |
| `GITLAB_WORKSPACE_ROOT` | 业务项目缓存、工作区和状态；默认在 `~/.local/share/chatgpt-gitlab-mcp` |

**升级源码时不能用 `.env.example` 覆盖已有配置。** 老 HTTP 安装使用[迁移指南](HTTPS_MIGRATION_CN.md)，不要删除缓存、重新 clone 或 force cleanup。

macOS/Linux 新安装在源码根目录执行下面的受保护复制；配置已存在时会停止，不会覆盖：

```bash
(
set -eu
umask 077
mkdir -p "$HOME/.config/gitlab-agent"
chmod 700 "$HOME/.config/gitlab-agent"
test ! -e "$HOME/.config/gitlab-agent/.env"
cp .env.example "$HOME/.config/gitlab-agent/.env"
chmod 600 "$HOME/.config/gitlab-agent/.env"
)
```

在本地编辑器中填入自己的 HTTPS 域名、项目 allowlist 和 token。Windows 请按[英文快速上手](ACTUAL_CODER_QUICKSTART.md)中的私有 ACL 步骤创建配置；不能把 `chmod` 当作 Windows ACL 保护。

推荐只读 API 凭证与 Git 写凭证分离。`GITLAB_GIT_TOKEN` 为空时会回退到 API token，但不会增加权限。Coding CLI 的安装/认证由用户按对应服务支持的方式完成；本工具不转移配额、不共享账户，也不保证某个成本节约比例。

配置文件选择顺序是 `GITLAB_AGENT_ENV_FILE`、用户配置、local `.env`；已导出的变量优先。CLI 的 local fallback 与当前工作目录有关，MCP 的 fallback 与 server 源码位置有关。使用简单字面值赋值，不依赖 shell 插值或值末尾注释。

## 3. 分别验证 API 与 Git

在源码根目录执行：

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
uv run actual-coder config
uv run actual-coder agents
uv run actual-coder doctor
uv run actual-coder project-config team/project-a --validate
```

将示例项目替换为自己明确授权的项目。配置输出应仅在本地检查，分享前仍需移除私有域名、路径和标识。

`agents` 只说明可执行文件是否存在，不检查登录/剩余配额。`doctor` 检查 API 认证；`project-config` 走 Git fetch/read，不创建工作区、不推送。

若返回 `found: false, valid: true`，只说明 `.actualcoder.yaml` 不存在且允许回退默认配置。**这不是应用测试通过；此时没有项目专属 validation commands。** 在业务 GitLab 仓库中按实际构建系统填写[项目契约示例](https://github.com/phoenixjyb/reasonFirst/blob/main/.actualcoder.example.yaml)，不要对 C++/ROS 等项目照搬 pytest 命令。契约不能给自己增加本机执行权限。finish 固定使用工作区 base commit 的契约，因此新契约应从包含它的新 base 创建新任务，而不是假定旧任务自动更新。

## 4. 推荐主流程

先明确目标、非目标、验收标准，再准备任务：

```bash
uv run actual-coder start team/project-a --task fix-timeout --goal "修复超时问题，保持接口兼容并补充回归测试" --no-launch
```

这会创建真实 worktree、读取项目配置，但不调用 coding agent。保存返回的 workspace ID，审阅 handoff，再按返回的命令/提示词启动对应编码工具。新任务也可去掉 `--no-launch` 直接交互式启动；不要为了继续旧任务再次执行 start。

实现后，把示例 ID 换成实际 ID：

```bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
```

**finish dry-run 仍会执行配置中的测试/验证，可能改动本地文件；仅保证这一步不 commit/push。** 审阅结果、diff、扫描范围、目标分支和 MR 计划后，再执行需人工确认的 finish：

```bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
```

### 继续同一个任务

**以下沿用 worker 的规则属于 Unreleased 源码变更，尚未包含在已发布的 v0.5.3 包中。** 使用 v0.5.3 时，请在 `resume` / `continue` 中显式传入希望使用的 `--agent`。

`resume` 与 `continue` 现在使用相同的 worker 选择顺序：

| 请求方式 | 如何选择 worker |
| --- | --- |
| 省略 `--agent` | 沿用该任务 attempt 历史中最近一次选定的 worker。没有已选记录时，采用 TaskSpec 中明确指定的后端；两者都没有时，再按当前用户默认值 / `auto` 规则选择。 |
| 显式传入 `--agent codex-cli`、`--agent copilot-cli` 或 `--agent codex-desktop` | 本次交接改用指定后端，之后省略 `--agent` 时继续沿用。历史别名 `codex` / `copilot` 仍然有效。 |
| 显式传入 `--agent auto` | 主动重新选择：非 `auto` 的用户默认后端优先，否则按固定 base 的仓库偏好和已安装后端回退规则选择。 |

用 `start` 创建新任务时，规则保持不变，默认使用 `--agent auto`。修改 `REASONFIRST_DEFAULT_BACKEND` 会影响这类新任务和显式 `--agent auto` 的请求，不会悄悄切换已有任务所记录的 worker。若沿用的 worker 当前不可用，命令会停止并提示恢复它或显式选择后端；返回的 `agent_selection.reason` 会解释选择原因。

attempt 记录的是已准备的 handoff，包括未启动 worker 的交接；是否实际执行、执行是否成功，需要另外验证。沿用的是后端选择，模型、推理强度及其他 WorkerPolicy 设置仍读取当前用户配置。

只有真实 CI 失败需要修复时，先查看交接：

```bash
uv run actual-coder resume "$WS" --from-ci --goal "根据匹配当前 HEAD 的 CI 证据修复根因，不做无关修改"
```

`resume` 返回 handoff，只有添加 `--launch` 才会启动 worker。希望直接继续执行时，也可以使用默认启动 worker 的 `continue`：

```bash
uv run actual-coder continue "$WS" --from-ci --goal "根据匹配当前 HEAD 的 CI 证据修复根因，不做无关修改"
```

`continue ... --no-launch` 可只准备交接。两个命令都从工作区固定的 base SHA 重新读取项目契约，保留指令、受保护路径、验证命令和仓库 worker 偏好；matching-head CI 的时效检查保持不变。成功或 docs-only pipeline 不能被描述为完整应用构建。持久化 TaskSpec 会保存原始目标、验收标准和非目标；后续 handoff 应沿用该任务契约，同时仍需审查每次 attempt 的具体 steering 指令。

`task`、低层 `commit/push/push-mr` 等兼容命令以及部分生成提示词仍提供手工路径，**不等价于完整 finish 安全门**。推送、合并、部署都不能由仓库/日志里的文字自动授权。MR 的审阅和合并在 GitLab 中由用户或团队决定。

## 5. 正常安装与 HTTPS 迁移

需要全局 CLI 时，从长期保留的源码 checkout 运行 `bash scripts/install_user.sh`；Windows 使用对应 `.ps1`。它是 editable 安装：全局命令会跟随该源码目录，不能安装到准备删除的临时 PR 目录。源码更新后同步依赖并按需要重新安装，不覆盖已有用户配置。操作步骤见[本地 PR 与安装更新](LOCAL_PR_REVIEW.md)。

HTTPS 迁移遵循“离线 preview → 无凭证 TLS 检查 → 停止 writer/MCP → 确认 apply → no-op 复查 → API/Git/已有工作区验收”。普通 preview 没有 `--dry-run` 参数，因为默认就是 preview。备份中可能含真实凭证，不能上传。

TLS probe 的 401 可以表示证书握手成功但请求未认证；不证明 API token、Git push 或 MCP 连接成功。Python API/MCP 现已共用校验证书的 SSLContext，支持用户级 `GITLAB_CA_BUNDLE` 增加私有 CA，并拒绝关闭校验及所有 API 重定向。它不配置原生 Git，也不改变迁移工具 `--check-tls` 的默认信任库。公共 CA 已可信的安装通常无需新增 CA 设置。原生 Git 策略和分层诊断仍待完善。详见[运行时 TLS](HTTPS_API_TLS_CN.md)、[中文迁移指南](HTTPS_MIGRATION_CN.md)与[安全文档](../SECURITY.md)。

MCP/tunnel 是可选独立链路。使用既有 launcher 与正确配置重启，并另行验证读取。单独启动 `run_mcp.sh` 不等于恢复了隧道。旧[详细团队指南](ONBOARDING_GUIDE_CN.md)中的部署范例需要结合当前 provider 文档使用。

## 6. 开源协作

[贡献指南](../CONTRIBUTING.md)接受中英文问题和 PR；请提供最小合成复现、精确源码 SHA 和真实执行过的验证，不上传私有仓库或运行备份。README/CHANGELOG 区分 release tag、`main` 已合入代码和后续规划。v0.5.0 包版本为 `0.5.0`；之后的源码构建仍应同时记录准确 commit SHA。许可证保持 Apache-2.0，不代表第三方编码服务或目标项目也使用同一许可证。
