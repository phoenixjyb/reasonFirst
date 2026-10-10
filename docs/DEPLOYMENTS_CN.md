# 登记既有部署的服务注册快照

**v0.5.2 包含此功能；v0.5.1 wheel 不包含。** 这是部署身份管理的第一步，
不是完整升级器；原有[安装与更新指南](INSTALL_CN.md)继续适用。

## 登记范围

`reasonfirst deployment` 只针对 macOS 用户级 `com.reasonfirst.v4-mcp` LaunchAgent
的**已保存注册文件**，识别旧 `v4-service` 和版本化 `uv-http-vX.Y.Z-…` HTTP sidecar
启动器布局。不搜索其他服务。Windows/Linux 在访问文件系统前返回
`unsupported_platform`，不猜测 Windows service 或 systemd 布局；其他 macOS 布局也不登记。

发现和登记分开。`reasonfirst status` 与 `setup --status` 仍不写入任何部署记录。
新登记文件独立于向导的 `setup.yaml`，不伪造已完成的配置阶段，也不替代它；
用 `deployment status` 查看新记录。

记录严格采用版本化 schema，只保存已知标签、注册路径与 SHA-256、启动器和工作目录、
静态传输布局、审阅摘要、登记时间和登记 CLI 版本。**登记 CLI 版本不是运行中服务版本。**
运行环境、应用配置绑定、凭据有效性、监听器归属和健康状态仍为 `not_inspected`；
`runtime_id` 与 `running_version` 为 null，`activation_authorized` 为 false。

完整 plist 在本地读取，可能包含环境值；摘要绑定全部字节，但原始环境值、额外参数和
plist 内容不输出、不复制到登记记录。不会读取被引用的脚本、`.env`、`bridge.yaml`、
setup state、凭据、工作区或审批状态，不调用 launchctl、HTTP、MCP、worker、包管理器或外部 API。
路径及 `streamable-http` 仅为静态布局证据，不是实际传输或代码验证。
摘要用于发现变更，不是签名、认证凭证或未来的重启授权。报告含本地路径和指纹，应私下保存。

## 审阅、显式登记、检查

使用包含本功能的开发构建：

```bash
reasonfirst deployment plan --json
```

该命令只输出计划和 `plan_digest`，不保存计划或创建目录。审阅服务、注册文件、启动器、
工作目录和登记位置；无效、不支持或记录冲突则停止。把审阅过的摘要替换下方占位符：

```bash
reasonfirst deployment adopt --expect-digest "REPLACE_WITH_REVIEWED_DIGEST" --yes --json
reasonfirst deployment status --json
```

不要直接执行占位符。这里的 `adopt` **只表示登记这份服务注册快照**，不接管运行环境、
应用配置或隧道，更不启用服务。必须同时提供匹配的摘要和 `--yes`；写入前重新检查源文件，
未输出的 plist 字段变化也使原审批失效。完全相同的既有记录直接复用，保留字节和时间戳；
即使有 `--yes` 也不能覆盖其他记录。

唯一持久写入是新增记录及缺失的私有父目录（创建中有临时兄弟文件）：

```text
~/.config/reasonfirst/deployments/com.reasonfirst.v4-mcp.json
```

检查所有权和 POSIX 权限，拒绝符号链接、非普通文件、硬链接及不可信目录。
私下写入后以“仅在不存在时创建”的方式发布，不覆盖竞态中先成功的记录，不 chmod 已有目录。
这不等于针对拥有完整用户目录访问权的恶意同用户进程提供安全隔离，也不声称检查 macOS ACL。

## 结果和失败处理

`not_recorded` 只表示登记缺失，不代表服务不存在。`matches_record` 表示本次检查的保存文件
匹配；`drifted` 表示 plist 已变且旧记录仍保留；`unavailable_or_unsupported` 表示无法完成
当前比较。状态检查 `ok: true` 不代表服务健康；`ready_for_activation` 一直为 false。
服务管理器**已加载**的定义可能不同于保存文件；此命令不检查它。脚本路径不变而代码被修改
也需后续运行环境审阅，不能用这里的快照证明未变。

登记错误中的 `record_written: null` 表示最终 flush/readback 失败后不能断言是否创建成功。
先用 `deployment status` 检查，不盲目重试。强制结束或断电可留下私有 `.pending-…` 文件或额外
硬链接；本功能不自动删除、覆盖、恢复或回滚。部分或无效记录会被拒绝，保留供审阅，
不要为得到成功结果而删除证据。期间从未停服务，登记修复不需要重启；应用配置、setup 记录、
工作区、发布资源不变。

## 后续边界

后续运行环境准备仍须独立检查配置引用、包/依赖/解释器身份、传输、端点与权限兼容性。
登记快照只是需重新验证的一项输入，不赋予执行权。并行准备运行环境、打包 HTTP 入口、
维护门控、服务切换、恢复和回滚都**不在本命令中实现**。不必为测试登记而更新或重启工作机器。

另见独立的[打包 HTTP 入口](BRIDGE_HTTP_CN.md)：它仅为开发期共享核心传输包装，
不使注册命令成为升级器；需要旧 `/control` 路由的部署仍不兼容。

[English](DEPLOYMENTS.md)

[与候选运行环境联合只读检查](DEPLOYMENT_PAIRING_CN.md)
