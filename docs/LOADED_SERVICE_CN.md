# 只读观察 macOS 已加载任务，不切换服务

**开发分支功能，不在已发布的 v0.5.1 wheel 中。** 这是独立于静态启动审阅的一项原生读取，
不会加载、卸载、重启、发送信号或注册 ReasonFirst 服务。

经明确批准，本功能契约为 **`partial-selected-fields-v1`（部分字段观察）**。
观察完成时，加载配置仍可能不完整，激活仍被阻塞。这是基于原生调查明确修正范围，
不代表原先要求四字段全部可见的原生验收已经成功。

```text
reasonfirst-runtime loaded-inspect --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

用已审阅的准确值替换占位符。没有任意标签、主机、PID、命令、其他 domain 选项，也不接受
`--yes` 或激活标志。只查询调用者用户级 launchd domain 中的 `com.reasonfirst.v4-mcp`；
Windows/Linux、root 和 setuid 环境会被拒绝。其他登录上下文查不到，不代表 GUI 中服务不存在。

## 证据边界

读取前后重验源代码／保存策略和 prepared runtime，并进行两次原生查询。私下比较
Program、ProgramArguments、WorkingDirectory、EnvironmentVariables；只返回匹配、不同或
缺失的分类，以及报告的 PID、历史退出状态和已审摘要，不回显或保存原生参数、环境值和 stderr。

Program 缺失时可按 argv[0] 推导；其他缺项不从保存文件补齐，报告 `not_reported`。
明确的空环境块可与保存文件中未设置环境匹配；原生环境块缺失则仍未知。两次 PID 或选中字段
变化，以及已审文件变化都会导致失败，不返回部分成功比较。

`ok: true` 只代表检查完成，可能伴随不同、缺项或无运行 PID。
`field_coverage` 为 `complete`、`partial` 或 `none`；`unreported_fields` 仅列出
本次响应未报告的已知字段。`reported_fields_match` 要求至少有一个返回字段且全部返回字段匹配，
不能当作完整配置等价。原有 `selected_launch_fields_match` 仍要求四个字段**全部匹配**，
任意 `not_reported` 都使它保持 false；即使 complete，也不涵盖全部 launch 选项或默认值。
PID 不是独立的可执行文件身份或监听归属；LastExitStatus 是历史状态，不是当前健康。
原生环境块不等于进程继承的完整环境或 shell 变换后的环境，也不能证明已加载 Python 模块身份。

保留全部先前阻塞项，包括旧 `/control` 不兼容和生成启动器未审计。
新增 `managed_startup_confirmation_not_verified` 无条件阻塞项，即使四字段匹配也不会移除；
`managed_startup_confirmation_verified` 始终为 false，此命令尚未实现启动确认协议。
`effective_environment_verified`、`running_code_verified`、`compatibility_verified`、
`activation_authorized`、`ready_for_activation` 始终为 false。不改登记、运行环境、向导状态、
应用配置或任务审批记录；除原有审阅的允许源文件／元数据外不读取应用文件。
原生字典可能含配置引用，但不会打开被引用的配置文件。

## 实现与限制

使用 Apple 结构化的 `SMJobCopyDictionary(kSMDomainUserLaunchd)` 读取 API，
不解析 `launchctl print` 诊断文字。**此 API 已被 Apple 标记为 deprecated。**
缺符号、空返回、格式错误、超大响应、崩溃、超时均保守失败，不猜测回退，不请求管理员权限。
空返回为 `job_unavailable_or_query_failed`，不据此断言服务不存在。
参见 [Apple 文档](https://developer.apple.com/documentation/servicemanagement/smjobcopydictionary(_:_:))。

固定的纯标准库脚本通过当前 Python 的 `-I -S -B` 运行，只用绝对 framework 路径和最小子进程
环境，不导入用户 site/.pth；这不能证明已启动 CLI／解释器／操作系统可信。
每次原生查询最多八秒，stdout 最多 256 KiB、stderr 最多 16 KiB，另有最多两秒子进程清理。
只有该查询子进程可能被终止；不构造应用 controller，不启动服务器或 worker，不访问 HTTP 或凭据服务。
重复读不是原子快照、维护锁、ABA/PID 重用防护或签名。

## 测试与后续

便携测试覆盖脱敏、缺项、差异、PID 漂移、重复审阅、错误与 CLI 边界；POSIX 测试真实执行管道限制／超时。
仅在 macOS Actions 的显式测试中加载一个随机标签的 `/bin/sleep` 临时任务，用 prepared runtime
的 Python 查询。必须观察到匹配的 Program 和 ProgramArguments；缺少这些关键证据仍失败。
修改临时保存 plist（含 sleep 参数从 `90` 变为 `91`），验证实际已加载参数仍与新保存值不同，
最后只卸载该随机标签任务。若原生返回 cwd/env，初始必须匹配且保存变化后必须不同；
若未返回，两次比较均须保持 `not_reported`，四字段全匹配仍为 false，不完整配置与启动确认
阻塞项必须保留。两次原生查询的选中投影和 PID 必须保持不变。
不使用真实 ReasonFirst 标签或服务器，不修改维护者机器。该测试验证原生适配器／比较器，
不是完整 CLI 配对路径、服务迁移或恢复验收；缺原生能力使 CI 失败，不静默跳过。
临时任务在尝试清理后才输出一份分类 JSON，同时保留最初失败阶段与独立的清理结果。
只报告固定错误码及字段匹配分类，不输出原生字典、参数、环境值或 stderr。
父测试先验证有界报告再记录；失败结果或矛盾的退出状态仍使 CI 失败。
报告明确标记修改后的 `observation_contract`、覆盖程度、原四字段结果与激活阻塞项。
父测试拒绝把缺项伪装成全匹配、移除启动确认阻塞、跳过已报告参数漂移或授予激活权。
新测试成功只证明较窄的观察契约和清理完成，不证明完整加载配置。

应用配置与 schema、生成启动器支持、完整生效环境和进程身份、任务准入、切换／恢复及旧客户端回连
仍需后续实现与验证。

[English](LOADED_SERVICE.md) · [启动审阅](LAUNCH_REVIEW_CN.md)

## 原生字段可用性调查

`1ce151e8` 的 CI 已成功查询并清理临时任务，但原生字典的预期顶层位置没有报告
`WorkingDirectory` 和 `EnvironmentVariables`。这不代表启动设置未生效，也不是
四字段比较成功；不能从保存的 plist 补齐原生证据。

仅 CI 临时任务增加 `native_shape`：固定字段的顶层类型，以及在返回字典中递归统计
这两个已知键及临时任务自身 cwd/env 字符串的出现次数。不输出任意原生键、值、标签、
路径或 stderr。最多访问 4096 节点、深度 16；不完整遍历明确标记，不能用于断言不存在。
形状诊断不参与比较或清理的通过判定，不新增查询、生产 API 或回退解析器。

Apple [历史 launchd-842.1.4 的 `job_export` 实现](https://github.com/apple-oss-distributions/launchd/blob/d448a1c8f70a61202f8705f94337f686b87c30c4/src/core.c#L985)
只导出包含程序、参数及进程状态的选中字段，不导出 cwd/environment；不是完整保存配置。
该旧源码提示一种 API 能力限制，但不能证明今天 macOS 的二进制实现。
具体 runner 的证据仍以实际字段及形状报告为准，不猜测改名或嵌套字段的别名。
后续 `8e1e4862` 的 CI **37781648284** 完成对该 runner 返回字典的有界遍历，
其中没有这两个字段名，也没有临时测试自身 cwd/env 的准确字符串。该轮仍是**原四字段验收失败**，
未进入保存文件漂移阶段；清理已确认。这不是对所有 OS/API 版本的能力保证。

用户随后明确批准把 #102 的范围改为部分观察。新原生验收验证可用的程序／参数证据、缺项处理、
已报告参数的漂移及清理，不伪造缺失证据。原先要求 API 必须返回 cwd/environment 的测试，
在这次范围决定下被显式修订；底层四字段全匹配的断言和自动激活阻塞仍保留，并新增回归保障。
不能把这称为原完整验证套件未变却已全部通过。

后续受管启动确认必须验证新鲜度及本地进程／对端绑定，才能依赖选中的非秘密有效配置及运行身份。
自报信息本身不是独立进程证明或授权；未具备该协议的旧服务需要经审阅的维护接管路径。
本次部分观察修改不实现这些协议、激活或恢复。
