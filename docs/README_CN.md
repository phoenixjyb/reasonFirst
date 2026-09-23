# 中文文档索引

[English](README.md) · **简体中文**

<!-- Paired with docs/README.md in this change; base 61f464ebbb8906817e32802be22df4012881ece5. -->

这些指南描述包含它们的源码修订，不一定对应最近发布 tag。参见[项目说明](../README_CN.md)和 [Unreleased 更新](../CHANGELOG.md)。

## 选择一个起点

**新用户：**按顺序阅读 **[首次完整接入：从前置条件到真实 ChatGPT GitLab 读取](GETTING_STARTED_CN.md)**。它涵盖 macOS/Keychain 主路径的软件安装、GitLab 配置、OpenAI 隧道/权限/runtime key、本地 profile、启动、ChatGPT 应用选择和逐步验收。[英文版](GETTING_STARTED.md)使用相同可执行步骤。

**先选择使用路径。** 只做**本地编程**时直接使用 [CLI 快速上手](QUICKSTART_CN.md)，不需要 Tunnel。需要让**普通 ChatGPT 读取 GitLab**时再按首次接入流程配置 read connector。可选的 **Bridge Preview** 是权限更高的本地编排 MCP（Codex App Server、审批、SSH workspace、finish preview），使用前应先阅读[架构说明](ARCHITECTURE_CN.md)，不要把它与只读 GitLab MCP 混为一谈。**已经接入：**使用[日常 start/status/stop/restart](TUNNEL_LIFECYCLE_CN.md)。

## 当前文档入口

| 主题 | 简体中文 | English |
| --- | --- | --- |
| 完整首次接入与第一条普通 ChatGPT 提示词 | **[从这里开始](GETTING_STARTED_CN.md)** | **[First-time setup](GETTING_STARTED.md)** |
| 产品与能力 | [项目说明](../README_CN.md) | [ReasonFirst](../README.md) |
| 启动必需服务与生命周期管理 | [隧道生命周期](TUNNEL_LIFECYCLE_CN.md) | [Tunnel lifecycle](TUNNEL_LIFECYCLE.md) |
| 使用新提出的 GitLab 项目之前 | [项目预检与用户授权](PROJECT_ACCESS_CN.md) | [Project access](PROJECT_ACCESS.md) |
| 演练 ChatGPT、Codex 与同一 MR 的三轮审查 | [实战演练](PRACTICE_LAB_CN.md) | [Practice lab](PRACTICE_LAB.md) |
| 界面职责与证据 | [工作流程](WORKFLOW_CN.md) | [Workflow](WORKFLOW.md) |
| 本地源码安装与受控实现 | [CLI 快速上手](QUICKSTART_CN.md) | [CLI quickstart](ACTUAL_CODER_QUICKSTART.md) |
| 手工/高级启动、凭证替代方式 | [运维指南/Windows](OPENAI_TUNNEL_TEAM_SETUP_CN.md) | [Manual operator guide](SETUP_TUTORIAL.md) |
| 人工交接批准任务与结果 | [写作模板，不是运行时 API](TASK_HANDOFF_TEMPLATE_CN.md) | [Handoff template](TASK_HANDOFF_TEMPLATE.md) |
| 当前架构与信任边界 | **[架构说明](ARCHITECTURE_CN.md)** | **[Architecture](ARCHITECTURE.md)** |
| 架构理念 / 设计动机 | [设计理念](DESIGN_PHILOSOPHY_CN.md) | [Design philosophy](DESIGN_PHILOSOPHY.md) |
| HTTP 到 HTTPS 迁移 | [迁移指南](HTTPS_MIGRATION_CN.md) | [Migration](HTTPS_MIGRATION.md) |
| API/MCP 私有 CA、重定向与原生 Git 边界 | [运行时 TLS](HTTPS_API_TLS_CN.md) | [Runtime TLS](HTTPS_API_TLS.md) |
| PR 检出、测试、源码更新 | [本地 PR 审阅](LOCAL_PR_REVIEW_CN.md) | [Local PR review](LOCAL_PR_REVIEW.md) |
| 不弱化控制的诊断 | [故障排查](TROUBLESHOOTING_CN.md) | [Troubleshooting](TROUBLESHOOTING.md) |
| 贡献流程 | [贡献指南](../CONTRIBUTING_CN.md) | [Contributing](../CONTRIBUTING.md) |
| 安全报告与限制 | [安全策略](../SECURITY_CN.md) | [Security](../SECURITY.md) |
| 公开发布之前 | [维护者清单](PUBLIC_RELEASE_CHECKLIST_CN.md) | [Release checklist](PUBLIC_RELEASE_CHECKLIST.md) |

