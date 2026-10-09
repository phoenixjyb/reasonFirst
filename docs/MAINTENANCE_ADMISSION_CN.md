# Controller 维护准入

**开发源码；仅供内部显式启用。** 本门控协调一个 `BridgeController`
实例中已纳入追踪的工作。成功预留维护窗口后，该 controller 的工作准入
持续关闭，直到原始 lease 被释放或 controller 关闭。它不证明全局空闲，
不接管已有服务，也不授权 activation、服务切换、重启、回滚或发布。

没有新增公共 CLI 命令、MCP 工具、环境变量、setup 状态转换，也不会默认启用。

## 显式启用与作用范围

内部调用方通过为 controller 提供一个新的 `ControllerAdmission` 显式
启用门控。一个 admission 对象只能由一个 controller 认领一次；其他
controller 不能重复使用，其他进程也不能使用它。未选择此选项的普通
controller 保持原有行为。

维护准入与一次性 `managed_startup` 上下文互斥。
[一次性启动探针](DISPOSABLE_STARTUP_CN.md)始终以
`startup_observation_only` 拒绝工具执行；维护 lease 不能开放该上下文
的工具准入。

追踪范围仅包括通过该 controller 的操作，以及附着于其 app-server
客户端的已接入生命周期 hook。独立 CLI、其他 controller 或进程，
以及其他客户端发起的 Desktop 活动不属于该范围。访问同一 target
不会使这些外部工作来源自动纳入追踪。

如果启用门控时已有 Bridge 状态文件，在读取文件之前，维护就绪状态就
会变为 unknown。持久化的会话元数据不能证明实际工作或审批已经结束，
即使其中 pending 审批列表为空，或在加载时被清空也一样。本改动没有
新增持久状态 schema、迁移、服务接管或恢复记录。

## 内部 API

primitive 位于
`src/gitlab_agent/bridge_preview/admission.py`，
controller 接口位于
`src/gitlab_agent/bridge_preview/controller.py`。

| 入口 | 结果与边界 |
| --- | --- |
| `BridgeController(admission=ControllerAdmission())` | 在读取 controller 状态前显式认领一个新的 admission 对象。同时提供 `managed_startup` 会在访问状态前失败。 |
| `controller.maintenance_snapshot()` | 返回诊断字典，不加载持久状态，也不联系 worker。 |
| `controller.try_enter_maintenance()` | 原子成功时返回原始 `MaintenanceLease`；否则以固定准入错误码抛出 `BridgeError`。 |
| `controller.leave_maintenance(lease)` | 消费当前完全相同的 lease，返回 `None`。 |
| `controller.close()` | 永久拒绝新准入、使 lease 失效，并关闭 controller 的 app 客户端；保留尚未完成的工作证据。 |

未启用门控的 controller 对上述三个维护方法返回
`maintenance_admission_not_enabled`。持有 lease 时，新工作以
`maintenance_active` 被拒绝。进入维护时，进行中的工作、已有 lease
和持续 unknown 都使用 `maintenance_busy`；安全快照可区分具体状态。
已关闭准入使用 `admission_closed`。无效释放返回
`invalid_maintenance_lease`，不会改变状态。

下面仅演示归属与释放；门控本身不执行维护动作：

```python
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.bridge_preview.controller import BridgeController

controller = BridgeController(admission=ControllerAdmission())
try:
    lease = controller.try_enter_maintenance()
    try:
        # 在另行获得授权的内部动作期间持续持有 lease。
        pass
    finally:
        controller.leave_maintenance(lease)
finally:
    controller.close()
```

进入维护失败时不会执行内层代码块。不能捕获失败后，在没有 lease 的
情况下继续维护动作。持有 lease 期间，已有 controller 工作方法仍受
准入检查约束。

## 维护窗口必须原子预留

先观察空闲、再决定暂停工作，两步之间仍可能出现新请求。维护入口
因此使用与工作预留相同的锁，在同一次原子决策中检查已追踪状态并
关闭新工作准入。

| 尝试进入维护时的情况 | 结果 |
| --- | --- |
| 准入开放、已追踪活动均已确认结束，并且没有现存 lease | 预留维护窗口并返回 lease。 |
| 操作、回调、审批或 turn 仍在进行 | 立即以 `maintenance_busy` 拒绝；不等待、排队、取消或排空工作。 |
| 活动状态或生命周期覆盖存在不确定性 | 立即以 `maintenance_busy` 拒绝；快照保留 unknown 就绪状态与固定原因。 |
| 已有维护 lease、未有效认领或 controller 已关闭 | 拒绝进入；不替换其他调用方的 lease。 |

工作预留与维护入口共享同一个决策点。工作先成功时，只要该预留仍
有效，维护就不能进入；维护先成功时，新操作就不能通过准入检查。
被拒绝的操作不会开始 handler 的副作用。

调用方必须在整个维护期间持有 lease。诊断快照或此前成功的就绪
检查都不是 lease，不能预留这段时间。

## unknown 就绪状态与关闭准入有区别

对这个已启用的 controller，不确定性标记一旦出现就持续保留。
没有 clear、reset 或通过一次成功快照消除 unknown 的路径；这样才能
保留“已观察到完成”与“丢失生命周期信号”的区别。

unknown 阻止进入维护。如果准入因其他条件仍然开放，普通操作、审批
回复和取消仍然可用。未解决的 turn 会保留其 app / thread 标识组合与
workspace 预留上的冲突；不相关的工作可以继续。就绪状态不确定不能
禁用解决或停止进行中工作所需的操作。

快照中的 `admission_open` 表明普通工作准入是否开放。
`state` 可以是 `unknown`，同时 `admission_open` 为 true。
如果持有维护 lease 期间出现不确定性，该 lease 仍阻止新工作准入。
释放当前有效 lease 可以重新开放普通准入，但不能恢复已知的维护
就绪状态。

