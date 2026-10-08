# 审阅已保存的 HTTP 启动配置

**开发源码功能，不在已发布 v0.5.1 wheel 中。** 本功能在[部署与运行环境配对](DEPLOYMENT_PAIRING_CN.md)
基础上检查实际启动器源码及保存的端点/权限信息，不是服务切换或完整兼容性验收。
不要为运行此审阅而更新工作中的安装。

## 检查内容

先重新验证调用者给出的配对摘要，再检查 macOS 两类明确的输入：旧
`v4-service/tools/codex_web_bridge` 启动链（入口、HTTP 启动器、代理辅助脚本、服务器），
以及首次维护迁移产生的 v0.5.1 隔离 HTTP sidecar（规范引用的 shell 入口、固定 bootstrap、
stage 清单和五份兼容源码）。仅有熟悉的路径不够，实际字节必须匹配已审阅的源码指纹。

不执行脚本、bootstrap、controller、解释器、shell、launchctl、HTTP/MCP 或包管理器。
`tests/fixtures` 中的 bootstrap 只是惰性的测试输入，不被安装，也不用于生成新启动器。
目标运行环境须先通过完整文件树验证，其 HTTP 入口和共享核心也须匹配明确的无 control
源码配置；不能仅凭版本号判断功能。新增源码配置需代码审阅，不提供“强制信任”参数。

读取保存的 `RF_MCP_PORT`、`RF_MCP_READ_ONLY` 和实验性 push 策略，不修改它们。
明确记录的配置、状态及旧运行环境路径仅作为引用返回，不打开其内容；当前只接受本用户目录下
无歧义的绝对路径。缺少变量时保留未知，因为 launchd 可能提供继承环境，不能据此声称使用了默认值。

已知旧服务器在 full-chat 模式暴露 `/control`，已知打包目标没有此路由，因此保存的完整模式
明确产生 `legacy_control_not_supported_by_target` 阻塞项。只读模式没有 control 或远端 push。
未知模式不能当成只读；不会探测、关闭或自动增加 `/control`。旧代理辅助脚本会改变 launchd
环境的事实仅被报告，不会运行该脚本。sidecar 清单的包指纹声明不是当前导入核心的证明。

## 使用

先从新的 deployment-plan 取得匹配的配对摘要。下方参数是占位说明，不可直接执行：

```text
reasonfirst-runtime launch-plan --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --json
reasonfirst-runtime launch-check --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

两条命令均只读，不保存计划。新的摘要绑定原配对、实际源码字节/权限、适用的 sidecar 清单、
保存的策略和目标 HTTP 配置。launch-check 重复检查并匹配新摘要，配对摘要不能代替启动摘要。
不接受安装、重启、修复或强制审批参数。

`ok: true` 只是检查成功，不是解除所有阻塞。`launcher_sources_verified` 只描述指定源码，
`static_control_compatible` 只比较静态 control 路由；`compatibility_verified`、
`activation_authorized` 和 `ready_for_activation` 始终为 false，`proposed_actions` 为空。
失败时不返回半份身份/摘要或原始解析错误。报告含本地路径和指纹，应私下保存。

## 尚未检查的内容

服务管理器已加载定义、实际继承环境、当前导入的应用核心、应用配置内容和状态 schema
仍为 **not_inspected**。这不是维护锁、原子快照、签名、重启许可或恶意同用户进程的隔离。
重复读取可以发现观察窗口中的变化，不能证明没有 ABA 变化。

私下读取的 plist/sidecar 清单可能包含环境值，但不会输出或持久化其原始内容及无关环境值；
不打开 `.env`、`bridge.yaml`、工作区/审批状态或凭据文件。识别了源码不等于服务一定能启动，
也不证明导入的完整依赖链。实际配置/状态兼容性、任务准入、受控激活和持久恢复仍是后续工作。

当前仅支持 macOS 配对/启动审阅。Linux 运行环境准备和 Windows 打包/源码安装支持不变；
本命令在不支持的平台上于访问用户目录前拒绝。测试区分 POSIX 惰性文件样本和原生 macOS
新准备运行环境中的 CLI 验收，不以维护者在线服务为目标，不声称已完成 launchd 迁移。

[English](LAUNCH_REVIEW.md) · [HTTP 入口](BRIDGE_HTTP_CN.md)
