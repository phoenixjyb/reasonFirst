# Managed HTTP 服务 owner

**开发源码；仅供内部显式集成。** `ManagedBridgeService` 将
[controller 维护准入](MAINTENANCE_ADMISSION_CN.md)连接到它自己创建并持有的
HTTP 服务。HTTP 请求、controller 工作和维护预留使用同一个 admission
对象。这是该门控的第一步服务集成。

实现位于 `src/gitlab_agent/upgrade/service_managed.py`。这是 Python 嵌入式
API，没有新增公共 CLI、MCP 工具、HTTP 管理路由或服务安装器。
启动会读取普通 Bridge 配置和状态；调用方应明确选择配置与状态目录。
原生集成测试使用全新的合成输入。

## 所有权与生命周期

owner 创建新的 `ControllerAdmission`、使用该对象的 `BridgeController`、
绑定此 controller 的共享 MCP core、独占 TCP socket 和 HTTP server task。
它不接受已有 controller、PID、已保存的 registration、pairing digest 或
一次性启动对象作为所有权证明。

构造、启动、运行和清理是不同状态。owner 只能使用一次；启动成功之前
不能申请维护，关闭或失败后不能重新启动或绑定另一个服务。

启动将持续持有的 socket 交给实际 server，并验证 server 正在预期地址上
使用该 socket 监听。申请维护时也必须确认持有的 task 与 listener 仍有效。
TCP 连接成功或 `/healthz` 成功都不能替代这个所有权检查。

嵌入方负责进程信号；adapter 不安装自己的信号处理器、不注册 daemon，
也不修改 launchd job。异步生命周期归属于同一个 event loop；维护 handle
和 lease 归属于创建它们的进程。

## 内部 API

| 入口 | 行为 |
| --- | --- |
| `ManagedBridgeService(launch)` | 选择显式 `HTTPLaunch`，不接管已有 server。 |
| `await service.start()` | 创建已启用门控的 controller，启动并验证自己持有的 HTTP listener。 |
| `service.maintenance_snapshot()` | 返回有界的服务及 ledger 诊断，不据此授权维护。 |
| `service.try_enter_maintenance()` | 运行中的 owner 在所有已追踪活动均已确认结束时原子预留维护。 |
| `service.leave_maintenance(lease)` | 释放当前原始的进程内 `MaintenanceLease`。 |
| `await service.aclose()` | 先关闭准入，再清理自己持有的 server/controller 资源。 |

这里直接使用 controller gate 的原始 lease，没有序列化 lease、bearer token、
第二套 lease 标识或自动转移。复制、外来的、过期的或已消费的 lease 都不能
重新开放准入。关闭会永久使未释放 lease 失效。

下面的嵌入模式假设调用方已经明确选择配置和未占用的端点：

```python
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.upgrade.service_managed import ManagedBridgeService


async def exercise(launch: HTTPLaunch) -> dict:
    service = ManagedBridgeService(launch)
    try:
        await service.start()
        lease = service.try_enter_maintenance()
        try:
            return service.maintenance_snapshot()
        finally:
            service.leave_maintenance(lease)
    finally:
        await service.aclose()
```

请求、操作、turn 或审批回复尚未结束，或者就绪状态为 unknown 时，
`try_enter_maintenance()` 立即拒绝。它不会等待、取消 turn、决定审批、
排空客户端或执行维护任务。预留成功后，新的工作准入持续关闭，直到
原始 lease 被正确释放。

## HTTP profile 与兼容性

新 adapter 明确使用 MCP **2026-07-28** 单次 JSON 请求 profile：
stateless HTTP、共享 MCP core 关闭 subscriptions、Uvicorn 使用 `h11`。
已审查的 SDK 组合为 **MCP 2.3.0 与 Uvicorn 0.54.0**，在创建 controller
之前检查。包声明的更广依赖范围不等于此 owner 已兼容其他 SDK 版本。

| 请求 | Managed owner 行为 |
| --- | --- |
| MCP `POST`，协议版本受支持且 method 元数据一致 | dispatch 前预留，覆盖请求执行、回复处理和请求内清理。 |
| 持有维护 lease 时的 MCP `POST` | 读取 body 或调用 controller 工具之前返回固定 `503`。 |
| 旧版、缺失、冲突或重复的协议元数据 | 进入不受支持的 SDK 请求路径之前拒绝。 |
| MCP `GET` 或 `DELETE` | `405`，本 profile 没有持久 GET stream 或 stateful session 删除。 |
| `subscriptions/listen` | 不支持；managed core 不注册或声明 subscription 能力。 |
| `/healthz` | 仅表示存活，与工作准入和维护就绪分开。 |
| `/control` 或 HTTP 维护路由 | 不暴露；维护通过可信的 Python owner handle 进行。 |

