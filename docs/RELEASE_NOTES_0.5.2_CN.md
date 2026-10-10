# ReasonFirst 0.5.2 发布说明

[English](RELEASE_NOTES_0.5.2.md) · [安装与更新](INSTALL_CN.md) · [变更记录](../CHANGELOG.md)

ReasonFirst 0.5.2 将 v0.5.1 之后合入主线的改进整理进版本化安装包。已有用户可以
复用原有配置，更清楚地查看安装状态；高级运维用户获得明确的部署、运行环境检查
与准备工具。新的服务保护机制需要显式接入，与普通 setup 流程分开。

基本工作流保持一致：在 ChatGPT 里读代码、想方案，将审阅后的任务交给编程后端，
完成后再检查结果并决定是否发布。打包安装和源码安装继续正式支持。

## 用户侧的变化

### 复用已有安装与配置

已有有效设置时，`reasonfirst setup` 会先提供复用选项，不必重新填写 GitLab、项目
与 worker 配置。也可以明确执行：

```bash
reasonfirst setup --reuse-existing --json
reasonfirst status --json
```

复用保留配置文件内容、项目授权及已导出环境变量的优先级，不启动服务、不创建
tunnel 或 app、不验证登录，也不补写已经完成的 setup 阶段。需要有意修改配置时，
使用 `setup --reconfigure`，原有验证与审批要求继续生效。对于格式错误、无法读取
或不支持的已选配置，程序会报告分类结果并保留原文件。
[PR #96](https://github.com/phoenixjyb/reasonFirst/pull/96)

`status` 分别报告已安装 CLI 与运行中服务；后者的版本仍可能是未检查状态。
macOS 上可以识别已知旧版/HTTP sidecar 注册文件，作为静态证据。缺少
`setup.yaml` 不再直接建议已有安装重复创建 tunnel。Windows/Linux 服务管理器
仍未检查；存在静态文件不代表服务健康。

### 明确、可审阅的运维工具

新增 `reasonfirst-bridge-http` 命令，明确指定本机回环地址、端点与
read-only/full-chat 模式，使用打包的 Bridge 核心。常规
`reasonfirst-bridge-mcp` 继续使用 stdio。HTTP 包装器不提供认证，也不包含旧版
`/control` 路由，因此不能直接替换所有已有 HTTP 部署。详见
[HTTP 传输说明](BRIDGE_HTTP_CN.md)。

高级部署操作可以分步审阅：

- macOS：通过 `deployment plan/adopt/status` 记录已知 LaunchAgent 的已保存
  注册信息。记录要求准确的已审阅摘要及 `--yes`；结果是注册快照，不是服务迁移。
- macOS/Linux：使用完整、已审阅的离线 wheel 集合，准备独立运行环境；不下载
  依赖，也不替换正在运行的服务。
- macOS：联合检查已保存部署、准备好的运行环境和已知启动器，并读取已加载 job
  的部分字段。无法取得的字段保持未知，未解决的兼容性问题继续阻止后续启用。

这些工具供运维用户主动使用，不构成自动升级器、服务切换、重启、回滚或恢复事务。
Windows 不支持这套运行环境准备；Linux/Windows 不支持部署配对。详见
[部署记录](DEPLOYMENTS_CN.md)、[运行环境准备](RUNTIME_PREPARATION_CN.md)与
[已加载 job 观察](LOADED_SERVICE_CN.md)。对应改动为
[PR #97](https://github.com/phoenixjyb/reasonFirst/pull/97) 至
[PR #102](https://github.com/phoenixjyb/reasonFirst/pull/102)。

## 内部服务可靠性改进

新的受管 HTTP owner 需要显式接入：它跟踪请求、worker turn 和审批的完整生命周期，
只通过所属本地租约进入维护状态。完成配置绑定的 owner 保留选定策略、当前进程
观察、子进程启动输入以及 GitLab API 使用的 CA 材料。发现绑定发生漂移后，该 owner 永久关闭工作准入；
传输失败或清理未完成时，继续报告不确定状态。

这个 owner 使用明确校验的 MCP 2026-07-28 JSON 协议配置，关闭 subscriptions，
并检查经过审阅的 MCP 2.3.0/Uvicorn 0.54.0 组合。现有启动器不会自动切换到它。
这些有限范围的输入检查不证明整个运行环境的完整性、所有 provider/native Git/SSH
配置、全局空闲状态，也不赋予替换服务的启用权限。

独立的一次性启动探针始终拒绝工具执行。启动声明与工具清单检查成功，也不会将
它转换成正式工作服务。详见[受管 HTTP 服务](MANAGED_HTTP_SERVICE_CN.md)与
[一次性启动观察](DISPOSABLE_STARTUP_CN.md)。对应内部改动为
[PR #103](https://github.com/phoenixjyb/reasonFirst/pull/103) 至
[PR #109](https://github.com/phoenixjyb/reasonFirst/pull/109)。

## 安装与更新

只有 `v0.5.2` 已发布、资源工作流成功、wheel 和校验和均可下载后，才使用以下地址：

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.2/chatgpt_selfhosted_gitlab_mcp-0.5.2-py3-none-any.whl
reasonfirst setup
```

已有打包安装应先检查运行中服务使用的环境，安排好包替换，再通过
`uv tool install --force` 安装已审阅的 wheel，并检查
`reasonfirst setup --status --json`。安装新包不会让已有进程自动加载新代码。
继续复用原配置，保留源码目录、服务目录、工作区状态和 tunnel identity。

源码用户可在发布后选择已审阅的 `v0.5.2` tag，再执行 `uv sync --python 3.12`；
editable 安装继续支持。完整命令与服务边界见[安装与更新](INSTALL_CN.md)。

发布构建改用 `uv build --no-create-gitignore`，解决输出目录额外 marker 导致的
打包失败，同时保留准确输入白名单与附件重名检查
（[PR #95](https://github.com/phoenixjyb/reasonFirst/pull/95)）。v0.5.1 的冻结 tag 与
附件保持不变；它的专用恢复工作流不能用于 v0.5.2。新版本走正常资源工作流，提供
wheel、sdist、formula、`RELEASE.json` 和 `SHA256SUMS.txt`。生成 formula 不代表
已经存在可用的 Homebrew tap。

## 验证范围与限制

版本提升前的集成基线
[`d02314a1681907b772ce9ec4527fb588dd5f18ac`](https://github.com/phoenixjyb/reasonFirst/commit/d02314a1681907b772ce9ec4527fb588dd5f18ac)
已经通过 [CI](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444782)、
[文档](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444779)、
[原生 tunnel 边界检查](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444786)及
[候选包构建](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444788)。
这是集成证据，不能替代后续 0.5.2 发布提交的验收。提升版本后的候选提交和最终合并
提交仍需各自通过检查，再完成真实公开下载、校验和与安装验证。

回归测试覆盖配置无写入复用、部署/运行环境漂移、原生部分字段观察、维护竞争、
请求与清理不确定性、保留的子进程输入和 GitLab API trust。干净 wheel/源码安装
夹具覆盖两种 Bridge 模式、真实 MCP 请求及本机合成 HTTPS，不登录真实 GitLab
或编程后端账号。三平台 CI 使用 Python 3.12；包元数据声明的更广解释器范围不代表
每个版本都经过同等测试。

已有 Windows 现场演练通过了 setup/只读访问、受管 clone/fetch、批准的解释器宿主
验证、正常初始 worker handoff 和干净收尾。**Windows 修复、重启服务/系统后的
恢复，以及可选高权限 Bridge/写入验收仍未验证。** 这些已有机器记录不等于新的
0.5.2 现场验收。详见[验收记录](RELEASE_DISTRIBUTION.md#v051-post-publication-staging-recovery)
及 [Windows 待验收项](https://github.com/phoenixjyb/reasonFirst/issues/88)。

引导式 setup 已可使用，但首次使用能否在几分钟内完成，尚无实测。
[Issue #80](https://github.com/phoenixjyb/reasonFirst/issues/80) 继续跟踪接入体验与
分发条件；目前不宣称已有经过验证的 Homebrew tap 或包注册表安装路径。

Windows tunnel 引号/UTF-8 解码、受管 Git 凭证隔离、工作区 Python 审批和跨平台
worker handoff 修复已经包含在已发布的 v0.5.1 源码中。本版继续保留，不重复宣称
为 0.5.2 新增修复。

本地安装成功或健康检查通过，仍不同于 ChatGPT app 授权及全聊天资格。全聊天
worker 控制使用 Codex App Server；Copilot CLI 继续通过终端 ActualCoder 路径
使用。本版不增加编程后端登录、无人审批发布或自动合并。
