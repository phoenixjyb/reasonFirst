# Managed HTTP 服务 owner

**v0.5.2 包含此功能；v0.5.1 wheel 不包含。仅供内部显式集成。** `ManagedBridgeService` 将
[controller 维护准入](MAINTENANCE_ADMISSION_CN.md)连接到它自己创建并持有的
HTTP 服务。HTTP 请求、controller 工作和维护预留使用同一个 admission
对象。可选的不可变配置对象进一步把选定的父进程策略绑定到这个确切的
owner 和 controller。配置了绑定的 owner 还会保留其覆盖的本地子进程
启动输入，并在首次使用覆盖范围内的 GitLab API 时保留选定的 API 信任材料，
将后续使用绑定到同一个 child context。

实现位于 `src/gitlab_agent/upgrade/service_managed.py`。这是 Python 嵌入式
API，没有新增公共 CLI、MCP 工具、HTTP 管理路由或服务安装器。
省略可选配置时，启动继续使用普通 Bridge 配置和状态 loader。提供配置时，
controller 使用传入的选定策略对象，并从明确指定的状态目录读取状态。
原生集成测试的两条路径都使用全新的合成输入。

## 所有权与生命周期

owner 创建新的 `ControllerAdmission`、使用该对象的 `BridgeController`、
绑定此 controller 的共享 MCP core、独占 TCP socket 和 HTTP server task。
它不接受已有 controller、PID、已保存的 registration、pairing digest 或
一次性启动对象作为所有权证明。

配置了绑定的 owner 要求 controller 与共享 core 持有传入的同一个
`ManagedServiceConfiguration` 对象，以及 owner 捕获的同一个 child context。
公共 digest 或 context 值相同，都不代表可以替换对象。
清理所有权由确切的 admission gate 决定：使用另一个 gate 的 controller
在被接纳之前就会被拒绝，也不会被关闭；已加入本 owner gate 的 controller，
即使因配置、child context 或状态路径不匹配而无法启动，仍由本 owner 负责清理。

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
| `capture_service_configuration(settings, bridge_config, *, bridge_config_path, state_dir, ...)` | 将已经解析的设置、target 策略和显式服务选项复制为不可变的选定策略对象。 |
| `ManagedBridgeService(launch, *, configuration=None)` | 选择显式 `HTTPLaunch`，可选地传入确切的选定策略对象，创建新服务。 |
| `await service.start()` | 在 listener 创建前捕获配置绑定的子进程输入和 runtime 观测，创建已启用门控的 controller；就绪及绑定检查通过后，才进入 running 状态。 |
| `service.maintenance_snapshot()` | 返回有界服务及 ledger 诊断、已保留的观测元数据，不重新读取 runtime、可执行文件或 API 信任文件。 |
| `service.try_enter_maintenance()` | 重新验证配置了绑定的 owner，再在所有已追踪活动均已确认结束时原子预留维护。 |
| `service.leave_maintenance(lease)` | 重新验证配置了绑定的 owner，再释放当前原始的进程内 `MaintenanceLease`。 |
| `await service.aclose()` | 先关闭准入，再清理自己持有的 server/controller 资源。 |

这里直接使用 controller gate 的原始 lease，没有序列化 lease、bearer token、
第二套 lease 标识或自动转移。复制、外来的、过期的或已消费的 lease 都不能
重新开放准入。关闭会永久使未释放 lease 失效。

下面的嵌入模式接收调用方已经明确解析的 settings 与 Bridge 策略。配置引用
必须是绝对 `Path`，包括 `AgentSettings` 中的路径。示例本身不选择配置文件、
调用普通 loader 或改变宿主环境：

