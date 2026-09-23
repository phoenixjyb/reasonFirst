# 全聊天闭环 E2E 演练

[English](CHAT_ONLY_PRACTICE.md) · [实战演练](PRACTICE_LAB_CN.md) · [架构](ARCHITECTURE_CN.md)

这个演练验证：用户只在一个普通 ChatGPT 对话里完成 ReasonFirst 的完整闭环，不需要手工打开 Terminal，也不需要手工操作 Codex CLI。

目标链路：

```text
ChatGPT
  → 实时 GitLab 只读证据
  → ReasonFirst Bridge 受管 workspace
  → Bridge 管理的 Codex App Server 执行
  → ChatGPT 审查 diff / finish preview
  → 人工明确批准发布
  → ReasonFirst 按已审 snapshot 发布
  → GitLab MR + matching-head CI
  → ChatGPT 读取 CI / EvidencePack
```

最终是否 merge 仍由人决定；本演练不启用自动合并。

## 演练任务

使用现有合成项目：

```text
https://gitlab.recomo.com.cn/phoenixjyb/reasonfirst-practice
ref: main
```

只做文档修改：

- 仅修改 `README.md`；
- 新增标题 `## Chat-only E2E rehearsal / 全聊天闭环演练`；
- 写四条简短 bullet：
  1. 这是一个合成的、仅文档修改的演练仓库；
  2. ChatGPT 负责读取、推理、范围和审查，Bridge 管理的 coding worker 负责执行；
  3. 发布前必须经过 finish preview 审查并得到人的明确批准；
  4. 最终验收要求 matching-head CI 成功；
- 保留 README 现有内容；
- 运行仓库配置的 unit tests；
- 不改 Python、tests、`.actualcoder.yaml`、`.gitlab-ci.yml`、`AGENTS.md`、`EXERCISE.md`。

这样会真实产生 edit、commit、branch、MR 和 CI，但不会混入 Stage 1/2/3 功能开发。

## 新 Chat 的四段提示词

### Prompt 1：只读 + 规划

```text
我们要在下面项目进行一次“完全由 ChatGPT 对话驱动”的 ReasonFirst E2E 演练：

https://gitlab.recomo.com.cn/phoenixjyb/reasonfirst-practice
ref = main

我不希望运行任何 Terminal 命令，也不手工操作 Codex CLI。
只能使用：
1）已连接的 Recomo GitLab 只读 MCP，用于 GitLab 仓库/MR/CI 证据；
2）ReasonFirst Bridge Preview MCP，用于受管 workspace 和 coding worker 执行。

不要用 web search、GitHub 副本、旧对话结果或手工 shell 代替。

第一阶段只 READ + PLAN。现在不要修改文件，也不要启动 coding worker。

先：
- 确认当前 GitLab 身份；
- 对精确 project/ref 做访问预检；
- 要求 README.md、AGENTS.md、.actualcoder.yaml、.gitlab-ci.yml、EXERCISE.md、
  clip_summary.py、tests/test_clip_summary.py 都存在；
- 报告 resolved commit SHA 和任何缺失证据。

然后在 resolved commit 上至少读取 README.md、AGENTS.md、.actualcoder.yaml、.gitlab-ci.yml。
同时调用 reasonfirst_doctor 和 reasonfirst_target_probe 检查本地/default 执行目标。

本次精确任务只改文档：
- 只修改 README.md；
- 新增标题 “## Chat-only E2E rehearsal / 全聊天闭环演练”；
- 新增四条简短 bullet：
  1. 这是合成的、只做文档修改的演练；
  2. ChatGPT 负责读取/推理/范围/审查，Bridge 管理的 coding worker 负责执行；
  3. 发布需要经过 finish preview 审查并得到人的明确批准；
  4. 最终验收要求 matching-head CI 成功；
- 保留 README 所有现有内容；
- 运行配置的 unit tests；
- 不修改 Python、tests、.actualcoder.yaml、.gitlab-ci.yml、AGENTS.md 或 EXERCISE.md。

核对这个任务是否符合仓库 contract/instructions。给我：
- 实际观察到的仓库事实；
- 审阅的精确 revision；
- 有边界的实现计划；
- acceptance criteria；
- non-goals；
- 预期修改文件。

到这里 STOP。不要 dispatch workspace，不要启动 Codex，等我批准。
```

