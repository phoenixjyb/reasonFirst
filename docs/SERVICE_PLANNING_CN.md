# 服务清点与计划（Slice 1）

Slice 1 增加只读服务证据和确定性的登录启动计划：

```text
reasonfirst service status --json
reasonfirst service plan --autostart login --json
reasonfirst service repair --dry-run --json
```

状态输出会分别报告已安装的 Python 包、服务定义、setup 元数据和可能的
部署角色。清点范围仅包括已知的 macOS ReasonFirst 静态定义。定义文件或记录的
运行时引用不能证明系统服务已注册、进程正在运行、进程归属明确、服务已就绪，
也不能证明端到端验收通过。未执行的实时观察保持为 `not_checked`，不会伪造时间戳。
Linux 和 Windows 原生适配器仍为 `not_inspected`；不支持的平台会明确报告。
旧版 `ownership_verified` 字段只表示已检查的静态定义文件元数据，不代表运行时权限。

计划会保留已知部署拓扑，并列出拟议的操作系统所有者、前置条件、阻塞项、交接证据
和未来回滚要求。Slice 1 始终返回阻塞、不可执行的计划。后续经授权的工作需要确认
身份、所有权、稳定运行时、配置、凭据引用及平台证据。stdio 模式下，MCP 子进程仍由上游隧道运行时拥有；HTTP 模式下，
Bridge 与隧道保持为不同角色。规划器不会根据静态或记录的角色推断正在运行的实例，
也不会声称已通过原生平台验收。

本 Slice 的命令均不修改系统：不会安装或控制服务、修改 setup/configuration、读取
凭据内容或执行计划。缺少经审查的凭据引用元数据时，问题仍作为阻塞项。若 Windows
无法提供所需的防跟随及文件描述符保证，安全状态读取器会报告不支持。源代码检出目录
不代表稳定的已安装运行时。

退出码：证据收集完成时 `status` 返回 0，即使个别证据无效或不可用也一样。被阻塞的
计划或 dry-run 修复返回 1。未带 `--dry-run` 的修复、启动前运行（boot/unattended）
以及其他不支持的参数返回 2；请求 JSON 时错误内容会经过脱敏。本 Slice 尚未实现
实际修复或服务生命周期命令。

收集或规划过程中出现内部故障时，退出码为 1，固定错误码为
`service_inspection_failed`。这与被阻塞的计划不同：后者具有
`plan_status: blocked` 和证据，且没有顶层 error。人类可读输出仅为摘要；
请使用 `--json` 查看证据状态及固定的读取原因码。观察到文件消失或目录被替换
时，读取器会丢弃字节并报告证据发生变化。父目录检查比较目录身份，允许无关的
同级文件活动；这些一致性检查不构成原子文件系统快照。

English: [Service inventory and planning](SERVICE_PLANNING.md).


## Slice 2A：显式触发的原生服务管理器只读观察（未发布）

默认 service status 仍只收集静态证据，不调用系统管理器。需要原生只读
观察时显式执行：

    reasonfirst service status --native --json

每个操作系统仅查询两个固定的候选标识：macOS 图形用户域的
com.reasonfirst.v4-mcp 与 com.reasonfirst.v4-tunnel；Linux systemd
用户实例的 reasonfirst-v4-mcp.service 与 reasonfirst-v4-tunnel.service；
Windows ReasonFirst 任务目录中的 V4 MCP 与 V4 Tunnel。这些标识只是
查询目标，不证明已有服务采用此名称。“未在该标识找到”不等于全机
没有服务。GUI 域、systemd 用户管理器或 Windows 计划任务模块不可用
时，应报告 unknown/unavailable，不能当作健康通过。

不会返回原生命令输出、已安装命令的参数、任意路径、环境变量值或凭据。
管理器状态不等于进程身份、运行时归属、健康状况、版本、真实登录启动或
端到端连接证明。登录设置仅提供配置线索。不扫描进程，不接管、安装、
注册、启动、停止、修复或修改服务，不读取凭据。查询超时最多清理其自身
短生命周期查询子进程，而不是 ReasonFirst/tunnel 服务。计划依然阻塞
且不可执行；真实机器的归属核验与平台验收仍需独立完成。

原生诊断使用受限的系统可执行文件路径，而不是通过 PATH 查找工具。
Linux 只传递当前用户 /run/user/UID 对应的运行时目录和匹配的本地
Unix D-Bus 地址；缺少或非标准会话总线时报告未知/不可用，不虚报健康。
Windows 通过系统 API 查询系统目录中的 PowerShell，不合成登录环境。