```python
from pathlib import Path
from typing import Any

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade.service_configuration import capture_service_configuration
from gitlab_agent.upgrade.service_managed import ManagedBridgeService


async def exercise(
    launch: HTTPLaunch,
    settings: AgentSettings,
    bridge_config: dict[str, Any],
    *,
    bridge_config_path: Path,
    state_dir: Path,
) -> dict:
    configuration = capture_service_configuration(
        settings,
        bridge_config,
        bridge_config_path=bridge_config_path,
        state_dir=state_dir,
        approval_timeout_seconds=300,
        gitlab_auth_mode="auto",
    )
    service = ManagedBridgeService(launch, configuration=configuration)
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

## 选定的父进程策略

`upgrade/service_configuration.py` 中的 `capture_service_configuration` 接收
已经加载的对象，不加载或读取 `.env`、Bridge 配置文件内容，不创建状态目录，
也不把值写入 `os.environ`。最初如何解析配置仍由调用方负责。显式文件引用
标识选定输入，但不证明这些文件当前的内容与传入对象一致。

捕获后的设置、worker 请求策略、target 策略和服务选项均不可变。返回的
projection 和 Bridge 配置视图都是独立副本；之后修改原始集合、兼容性视图、
环境变量或配置文件，不会自动重新加载这些选定值。

| 选定输入 | 配置了绑定的 controller 行为 |
| --- | --- |
| Agent settings 与 workspace root | 直接 helper 和私有 ReasonFirst 子进程入口使用保留的设置，不重新加载另一个 `.env` 文件。 |
| Codex worker 请求 | effective policy、session 恢复及继续执行都使用保留的 worker 策略；不因此证明 backend 已执行该策略。 |
| 按名称请求的 target 与已保存的 target | 名称从选定 target 集合解析；状态中的 target 必须与某个选定 target 的规范记录一致，包括 validation 策略。 |
| `gitlab_auth_mode` | 接受 `auto`、`api`、`git-only`；`auto` 根据保留的 API 凭据是否存在决定，直接模式 helper 不重新读取环境覆盖值。 |
| `approval_timeout_seconds` | 接受 30 至 1,800 秒的整数，默认 300；审批处理使用保留的超时值。 |
| 实验性远端 push | 此 owner 始终禁用，包括远端 dynamic-tool 的选择。 |

已保存的 target 使用 `ExecutionTarget.to_dict()` 的规范结构，包括平铺的
validation 字段。JSON list 与 tuple 表示可等价转换；标量类型必须完全一致，
未知或缺失字段会被拒绝。处理 legacy SSH 记录时，配置了绑定的 controller
保留选定 backend，不会因探测不到远端 binary 而自动迁移到另一种 backend。

已保存的 session worker policy 必须与保留策略的规范结构一致；没有保存
policy 时，使用同一个保留的 Codex policy。controller 在连接或恢复 AppServer
之前检查，并在继续执行已有缓存 session 时再次检查。过期的已保存 push 审批
不能启用绑定服务的 `commit_push` callback：先按原有规则核对请求属于当前
AppServer generation，再在读取 session 或访问 manager 之前拒绝该操作。

公共 projection 的版本为 `reasonfirst-selected-service-policy-v1`，范围为
`selected-parent-service-policy`。digest 是紧凑、键排序 UTF-8 JSON 的
SHA-256，包含选定的非秘密策略、引用和实际 HTTP launch。凭据、凭据是否
存在及其哈希、Git username、author 元数据和未使用的任意配置字段都不进入
该 projection。因此两个对象可能拥有相同公共 digest，但持有不同私有设置；
运行中的绑定要求对象身份一致，不能只比较 digest。

下文的本地子进程启动绑定提供保留的进程输入。独立的 API 信任绑定在首次
选择之后，保留覆盖范围内的 GitLab API client 实际使用的 CA 材料。
provider 状态、其他外部配置文件、原生 Git 与 SSH 配置、远端 runtime 状态
和 backend 策略执行仍在选定策略声明之外。仅在策略对象中保留 CA 路径或
环境信任布尔值，不会冻结所引用的文件内容。

该对象独立于一次性启动观测状态。disposable controller 仍拒绝所有工具调用，
不能与 live admission 组合。普通 controller 以及省略 `configuration` 创建
的 owner 保持原有加载行为。

## 选定的本地子进程启动输入

配置了绑定的 owner 在创建 listener 之前捕获私有 `ServiceChildContext`。
该 context 归属于创建它的 PID 与确切配置对象，保留独立的环境副本、绝对
工作目录，以及用于本地 ReasonFirst 子进程的 Python 调用路径。返回的环境
视图都是独立副本。之后修改 `os.environ` 或父进程工作目录，不会替换已捕获
的值；一个 owner 启动工作时，也不会临时修改进程全局变量来影响另一个 owner。

需要 ReasonFirst CLI 子进程的 controller 操作，通过有界私有入口
`upgrade.service_child` 传递已经解析的 settings。子进程跳过普通 settings
及 `.env` 发现逻辑。环境独立传给进程，凭据不会放进命令行参数。Python
isolated mode 将捕获的工作目录与 `PYTHONPATH` 排除在模块查找之外。每个
helper 从继承的环境建立仅属于该子进程的 context，供其后代使用；可执行
文件缓存与进程身份不跨进程传递。这是内部执行通道，没有新增面向用户的
CLI、MCP 工具或服务管理入口。

本地 Git、history inspection、validation 和本地启动的 SSH client 也接收
保留的启动 context。可执行文件按需从该 context 解析，覆盖的启动路径使用
明确的绝对可执行文件路径，并观测、复核已经选定的文件是否漂移。Codex
配置探针和自有 AppServer 进程使用选定的本地环境与工作目录；缓存的 client
也必须继续属于同一个 context。
controller 在覆盖的使用路径上检查选定 context；owner 在进入 running
之前及两个维护转换处重新验证。观察到 context 或可执行文件不匹配会永久
关闭准入；之后恢复文件或收到成功的子进程响应，都不会刷新基线。

这个绑定描述 ReasonFirst 提供的本地输入，不证明可执行文件随后加载或启动
的内容。Codex/provider 配置文件、额外 MCP 服务、原生 Git hook、filter、
trust store 及 include 配置、SSH 配置与 host-key/agent 状态、远端 shell
环境、远端 binary 和容器 image，以及已经运行的 Desktop daemon 都仍属于
外部状态。本地 validation 仍是原有的结构化宿主命令 runner；context 不会
把它变成 sandbox。文件观测也不证明已加载代码身份，不能使进程创建成为
原子操作。

## 选定的 GitLab API 信任材料

API 信任材料按需选择。创建配置了绑定的 owner、启动 listener、执行本地
检查，或使用 offline、Git-only、无 API token 的 helper，都不要求预先加载
API trust bundle。启动可能调用 GitLab API 的 helper 之前，父进程根据实际
解析后的参数选择该操作所需的信任材料；直接创建的绑定 API client 也可以
触发首次选择。此后，覆盖的 API 操作持续使用同一次选择。

私有 `ServiceAPITrust` 对象保留选定 certifi 公共根证书的有界 PEM 字节，
以及配置了 `api_ca_bundle` 时该附加 CA 文件的字节，并保留对这些文件的
一致性观测。它归属于创建进程与确切的选定 `AgentSettings`；公共摘要相同
或 settings 值相同，都不能授权替换对象。原始策略捕获仍不读取配置文件。

覆盖的同步 `GitLabAPI` client、异步项目预检和 CLI Doctor 路径从保留的
字节创建全新的 TLS context，维持 hostname 检查、强制证书验证、公共根证书
加选定附加 CA，以及既有的凭据目的地址和重定向防护。它们不复用调用方
可修改的 SSL context，也不会根据环境中的 CA 路径重新选择信任来源。
原生 Git 与 SSH 有各自的信任机制，不属于此绑定范围。

带版本的私有 helper 请求通过 stdin 传递父进程选定的材料与文件观测，分别
限制 command/settings 数据和信任数据的大小。不写入信任缓存，不把材料
放进子进程参数或环境变量。helper 重建属于本进程、绑定到确切解码 settings
的信任对象，并检查传入基线；它不能重新发现同一路径，再把当前文件内容
当成一次新选择。这种传递不证明子进程 runtime 身份。

首次成功选择之前，bundle 不可用或格式无效只使本次 API 操作失败。owner
仍能执行本地工作，后续 API 尝试也可以完成首次成功选择。一旦材料被选定，
观察到文件改变、丢失或对象被替换，都会成为永久绑定失败。context 在后续
覆盖的使用之前检查已选定信任；API request guard 在发送请求之前检查；
owner 在两个维护转换处检查。失败会关闭准入，恢复文件不能重新开放准入
或刷新基线。

这个声明只涉及覆盖的 Python GitLab API TLS client 所使用的保留输入，
不表示 snapshot 已验证 TLS 对端，也不表示完整 provider 配置清单、原生
Git/SSH 信任验证、全局空闲或 activation 就绪。没有后台文件 watcher。
重复文件检查不能使文件系统或网络操作成为原子操作；保留的字节保证新建
覆盖的 TLS context 时不会悄悄加载替换后的信任材料。

## 当前进程的 runtime 观测

配置了绑定的 owner 在创建 socket 之前，从内部捕获 runtime 观测；调用方
不能传入期望的 runtime digest。观测关联当前 PID、解释器调用路径和 prefix、
选定的解释器文件、选定模块对象及一致的源码根目录、这些文件的元数据与
字节内容，以及已审查的 SDK 版本。源码和安装路径分别保留各自实际观测到的
模块来源。

owner 在进入 running 状态之前、申请维护之前，以及释放 lease 之前重新验证
该观测。出现不一致会关闭准入、使维护资格失效，并持续保留这个 owner 的
失败状态。恢复文件或引用不会刷新基线，也不会让失败的 owner 重新运行。
进程不匹配会在获取继承的 owner lock 之前被拒绝。

这些检查是对选定文件和进程内引用进行有界、重复观测，不是原子的文件系统
快照，不证明内存里已加载的代码、整个 runtime 的完整性，也不证明某个独立
prepared runtime 的身份。没有后台漂移 watcher，也不会在每个 HTTP 请求上
重新读取这一组当前进程文件。覆盖的子进程路径另行执行按使用触发的检查。
诊断展示的是历史观测，维护状态转换执行上面所述的新一轮检查。

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

绑定的 AppServer client 只有在其确切 child context 被接纳之后，才成为
自有清理对象。工厂返回的外来 client 或缓存中替换进来的外来 client 会被
拒绝，但不会被关闭。缓存条目被替换，也不会丢弃原来已持有 client 的
清理 handle。

清理超时或失败是独立结果。listener 停止不证明 controller callback、
coding worker 或远端容器已结束。计数与 unknown 证据持续保留，不会为了
显示空闲而清空。审批决策及 Desktop 所持有 worker 的既有行为保持独立。

配置捕获使用有限集合的 `ServiceConfigurationError` 错误码。owner 用
`configuration_binding_failed` 表示选定策略检查失败，用
`runtime_binding_failed` 表示 runtime 捕获或重新验证失败；
`child_binding_failed` 表示 child context 捕获无效、身份不匹配、选定可执行
文件或 API 信任材料漂移，或 helper 报告了 child context 失败。选定信任
失败在内部归类为 `child_trust_failed`；信任捕获与材料验证使用有限集合的
`ServiceAPITrustError` 错误码。首次成功选择之前的失败仍属于 API 操作失败，
不会关闭 owner。普通子进程命令失败继续使用独立分类。
`controller_binding_failed` 与 `core_binding_failed` 表示启动对象不匹配。
绑定的 controller 或 gate 被外部关闭，也会使配置绑定永久失败。这些 owner
错误不回显原始配置值、runtime 路径或底层异常文本。

## 诊断范围

快照用固定 schema、计数、布尔值和分类原因描述本 owner 与内嵌的 controller
ledger，不返回 lease 对象、凭据、请求 body、提示词、workspace 路径、
worker/session 标识或原始异常文本。

| 快照字段 | 含义 |
| --- | --- |
| `resolved_policy_bound` | 仅在配置了绑定的 owner 运行、controller/core 持有确切配置且 gate 仍有效时为 true。 |
| `current_process_bound` | 仅在上述运行绑定同时持有内部捕获的当前进程观测时为 true；不表示本次 snapshot 调用完成了新的磁盘完整性检查。 |
| `child_inputs_bound` | 仅在运行中的 owner、controller 与 core 持有确切的已捕获 child context 且 gate 有效时为 true；不验证子进程 runtime 或 provider 配置。 |
| `api_trust_bound` | 仅在已选定 API 信任，且运行中的 owner 通过 child context 持有确切信任对象、gate 仍有效时为 true；不表示本次 snapshot 刷新了对端或文件检查。 |
| `configuration_digest` | 选定 projection 与 launch 的历史 digest；未捕获时为 `null`。 |
| `runtime_observation` | 选定 runtime 观测的有界历史摘要，包含 digest 和模块数；未捕获时为 `null`。 |
| `child_observation` | `owned-local-child-inputs` 有界摘要，包含输入保留布尔值与选定可执行文件计数；未捕获时为 `null`。不包含原始环境、环境 digest、路径或凭据指标。 |
| `api_trust_observation` | 已缓存的 `selected-gitlab-api-trust-v1` 摘要，包含材料保留、仅限当前进程的布尔值及一或两个选定 bundle 的计数；选择前为 `null`。不包含文件路径、PEM、证书身份或信任材料哈希。 |

子进程摘要中的 `child_runtime_verified`、`provider_configuration_verified`、
`remote_runtime_verified`、`desktop_daemon_verified` 和 `activation_authorized`
保持 false。计数只表示已选择的观测，不枚举完整后代进程树。

API 信任摘要中的 `tls_peer_verified`、`provider_configuration_verified`、
`native_git_trust_verified`、`native_ssh_trust_verified` 和
`activation_authorized` 保持 false。bundle 计数描述选定输入，不表示证书
数量或已验证对端数量。既有 child 摘要的版本和范围保持不变。

关闭 owner 后，四个运行绑定标志都变为 false。成功捕获的 digest 和观测
元数据继续作为历史证据保留。未配置绑定的 owner 的这些标志为 false，
configuration digest 与所有观测字段为 `null`。配置了绑定但尚未选定 API
信任的 owner，`api_trust_bound` 为 false，`api_trust_observation` 为 `null`。

即使四个运行绑定标志都为 true，下列更广的标志仍保持 false：
`effective_configuration_verified`、`recovered_state_verified`、
`external_producers_quiesced`、`global_idle_verified`、
`runtime_identity_verified`、`activation_authorized`、`ready_for_activation`、
`existing_service_adopted`。

观察到已有状态文件时保留 `recovered_state_unverified`；文件不存在不等于
跨进程所有权锁，也不能证明其他工作来源没有活动。选定策略绑定不证明状态
恢复、外部工作来源静止、全局空闲或 activation 授权。

静态部署记录、原生 loaded-job 样本、startup claims 与此 owner 的本地
维护预留继续作为不同证据。现有[launch-review blockers](LAUNCH_REVIEW_CN.md)
不会因本次集成而移除。`bridge_mcp.py` 源码 pin 覆盖显式 subscription 选项
和配置/child context 引用的保留，不表示这个 owner 已成为可批准的服务启动器。

## 验证

定向测试覆盖 owner 身份和生命周期、不可变选定策略的实际使用、外来或过期
绑定、runtime 漂移、真实请求/回复间隙、协议拒绝、task/lease 竞态、传输
不确定性与清理。真实 HTTP fixture 位于
`tests/test_managed_http_integration.py`，使用隔离的合成配置、两种 Bridge
模式，并以普通共享 core catalog 为比较来源，不联系 coding backend 或真实
GitLab 服务。

子进程集成测试覆盖真实 controller helper 路径、相互独立的 owner、preview
与直接 finish 中保留的 settings、外来 client 清理边界、持续失败的 child
绑定，以及普通 controller 兼容性。直接 backend 测试覆盖本地 Git、
validation、已批准 Python、history 和本地 SSH 输入。

API 信任测试覆盖按需选择、确切 settings 与对象所有权、独立 owner、跨进程
传递的 helper 材料、首次选择失败、持续文件漂移、request guard、两个维护
转换、私有缓存 snapshot 以及关闭后的历史证据。测试使用新建的合成 CA
和 loopback HTTPS fixture，并验证信任绑定失败时，在发送合成凭据请求之前
拒绝操作。

安装 harness 分别从干净 wheel 与源码环境运行同一组 16 个 fixture 用例，
并检查各自模块来源。其中包含两种模式的配置绑定 owner，以及真实的
`reasonfirst_files`/`reasonfirst_read` HTTP 调用。这些调用在 `.env`、环境及
父进程工作目录改变后仍使用选定 workspace，并在捕获的子进程目录放入
同名 shadow package，以验证 isolated 模块查找。两种模式还使用新建的合成
CA 材料及 loopback HTTPS 预检，验证绑定 API 信任，并确认选定信任文件漂移
后的操作被拒绝。另有四个 report-validator 用例约束报告格式。严格报告除
原有真实 MCP 和维护准入证据外，还要求 `selected_policy_binding_exercised`、
`runtime_observation_exercised`、`child_launch_binding_exercised` 与
`api_trust_binding_exercised` 都严格为 true；
跳过或不完整运行都不算成功。`working_service_touched` 和 `activation_tested`
保持 false。这些检查验证新创建并持有的 HTTP runtime，不证明用户正在使用
的服务已完成接管、身份认证、重启恢复或回滚验收。

参见[controller 准入](MAINTENANCE_ADMISSION_CN.md)、
[HTTP transport](BRIDGE_HTTP_CN.md)、[架构](ARCHITECTURE_CN.md)及
[English](MANAGED_HTTP_SERVICE.md)。
