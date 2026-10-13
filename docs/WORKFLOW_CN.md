# 一个推理界面，一套受控实现流程

[English](WORKFLOW.md) · [项目说明](../README_CN.md) · [架构说明](ARCHITECTURE_CN.md) · [CLI 快速上手](QUICKSTART_CN.md) · [任务交接模板](TASK_HANDOFF_TEMPLATE_CN.md)

<!-- Translation source: docs/WORKFLOW.md @ a3e33c72c55efef6a0dc3808fb9853c62ad15f9d -->

**让 ChatGPT 位于推理最前端，让编程代理保持执行角色。** 普通 ChatGPT 是默认的读取、架构、诊断、范围、验收标准和审查界面；ActualCoder 或可选 Bridge Preview 将已批准意图转成对 `codex-cli`、`copilot-cli`、`codex-desktop` 的受控 handoff。localhost Assistant 不是必需组件。

## 各项操作应该在哪里进行

| 界面或组件 | 职责 | 是否必需 |
| --- | --- | --- |
| 普通 ChatGPT 对话 | 通过选定连接读取代码、诊断、明确范围、审查结果 | 默认推理界面 |
| 只读 GitLab MCP + Tunnel | 只负责仓库/MR/CI 检查 | 仅 ChatGPT read-connection 路径需要 |
| 终端 ActualCoder | 准备/管理 worktree、选择/启动 worker、验证、reviewed finish | 获批实现的执行引擎 |
| Bridge Preview MCP | 可选本地编排：App Server、审批、受管 SSH workspace、finish preview | 可选，权限高于 read connector |
| 编程 worker | `codex-cli`、`copilot-cli` 或 `codex-desktop` 执行获批实现 | 真正实现任务时需要 |
| GitLab MR/CI 界面 | 检查仓库证据，由人工作出合并决定 | 审查或发布变更时使用 |
| 本地仪表盘 Overview/Logs | 诊断 Tunnel | 可选 |
| 本地仪表盘 Assistant（`/ui#codex`） | 上游提供的独立 Codex 界面 | **不是必需组件，也不是验收门槛** |
| Codex Tunnel 插件或 MCP Inspector | 独立的集成或调试工具 | 可选；标准 ChatGPT 读取测试不需要它们 |

不使用 localhost Assistant 不等于卸载 Codex：Codex CLI 仍可作为实现任务的执行者。关闭仪表盘不会停止 Tunnel，也不一定会禁用上游客户端附带的后台辅助进程。本指南没有修改该进程，也不声称将其禁用。

## 一条主闭环，多个支撑表面

```text
普通 ChatGPT
  -> 读取仓库 / MR / CI
  -> 做架构判断与根因分析
  -> 定义目标、非目标、验收标准
  -> TaskSpec
  -> ReasonFirst 控制层
  -> codex-cli / copilot-cli / codex-desktop
  -> 受控实现与 validation
  -> diff / EvidencePack / MR / matching-HEAD CI
  -> 普通 ChatGPT + 人工审查
  -> 继续、调整或合并
```

下面这些界面服务于同一条主闭环的不同环节，**不是三种并列产品模式**。只读 GitLab MCP 向 ChatGPT 提供证据；ActualCoder 是获批任务的正常执行引擎；Bridge Preview 是权限更高的可选编排表面，用于 App Server 控制、显式审批、命名 SSH workspace 和 finish preview。

ActualCoder 也可以由终端、CI 修复任务、IDE 或其他客户端直接驱动。这个能力应保留给测试、恢复和自动化，但它是次要运维表面，不是 ReasonFirst 的定义性工作流。

Bridge SSH target 必须由用户预先命名配置，调用方不能临时指定任意 host。远端 build/test 只有配置结构化容器 validation policy 后才可用；不暴露任意远端 shell。

## 日常任务循环

### 1. 在普通 ChatGPT 对话中读取并决策

