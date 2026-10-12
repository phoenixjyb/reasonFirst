# 平台验证记录

[English](PLATFORM_VALIDATION.md) · [安装与更新](INSTALL_CN.md) · [公开下载](RELEASE_DISTRIBUTION.md#简体中文)

**更新日期：2026-10-12。** ReasonFirst 已有 **macOS、原生 Windows 和
Ubuntu/Linux 三个平台的实机验证记录**。这些记录的日期、安装源码和验证操作各有
不同；已发布的 **v0.5.2** 还通过了三平台的打包/源码安装与回归 CI。

## 验证范围一览

| 平台 | 实机记录 | 已验证范围 | 仍未覆盖的边界 |
| --- | --- | --- | --- |
| macOS | 2026-09-28 记录源码全聊天演练；2026-10-01 激活公开 v0.5.1 包 | 历史实时读取/worker/MR/对应 HEAD 的 CI 闭环；另有 v0.5.1 激活、三次本地健康样本、listener 归属和只读 Bridge doctor | 本地激活不独立证明远程连接再次通过；没有全新 v0.5.2 安装或系统重启验收。见下方 macOS 记录。 |
| Windows 11 Pro x64 | 2026-09-30 读取/重连，2026-10-01 普通 worker 收尾；v0.5.1 候选包的源码和文件身份有记录 | 引导配置、指定 Windows MCP 读取、受管 Git clone/fetch、批准的 Python、正常初始 worker handoff、10 项既有测试、干净的本地收尾 | 未重新进行 v0.5.2 现场验收；健康 runtime 修复/no-op、受控重启、系统重启和可选高权限 Bridge/写入仍由 [#88](https://github.com/phoenixjyb/reasonFirst/issues/88) 跟踪。 |
| Ubuntu 22.04.5 LTS x86_64 | 2026-10-12；已发布 v0.5.2 wheel，Codex CLI 0.162.1 | 全新安装与配置、经校验的 tunnel-client 下载恢复、Linux MCP 实时读取、受管 Git/项目契约、Codex 读文件并执行 10 项既有测试、受跟踪文件未改动 | 自动下载需要恢复；未执行仓库修改/发布、全聊天 Bridge、重启/系统重启，也未在该机器验收后续修复。 |

可以简洁地介绍为：**“已在 macOS、Windows 和 Linux 上测试，具体版本与范围见
平台验证记录。”** 这不表示同一版本的每项功能都在三个平台进行了相同验收。

## 证据来源与发布身份

本记录区分三种来源：

- **实时 MCP 调用：**审阅对话通过实际选中的平台连接调用工具，核对返回的身份、
  项目权限和固定提交的文件内容。
- **操作者提供的终端证据：**操作者在自己的机器上运行命令并提供结果，包括
  worker 报告；审阅者没有远程登录该机器执行这些命令。
- **构建/源码证据：**版本化源码、公开 Release 附件和 GitHub Actions 结果。
  合成 CI 不登录真实服务商账号。

这份公开摘要省略私有域名、项目命名空间、账号信息、用户目录、工作区标识、
Tunnel ID 和凭证。下方不含地址的练习提交 SHA 用于记录测试基线，不是公开仓库
入口，也不是要求用户授权另一个项目。

已发布的 [v0.5.2](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.2)
对应源码 `a4aa63b91109c6ddb8a721d93fe4b9be2543bc23`、源码树
`acc799593658c4fcfc7f03531a20f5c77ca42ea4`。
[最终源码 CI](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598934)
中的 Ubuntu/macOS/Windows 回归及打包/源码安装均已通过；同一源码的
[文档](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598967)、
[原生 tunnel 边界](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598923)
和[候选包构建](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598899)
也已通过。CI 的主要解释器覆盖为 Python 3.12。

[发布资源工作流](https://github.com/phoenixjyb/reasonFirst/actions/runs/38042829135)
于 2026-10-10 成功。Release 已记录五项附件的匿名下载与校验，以及脱离源码目录的
独立 Linux/Python 3.12.14 安装。那次包/命令清单检查没有登录 GitLab、启动用户服务
或调用编程 worker，与下方的实机演练分别计入证据。

v0.5.2 的 tag 和附件内容保持不变。后续源码修复列在
[v0.5.3 发布说明](RELEASE_NOTES_0.5.3_CN.md)；本记录不把后续修复写成已经在用户机器
安装或重新验证的内容。

## Ubuntu/Linux：2026-10-12

### 环境与安装产物

操作者从 Linux 机器提供了以下版本：

| 组件 | 观察到的版本或身份 |
| --- | --- |
| 系统 / 架构 | Ubuntu 22.04.5 LTS / x86_64 |
| Git | 2.34.1 |
| uv | 0.9.5 |
| 系统 `python3` | 3.10.12 |
| ReasonFirst 工具环境 | Python 3.12.12，通过 `uv tool install --python 3.12` 安装 |
| ReasonFirst / ActualCoder | 0.5.2，GitHub 已发布 wheel |
| 安装的 MCP / Uvicorn | 2.3.0 / 0.54.0 |
| Codex CLI | 0.162.1；`codex login status` 返回使用 ChatGPT 登录 |
| 官方 tunnel client | 0.0.16，上游提交 `5f99daabd4aa4a77049e6d81d54a0d8c18335397` |
| 模式 / 选定 worker | `standard` / `codex-cli` |
| 练习基线提交 | `4561c76d57dda1d2fc6e7cd076b6d52638400a78` |

公开 Release 中 ReasonFirst wheel 的 SHA-256 为
`fa23112b8003ce7ee46d072a7fa053b51dc5aaacf0fc9f6a72eb678f5fab1466`。
Linux 终端记录确认了从该版本化 wheel 地址安装、命令输出版本 0.5.2；这不等于
额外独立检查了用户机器上全部安装文件的校验和。

Linux 主机上的官方下载包 `tunnel-client-v0.0.16-linux-amd64.zip` 已完成校验，
SHA-256 为
`d60cdba019bce451bcc3a15478cc5b9cb11270b049f5b56ea39a80b517f8b117`。
下载的 `SHA256SUMS.txt` 也已校验，SHA-256 为
`fcce864a5935655534cc3d981257509579dbb5c71dbaec64c17a157ca9d65e56`。
对应[官方上游发布](https://github.com/openai/tunnel-client/releases/tag/v0.0.16)。

### 安装与隧道恢复

全新 wheel 安装完成，引导式 setup 成功解析获准访问的合成练习项目 `main`。
操作者确认写入私有配置并只授予该项目权限；模式为 standard，worker 为 Codex CLI。
ChatGPT 隧道在本地配置完成后单独接入。

首次执行 `reasonfirst tunnel connect --install` 时，Python 下载路径返回
`SSL: UNEXPECTED_EOF_WHILE_READING`。原错误没有指出哪一个下载请求失败，
因此记录不能确定具体失败地址、代理或证书原因。

恢复时使用保持 HTTPS 校验的 `curl` 下载固定官方 v0.0.16 校验文件和 Linux 压缩包，
核对两份文件哈希，再通过已安装包提供的下载函数注入接口调用原安装器。完整配套
文件被安装，实际二进制版本探测通过。随后普通 `reasonfirst tunnel connect`
自动发现该安装并报告 runtime 健康、就绪。Runtime key 通过隐藏输入传入，没有
由 ReasonFirst 持久化保存。

**这次全新安装在经过校验的恢复后成功。** 不将其描述为 v0.5.2 自动下载全程无需
干预。下载恢复和诊断修正见[故障排查](TROUBLESHOOTING_CN.md)。

### 实时读取与受管 Git

助手仅通过选中的 **Linux 测试只读连接**调用 `gitlab_whoami`、
`check_project_access` 和 `get_file`。身份认证和获准项目的 `main` 预检通过，
随后使用本次预检返回的完整 SHA 读取 `README.md`，没有使用会移动的 `main`。
文件返回的版本与固定 ref 一致，2,488 字节 UTF-8 README 完整返回、没有截断。
先前的 Mac 或 Windows 连接未被用作替代。

操作者另行运行 `actual-coder project-config`，指定同一完整 SHA 并添加
`--validate`。原生 Git bare clone 和 fetch 完成；项目契约返回 `found: true`、
`valid: true`，errors/warnings 均为空。契约要求的测试命令为
`python3 -m unittest discover -s tests -v`，超时设置 120 秒。
这补充验证了原生 Git 读取/认证及契约有效性，与 MCP 的 GitLab API 证据互不替代。

### Doctor 与 worker 执行

`actual-coder doctor` 返回 `ok: true`，**19 项通过、2 项跳过、1 项警告、0 项
失败**。警告来自未安装可选 Copilot CLI；跳过项来自旧 doctor 只查 PATH 的
tunnel-client 检测，以及父 shell 没有 runtime key。版本化隧道安装和健康 runtime
已经分别验证，key 也只传入了隧道子进程环境；不需要为改变 doctor 摘要而重装
客户端或将 key 导出到父 shell。

随后操作者运行 `actual-coder start`，显式选择 `codex-cli`，固定同一基线，
任务只读文件和执行既有测试。命令创建一个隔离受管工作区、保存任务并通过正常
初始 handoff 启动 worker。Worker 被要求读取原有规则及练习文件，运行一次既有
测试，遇到问题只报告，不修复或实现下一阶段。

提供的 worker 报告记录了：

```text
python3 -m unittest discover -s tests -v
Ran 10 tests
OK
exit code: 0
```

操作者提供的 worker 报告明确写明“Final Git status: clean; no tracked or
untracked changes”和“Files modified: none”，并报告 HEAD 仍等于固定基线。
这是 worker 报告的文件状态；审阅者没有在 Linux 主机上独立执行 Git 状态检查。
操作者退出交互式 worker
后，ActualCoder 返回 `launch.returncode: 0`。测试结果和外层 CLI 正常退出是两项
独立观察，仅外层零退出码不能证明测试通过。Doctor 显示的 Python 版本指
ReasonFirst 工具环境，不自动等于项目 `python3` 命令实际使用的解释器。

请求的 Codex 策略为 `gpt-5.6-sol`、`high`、`workspace-write`、`on-request`，
sandbox 网络访问为 `false`。启动证据返回
`catalog_verified_launch_arguments_encoded`、`runtime_verified: false`。
这证明目录/策略输入被接受并编码为显式启动参数，不独立证明服务商最终模型或
sandbox/网络限制的实际执行。

### 本次 Linux 演练未执行的操作

本次没有修改源码、测试或文档，没有创建本地提交、推送分支、创建/合并 MR、部署、
运行可选全聊天 Bridge，也没有重启服务或系统。这些能力不计入本次通过范围。
后续下载器/doctor 代码修复尚未在该 Linux 主机安装验收，也没有记录可用于证明
“几分钟完成接入”的完整耗时。

## Windows：2026-09-30 至 2026-10-01

原测试主机为 Windows 11 Pro x64、PowerShell 5.1、uv 0.12.21，使用独立
Python 3.12.10 工具环境。恢复/读取检查针对 v0.5.1 候选源码
`235ddc81cf12341266d14e68ae94d0729bf0e462`。

[实时读取检查点](https://github.com/phoenixjyb/reasonFirst/pull/90#issuecomment-5914490847)
记录同一连接恢复成功，并通过指定 Windows connector 独立重跑身份、项目预检和
固定提交 README 读取。
[新终端检查点](https://github.com/phoenixjyb/reasonFirst/pull/90#issuecomment-5914743199)
记录了 `PYTHONUTF8` 不存在时，有效持久化配置和健康 runtime 状态；它检查的是
已运行的 runtime，不是移除该变量后的新启动。

2026-10-01 的[正常 worker/宿主收尾](https://github.com/phoenixjyb/reasonFirst/pull/94#issuecomment-5925996668)
使用集成 v0.5.1 wheel，SHA-256 为
`12d5663c44a68a7b417e510028f4780dd363feaf590a609d46de8b19fd21a7e6`。
[集成记录](https://github.com/phoenixjyb/reasonFirst/pull/94#issuecomment-5924958060)
确认其字节与最终源码 `9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b`
对应 wheel 一致。

正常初始 handoff 已带入批准的 CPython 3.12.10 绑定和 worker 执行配方，无需纠正
提示。10 项既有测试全部通过；子进程与 supervisor 退出码均为零，输出完整，
没有触发 120 秒超时。宿主收尾报告原分支保留、HEAD 等于 base、`dirty: false`、
本地领先提交数为零。`pushed: false` 是本地发布元数据，不是独立远端分支审计。

这些操作者提供的 worker 证据与先前宿主 validator、失败的诊断尝试分别保留。
后来的 v0.5.1 发布不改变原验收日期，也不把它变成 v0.5.2 实机记录。
修复/no-op、受控重启、系统重启及高权限 Bridge/写入继续由
[#88](https://github.com/phoenixjyb/reasonFirst/issues/88) 明确跟踪。

## macOS：历史部署

### 2026-09-28 记录的全聊天流程

[2026-09-28 发布检查记录](PUBLIC_RELEASE_CHECKLIST_CN.md)和
[全聊天演练指南](CHAT_ONLY_PRACTICE_CN.md)记录了一次真实合成任务：受管 worker
执行、审阅 snapshot 后人工批准、GitLab MR 发布、对应 HEAD 的单元测试 CI 成功，
并完成 EvidencePack 审阅。那次发现的 macOS launchd 代理继承边界也已记录在演练
及故障排查指南中。

这是全聊天流程的历史源码部署证据；所引公开摘要没有确定那次演练的安装 wheel
或系统准确版本。

### 2026-10-01 的 v0.5.1 本地激活

维护者保留的部署记录在 2026-10-09 经过复核，确认当时使用 uv 管理的公开
**0.5.1** CLI，并复用已有配置。在获准的空闲维护窗口中，只更新保存的启动参数，
保留原工作目录；激活成功，没有回滚。三次连续本地健康采样均报告 **0.5.1**，
listener 归属检查通过。后续只读 Bridge doctor 报告 **0.5.1 / Python 3.12.12**。

这是有日期的维护者本地激活/健康记录，不是新一轮独立远程连接验收，也不公开
原始宿主日志。它本身不证明所有已加载模块的身份或该包下另一轮全聊天闭环。
公开摘要省略私有启动器路径等机器信息。

当前 v0.5.2 的 macOS 安装/回归 CI、较早的真实服务商工作流和 v0.5.1 本地激活
分别计入证据。全新 v0.5.2 Mac 安装或系统重启演练需要自己的日期与结果。
