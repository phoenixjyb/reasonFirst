# ReasonFirst 架构

[English](ARCHITECTURE.md) · [项目说明](../README_CN.md) · [工作流程](WORKFLOW_CN.md) · [CLI 快速上手](QUICKSTART_CN.md)

ReasonFirst 是一个**推理优先的编程编排系统**：架构、诊断、任务边界和最终审查保留在推理层；具体实现交给用户选择的编程后端，并由明确的工作区、策略、验证和发布控制约束。

本文描述当前 `main` 的实际架构，而不是未来愿景。

## 1. 总体结构

```text
                      ┌────────────────────────┐
                      │ 人 + 推理界面           │
                      │ ChatGPT / 兼容客户端    │
                      └───────────┬────────────┘
                                  │ 方案 / 审查 / 证据
                                  ▼
                   ┌────────────────────────────┐
                   │ ReasonFirst 控制层         │
                   │ ActualCoder CLI / Bridge MCP│
                   └───────────┬────────────────┘
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
        WorkerPolicy       工作区控制          审查 / finish
      模型/努力/权限       本地 Git / SSH       共享 review gates
             │                 │                  │
             └────────────┬────┴─────────────┬────┘
                          ▼                  ▼
                 编程后端                  证据
          codex-cli / copilot-cli      diff / CI / artifacts
              / codex-desktop          approvals / policy
                          │
                          ▼
                    隔离功能分支
                          │
                          ▼
                     GitLab MR / CI
```

核心原则是：**worker 可以替换，控制契约不能随 worker 改变。**

## 2. 两种 MCP 表面

当前 ReasonFirst 有两种职责不同的 MCP，不能混为一谈。

### A. 只读 GitLab MCP

原有 GitLab MCP 用于仓库、MR、pipeline、job 等有边界读取，适合通过 Tunnel 将代码阅读和 CI/MR 检查能力交给推理客户端。

它不负责本地 worker 执行，也不读取未发布本地 worktree。

### B. Bridge Preview 编排 MCP

`tools/reasonfirst_v4_0_3/` 下的 Bridge Preview 是权限更高的**本地编排接口**。目录名为兼容路径，不代表产品版本。

它可以准备受管本地/SSH 工作区、启动和控制 Codex App Server、读取 diff/artifact、处理审批、执行完整 finish preview，并在用户配置容器验证策略时运行结构化远端验证。

远端发布仍是保守能力：默认不暴露、不启用，只有显式设置 `RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH=true` 才出现。

## 3. Worker 后端

用户可明确选择：

```text
codex-cli
copilot-cli
codex-desktop
```

历史 `codex` / `copilot` 继续作为兼容别名。

选择优先级：

```text
显式 --agent
   ↓
非 auto 的 REASONFIRST_DEFAULT_BACKEND
   ↓
仓库 agents.preferred
   ↓
已安装后端 fallback
```

Codex CLI 与 Codex Desktop 共用 Codex WorkerPolicy。仓库配置不能扩大用户自己的执行/信任权限。

## 4. WorkerPolicy 与验证

Codex 策略可包含模型、reasoning effort、执行模式、sandbox、审批和网络策略；Copilot 则映射为其支持的模型/effort、执行模式以及 allow/deny tool 规则。

ReasonFirst 区分三件事：

1. 用户请求了什么；
2. ReasonFirst 实际编码/传给 backend 的是什么；
3. backend 在运行时解析成了什么。

CLI 后端至少能证明 launch argv 已按策略编码。Codex CLI 还会做不触发模型推理的 App Server model/admin preflight。

Codex Desktop 会检查实时 model catalog、effort 支持、admin 限制以及 `thread/start` 的解析结果。不匹配时以 `WORKER_POLICY_UNSATISFIED` 失败关闭。运行中的 model reroute 会记录为策略违规。

当前 App Server 不提供 provider 响应包中的独立 model 证明，因此文档不把线程级解析结果夸大成 provider 级 attestation。

## 5. 审批模型

`codex-desktop + approval_policy=on-request` 时：

- ActualCoder CLI 会在终端显示 command/file/permission 审批请求；
- Bridge MCP 通过 `reasonfirst_pending_approvals`、`reasonfirst_approve`、`reasonfirst_decline` 暴露显式审批。

默认拒绝；超时拒绝。权限批准默认只在当前 turn 生效，不会静默升级成 session 级。

## 6. 工作区与并发

本地任务使用受管 Git cache/worktree、功能分支和固定 base identity。

