# 只读观察 macOS 已加载任务，不切换服务

**开发分支功能，不在已发布的 v0.5.1 wheel 中。** 这是独立于静态启动审阅的一项原生读取，
不会加载、卸载、重启、发送信号或注册 ReasonFirst 服务。

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
`selected_launch_fields_match` 只涵盖四个字段，不涵盖全部 launch 选项或默认值。
PID 不是独立的可执行文件身份或监听归属；LastExitStatus 是历史状态，不是当前健康。
原生环境块不等于进程继承的完整环境或 shell 变换后的环境，也不能证明已加载 Python 模块身份。

保留全部先前阻塞项，包括旧 `/control` 不兼容和生成启动器未审计。
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
的 Python 查询，修改临时保存 plist 后验证已加载任务仍不同，最后只卸载该随机标签任务。
不使用真实 ReasonFirst 标签或服务器，不修改维护者机器。该测试验证原生适配器／比较器，
不是完整 CLI 配对路径、服务迁移或恢复验收；缺原生能力使 CI 失败，不静默跳过。
临时任务在尝试清理后才输出一份分类 JSON，同时保留最初失败阶段与独立的清理结果。
只报告固定错误码及字段匹配分类，不输出原生字典、参数、环境值或 stderr。
父测试先验证有界报告再记录；失败结果或矛盾的退出状态仍使 CI 失败。
诊断改进不放宽缺项、保存与加载状态漂移的验收要求。

应用配置与 schema、生成启动器支持、完整生效环境和进程身份、任务准入、切换／恢复及旧客户端回连
仍需后续实现与验证。

[English](LOADED_SERVICE.md) · [启动审阅](LAUNCH_REVIEW_CN.md)
