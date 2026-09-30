# ReasonFirst 0.5.1 发布说明

[English](RELEASE_NOTES_0.5.1.md) · [安装与更新](INSTALL_CN.md) · [变更记录](../CHANGELOG.md)

ReasonFirst 0.5.1 将五个引导式安装配置切片整合为一套更易使用的入口，同时保留正式支持的源码/手工路径，以及人工审查和发布边界。

**发布身份与可用性：**源码版本号或已合并 PR 不等于已经公开发布。维护者发布后，`v0.5.1` tag 才标识准确发布提交。只有 Release assets 工作流成功、wheel 已附加到该 Release 后，才应使用下方的打包安装命令。已发布的 `v0.5.0` tag 继续固定在 `6b3b1983b3dd8ad56f214393d00e7a74c60c7f95`；其[历史发布说明](RELEASE_NOTES_0.5.0_CN.md)保持不变。

## 用户侧的变化

### 一个统一的引导入口

`reasonfirst setup` 引导完成本地 GitLab、项目和 worker 配置，然后提供只读 ChatGPT 连接，以及符合条件时的可选全聊天 Bridge。项目访问先验证，再经明确确认写入本地授权。配置更新保留无关设置，采用私有备份和原子替换。

独立命令仍然可用：

```bash
reasonfirst setup --status --json
reasonfirst project list
reasonfirst worker list
reasonfirst tunnel status
reasonfirst bridge inventory --json
```

`setup --status` 只做本地检测，不调用 provider、不启动服务，也不声称 ChatGPT 已完成授权。检测到 worker 可执行文件不等于验证了 provider 登录。

### 打包安装与源码安装都正式支持

完成发布且附件生成成功后，普通用户可以使用：

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup
```

wheel 包含 `reasonfirst`、`reasonfirst-gitlab-mcp`、`reasonfirst-bridge-mcp`、ActualCoder 与兼容 CLI。运行打包服务不再要求保留 ReasonFirst 源码检出目录；受管编程工作区仍需要 Git。

贡献者和高级用户可以在已审阅的源码目录中执行：

```bash
uv sync --python 3.12
uv run reasonfirst setup
```

editable 安装继续支持。两条路径共用私有配置、非秘密 SetupState、项目授权和已记录的 tunnel identity。使用 editable 安装时，应保持所选源码目录稳定。[详细/手工接入指南](GETTING_STARTED_CN.md)继续正式支持，并未废弃。本次发布不依赖 PyPI 上架。

### 受管只读隧道与可选 Bridge

只读 MCP 已打包；后台运行监督交给官方 tunnel-client，而不是再实现一套 ReasonFirst 服务管理器。安装器校验发布归档的 checksum，保留配套 runtime bundle，并在复用已有 bundle 前重新验证其内容。

打包 Bridge 是可选、权限更高的 stdio/tunnel 接口。启用前需要明确确认客户端/workspace 支持可写 custom MCP action，使用区别于只读 app 的独立 tunnel ID，通过工具清单检查，并具备本地 Codex App Server 能力。常规工具清单不暴露实验性远端推送授权工具，也不提供旧版 HTTP control-token 路径。

Copilot CLI 继续在终端 ActualCoder 工作流中受支持。当前全聊天 worker 控制器基于 Codex App Server，不声称能够通过该接口控制 Copilot CLI。

### 恢复与修复不重建账号侧资源

重复执行普通 setup 会复用已记录且健康的只读隧道和 Bridge runtime。修复已记录的本地 runtime 状态可使用：

```bash
reasonfirst setup --repair
reasonfirst setup --status
```

repair 不创建 GitLab 项目、OpenAI tunnel 或 ChatGPT app。重新连接不健康的已记录 runtime 时，需要用户提供 runtime 凭证；修复高权限 Bridge 还需要明确确认 workspace 资格。runtime key 通过环境变量或掩码输入取得，不接受命令行字面量，也不保存在 SetupState 中。

安装新版包不代表已运行的进程自动加载了新版，应另外检查实际服务。新 repair 命令基于记录的状态，并非任意旧版 launchd profile 或手工服务的自动迁移工具。

## 验证范围及限制

功能整合基线 `ee1ed4564e07b52adbbadaea750d9799c43d7d56` 已通过[十个 CI job](https://github.com/phoenixjyb/reasonFirst/actions/runs/36669048813)及[文档构建/部署](https://github.com/phoenixjyb/reasonFirst/actions/runs/36669048675)。这是版本提升前的证据，不能替代最终发布准备提交的验证。

发布门槛包括核心回归、Ubuntu/macOS/Windows 回归、两个 Bridge 回归 job、wheel/sdist 检查及源码/历史扫描、三个平台的打包/源码安装 E2E，以及文档构建和链接检查。发布记录应保存最终 PR head、合并提交与成功的 run ID。

安装 E2E 构建 wheel，真实执行 `uv tool install`，离开源码目录后运行版本/status 和打包 Bridge inventory，再独立验证源码目录中的 `uv sync` / `uv run`。这**不等于**登录了真实 provider、创建了浏览器端 app，或在三个平台完成真实全聊天接入。CI 使用 Python 3.12；包元数据声明的更广解释器范围并不代表同等测试覆盖。

## 发布流程与保持不变的边界

Release assets 工作流只在人工发布 Release 后触发：校验 tag/package/runtime 版本一致，构建并检查 wheel/sdist，执行安装验证、源码/历史扫描，生成 checksum 固定的 Homebrew formula，再把附件加入已有 Release。工作流不创建 tag 或 Release。formula 生成和 Ruby 语法检查不等于真实 Homebrew 安装测试；tap 尚不存在时不得宣传其安装命令。

本地 runtime 健康不等于 `CHATGPT_READY` 或 `FULL_CHAT_READY`。仍需完成对应 handoff 和实时验收。worktree 不是安全沙箱，选定的 MCP 数据可能离开本机，编程后端沿用各自认证和计费。本次发布准备不增加直接模型推理服务、无人审批合并、新源码管理适配器、凭证迁移或仓库权限扩张。

采用或发布前，请阅读[安全边界](../SECURITY_CN.md)、[架构说明](ARCHITECTURE_CN.md)及[维护者发布清单](PUBLIC_RELEASE_CHECKLIST_CN.md)。
