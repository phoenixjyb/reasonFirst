# 证据与审查

ReasonFirst 把**证据当作一等输出**。Coding worker 的“完成”消息可以提供上下文，但不能证明任务已经可以接受。

## 生成证据

任何时候都可以：

~~~bash
uv run actual-coder evidence "$WS"
~~~

附带脱敏后的 CI 证据：

~~~bash
uv run actual-coder evidence "$WS" --from-ci
~~~

生成 EvidencePack 是只读操作：不会运行仓库代码、修改 task state、commit 或 push。

## EvidencePack 包含什么

| 字段 | 审查用途 |
| --- | --- |
| `workspace` | project/base/branch/HEAD identity、dirty/push/MR 状态。 |
| `task` | 持久 TaskSpec 和有界 attempt 历史。 |
| `project_config` | 固定 base 上的 `.actualcoder.yaml` policy/effective config。 |
| `review.changed_paths` | 实际改了什么。 |
| `review.reviewability` | 变化内容是否能完整作为有界文本审查。 |
| `review.diff` | 有界、脱敏的人工 diff，以及 truncation metadata。 |
| `ci` | 可选的脱敏 pipeline/job 证据。 |
| `redactions` | 导出证据时移除的敏感信息类别。 |
| `warnings` | 缺失、过期、不完整、截断等告警。 |
| `complete_for_human_review` | 核心 reviewability/diff/attached-CI 完整性条件是否满足。 |

!!! warning "完整不等于正确"
    `complete_for_human_review=true` 只表示证据包在这些检查下足够完整，可以进入人工审查。它**不**自动证明业务正确，也不自动满足全部 acceptance criteria，更不授权 merge。

## 六轮审查

### 1. Identity

确认目标 project、固定 base、当前 HEAD、branch 和 MR。不要在错误 revision 上审查一个看起来很漂亮的 diff。

### 2. 任务边界

读取原始 goal、acceptance criteria 和 non-goals。后续 attempt steering 不应该静默改写它们。

### 3. Diff coverage

检查 changed paths、reviewability，以及 diff 是否被截断。审查覆盖不完整应该是 blocker，而不是普通提示。

### 4. Validation

确认预期 project validation 确实运行，而且命令适合这个项目。缺少 `.actualcoder.yaml` 可能意味着没有项目专用测试契约。

### 5. 安全与发布门槛

检查 protected paths、candidate secret scan、bounded history scan、destination/candidate identity 和任何显式 override。

### 6. CI 新鲜度

CI 应对应目标 HEAD。缺失、过期、running、scheduled 或其他不完整 CI 应保持明确“不完整”，不能被总结成成功。

## 紧凑审查清单

<div class="rf-checklist" markdown>

- **Identity：**project/base/HEAD/branch/MR 是否正确？
- **Task：**goal + acceptance + non-goals 是否保持？
- **Coverage：**所有 changed paths 都可审查，diff 未截断？
- **Validation：**预期测试/命令真的运行了？
- **Security：**protected path 与 secret/history finding 都解决了？
- **CI：**对应 HEAD，并且完整程度足够支持当前结论？
- **Decision：**继续、调整、发布或合并，是否基于证据？

</div>

## 给 ChatGPT 的审查 Prompt

~~~text
请按照持久 TaskSpec 审查这个 EvidencePack。
不要把 complete_for_human_review 当成自动验收。
对每一条 acceptance criterion，指出准确支持证据；没有就标记未验证。
根据 non-goals 检查 scope creep。
标出被截断、过期、缺失、不完整或 revision 不匹配的证据。
把代码质量建议与硬性 acceptance blocker 分开。
只提出为了人工 merge 决策所需的最小下一步动作。
~~~

## EvidencePack 自己不能证明什么

- 产品需求本身就是正确的；
- 所有相关测试都已经运行；
- 审查者理解了全部领域语义；
- deploy 一定安全；
- merge 已获授权。

这些仍然属于推理与人的决策。

下一步：[与 ChatGPT 协作](chatgpt-workflow_cn.md) · [工作流程](docs/WORKFLOW_CN.md) · [安全边界](SECURITY_CN.md)