### Prompt 2：执行并审查，但不发布

```text
批准。完全通过 ReasonFirst Bridge Preview 执行刚才那个精确计划。

对精确 GitLab project/ref 调用 reasonfirst_dispatch，focus=README.md。
把第一阶段批准的文档 goal、acceptance criteria 和 non-goals 一并传给 dispatch，
使它们持久化到 managed TaskSpec。
dispatch 后：
- 确认受管 workspace 的 base SHA 与刚才 ChatGPT 审阅的 revision 相同；
- 如果不同，先从受管 workspace 重新读取相关文件并重新核对；
- 不要创建第二个 workspace。

随后启动 Bridge 管理的 coding worker，并把刚才批准的文档任务、acceptance criteria、non-goals
原样作为执行边界。

通过 reasonfirst_codex_status/events 监控执行。必要时查看 pending approvals。
不要批准任何扩大范围、读取凭证、修改 CI/policy、发布代码或触碰 README 之外文件的请求。

worker idle 后：
- 读取 reasonfirst_workspace_status；
- 读取 reasonfirst_review_bundle 和 reasonfirst_diff；
- 确认 README.md 是唯一 changed path；
- 确认配置的 unit tests 真正运行且成功；
- 调用 reasonfirst_finish_preview，commit message 使用：
  “docs: add chat-only E2E rehearsal note”

现在不要 publish。

向我展示：
- workspace ID 和 branch；
- base SHA 和 current HEAD；
- changed paths；
- 简明 diff 摘要；
- 精确 validation 结果；
- protected-path 和 secret scan 状态；
- blockers/warnings；
- finish-preview snapshot digest。

STOP，等待我明确批准发布。
```

### Prompt 3：批准精确 snapshot 发布

```text
我批准发布刚才已经审阅的那个 finish-preview snapshot。

调用 reasonfirst_finish：
- 使用同一个 thread/workspace；
- commit message = “docs: add chat-only E2E rehearsal note”；
- 使用刚才 preview 返回的精确 snapshot digest；
- 不允许 protected-path override；
- 不允许 secret-scan override。

如果 snapshot 已经不匹配，不要发布；重新运行 finish preview，并展示新的证据等待我再次批准。

如果发布成功，报告 commit SHA、feature branch 和返回的 MR URL/IID。
随后对同一个 workspace/thread 调用一次 reasonfirst_ci。

不要 merge MR。
如果 CI 仍是 pending/running，准确报告并停止，不要把它说成成功。
```

### Prompt 4：最终 CI + EvidencePack 验收

```text
再次检查这个已经发布的 candidate，全程只通过已连接工具完成。

对同一个 thread/workspace 调用 reasonfirst_ci 和 reasonfirst_evidence(from_ci=true)。
同时用实时 GitLab 只读 MCP 检查真实 MR 和最新 pipeline/jobs。

验证：
- pipeline SHA 精确等于 published workspace HEAD；
- stale_for_workspace=false；
- pipeline 已终态且 success；
- 真实 unit-tests job 成功；
- 没有 blocking failed jobs；
- EvidencePack complete_for_human_review=true；
- MR diff 仍然只改 README.md；
- 刚才批准的文档 acceptance criteria 全部满足。

报告精确 MR IID/URL、candidate SHA、pipeline ID/status、job result。
不要 merge。
最后明确说明：是否已经到达“由人决定 merge”的节点；如果还没有，列出缺失的证据。
```

## 通过标准

只有真实观察到下面证据才算通过：

- 从指定实时 GitLab project/ref 读取了源码并报告 revision；
- 全程只有一个 managed workspace/branch；
- worker 通过 Bridge 执行，没有手工 Codex CLI 会话；
- 只修改 `README.md`；
- finish preview 无 blocker，并给出 snapshot digest；
- 人明确批准了那个 digest；
- 发布真实创建/更新 GitLab MR；
- ChatGPT 在对话内读回 matching-head CI；
- 真实配置的 unit-test job 成功；
- 最终 EvidencePack 可供人审查；
- 没有自动 merge。