关闭 controller 会永久关闭准入并使 lease 失效。shutdown 不会产生
新的空闲证明，也不会产生可转交的维护授权。

## Lease 的归属

维护 lease 绑定原始 admission 实例、进程和本次预留的 generation。
调用方必须把同一个仍被持有的 lease 交回原实例。来自其他实例或
进程、过期、格式错误或已消费的 lease 都会失败，且不会改变状态或
释放其他调用方的维护窗口。

就绪状态变成 unknown 后，当前有效 lease 仍可消费；这只释放它自己的
预留，不会清除不确定性。controller 关闭时会使所有未释放 lease
失效，旧 lease 无法重新打开已关闭的实例。

primitive 的每个操作都会在获取进程内锁之前检查进程归属。把状态
复制到另一个进程不会转移认领或 lease 归属。lease 不写入持久化
Bridge 状态，也不是 service manager 的凭据。

## 追踪完整活动周期

### 同步 controller 操作

已纳入追踪的外层 handler 在首次副作用之前预留操作，并一直持有到
最终返回或异常清理结束。handler 内部的嵌套工作不会留下无保护的
空档。同步 handler 返回时可以释放其操作预留，但这不表示异步
turn 已经完成。

### 异步 turn

外层操作覆盖准备与 thread 创建，包括尚未取得 thread 标识的阶段。
在 `turn/start` 外部请求发送前，紧邻该调用建立独立 turn 预留。
返回的 turn 标识会绑定到该预留。启动 RPC 返回不会移除仍在执行的 turn。

生命周期通知持续保留 starting、running 和 stopping 工作，直到
观察到终态。请求停止不等于已经停止。无法判定的启动失败、transport
丢失、生命周期标识不匹配或缺少完成事件覆盖，都必须保留不确定性，
不能制造空闲状态。

远程验证报告包含 `timed_out`，或验证过程捕获并报告 transport 错误时，
也必须持续保留不确定性。容器引擎命令结束后，远程容器仍可能继续运行。
父 turn 完成和回调回复发送成功，都不能消除这种不确定性。

已完成的精确标识会保留在有界集合中。对已完成标识重复发送 completed
或 started 通知是幂等操作，不会复活已结束的工作，也不会清除另一个
turn。新 turn 必须在 `turn/start` 前为其最终完成标识预留空间。达到
容量后，后续 turn 以 `admission_capacity` 被拒绝；已经正常完成、其他
状态也确定为空闲的 controller 仍可进入维护。没有 eviction 或 reset
会丢弃区分重复通知所需的证据。

内部边界为：三种操作类别合计最多同时持有 256 个操作预留，最多保留
128 个活动 turn，已完成标识与预留的完成槽位合计最多 256 个，每个
活动标识最多 4,096 个字符。嵌套 handler 可能持有多个操作预留，因此
计数表示预留数，不表示独立用户请求数。这些边界限制的是记录空间，
不会对已有工作设置超时或自动取消。

### 审批与回调

审批与回调在分发前预留。审批从等待决定一直追踪到实际 app-server
最终回复发送结束。从内存 pending 列表移除审批，或仅决定回复内容，
都不足以确认完成。回复发送失败或状态无法确认时保留 unknown。

原有审批策略不变：审批仍通过正常流程作出决定。进入维护不会自动
批准、拒绝、使其过期或以其他方式处理待决请求。unknown 在普通准入
因其他条件仍开放时不会关闭准入，因此审批和取消工作仍可继续。

## 安全诊断快照

快照只返回有界 schema 字段、计数、布尔值和固定原因标签，不返回
提示词、工具参数、审批内容、凭据、workspace 路径、app / thread /
turn / request 标识、任意异常文本或 transport payload。

| 字段 | 含义 |
| --- | --- |
| `schema_version`、`scope` | 带版本的诊断结构；scope 为 `controller-instance`。 |
| `state` | 本实例的认领 / 就绪状态：unclaimed、open、maintenance、unknown 或 closed。 |
| `admission_open` | 门控当前是否允许新的普通工作预留。 |
| `counts` | 仍保留的操作、回调、审批，以及 starting、running、stopping 或 unknown turn。 |
| `unknown_reasons` | 持续保留的不确定性的固定原因标签。 |
| `idle_observed` | 该时刻对已追踪活动的诊断观察。 |
| `maintenance_held` | 本实例当前是否持有维护预留。 |
| `activation_authorized` | 始终为 false。 |
| `global_idle_verified` | 始终为 false。 |

快照不包含 lease，也不提供释放能力。其计数是本地观察，不是整个
target 的活动普查。需要独占维护的调用方必须尝试原子预留并持有返回
的 lease；不能只凭 `idle_observed` 开始维护。

## 本门控仍未覆盖的事项

primitive 不发现或自动纳入外部工作来源，不证明旧服务没有工作，
不停止服务、变更 listener、切换 runtime、重新连接客户端，也不
执行维护任务。未接入追踪的来源所拥有的现存工作仍超出其知识范围。

服务接管仍需明确设计如何处理已有会话和审批、所有相关工作来源、
配置与状态 schema 兼容、旧 `/control`、恢复与回滚、客户端重连及
人工授权。只关闭一个 controller 的准入不能解决这些门槛。

[启动声明 codec](STARTUP_CONFIRMATION_CN.md)、一次性启动观察、runtime
准备和静态 launch review 各自保留原有证据与阻断项。维护 lease 或
空闲快照都不会改变它们的 activation 或兼容性结论。

另见[架构](ARCHITECTURE_CN.md)、
[一次性启动](DISPOSABLE_STARTUP_CN.md)和
[English](MAINTENANCE_ADMISSION.md)。
