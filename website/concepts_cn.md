# 核心心智模型

理解 ReasonFirst 最简单的方法，是把它看成五层能力组成的一条反馈闭环。

## 1. 推理

最强的推理界面应该把能力用在“推理质量会改变结果”的地方：

- 理解系统；
- 找根因；
- 决定架构；
- 定义范围和非目标；
- 写验收标准；
- 审查实现与 CI 证据。

参考主流程中，这一层就是普通 ChatGPT。

## 2. 任务契约

**TaskSpec** 把推理层已经批准的意图交给执行层，并绑定受管 workspace identity。它持久保存：

- goal；
- acceptance criteria；
- non-goals；
- project 与固定 base identity；
- requested backend。

后续 steering 可以指导某一次 attempt，但不应该静默改写原始任务。

## 3. 控制

ReasonFirst 负责约束执行边界：

- 隔离的受管 workspace；
- backend 选择；
- WorkerPolicy；
- protected paths；
- validation commands；
- locking 与 publication rules；
- review 与 secret gates。

即使 worker 被替换，控制契约也应保持稳定。

## 4. 执行

Coding worker 负责大量实现循环：

- 查看文件；
- 修改代码；
- 运行允许的命令；
- 根据编译/测试反馈迭代；
- 形成具体 diff。

当前执行表面包括 Codex CLI、GitHub Copilot CLI 和 Codex Desktop/App Server。

## 5. 证据

Worker 声称“完成了”并不等于任务验收。

ReasonFirst 返回可审查证据：

- status 与 changed paths；
- bounded diff；
- validation 结果；
- MR 与 matching-HEAD CI；
- TaskSpec/attempt metadata；
- 递归脱敏的 **EvidencePack**。

这些证据再回到 ChatGPT 和人，由推理层决定下一步。

## 不变原则

> **推理层主导，worker 执行，证据返回，人做最终决定。**

更换 worker 不能静默改变任务范围、信任边界、验证要求或发布策略。
