# Antigravity CLI 执行者（Unreleased）

[English](ANTIGRAVITY_CLI.md)

本功能仅在此次候选源码中，**不包含在已发布的 v0.5.3 wheel 中**。它让 `agy`
作为可选的交互式终端执行者，与现有 Codex、Copilot 并列。接口依据维护者提供的
`agy 1.3.4 --help`；该记录只证明这台 Mac 上列出了相应参数，不等于登录成功、
沙箱生效、任务执行成功或三平台实机验收。

## 选择与启动

沿用已安装并登录的 Antigravity。ReasonFirst 不安装、更新它，不读取其凭证或
设置文件，也不要求新增模型 API key。只向获准使用的提供方发送获授权的仓库内容；
先检查工具权限与数据共享设置。

在已审阅并安装正常项目依赖的源码目录中：

```bash
uv run actual-coder agents
uv run reasonfirst worker list
uv run actual-coder start team/project \
  --agent antigravity-cli \
  --task agy-smoke \
  --goal '读取项目说明并运行现有测试。报告实际命令、测试数量、退出码、HEAD 和最终 Git 状态。失败时停止，不自行修复。' \
  --acceptance '现有测试通过，已跟踪文件不变。' \
  --non-goal '不编辑文件、不安装依赖、不查看凭证、不提交或推送、不创建 MR、不部署、不启动其他编程执行者。'
```

将 `team/project` 换成已授权的项目。`start` 创建受管 worktree 并启动终端中的
交互式执行者；添加 `--no-launch` 则只准备交接，不启动提供方。`--agent` 接受
`antigravity-cli` 与别名 `agy`。自动候选顺序仍为 Codex、Copilot：单纯安装 `agy`
不会自动切换默认执行者，但显式的用户默认值或固定版本的项目偏好可以选用它。

与旧版 CLI 或 daemon 并存做源码试验时，优先使用任务级 `--agent`。不要把新执行者
名称写入仍由旧运行时共用的配置：v0.5.3 及更早版本会拒绝该名称。运行时版本兼容
须另行协调，本次试验不包含服务升级。

保存后续终端任务的默认选择：

```bash
uv run reasonfirst worker use antigravity-cli
```

此命令修改现有私有 ReasonFirst 配置中的默认执行者和 setup 进度，保留项目授权
与连接标识；不修改 Antigravity 设置，不重新配置或重启服务。`worker use agy`
会保存规范名称 `antigravity-cli`。

把 `WS` 设为已有 ReasonFirst 工作区 ID 后，可显式继续：

```bash
uv run actual-coder resume "$WS" --agent antigravity-cli --launch
```

这里延续的是 **ReasonFirst 的任务和 worktree**，向新的提供方会话传入完整任务交接；
不是恢复 Antigravity 内部会话。不会加入 `--continue` 或 `--conversation`。
自动记住执行者属于独立的 PR #114；本候选基于 main，继续任务时请显式传入 `--agent`。

## 启动约定

启动前从 PATH 找到 `agy`，转换成绝对调用路径；以受管 worktree 为 `cwd`，直接
传递参数数组，不通过 shell 拼接：

```text
agy --sandbox [--model=VALUE] [--effort=VALUE] --prompt-interactive TASK_PROMPT
cwd = ReasonFirst 受管 worktree
```

启动时 stdin 和 stdout 都必须是终端。非交互环境直接失败，不降级为 print/headless
模式或其他执行者。找不到程序或提供方拒绝参数时，不通过删减保护参数重试。
`agents`、`doctor`、setup 的存在性检查不会执行 `agy`，也不能证明已登录。

现有 ReasonFirst 配置中可选：

```bash
REASONFIRST_ANTIGRAVITY_MODEL=
REASONFIRST_ANTIGRAVITY_REASONING_EFFORT=
```

留空即继承提供方设置。推理强度支持 `low`、`medium`、`high`、`xhigh`、`max`，
其余值会报错。模型名称作为单一参数传递，不做 shell 插值；不声称验证了模型是否
可用或实际响应来自哪个模型。

不强制指定 `--mode`；不加入自动跳过权限、额外目录、项目 ID、内部 agent、日志
路径覆盖、remote control、提供方会话 ID 或 headless 参数。Antigravity 的
`--project` 不是目录参数；ReasonFirst 的 `--agent` 也不会原样转发给其内部 agent。

## 证据与边界

策略中的 backend 为 `antigravity`，证据 surface 为 `antigravity-cli`。
`sandbox_requested: true` 仅表示编码了 `--sandbox`；`runtime_verified` 与
`permissions_verified` 仍为 false。网络和审批策略记为未知（null），不能套用 Codex
的保证。提供方已有权限、MCP、hooks、模式、外部目录及沙箱外授权继续生效，
ReasonFirst 不读取或重写这些设置。worktree 不构成操作系统安全边界。

返回码是 CLI 进程退出状态，**不证明工具执行、测试通过或任务验收**。需核对真实
测试输出及 Git 变更，再通过原有验证、审查与 finish 流程发布。即使进程返回零，
权限被拒绝或未完成的工作也不能视为验收成功。

本适配器仅用于 standard 与 cli-only 的终端流程；full-chat setup 显式选择它会
被拒绝，Bridge 仍使用原有执行路径。服务配置的严格字段清单识别新增的可选字段，
但已有版本化 Bridge `worker_requests` 投影不添加 Antigravity；这不构成受管服务
执行者支持。没有修改持久化格式、依赖、版本号或服务生命周期。

暂不提供 headless/流式事件、提供方会话 ID 持久化、远程执行控制、自动禁用 MCP
或独立验证沙箱策略。合成测试、原生 CI 与真实 Antigravity 会话验收须分别记录。

## 参考与安装方式

[安装包快速安装与源码构建](INSTALL_CN.md) 两条路径都保留；本次未发布功能目前
需要已审阅的源码。另见[工作流](WORKFLOW_CN.md)及[安全边界](../SECURITY_CN.md)。
提供方参考：[CLI 使用](https://antigravity.google/docs/cli-using)、
[设置](https://antigravity.google/docs/settings)、
[终端沙箱](https://antigravity.google/docs/sandbox-mode)。在线说明可能更新；本适配器
显式请求已确认的参数，不仅凭文档推断运行时保护已经生效。