在对话中选择目标 GitLab 连接，要求读取真实文件，并区分观察到的事实与改进建议。明确目标、非目标、涉及组件、验收标准和停止条件。不要将令牌、私有配置文件或含凭证的参考笔记放入任务上下文。

用人工模板记录已批准的要求。为某次任务更换编程后端，并不意味着允许更改任务目标或扩大范围。

### 2. 在终端准备并实现

对于真正获得批准的新任务，从源码检出目录运行；将示例项目和目标替换为实际值：

```bash
uv run actual-coder start team/project-a --task fix-timeout --goal "Fix the timeout bug; preserve the API and add regression coverage" --no-launch
```

这会获取上下文并创建工作树，不是离线或无写入的预览。检查返回的 workspace ID、路径、分支和交接内容，再使用返回的后端启动命令及提示词。继续同一工作区时，不要再次执行 `start`。新任务可以省略 `--no-launch`，直接以交互方式启动后端。执行者的登录状态和额度应通过其自身支持的流程检查，不能由“已安装可执行程序”推断。

### 3. 发布前验证并审查

将 `WS` 设置为真实返回的 ID；下面的值仅作示例：

```bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
```

检查 diff、验证结果、秘密扫描覆盖范围、受保护路径及目标 MR/分支。**Finish dry-run 会运行配置的验证命令，可能改变本地文件；它不会提交或推送。** 缺少 `.actualcoder.yaml` 表示没有加载项目专用验证项。只有先定义实际项目测试，才能将其称为项目测试门槛。

确认检查未被阻断、变更符合意图之后，才运行：

```bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
```

Finish 会请求确认。仍需审查执行者提出的操作：低层 commit/push 命令，包括部分生成提示词仍提到的命令，不会执行全部 finish 检查。不要将本指南中的行为要求当作操作系统级强制约束。详见[安全边界](../SECURITY_CN.md)。

### 4. 返回证据，而不只是执行者声称成功

记录 base 与 HEAD、尚未提交的变更、实际验证命令及结果、MR 链接、CI SHA 和覆盖范围。成功的 docs-only 流水线不是完整构建；历史上成功且 SHA 匹配的流水线，不是发生了新推送的证据。HEAD 对应的 CI 不覆盖未提交变更。

对于确实失败且与当前 HEAD 匹配的 CI，`resume --from-ci` 会准备修复交接；只有明确希望继续执行该 workspace 时，才使用 `resume --launch` 或 `actual-coder continue` 启动所选 backend。CI 已成功时，不要为了附带的 CI 上下文凭空修改代码。人工审查和合并仍是独立操作。

## 验证输出与清理证据

**以下为 Unreleased 源码行为，已发布的 v0.5.3 wheel 不包含它。** 本次修复不修改
执行者选择、命令白名单、Python 绑定、环境过滤、发布审批或正在运行的服务配置。
本地 `run`、`validate` 和 finish 验证共用 `CommandRunner`；远端验证及编程执行者、
探测、Git 子进程仍有各自的实现，不属于本次修改范围。

`GITLAB_COMMAND_MAX_OUTPUT_BYTES` 平分给 stdout 与 stderr，每一份向下取整。
执行器按固定大小分块读取，只保留预算内的规范化 UTF-8 前缀；超过预算的内容继续
读取并丢弃，而不是先完整缓冲。即使一整行没有换行符，保留内容也有界。
达到输出上限不会自行终止命令，也不会把非零退出码变成成功。
绑定的 Python 输出使用 UTF-8，其他命令使用宿主机首选编码；非法字节采用替换字符，
不再因区域编码解码错误而异常退出。CRLF 与 CR 统一为 LF，超时输出也按同样规则处理。

原有结果字段保留，新增证据应分别理解：

