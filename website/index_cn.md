# ReasonFirst

**把强推理放在编程最前端，让 coding agent 负责执行。**

ReasonFirst 是一个推理优先的编程编排系统。参考主流程把 **ChatGPT（或用户明确选择的其他强推理界面）** 放在架构、诊断、范围、验收标准和最终审查的位置；可替换的 coding agent 负责实现。

[首次接入](docs/GETTING_STARTED_CN.md){ .md-button .md-button--primary }
[理解工作流程](docs/WORKFLOW_CN.md){ .md-button }
[English](index.md){ .md-button }

---

## 一眼看懂

~~~text
ChatGPT / 强推理界面
        │
        │ 读取仓库 / MR / CI 证据
        ▼
架构 · 诊断 · 范围 · 验收标准
        │
        ▼
TaskSpec — 持久的已批准意图
        │
        ▼
ReasonFirst 控制层
workspace · policy · validation · review gates
        │
        ▼
coding worker
codex-cli · copilot-cli · codex-desktop
        │
        ▼
实现 + 测试 + MR / CI
        │
        ▼
EvidencePack · diff · CI 证据
        │
        └──────────────► ChatGPT + 人工审查
~~~

!!! info "只有一条主闭环"
    ActualCoder 也可以被终端、CI、IDE 或其他客户端直接调用，适合测试、恢复和自动化；但这是次要运维能力，不是另一种并列产品模式。

## 我应该从哪里开始？

<div class="grid cards" markdown>

-   **第一次使用**

    先看[首次完整接入](docs/GETTING_STARTED_CN.md)，然后跑一遍[实战演练](docs/PRACTICE_LAB_CN.md)。

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
