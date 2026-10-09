# 一次性受管启动验证

**这是开发源码功能，不在已发布的 v0.5.1 wheel 中。** 该内部适配器面向
macOS/Linux，只启动新的、一次性的已准备运行环境，观察启动结果后清理。
它不接管、替换或重启现有服务，也没有新增公开 CLI、MCP 工具、安装向导状态、
发布或激活命令。

## 范围

内部入口 `gitlab_agent.upgrade.startup_managed.probe_disposable` 接收已选择的
prepared runtime 和明确的 `HTTPLaunch`。监督进程重新核验运行环境，创建全新的
合成 HOME、配置和状态目录，启动选定解释器，验证私有启动消息、真实监听器和
MCP 工具目录，然后清理本次尝试拥有的资源。

合成配置使用 `.invalid` GitLab 地址、空凭据和本地目标，不读取维护者的实际
配置、凭据、工作区、审批或隧道。候选进程复用共享核心，注册原有 read-only /
full-chat 工具目录，但**所有工具调用都由关闭的准入门拒绝**，固定错误为
`startup_observation_only`。初始化、工具目录枚举和既有健康路由可以访问。
正式探针只枚举目录，不调用编码工具；该一次性上下文没有开放工作准入的状态转换。

运行的制品及其依赖必须经过信任与审阅；此机制不是执行恶意代码的沙箱，也不是
通用服务启动器。

## 配置投影与来源

