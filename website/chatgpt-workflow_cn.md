# 与 ChatGPT 协作

ReasonFirst 最有效的使用方式，是让 ChatGPT 始终作为**推理与审查层**，而不是把它变成一个不可见的 coding agent 外壳。

## 谁负责什么？

| 层 | 主要职责 |
| --- | --- |
| **ChatGPT / 推理界面** | 读取证据、诊断、决定架构、定义范围/非目标/验收标准、审查结果。 |
| **ReasonFirst** | 持久化 TaskSpec、控制 workspace/backend policy、运行 review gates、产生有界证据。 |
| **Coding worker** | 查看文件、改代码、运行允许命令、根据实现反馈迭代。 |
| **人** | 批准范围变化、发布以及最终 merge/deploy 决策。 |

更换 worker 不应该改变已经批准的任务。

## Prompt 1 — 先读取，再提建议

新仓库或新问题开始时：

~~~text
仅使用已选择的 GitLab 连接。
先确认当前身份和项目访问权限。
在 resolved revision 上读取与问题相关的真实源码。
把结果分成：
1. 由文件 / MR / CI 直接支持的事实；
2. 不确定项或缺失证据；
3. 改进建议。
现在不要实现。
~~~

这样可以避免架构讨论建立在文件名、旧总结或历史聊天记忆上。

## Prompt 2 — 定义 worker 可执行的任务

实现前：

~~~text
把已经同意的变更整理成有边界的实现契约。

返回：
- Goal
- Acceptance criteria
- Non-goals
- Affected components
- Constraints / compatibility requirements
- Expected validation
- Evidence required before acceptance

不要把还没有解决的产品或架构决策交给 coding worker。
~~~

然后用 `actual-coder start ... --acceptance ... --non-goal ...` 将边界持久化。

## Prompt 3 — 审查实现

ReasonFirst 产生 EvidencePack 或 finish preview 后：

~~~text
按照原始 TaskSpec 审查这次实现。

对每一条 acceptance criterion：
- 指出准确支持证据；
- 如果证据缺失、过期、不完整或被截断，标记为未验证。

检查 non-goals 是否出现 scope creep。
检查 diff 是否真正对应所述根因。
把 validation 与 matching-HEAD CI 分开检查。
不要把 worker 自己的总结当作证据。

返回：
1. 已验证结论；
2. blocker / 缺失证据；
3. 如果需要，最小下一步动作。
~~~

## Prompt 4 — 修复真实 CI 失败

只有 CI 确实对应当前 workspace HEAD 时才使用：

~~~text
附带的 CI 证据对应当前 HEAD。
识别失败 job 的根因。
除非我明确批准范围变化，否则保持原始 TaskSpec 不变。
提出最小修复 attempt，以及证明它的 validation。
不要因为旧的或无关的 CI 失败而修改代码。
~~~

然后在同一 workspace 上使用 `resume --from-ci` 或 `continue --from-ci`。

## ChatGPT 应该停下来询问的情况

- 目标 project/ref 实际上没有被读取；
- 任务没有可验证的 acceptance criteria；
- 新发现会实质改变范围或架构；
- repository policy / protected path 与方案冲突；
- CI 对当前 HEAD 已过期；
- 证据被截断或 reviewability 不完整；
- 需要把凭证或私有配置放进 prompt。

## Prompt 要小，证据要准

有效推理不需要整个仓库或全部原始日志。优先提供：

- 固定 revision 的相关文件；
- TaskSpec；
- 有界 changed paths / diff；
- validation 结果；
- 脱敏的失败 job 证据；
- matching-HEAD pipeline 状态。

这正是 ReasonFirst 持久化任务意图并产生 bounded EvidencePack 的原因。

下一步：[十分钟上手](first-10-minutes_cn.md) · [证据与审查](evidence-review_cn.md)