| 字段 | 含义 |
| --- | --- |
| `stdout_truncated` / `stderr_truncated` | 已观察到的规范化输出超过该流的保留预算。 |
| `stdout_original_bytes` / `stderr_original_bytes` | **已观察到**的规范化 UTF-8 字节数，包含丢弃的内容。不是原始管道字节数；没有 EOF 时，不能当作全部已产生输出的总量。 |
| `stdout_complete` / `stderr_complete` | 读取器已观察到 EOF，而不是仅仅装满保留缓冲区。 |
| `output_complete` | 两个流均达到 EOF。展示内容被截断或运行超时时，它仍可能为 true。 |
| `cleanup` | 报告信号作用范围、是否请求终止、直接子进程是否已回收，以及父进程的输出管道是否已关闭。`descendants_verified` 始终为 false。 |

命令截止时间在创建进程之前开始计算；不会因为父进程退出或输出达到上限而重新计时。
输出收集与直接进程等待共用这个截止时间。创建进程和此前的可执行程序/绑定预检本身
可能占用额外时间。超时后的清理另有 POSIX 终止宽限 0.2 秒、直接子进程回收等待
1 秒及最终管道排空 0.2 秒；这些是有界等待，不是硬实时调度保证。
超时仍返回 `timed_out: true`、`returncode: null`，`run` CLI 的退出码仍为 124。
必需的项目验证不能因超时而通过。

POSIX 清理仅向新会话中的命令进程组发送 SIGTERM，并在**回收组长之前**升级到
SIGKILL；不按名称查找进程，也不扫描宿主机。它要求独占管理该子进程，不支持其他
SIGCHLD 回收器抢先回收。如果 `Popen.wait()` 在处理中断时已回收组长，则不会再对
可能被复用的进程组编号发信号，而是报告清理不完整。Windows 仅操作持有的直接
`Popen` 进程对象；本次未实现 Windows Job Object 或后代进程约束。

工作区变更锁覆盖输出收集、终止、有界排空和输出关闭。逃离进程组的进程继续持有
管道时，会得到 `output_complete: false` 的有界失败，而不是无限等待。
固定的 `CommandProcessError` 错误码分别保留原始原因和清理错误，不会将清理失败
包装成成功；清理成功后的普通 KeyboardInterrupt/SystemExit 会继续向上传播。
正常退出后的后台进程和逃离的后代仍需要外部隔离。详见
[安全边界](../SECURITY_CN.md#构建与测试执行不是沙箱)。

## 分层验收

| 证据 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| `actual-coder doctor` 的 API 检查成功 | 当前环境的 CLI API 认证成功 | Git 推送权限、编程代理登录、Tunnel 可用性 |
| `project-config` 获取成功 | 该次受管 Git 读取成功 | 应用测试已通过，或已经获得写入授权 |
| Tunnel 启动 / 获取到元数据 | 启动及所报告的控制平面操作成功 | ChatGPT 已发现或调用 GitLab 工具 |
| 本地 health/readiness 响应 | 已安装 Tunnel 版本定义的健康条件 | 某个具体 GitLab 工具调用成功 |
| 普通 ChatGPT 中实时读取身份和文件成功 | 实际 ChatGPT 读取连接可用 | 本地任务执行，或新的构建/推送 |
| 本地 Assistant 的审批错误 | 该可选 Codex 会话中发生了失败 | 独立的普通 ChatGPT 连接也失败了 |

最终读取验收位于 [ChatGPT MCP 接入指南](OPENAI_TUNNEL_TEAM_SETUP_CN.md)，不是在 `/ui#codex` 中进行。现有安装应按该指南重启，不必重复安装或迁移。

## 下一步产品工作

后续演进应继续保持 reasoning/control/execution/evidence 分层。工作区锁、resume launch、远端容器 validation 与本地/SSH 共用 review gates 已经实现；持久 TaskSpec/attempt 记录与 bounded EvidencePack 已实现；下一步应完善跨界面交换和结果生命周期，同时保持 reasoning/control/execution/evidence 分层。
