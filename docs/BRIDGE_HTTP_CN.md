# 打包的 Bridge HTTP 入口

**开发源码功能，不在已发布的 v0.5.1 wheel 中。** 这是 B2 的 HTTP/共享核心部分，
不是并行运行环境准备、旧服务切换或完整升级器。原有 `reasonfirst-bridge-mcp`
仍为 stdio；不改写任何既有启动器。

## 支持边界

`reasonfirst-bridge-http` 复用 `gitlab_agent.bridge_mcp.build_server` 及打包的
controller，不复制旧服务器，不依赖 PYTHONPATH sidecar。必须明确给出固定端点及
`read-only` 或 `full-chat`。二者是 Bridge 工具面的权限模式，不是独立的只读 GitLab MCP app。
完整模式的工具及注解与正常打包 Bridge 相同；只读模式与其严格兼容模式相同。
两者都不暴露实验性远端 push 授权或 `/control`。

**需要旧 `/control` 路由的部署不能直接换成此入口。** 即使没有正在使用的 relay，
旧 HTTP 服务也可能暴露该路由，迁移不能悄悄删除它。`--control-policy legacy`、
不一致的权限环境变量、启用的实验性远端 push，都会在构造 controller 前被拒绝。
保持原部署，等待单独审阅的兼容实现。[deployment adopt](DEPLOYMENTS_CN.md)
保存的注册快照，也不能证明实际端点或 control 策略与新入口兼容。

主机仅接受字面量 `127.0.0.1` 或 `::1`，不支持通配绑定或域名；端口必须为 1..65535，
不接受动态端口 0。路径仅允许以 `/` 分隔的 ASCII 字母、数字、下划线、连字符；
拒绝尾斜杠、查询、片段、百分号编码、点路径，以及以 control/healthz 为首段的路径。
不支持的输入直接失败，不自动改成另一个端点。

本入口**不增加 HTTP 身份认证**。回环绑定不能隔离同机用户或进程，完整 Bridge 是高权限面；
远程访问仍需独立保护的认证传输。本功能不配置隧道、app、授权项目、令牌或服务管理器。
不要同时启动两个服务使用同一份在线 Bridge 状态目录。

## 只检查，不启动

端点和权限参数都必须明确给出。下例仅描述输入，不发现或验证现有部署：

```bash
reasonfirst-bridge-http --inspect --host 127.0.0.1 --port 8765 --path /mcp --mode read-only --control-policy disabled
```

`--inspect` 输出一个 JSON 对象，不导入 controller/MCP SDK，不读应用配置或状态，
不建目录、不探测监听器、不执行命令。`configuration_inspected`、
`package_integrity_verified`、`server_started`、`ready_for_activation` 均为 false。
它不是 deployment plan、运行环境清单、审批摘要或健康/认证证明。
`--help` 同样不需要配置或 controller。

## 前台运行是另一项操作

开发者明确用 `--serve` 替换 `--inspect`，并保留审阅过的端点和权限参数，才会前台启动。
没有默认动作，也不接受 restart/install/repair/doctor 选项。测试应使用临时配置和状态目录，
不要把工作中的 launchd 服务指向此开发命令。

启动会通过既有 controller 读取 Bridge 配置和状态，并可能创建或 chmod 状态目录。
**只读工具面不等于启动过程零文件系统操作。** 本功能没有替换 controller 的生命周期语义，
也没有实现升级事务或任务准入锁；不复制、伪造或改写配置引用和工作目录，但调用者仍须正确选择它们。

非空 `PYTHONPATH`/`PYTHONHOME` 被拒绝，不会被静默清除。应使用审阅过的包环境，
适用时采用隔离 Python 导入；参数检查不能证明 Python 启动前已导入的代码可信。
已有 `RF_MCP_READ_ONLY` 必须与显式模式一致；策略变量为空或不是合法布尔值时失败。
前台运行在构造 controller 前移除继承的隧道/管理凭据；只检查不改环境变量。

服务启动失败返回非零，并在 controller 可用时关闭它；清理失败也返回非零。
不终止无关监听器，不重试端口绑定，不调用服务管理器，不恢复状态，不声称回滚成功。
`/healthz` 保留既有存活信息，不证明外部客户端验收、监听归属、worker 执行或升级成功。

## 验证与下一步

测试区分静态参数/模拟注册和真实 SDK/HTTP。原生测试使用临时用户目录、测试端口，
分别在现代及旧客户端模式比较 HTTP 与共享核心的完整工具 schema，验证两种 Bridge 权限、
缺失的 `/control`，以及端口被占用时不停止原监听器；不启动 worker 或访问外部模型服务。
安装验收还在源码目录外，用干净 wheel 的解释器运行这些测试并检查模块确实来自包环境。
源码安装与打包安装仍是独立且受支持的路径。

这些是合成传输测试，不是现有 ChatGPT 连接、身份认证审计、服务管理器迁移、重启或回滚验收。
当前 CI 使用 Python 3.12，不表示包声明的全部 Python/SDK 范围都测过。
IPv6 仅验证参数接受；当前真实传输测试使用 IPv4 回环地址。

独立的内部 [Managed HTTP owner](MANAGED_HTTP_SERVICE_CN.md)添加 controller
准入、真实 listener/task 所有权，以及关闭 subscriptions 的现代 JSON 请求
profile。它是 Python 嵌入式 API；本前台命令保持既有启动与协议行为。

[并行运行环境准备](RUNTIME_PREPARATION_CN.md)在独立的最终路径绑定审阅过的
wheel、解释器与已解析依赖。它与 managed HTTP owner 仍是不同组件；后续
服务切换之前，需要共同确认端点、应用配置、客户端/control 兼容性、工作
所有权与持久恢复能力。

[English](BRIDGE_HTTP.md) · [安装/更新](INSTALL_CN.md)
