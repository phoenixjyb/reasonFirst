# ReasonFirst

[English](README.md) · **简体中文** · **[首次完整接入](docs/GETTING_STARTED_CN.md)** · [文档索引](docs/README_CN.md)

<!-- Paired with README.md in this change; base 61f464ebbb8906817e32802be22df4012881ece5. -->

> **推理优先的编程编排。**
> 让你最强的推理模型负责推理，让编程代理负责写代码。

[![CI](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml/badge.svg)](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

ReasonFirst 将交互式工程推理、可替换编程代理和本地 GitLab 流程连接起来。人和选定的推理界面共同定义任务；**ActualCoder** 准备 Git worktree，将任务交给 **Codex CLI** 或 **GitHub Copilot CLI**，并提供验证、Merge Request 和 CI 证据供审阅。

目标是将推理能力用于架构、诊断和审查，把实现迭代交给编程代理。ReasonFirst 不是模型代理、额度转移服务或自动合并机器人。不直接调用模型推理接口，外部编程工具沿用各自认证和计费方式。节省成本是设计目标，不是已测得的保证。

**这是早期开发工具：**在可信开发机使用可信仓库，Git worktree 不是安全沙箱。使用真实凭证或执行仓库代码前，阅读 [SECURITY_CN.md](SECURITY_CN.md)。MCP 端点保持私有，不意味着选定数据不会返回给推理服务；应获得相应数据共享批准。

## 从这里开始：先选你真正需要的路径

ReasonFirst 不要求所有使用场景都完成同一套接入。

| 目标 | 推荐入口 | 是否需要 Tunnel |
| --- | --- | --- |
| 本地受控编程：`codex-cli` / `copilot-cli` / `codex-desktop` | **[CLI 快速上手](docs/QUICKSTART_CN.md)** | 不需要 |
| 让普通 ChatGPT 读取/审查获批 GitLab 仓库、MR、CI | **[首次完整接入](docs/GETTING_STARTED_CN.md)** | 这条路径需要 |
| App Server 编排、显式审批、命名 SSH workspace、远端 validation / finish preview | **[架构说明](docs/ARCHITECTURE_CN.md)** 后再看 Bridge Preview 指南 | 可选，取决于部署方式 |

如果目标只是本地编程，不需要先配置 Tunnel。需要 ChatGPT read connection 时，再按首次完整接入逐步配置。Bridge Preview 的权限高于只读 GitLab connector，应明确选择和部署，不应作为普通读取的默认前置条件。

```text
本地编程：
安装 -> 用户配置 -> actual-coder doctor -> start/resume -> worker -> finish -> MR/CI

ChatGPT 读取：
权限 -> 安装 -> GitLab 配置 -> Tunnel -> 选择应用 -> 实时身份/项目/文件读取

Bridge Preview：
显式本地配置 -> target/policy -> App Server -> 审批 -> finish preview
```

任何路径都**不要求 localhost Assistant**；Overview/Logs 只是可选诊断。

## 按任务选择指南

| 目标 | 指南 |
| --- | --- |
| 从前置条件到第一条 ChatGPT 工作提示词 | **[完整首次接入](docs/GETTING_STARTED_CN.md)** · **[English](docs/GETTING_STARTED.md)** |
| 已配置后的启动/状态/停止/重启 | [隧道生命周期](docs/TUNNEL_LIFECYCLE_CN.md) · [English](docs/TUNNEL_LIFECYCLE.md) |
| 确认新项目存在/可访问并明确授权 | [项目访问](docs/PROJECT_ACCESS_CN.md) · [English](docs/PROJECT_ACCESS.md) |
| 演练推理、worker 和 MR 迭代 | [实战演练](docs/PRACTICE_LAB_CN.md) · [English](docs/PRACTICE_LAB.md) |
| 分清界面职责 | [工作流程](docs/WORKFLOW_CN.md) · [English](docs/WORKFLOW.md) |
| 纯本地安装与受控实现 | [CLI 快速上手](docs/QUICKSTART_CN.md) · [English](docs/ACTUAL_CODER_QUICKSTART.md) |
| 已批准要求与实际结果 | [人工交接模板](docs/TASK_HANDOFF_TEMPLATE_CN.md) · [English](docs/TASK_HANDOFF_TEMPLATE.md) |
| 当前架构 | **[架构说明](docs/ARCHITECTURE_CN.md)** · **[English](docs/ARCHITECTURE.md)** |
| 设计理念 | [设计理念](docs/DESIGN_PHILOSOPHY_CN.md) · [English](docs/DESIGN_PHILOSOPHY.md) |
| 已有 HTTP 到 HTTPS 迁移 | [迁移指南](docs/HTTPS_MIGRATION_CN.md) · [English](docs/HTTPS_MIGRATION.md) |
| 证书、API 重定向与原生 Git 边界 | [运行时 TLS](docs/HTTPS_API_TLS_CN.md) · [English](docs/HTTPS_API_TLS.md) |
| 审阅 PR 或更新长期检出目录 | [本地 PR 审阅](docs/LOCAL_PR_REVIEW_CN.md) · [English](docs/LOCAL_PR_REVIEW.md) |
| 不受支持/高级 profile 或手工启动 | [手工运维/Windows](docs/OPENAI_TUNNEL_TEAM_SETUP_CN.md) · [English](docs/SETUP_TUTORIAL.md) |
| 排错、贡献、安全报告 | [故障排查](docs/TROUBLESHOOTING_CN.md) · [贡献指南](CONTRIBUTING_CN.md) · [安全](SECURITY_CN.md) |

## 实现任务如何流转

```text
人 + 推理界面：定义目标、约束、验收标准
    -> ReasonFirst 控制层：ActualCoder CLI 或 Bridge Preview
    -> 选择 worker：codex-cli / copilot-cli / codex-desktop
    -> 隔离本地 worktree 或用户配置的 SSH workspace
    -> validation + 共享 review/secret/protected-path gates
    -> 经审阅的 feature branch / GitLab MR / matching-HEAD CI
    -> 人决定继续还是合并
```

ReasonFirst 现在有**两种职责不同的 MCP 表面**。原有 GitLab MCP 仍以仓库/MR/CI 读取为主；可选的 **Bridge Preview** 是权限更高的本地编排接口，可以准备受管 workspace、控制 Codex App Server、暴露待审批请求、读取未发布的受管 diff/artifact，并执行完整 finish preview。SSH 写入/验证仅限用户预先配置的 target；不暴露任意远端 shell。实验性远端发布默认隐藏且关闭。详见[架构说明](docs/ARCHITECTURE_CN.md)。持久化 core TaskSpec/attempt/EvidencePack 目前仍未进入 `main`。

用[访问预检](docs/PROJECT_ACCESS_CN.md)确认真实项目后，按 [CLI 快速上手](docs/QUICKSTART_CN.md)或[演练](docs/PRACTICE_LAB_CN.md)执行。`start --no-launch` 创建真实本地工作区与交接，但不调用编程模型；不是无副作用预览。继续同一任务时保留 workspace ID，使用 `resume`，不要反复 `start`。

检查实际本地差异后运行 `finish --dry-run`，再明确批准实际 `finish`。**Dry-run 仍运行配置的验证命令，只意味着不提交/推送，不意味着不执行代码。** 缺少 `.actualcoder.yaml` 是允许的，但没有项目专用测试。没有真实必需测试的绿色结果不足以验收任务。

`resume --from-ci` 返回匹配 HEAD 的失败证据，不会自动执行修复。不要为成功 CI 编造修改。低层 `commit`/`push` 与部分生成交接不包含全部 finish 门槛；保留明确任务边界，使用审阅后的 finish 主流程。详见[工作流程](docs/WORKFLOW_CN.md)。

GitLab 是已实现的目标源码管理/CI 集成。本项目源码托管于 GitHub，不代表有 GitHub 目标任务适配器。推荐显式后端名为 `codex-cli`、`copilot-cli`、`codex-desktop`；历史 `codex` / `copilot` 继续作为兼容别名。

## 无生产凭证试用源码

只想跑测试/help 的贡献者，安装 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。包元数据要求 Python 3.10+，CI 使用 3.12。在新检出目录逐条运行，遇错停止：

```bash
git clone https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run actual-coder --help
uv run actual-coder-tunnel --help
uv run actual-coder-check-project --help
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
```

测试使用临时仓库、模拟服务和回环测试环境，不需要生产凭证或真实隧道。安装依赖会访问包索引。本源码基线的 `uv sync` 可能生成未跟踪 `uv.lock`，保留它，不要忽略/重置其他文件。纯本地测试路径不要求 MCP/Tunnel 接入。

## main 上已有的能力

最近一个文档记录的发布版本为 **v0.3.0**。已合并源码不一定包含于该 tag，包元数据仍为 `0.3.0`。报告问题时记录 commit SHA 与版本，参见 [CHANGELOG.md](CHANGELOG.md)。

| 能力 | 当前范围 |
| --- | --- |
| 受管工作区 | 本地 Git cache/worktree + 用户配置的 SSH workspace；功能分支、恢复与跨进程 mutation lock |
| 受控 finish | 本地/远端共用 review gates：validation、reviewability、protected path、candidate/history secret scan 与准确 candidate identity |
| 发布安全 | 本地 reviewed finish 为正式默认路径；SSH 发布绑定准确 tree/destination，实验性且默认关闭 |
| 远端验证 | 仅结构化 argv + 用户配置容器策略；不暴露任意远端 shell，不隐式拉取镜像 |
| 命令/CI 证据 | 传递非零失败；matching-HEAD CI 与有界、脱敏日志/artifact |
| Worker 策略 | 用户默认后端 + model/effort/sandbox/network/permission；Codex Desktop 校验解析后的策略并记录 reroute |
| 审批 | Codex Desktop 通过本地终端或 MCP pending/approve/decline 显式审批；超时默认拒绝 |
| 项目访问预检 | 本地 allowlist -> GitLab 项目/ref/文件；明确诊断和固定修订；不自动授权 |
| 隧道生命周期 | macOS/Linux 已有 profile 的 configure/start/status/stop/restart、可选准确 Keychain 读取、所属进程清理；仅前台 |
| HTTPS 迁移 | 离线预览、确认后本地 URL 更新、私有备份、向前恢复 |
| MCP 表面 | 只读 GitLab MCP + 可选本地 Bridge Preview 编排 MCP；信任边界见架构文档 |

**尚未进入 `main`：**持久 core TaskSpec/attempt 与 bounded EvidencePack，以及进一步的原生 Git trust/destination policy。工作区 mutation lock 与本地/SSH 共用 finish gates 已经在 `main` 实现。当前边界见[架构说明](docs/ARCHITECTURE_CN.md)。

API/MCP 客户端拒绝关闭 TLS 验证和所有 API 重定向，请配置最终端点。`GITLAB_CA_BUNDLE` 增加 Python API/MCP 信任，不配置原生 Git 或迁移 `--check-tls` 探针。范围不要混淆，详见[运行时 TLS](docs/HTTPS_API_TLS_CN.md)。

## 名称、兼容性与贡献

**ReasonFirst** 是项目；**ActualCoder**（`actual-coder`）是高层 CLI。`gitlab-agent` 是低层，`codingagent` 为兼容别名。分发名 `chatgpt-selfhosted-gitlab-mcp`、Python 包 `gitlab_agent`、私有配置 `~/.config/gitlab-agent/.env` 与已有工作区路径有意保留，更新时不要重命名受管目录。

全局 editable 命令跟随源码目录；PR 实验使用独立 worktree。欢迎贡献及中英文可复现报告：[贡献指南](CONTRIBUTING_CN.md)、[安全报告](SECURITY_CN.md)、[公开发布清单](docs/PUBLIC_RELEASE_CHECKLIST_CN.md)。不要在公共 issue 发布凭证文件、私有源码、迁移备份或未经审阅日志。

采用 **Apache License 2.0**，见 [LICENSE](LICENSE)。外部工具各有许可和条款。本项目独立维护，不是 OpenAI、GitHub 或 GitLab 官方产品。
