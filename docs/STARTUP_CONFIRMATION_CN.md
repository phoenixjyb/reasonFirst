# 受管启动确认：先定义消息契约

基于 main `0fcc977a48d91195574c94370f1a966494a04bc4` 的开发提案。
**不在已发布 v0.5.1 中，不是自动升级器。**

## 本步解决什么

#102 只报告 macOS 原生 API 实际提供的字段。未返回的工作目录和环境字段仍未知，
`managed_startup_confirmation_not_verified` 不因其他字段匹配而移除。
下一种证据须来自受管进程实际解析后的启动状态，同时独立建立它与预期进程的关系。
读取另一份配置文件、信任 `/healthz` 的版本字符串，都不能替代这一过程。

本步仅实现 `gitlab_agent.upgrade.startup_protocol` 内部的**消息编码及一次性校验器**。
不含进程身份绑定、实际状态收集器、HTTP 路由、启动器或激活功能；没有现有 CLI/MCP
命令自动启用它，不改依赖、已有持久化 schema、运行中的 Bridge 或服务管理器。

## 已实现的契约

`PendingStartupChallenge` 接收独立选定的 `StartupClaims` 和预期 PID。
`start()` 只返回一次挑战；`make_reply()` 回应挑战标识，并附加当前进程的 `os.getpid()`
和调用方另行收集的声明。挑战中**不携带预期声明**，避免直接把计划值当作观测值回显。
`finish()` 校验一份响应，无论成功失败都消耗该挑战；传输失败时调用 `cancel()`。

协议名 `reasonfirst-startup-claims-v1`。单条消息最多 16 KiB，必须是一个 UTF-8 JSON 对象。
拒绝重复键（含嵌套重复）、缺字段、额外字段、NaN/无穷、错误编码、多份拼接 JSON、
错误协议/消息类型和字段类型。错误只输出固定代码，不携带原始响应或解析异常链。
消息层接收的是有界字节串；未来传输层须在读取过程中限制字节量和时间，不能先无限缓存。

挑战字段仅为 `protocol`、`kind: challenge`、`launch_id`、`nonce`；后两者分别随机生成
256 位十六进制值。响应字段仅为 `protocol`、`kind: reply`、`launch_id`、`nonce`、`pid`、`claims`。
不接受对方给出的过期时间或任意扩展参数，也没有 token、签名或授权字段。

声明固定为九项：`runtime_id`、`manifest_digest`、`interpreter_digest`、
`configuration_digest`、`host`、`port`、`path`、`mode`、`control_policy`。
前四项须为 64 位小写十六进制标识；端点和模式复用当前 packaged HTTP 参数校验，
不自动规范化路径，control 只接受 disabled，不悄悄删掉旧 control 要求。

此版本**未实现**解释器/配置收集器。配置摘要必须来自经过审阅的非秘密有效配置投影，
不得直接使用 `.env`、凭据或整个环境变量字典。其具体投影版本需后续审阅。
收到哈希只表明一项身份声明，不证明磁盘文件、所有权、加载到内存的代码或收集过程可信。
消息仍含本地端点与标识，应私下处理。

挑战自创建起固定八秒，以校验器本进程的 `time.monotonic_ns()` 度量。
延迟发送不续期，到达截止时刻即失效，解析前后都检查时限。
成功、失败和取消均不能使用同一挑战再次校验；锁保证并发调用最多一次成功。
比较使用 `hmac.compare_digest`，但这里仅是比较操作，**没有生成 MAC、签名或认证通道**。

待验证对象仅保存在本进程内存，不是可序列化的持久凭证。监督进程重启、取消、系统睡眠恢复
或启动预期改变后，未来集成须丢弃该对象重新发起，不宣称睡眠感知时钟或断电恢复已实现。

## 成功不代表什么

成功仅返回 `fresh_reply_claims_match: true` 和 `pid_claim_matches: true`，不输出原始声明、
nonce 或 launch_id。以下状态一直为 false：

```text
peer_identity_verified
running_code_verified
effective_configuration_verified
managed_startup_confirmation_verified
compatibility_verified
activation_authorized
ready_for_activation
service_changed
```

始终保留启动确认、对端身份、声明收集尚未验证的 blocker；API 不提供把它们设为 true 的选项。
#102 原有命令与所有阻断条件不改。测试特意演示：伪造响应即使匹配预期值，也不能因此获得
独立身份认证或激活授权。

## 后续集成的明确要求

监督进程须先重验准备好的运行环境和启动/配置兼容性，持有自己启动的进程句柄，
从操作系统取得预期 PID，并拥有与该进程连接的新建私有通道。文件或响应里的 PID 不能替代它。
通道不得泄露给其他 worker。初始 macOS/Linux 实现优先评估专用继承匿名管道，
不增加公共 HTTP 路由或长期 socket 路径。通道只与持有句柄者关联，不天然认证特定二进制；
socketpair 的对端凭据也不能未经验证就当作后续子进程凭据。

运行端必须从服务器真正使用的已解析对象收集声明，而非照抄预期计划。
监听成功、监听器归属、工具目录、健康和客户端回连须另有证据；消息匹配不证明这些事实。
Windows 的句柄继承和进程绑定要单独原生测试，消息层可移植不等于 Windows 服务升级已完成。

老的无启动确认机制服务仍走明确审批的维护接管，不伪造等价证明。
即使将来确认流程完整，也不能清除任务准入、配置 schema、旧 control、回滚和人工授权等独立门槛。

## 验证边界

测试涵盖完整 schema、逐字段差异、过期边界、重放、错误 PID、时钟失败、并发、
固定错误和预期状态快照。有一个真实临时 Python 子进程通过私有标准输入/输出交换消息，
不启动 Bridge、监听器、外部服务或系统服务。该进程先退出再校验，说明成功不是存活证明。
新增测试没有按平台跳过。原三平台 CI 仍为新 PR 的验收要求，本地结果必须与原生 CI 区分。

Windows 的虚拟环境可执行文件可能只是启动另一个解释器的重定向器，两个进程的 PID 不同。
因此该进程测试在启动前明确选用当前解释器的基础可执行文件，并核对 Python 版本及源码导入位置；
这是测试夹具选择，不是生产环境的失败回退。预期 PID 仍只取自 `Popen.pid`，不从探针或响应采信。
独立探针仅报告原可执行文件的两个 PID 是否一致；真实中继进程反例要求不同 PID 的响应被拒绝。
基础解释器缺失、PID 不匹配或挑战失败都不能换一个预期值重试。此夹具不验证已安装 Windows venv
或真实服务启动器。参考 [CPython 的 Windows 启动实现](https://github.com/python/cpython/blob/v3.12.10/Lib/multiprocessing/popen_spawn_win32.py)。

本步不是 installed-wheel、原生 macOS API、受管 daemon、有效配置收集、对端凭据、
激活/回滚/重启验收；不削弱既有测试或激活阻断条件。

[English](STARTUP_CONFIRMATION.md) · [部分原生观测](LOADED_SERVICE_CN.md) ·
[HTTP 入口](BRIDGE_HTTP_CN.md) · [运行环境准备](RUNTIME_PREPARATION_CN.md)