所有 MCP POST 共用预留边界，包括 discovery 和 catalog 请求。准入开放时
可以枚举 catalog，正确释放维护 lease 后也可以再次枚举；持有 lease 期间，
这些请求与其他 MCP 请求一样被拒绝。

普通[打包 HTTP 命令](BRIDGE_HTTP_CN.md)、stdio Bridge 和
[一次性启动探针](DISPOSABLE_STARTUP_CN.md)保持各自的协议选择。
这个 owner 是独立 profile，不证明已有 legacy 服务的客户端能够切换过来。
后续服务切换必须独立验证客户端重连和协议兼容性。

`read-only` 与 `full-chat` 两种模式均使用共享 core 的准确工具 schema 和
annotations。关闭 subscriptions 会相应改变此 owner 的 discovery 能力。
普通调用方省略新增的共享 core 选项时，继续使用 SDK 默认行为。

## 为什么需要传输边界

已审查 SDK 的旧版 stateful 和旧版 stateless HTTP 路径都可能拥有比 ASGI
请求活得更久的 session/dispatcher task。单独设置 `stateless_http=True`
不能等待所有这些工作结束。单独选择 JSON 回复，也不能去掉 SDK 默认的
subscription handler 及其持久 stream。

所选现代 JSON 路径会等待本次请求执行。外层 adapter 在 dispatch 之前获取
准入，并保留到最终回复处理和请求内清理结束。controller 的 turn 与审批
预留继续覆盖合理地长于工具响应的工作。

ASGI send 返回不证明对端已经收到回复。观察到的断连、取消、发送失败或
不完整请求清理持续保留不确定性；没有 peer acknowledgement 或远端投递证明。

## 清理与失败

关闭先拒绝新准入、使 lease 失效，然后等待传输清理。server task 意外退出
也会关闭准入。仅清理自己持有的 socket、task 和 controller client；
不会接管已占用端口或其他 listener。

清理超时或失败是独立结果。listener 停止不证明 controller callback、
coding worker 或远端容器已结束。计数与 unknown 证据持续保留，不会为了
显示空闲而清空。审批决策及 Desktop 所持有 worker 的既有行为保持独立。

## 诊断范围

快照用固定 schema、计数、布尔值和分类原因描述本 owner 与内嵌的 controller
ledger，不返回 lease 对象、凭据、请求 body、提示词、workspace 路径、
worker/session 标识或原始异常文本。

不可变的 HTTP launch 仅描述本 owner 实际消费的传输参数，不是完整配置
digest。普通 controller 操作仍会通过既有 loader 解析应用策略、环境变量
和子进程配置。

配置绑定、状态恢复、外部工作来源静止、全局空闲和 activation 授权仍然是
未验证或 false。观察到已有状态文件时保留 `recovered_state_unverified`；
文件不存在不等于跨进程所有权锁，也不能证明其他工作来源没有活动。

静态部署记录、原生 loaded-job 样本、startup claims 与此 owner 的本地
维护预留继续作为不同证据。现有[launch-review blockers](LAUNCH_REVIEW_CN.md)
不会因本次集成而移除。`bridge_mcp.py` 源码 pin 仅因显式 subscription
opt-out 更新，不表示这个 owner 已成为可批准的服务启动器。

## 验证

定向测试覆盖 owner 身份和生命周期、真实请求/回复间隙、协议拒绝、
task/lease 竞态、传输不确定性与清理。真实 HTTP fixture 位于
`tests/test_managed_http_integration.py`，使用隔离的合成配置、两种 Bridge
模式，并以普通共享 core catalog 为比较来源，不联系 coding backend 或 GitLab。

安装 harness 分别从干净 wheel 与源码环境运行同一 fixture，并检查各自
模块来源。这些检查验证新创建并持有的 HTTP runtime，不证明用户正在使用
的服务已完成接管、身份认证、重启恢复或回滚验收。

参见[controller 准入](MAINTENANCE_ADMISSION_CN.md)、
[HTTP transport](BRIDGE_HTTP_CN.md)、[架构](ARCHITECTURE_CN.md)及
[English](MANAGED_HTTP_SERVICE.md)。
