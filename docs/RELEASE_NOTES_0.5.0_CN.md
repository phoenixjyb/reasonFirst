# ReasonFirst 0.5.0 发布说明

本文汇总 ReasonFirst v0.5.0。GitHub release/tag 标识准确的发布提交；
源码检出可能继续领先，因此复现验证或报告问题时仍应记录准确 commit SHA。

## 为什么是 0.5.0

0.5.0 主要是兼容性、安全性和公共可用性更新，延续 v0.3.0 的核心方向：
强推理界面先负责判断和审查，可替换的 coding worker 再负责实现。

选择 0.5.0 是有意的版本线调整：此前开发工作已经使用过 0.4 版本线，
因此本次发布不再复用该版本族。

## 主要用户可见变化

- 完整 Bridge 全聊天闭环：固定 TaskSpec → 单一 managed workspace → Codex App
  Server 执行 → diff 审查 → snapshot-bound finish preview → 人工明确批准 →
  GitLab MR → matching-HEAD CI → EvidencePack。
- 修复当前 Codex App Server worker-start 兼容问题，包括 thread policy wire value
  以及 ReasonFirst MCP 未配置时不再合成无 transport 的无效 MCP 条目。
- macOS launchd 会话级代理同步：在外部模型访问依赖 HTTP(S) proxy 时，不把代理值写入
  source 或 LaunchAgent plist。
- MCP 安装/重启后的 health polling，避免健康服务因为启动延迟被误判失败。
- v0.3.0 之后累积的 TaskSpec/attempt 持久化、EvidencePack、worker-policy 证据、
  显式审批、practice tooling、CI failure classification 和 finish safety gates。
- 面向外部用户的中英文全聊天演练、onboarding、troubleshooting、architecture、
  contribution 与 release maintainer 文档。

## 已观察到的端到端验收

2026-09-28 的合成 GitLab 演练真实观察到：

- 在固定 revision 上读取实时 GitLab 源码；
- 单一 managed workspace 与 feature branch；
- Bridge-managed Codex App Server worker 执行；
- 仅修改 README 的已审 diff；
- 配置的 unit tests 成功；
- 具体 finish-preview snapshot digest；
- 人工对该准确 snapshot 明确批准；
- 受控 commit/push 并创建 Merge Request；
- matching-HEAD GitLab CI 的真实 unit-test job 成功；
- EvidencePack 完整；
- 没有自动 merge。

这证明的是已演练路径，不代表每一种部署拓扑都已验证。

## 已知边界

- GitLab 是当前已实现的 SCM/CI target adapter；ReasonFirst 自身托管在 GitHub，
  不代表已实现 GitHub target task adapter。
- Git worktree 与 worker subprocess control 不是 OS security sandbox。
- native Git trust/destination policy 与 Python API/MCP TLS 仍是不同边界。
- Bridge Preview MCP 权限高于 read-oriented GitLab MCP，应明确启用；其 write-capable
  工具能否通过 ChatGPT 使用取决于所连接客户端/workspace，终端 ActualCoder 仍是通用执行兜底。
- macOS launchd 不会自动继承交互 shell 的 proxy 环境；需要时使用文档化的
  session-scoped sync helper。
- 最终 merge 仍由人决定，ReasonFirst 不自动 merge。
- 外部 coding tools 使用各自认证、quota 与 billing；ReasonFirst 不直接调用模型推理 API。

## 发布验证

v0.5.0 发布门要求最终 exact HEAD 通过：

- `validate`（完整 Python 单元/集成测试）；
- Ubuntu、macOS、Windows 跨平台 job；
- Ubuntu、macOS Bridge 回归 job；
- `package-release-candidate`（wheel + sdist 构建、archive path 检查、
  干净 wheel 安装/版本/CLI 检查，以及源码/完整历史 secret scan）；
- 文档有变化时的 documentation-site build/deploy。

预发布审计基线 `7d16061061f6337604bd3135c9e4a693ad1fd68a`
的七个 CI job 与 Docs site 均成功；`validate` 共运行 **445 个测试**。
最终 GitHub Release 记录准确 tag SHA，只有最终文档提交在相同 exact-head
门槛下通过后才应发布。

完整维护者清单见 `docs/PUBLIC_RELEASE_CHECKLIST_CN.md`。
