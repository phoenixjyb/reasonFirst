# 十分钟上手

**目标：**完整走一遍 ReasonFirst 主闭环，同时不丢失“推理层先定义边界”的原则。

!!! note "这是最短路径"
    本页假设 GitLab 连接和至少一个 coding worker 已经可用。如果还没有，请先完成[首次完整接入](docs/GETTING_STARTED_CN.md)。

## 1. 先读，再写

在普通 ChatGPT 中选择目标 GitLab 连接，先发一个只读请求：

~~~text
仅使用已选择的 GitLab 连接处理 team/project-a。
先确认当前 GitLab 身份，并执行项目访问预检。
访问成功后，在 resolved commit 上读取 README.md 和与问题相关的真实文件。
把观察到的事实与改进建议分开。现在不要修改任何内容。
~~~

关键不是得到一篇很长的总结，而是确认推理建立在真实仓库和准确 revision 上。

## 2. 把对话收敛成有边界的任务

实现前，让推理层把边界写清楚：

~~~text
把这个问题整理成实现契约，包含：
- 一个明确 goal；
- 可验证的 acceptance criteria；
- 明确 non-goals；
- 可能涉及的组件；
- 风险与假设；
- 接受变更前必须看到的证据。
不要超出已经读取到的证据扩大任务。
~~~

好的任务应该足够明确，使 worker 不需要自行做产品或架构决策。

## 3. 将意图持久化为 TaskSpec

从 ReasonFirst 源码目录准备 workspace，但先不启动模型：

~~~bash
uv run actual-coder start team/project-a \
  --task fix-timeout \
  --goal "修复 timeout bug" \
  --acceptance "timeout 回归测试通过" \
  --acceptance "现有 API 保持兼容" \
  --non-goal "不做无关重构" \
  --no-launch
~~~

`--no-launch` 仍会创建真实的受管 workspace。保存返回的 workspace ID。goal、acceptance criteria 和 non-goals 会持久化到 TaskSpec。

!!! warning "继续同一任务时不要再次 start"
    保留 workspace ID。后续使用返回的 handoff、`resume` 或 `continue`。

## 4. 让 worker 负责执行

检查返回的 workspace path、branch、backend、WorkerPolicy 和 handoff，然后用返回的 backend 命令/提示词启动所选 worker。

Worker 可以查看、修改和运行允许的 validation，但不应该重定义任务。如果发现会改变范围的新问题，应把证据带回推理层，而不是静默扩大实现。

## 5. 发布前先看证据

把 `WS` 换成真实 workspace ID：

~~~bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder evidence "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
~~~

检查 diff、reviewability、validation、protected paths、secret/history coverage 和 candidate identity。Worker 说“完成了”不等于验收。

确认 dry-run 无阻断且变更符合意图后：

~~~bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
uv run actual-coder evidence "$WS" --from-ci
~~~

## 6. 把证据送回 ChatGPT

将有界证据返回推理会话——有可用编排接口时通过接口提供，否则粘贴/附加不含秘密的 EvidencePack 输出。

可以这样要求审查：

~~~text
请按照原始 TaskSpec 审查这次实现。
逐条检查 acceptance criteria 和 non-goals。
区分已验证证据与缺失、过期、不完整或被截断的证据。
不要扩大任务范围。
如果当前 HEAD 确实有 CI 失败，识别根因并提出最小修复任务。
否则说明在我做 merge 决策前是否还缺少证据。
~~~

## 完成主闭环的判断标准

<div class="rf-checklist" markdown>

- ChatGPT 在实现前读取过目标仓库的准确 revision。
- 任务有明确 goal、acceptance criteria 和 non-goals。
- 一个受管 workspace 对应这一个任务。
- 可替换 worker 在边界内执行。
- ReasonFirst 返回可审查的 diff/validation 证据。
- matching-HEAD CI 被真正核对，而不是想当然。
- ChatGPT 和人依据证据决定下一步，而不是相信 worker 的“成功”声明。

</div>

下一步：[与 ChatGPT 协作](chatgpt-workflow_cn.md) · [证据与审查](evidence-review_cn.md) · [完整工作流程](docs/WORKFLOW_CN.md)