## 第一次 ChatGPT 对话之前的必需步骤

完成前置条件，启动选定 Tunnel/MCP，保持其 Terminal 运行，检查本地就绪，然后在普通 ChatGPT 输入框选择真实应用。随后调用 `gitlab_whoami`、`check_project_access`，并在 `resolved_commit_sha` 读取文件。本地健康、单独身份成功、消息里写一个连接名称，都不等于项目读取端到端验收。

Keychain 是 Mac 指南推荐的 runtime-key 来源，不是所有平台强制组件。OpenAI tunnel key、Tunnel ID、GitLab token、本地 allowlist、编程代理登录分别不同。Platform 隧道权限、ChatGPT workspace 权限、GitLab/本地项目授权也互不替代。完整指南在每个停止点指明应由谁处理；不能假定上次 shell export 在新 Terminal 仍存在。

## 新项目访问门槛

项目名/本地目录不能证明远端存在或授权。首次引入项目时，批量读取或交接前调用 `check_project_access`。`ok: false` 时展示诊断，等待用户/操作者处理。404 或过滤后的空列表不能区分不存在和不可访问。

逐项目授权在本地 MCP 的 `GITLAB_ALLOWED_PROJECTS`，不是 OpenAI Tunnel 设置。仅在明确批准后追加准确目标项目、保留已有项，并重启原 MCP。助手不会创建项目或自动授权。使用较早的首任务示例前，同样需要此门槛。

## 一个推理界面，不额外要求聊天工具

普通 ChatGPT 负责推理与审阅。只读 GitLab Tunnel/MCP 提供仓库/MR/CI 读取；ActualCoder 使用 `codex-cli`、`copilot-cli` 或 `codex-desktop` 实现获批任务。可选 Bridge Preview 是另一条权限更高的本地编排接口。localhost Assistant、Codex tunnel plugin、Inspector 不是前置条件或验收门槛，Overview/Logs 只是可选诊断。不使用 Assistant 不会禁用上游附带 helper，也不会卸载 Codex。

标准 GitLab read connector 不承担本地任务执行。可选 Bridge Preview 已支持本地编排、审批、finish preview 和用户配置 SSH 操作；持久化 core TaskSpec/EvidencePack 仍未进入 `main`。读取成功不证明 Git push、编程模型登录或完整应用 CI；这些在获批实现中验证。

## 翻译范围与维护

同步维护中英文当前指南，包括可执行示例、停止条件、权限与证据边界。新增完整接入双语页记录源码基线与服务商核对日期；测试验证命令一致和初始化安全。既有 quickstart、生命周期、访问、迁移、TLS 双语指南继续作为专项参考。

历史说明/CHANGELOG 保留记录，LICENSE 不变。文档不产生新发布、不改变权限、不宣称未支持的功能。服务商界面/资格会变化，核对完整接入及手工指南中的一手参考。

## 历史设计与旧操作示例

[旧中文 onboarding](ONBOARDING_GUIDE_CN.md)与[旧中文配置教程](SETUP_TUTORIAL_CN.md)仅为历史记录，不是必需清单。不执行旧的含 token 示例、不覆盖工作中的 `.env`，不因历史文档提及就安装可选组件。

[V0.2 设计](V0.2_WRITE_ACCESS_DESIGN.md)、[V0.2 Codex 指南](V0.2_CODEX_QUICKSTART.md)、[CodingAgent 兼容指南](CODINGAGENT_QUICKSTART.md)、[V0.3 路线图](V0.3_ROADMAP_CN.md)、[V0.3 审计](V0.3_RELEASE_AUDIT_CN.md)保留历史背景。

专项实现说明包括[发布安全](V0.3.1_PUBLICATION_SAFETY_CN.md)、[退出状态](V0.3.1_COMMAND_EXIT_CN.md)、[历史扫描](V0.3.1_SECRET_HISTORY_CN.md)、[日志证据](V0.3.1_SHARED_LOG_EVIDENCE_CN.md)。文件名不代表 v0.3.1 已发布。[Issue #6](https://github.com/phoenixjyb/reasonFirst/issues/6)、[Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10)跟踪剩余任务循环/HTTPS 工作。