SSH 工作区只能来自用户预先配置的**命名 target**；MCP 调用方不能临时指定任意 host/repo。

ReasonFirst 已加入可重入、跨进程文件锁，用于本地工作区 mutation、validation 和 bridge-managed 远端 mutation，降低并发写入和 publish race。

锁不是通用 OS sandbox。

## 7. 远端验证

worker 不再获得任意 `bash -lc` 远端执行能力。

只有用户为 target 配置结构化容器验证策略时，才暴露 remote run。worker 提交 argv 数组，而不是 shell 字符串；执行受 image、允许 executable、cwd/worktree、timeout 和 network 策略约束。

验证时禁止隐式 image pull：本机不存在镜像时应失败，而不是静默访问 registry。

## 8. 审查与 finish gates

本地和 SSH 发布审查共用 `src/gitlab_agent/review_gates.py`。

发布前可检查：

- 必需 validation；
- changed paths；
- reviewability / 完整性；
- 完整且有上限的 diff；
- protected paths；
- candidate secret scan；
- 有界 commit-history secret scan；
- base / HEAD / candidate identity。

validation 失败、审查/secret coverage 不完整、protected path 未批准或 secret finding 未解决时，都应阻止发布。

`finish --dry-run` 与 bridge finish preview 可能运行验证代码；dry-run 只表示不 commit/push，不表示不执行代码。

## 9. 发布

### 本地发布：默认正式路径

```text
workspace
 → finish preview / review
 → 人工确认
 → feature branch
 → GitLab MR
 → matching-HEAD CI
 → 人决定是否 merge
```

### SSH 远端发布：实验能力

默认关闭。显式启用后，审批绑定 project、target、base SHA、HEAD、branch、origin/push URL 与准确 candidate Git tree，并在发布前再次检查。

除非操作者有意启用实验路径，否则仍优先使用本地 `actual-coder finish`。

## 10. 项目契约

仓库 `.actualcoder.yaml` 可声明 base branch、backend preference、validation、protected path、implementation instruction、required executable 与 MR 约定。

仓库策略不能自行授权新的 executable 或项目访问权限。

finish 从 workspace 固定的 base revision 读取策略，防止 feature branch 修改自己的发布门槛。

## 11. 安全边界

ReasonFirst 是控制软件，不是通用 sandbox。需要明确区分：

- 推理服务与本地主机；
- 只读 GitLab MCP 与 Bridge Preview 编排 MCP；
- 仓库策略与用户策略；
- coding backend 登录/额度与 ReasonFirst 配置；
- 本地 worktree 与用户配置 SSH target；
- 容器 validation 与任意主机命令；
- 自动收集证据与最终人工 merge 决策。

凭证不应进入 prompt/tool 参数。仓库 CI 会扫描 tracked files 与 Git history 中的高风险 secret 模式。

## 12. 代码位置

| 关注点 | 当前实现 |
| --- | --- |
| 用户配置/默认后端 | `src/gitlab_agent/config.py` |
| backend 选择 / CLI 编排 | `src/gitlab_agent/cli.py` |
| WorkerPolicy | `src/gitlab_agent/worker_policy.py` |
| Codex App Server | `src/gitlab_agent/codex_app_server.py` |
| 本地 workspace | `src/gitlab_agent/workspace.py` |
| 跨进程锁 | `src/gitlab_agent/locking.py` |
| 共享 review gates | `src/gitlab_agent/review_gates.py` |
| local finish | `src/gitlab_agent/finish.py` |
| CI / log evidence | `src/gitlab_agent/ci_feedback.py`、`log_evidence.py` |
| project contract | `src/gitlab_agent/project_config.py` |
| Bridge controller | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/controller.py` |
| SSH workspace / remote validation / reviewed push | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/remote_workspace.py` |
| Bridge target config | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/bridge_config.py` |
| Bridge MCP | `tools/reasonfirst_v4_0_3/reasonfirst_mcp_server.py` |

## 13. 尚未进入 main 的能力

当前开放的 TaskSpec / EvidencePack PR 尚未进入 `main`，因此本文不把持久 task revision/attempt 与 core EvidencePack 说成已发布能力。

GitLab 仍是已实现的 SCM/CI adapter；源码托管在 GitHub 不代表已经有 GitHub target workflow。

进一步设计动机见[设计理念](DESIGN_PHILOSOPHY_CN.md)，实际使用见[首次上手](GETTING_STARTED_CN.md)和 [CLI 快速上手](QUICKSTART_CN.md)。