`configuration_digest` 是版本为 `reasonfirst-selected-startup-policy-v1` 的
固定投影经过紧凑、键排序 UTF-8 JSON 序列化后的 SHA-256。禁止 NaN，投影上限
64 KiB。只有明确列出的字段进入摘要，数据类新增字段不会自动进入当前协议。
完整、逐字段键名以[英文规范](DISPOSABLE_STARTUP.md#configuration-checkpoint-selected-policy-with-provenance)为准。

| 分组 | 选取范围与来源 |
| --- | --- |
| `protocol` / `scope` | 投影版本和 `disposable-startup-observation` 范围。 |
| `transport` | 实际用于构建应用和监听器的 HTTPLaunch：传输、host、port、path、mode、control policy；远程 push 不开放，工具准入关闭。 |
| `references` | 实际捕获的 Agent / Bridge 配置文件、Bridge 状态目录和 state.json、工作区目录、CA 文件引用。只记录引用，不散列文件内容。 |
| `agent_policy` | 已加载 AgentSettings 的 GitLab URL、TLS / 代理策略、项目和命令白名单、写入白名单要求、分支及默认 ref、命令时限、输出和文件上限、默认后端。集合稳定排序。 |
| `bridge_policy` | 加载并规范化后的版本 4、默认目标/后端，以及通过原有解析器得到的命名 ExecutionTarget 全部选定字段。 |
| `worker_requests` | 通过原有 resolve_worker_policy 得到的 Codex / Copilot 模型、推理强度、执行模式、沙箱/审批/网络请求和工具策略。它们是请求值，不代表后端实际执行了这些限制。 |

监督进程从明确的合成输入直接构建期望对象，并写出相应的普通配置文件，不在自身
进程中加载维护者 `.env` 或修改全局环境。子进程在专门构建的清洁环境中运行既有
加载器；`.env` 加载后再次验证传输策略，再捕获真正加载的对象。子进程获得普通
启动参数，不获得父进程的预期身份摘要。

捕获时将 AgentSettings 内部可变集合转为 `frozenset`；解析后的目标与 worker
策略使用不可变值，Bridge 字典视图为独立副本。受管 controller 接收这一快照，
使用其中选定的状态目录、目标、设置和策略，并把同一上下文提供给采集器。
普通构造器及非受管路径的配置重载语义保持原样。

**不在投影内：**凭据、凭据是否存在及其哈希；Git 用户名和作者身份；任意环境
变量 / YAML 项（包括旧 `control`）；原始 `.env` 字节；CA 文件内容；仓库中的
`.actualcoder.yaml`；恢复会话的策略；提供商及全局 Codex 配置；实际沙箱/网络
执行证据；本地 CLI 子进程的设置；后续工作准入。Agent 上限表示加载值，不宣称
所有下游组件经过截断后的有效上限都已观察。关闭工具准入保证本上下文不会开始
这些尚未验证的工作。

## 监听器生命周期

当前适配器只支持已核查的 **MCP 2.3.0 / Uvicorn 0.54.0** 组合。其他组合明确
拒绝，不自动安装、升级或降级依赖。仓库既有依赖范围与 CI 策略不变。新增支持
组合需要重新审阅源代码、生命周期以及原生测试结果。

Uvicorn 会先执行 ASGI lifespan，再开始监听，因此 lifespan 回调不能证明端口
已经监听。受管路径复用共享 MCP server 的 ASGI 应用，并使用 Uvicorn 的
`serve(sockets=...)` 接口：

1. 父进程排他绑定指定的固定环回端口，保留 socket，但不调用 `listen()`，也不
   释放端口后再交给子进程竞争。
2. 只向本次新建的子进程显式传递监听 socket 和两根匿名管道所需的文件描述符。
3. 子进程立即禁止这些句柄继续被 exec 继承，独立检查实际运行环境及配置，然后
   创建共享核心。
4. Uvicorn 的真实 `startup()` 返回后，检查实际 asyncio.Server、serving 状态
   和 socket 身份，随后才发送启动回复。
5. 父进程通过内核查询保留 socket 的监听状态，并检查自己持有的子进程存活状态。
   独立且由本次尝试拥有的辅助进程检查精确 MCP 端点和目录；父进程监视两个进程，
   随后再次检查监听器。已占用或伪装端口会导致失败，不停止原监听者。

各平台使用其支持的内核查询，在子进程启动前和实际开始监听后检查同一个自有描述符：

| 平台 | 内核查询 | 启动前 | 开始监听后 |
| --- | --- | --- | --- |
| Linux | `getsockopt(SOL_SOCKET, SO_ACCEPTCONN)` | 必须为 `0` | 必须为 `1` |
| macOS | `getsockopt(IPPROTO_TCP, TCP_CONNECTION_INFO, 1)` | 恰好一个字节：`TCPS_CLOSED`（`0`） | 恰好一个字节：`TCPS_LISTEN`（`1`） |

macOS 虽然定义了 `SO_ACCEPTCONN`，但不支持该查询。公开的 `TCP_CONNECTION_INFO`
在第一个字节返回内核 TCP 状态；只请求这个字节，无需依赖其余结构布局。缺少查询常量
会在副作用之前明确拒绝；查询失败、结果格式错误或其他状态都不能作为监听证据。
不会用健康检查或目录结果代替不可用的 socket 证据。英文规范列出了固定版本的 Apple
XNU 一手源代码，包括公开字段、TCP 状态、监听转换、查询实现及有界复制规则。

HTTP 探针关闭环境代理，拒绝偏离精确端点的重定向，并限制响应字节和总耗时。
目录验证采用单独、明确的回复后时限，不延长消息协议原有的八秒有效期。
版本及一手源代码链接见英文规范。

目录辅助进程以隔离模式运行监督进程所用的已安装解释器，加载同一套普通合成配置，
并检查 import 来源。它不继承启动管道或监听 socket，也不接收预期身份摘要。
SDK 日志和全局日志设置限制在该辅助进程内，普通 stdout/stderr 被丢弃。父进程
只接受固定退出结果，并要求辅助进程已回收且 controller 清理得到确认。
跨越该边界时，具体目录或网络错误合并为公开的 `catalog_probe_failed` 类别；
辅助进程的 controller 清理错误仍单独保留，具体目录错误的子原因不跨进程传递。

## 私有通道与资源归属

每个消息为四字节无符号大端长度、1–16,384 字节负载，然后 EOF。只接受一个帧；
使用非阻塞读写，在 I/O 期间限制内存和时间，最长每 50 ms 检查一次。超长、截断、
尾随字节、缺失 EOF、卡住的读写、取消、断管、子进程提前退出以及刚好到期均失败。

绝对单调时钟截止点在启动子进程前建立，不晚于协议尝试开始，不通过重试续期。
预期 PID 来自保留的 Popen 对象，不来自回复或 PID 文件。失败会消费一次性
challenge。消息不放入 HTTP、普通 stdout/stderr、日志、argv 或持久通道路径；
argv 只包含文件描述符编号和普通启动参数。

清理仅关闭本次句柄、结束并回收本次服务器子进程和目录辅助进程、移除新建的
一次性配置区域。原始错误与清理错误分别保留；清理完成前不报告成功。若在监督
进程请求关闭前观察到服务器已退出，即便退出码为零也失败。启动前、目录观察后
以及最终成功判定前均检查取消。强制 kill 不构成 controller 正常清理的证据。
不宣称已实现所有后代进程的隔离或抵抗同用户恶意进程。

## 运行环境身份与证据边界

父进程使用既有 prepared-runtime 校验器和选定 manifest。子进程从实际
`sys.prefix` 推导运行环境，检查实际安装包集合 / import 来源，并指纹化真正调用
的解释器及保留的 base Python。保留 venv 调用路径与 pyvenv.cfg 指纹，不把
Windows 源码 fixture 的 base-executable 选择方法移植为生产启动方案。
任何缺失、不一致或漂移都失败，不使用占位摘要或较弱解释器回退。

运行环境 / manifest 标识与解释器、磁盘树指纹表示所选制品和变化检测，不是
运行内存或二进制的独立认证。venv 仍依赖外部 base Python，不能据此删除或替换它。

私有通道、子进程观察、选定策略采集、内核监听状态、端点 / 目录比较及清理结果
分别报告。原消息 codec 和 43 个既有协议测试保持不变；codec 的
`peer_identity_verified`、`running_code_verified`、`effective_configuration_verified`、
`managed_startup_confirmation_verified`、`compatibility_verified`、
`activation_authorized`、`ready_for_activation`、`service_changed` 仍为 false。
新增观察不改写 codec 的结论，也不清除旧服务观察 / pairing 的兼容性阻断。

维护窗口、空闲准入、进行中的工作与审批、状态 / schema 兼容、旧 `/control`、
持久恢复和回滚、客户端重连以及人工授权仍属于后续阶段。

安装测试分别从干净 wheel 和单独准备的 runtime 启动监督进程，在源码目录外、
使用已准备的子运行环境验证两种模式。源码测试、Linux 安装测试、原生 macOS CI、
Windows 明确不支持边界、PR CI 和合并后 CI 必须按精确源码身份分别报告。
Windows 在访问文件系统、socket 或进程前拒绝本 POSIX 适配器；既有可移植
codec 和 HTTP 安装测试仍需执行。

原生测试失败时，在测试自身的错误分类之外，单独保留探针的固定
`probe_error_code` 和 `probe_cleanup_error_code`。只有允许列表内的错误码能够输出；
任意值、身份声明、异常文本及子进程 stderr 均不回显。成功报告必须将这两个字段设为
null，包括通过预期的端口占用及伪装监听器负向测试之后。

[消息协议](STARTUP_CONFIRMATION_CN.md) · [运行环境准备](RUNTIME_PREPARATION_CN.md) ·
[English](DISPOSABLE_STARTUP.md)
