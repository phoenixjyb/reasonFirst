# ReasonFirst

<div class="rf-hero" markdown>

<p class="rf-eyebrow">推理优先的编程编排</p>

## 强推理负责判断，coding agent 负责执行，证据回到推理层审查。

<p class="rf-lead">ReasonFirst 把 ChatGPT（或用户明确选择的其他强推理界面）放在架构、诊断、范围、验收标准和最终审查的位置；可替换的 coding agent 在受控 workspace 中完成实现。</p>

[安装 ReasonFirst](docs/INSTALL_CN.md){ .md-button .md-button--primary }
[十分钟上手](first-10-minutes_cn.md){ .md-button }
[查看日常工作流](docs/WORKFLOW_CN.md){ .md-button }
[English](index.md){ .md-button }

</div>

## ReasonFirst 如何工作

<div class="rf-flow">
  <div class="rf-flow-step"><span class="rf-flow-label">1 · 推理</span><strong>ChatGPT</strong><small>读取仓库/MR/CI 证据，做诊断、范围控制和验收标准。</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">2 · 契约</span><strong>TaskSpec</strong><small>持久保存已批准目标、非目标、验收标准和固定 workspace identity。</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">3 · 执行</span><strong>Coding worker</strong><small>Codex CLI、Copilot CLI 或 Codex Desktop 在边界内实现任务。</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">4 · 证据</span><strong>EvidencePack + CI</strong><small>返回有界 diff、validation、reviewability 与 CI 新鲜度/完整性证据。</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">5 · 决策</span><strong>ChatGPT + 人</strong><small>审查证据，决定继续、调整或合并。</small></div>
</div>

!!! info "只有一条主闭环"
    ActualCoder 也可以被终端、CI、IDE 或其他客户端直接调用，适合测试、恢复和自动化；但这是次要运维能力，不是另一种并列产品模式。

## v0.5.2：复用配置，检查服务状态

v0.5.2 为已有安装增加了更明确的操作入口：

- 用 `reasonfirst setup --reuse-existing` 复用有效的已有配置；需要修改时明确选择 `--reconfigure`。
- 用 `reasonfirst status` 检查已安装 CLI、选定配置和已知的服务注册记录；正在运行的服务身份仍需单独核验。
- 高级运维可在 macOS/Linux 上离线准备独立运行环境，并在 macOS 上审阅已知部署，为后续服务变更提供依据。

**打包安装和源码安装都是正式支持的路径**，共用引导配置与保存状态。高级服务功能仍有各自的平台与兼容性边界；安装 v0.5.2 不会激活或迁移已有服务。

只有 v0.5.2 的 GitHub Release 附件已发布、实际公开下载完成核验后，才应使用该版本 wheel。发布状态和与准确源码绑定的验证证据见发布说明；安装检查不代表 provider 已登录或浏览器侧 ChatGPT 已连接。

[阅读 v0.5.2 发布说明](docs/RELEASE_NOTES_0.5.2_CN.md){ .md-button }
[选择安装路径](docs/INSTALL_CN.md){ .md-button }

## v0.5.0 已验证基线

v0.5.0 把上面的架构组合为一套经过测试的系统：**三个 worker backend**（`codex-cli`、`copilot-cli`、`codex-desktop`）、**两个刻意分开的 MCP 面**（只读 GitLab 证据面与可选 Bridge Preview 编排面）、持久 **TaskSpec/attempt**、跨进程 workspace 变更锁、有界 **EvidencePack**、共享 finish/review gates，以及 matching-HEAD CI 反馈。

预发布审计基线 `7d16061061f6337604bd3135c9e4a693ad1fd68a` 的七个 release-critical CI job 与文档部署全部通过；主验证 job 共运行 **445 个测试**。package job 构建 wheel + sdist、检查 archive path、在干净环境安装 wheel、验证版本/CLI 入口，并重新执行源码/完整历史 secret scan。冻结发布由已发布的 v0.5.0 tag 标识，而不是这里更早的审计基线；这个历史测试数量不代表当前测试集规模。

[阅读 v0.5.0 发布说明](docs/RELEASE_NOTES_0.5.0_CN.md){ .md-button }
[查看架构说明](docs/ARCHITECTURE_CN.md){ .md-button }

!!! note "Bridge Preview 可用性"
    只读 GitLab MCP 是通用证据连接；Bridge Preview 需要所连接客户端/workspace 允许其权限更高的 custom-MCP action。该面不可用时使用终端 ActualCoder；TaskSpec、workspace identity、validation 与 review gates 保持相同。

## 我应该从哪里开始？

<div class="grid cards" markdown>

-   **第一次使用**

    先看[安装与更新](docs/INSTALL_CN.md)，选择打包快速路径或继续正式支持的源码/开发者路径；两条路径都进入 `reasonfirst setup`。然后用[十分钟上手](first-10-minutes_cn.md)走最短闭环。

-   **ChatGPT 已经能读取 GitLab**

    按[工作流程](docs/WORKFLOW_CN.md)走：ChatGPT 推理 → 有界任务交接 → worker 执行 → 证据回到 ChatGPT 审查。

-   **需要配置 coding worker**

    看[快速上手](docs/QUICKSTART_CN.md)。它是执行引擎/运维参考。

-   **遇到问题**

    看[故障排查](docs/TROUBLESHOOTING_CN.md)，不要把 API、Git、Tunnel、worker 登录、workspace 和 CI 问题混在一起。

</div>

## 核心概念

| 概念 | 含义 |
| --- | --- |
| **推理层** | ChatGPT 读取证据、诊断、决定架构、限制范围并审查结果。 |
| **TaskSpec** | 持久保存目标、验收标准、非目标以及 workspace/base identity。 |
| **Worker** | 可替换执行者：Codex CLI、Copilot CLI 或 Codex Desktop/App Server。 |
| **WorkerPolicy** | 用户拥有的 model/effort/sandbox/approval/network/tool 约束。 |
| **EvidencePack** | 有界、递归脱敏、只读的实现证据，用于返回推理层。 |
| **Finish gates** | 发布前的 validation、reviewability、protected path、secret/history 和 candidate identity 检查。 |

## ReasonFirst 不是什么

ReasonFirst **不是**模型代理、额度转移服务、通用安全沙箱或自动合并机器人。它不直接发起模型推理 API 调用；coding backend 使用各自的认证和使用权益。

更完整的边界见[架构说明](docs/ARCHITECTURE_CN.md)和[安全边界](SECURITY_CN.md)。
