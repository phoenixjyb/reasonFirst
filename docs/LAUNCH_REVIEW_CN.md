# 只读启动器兼容性审阅

本功能 B3b 是开发分支的新功能，不在已公开的 v0.5.1 wheel 中；接续部署／运行环境配对，不执行服务迁移。

```text
reasonfirst-runtime launch-plan --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --json
reasonfirst-runtime launch-check --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

第一条命令重新校验 macOS 已保存的部署登记和 prepared runtime，核对四个指定旧启动器源码及目标 HTTP/MCP 包源码的固定指纹，并只读解析特定保存的策略字段。第二条命令重复检查并要求精确的启动审阅摘要。两者均不写入配对关系，也不启动、停止或重启服务，不接受审批与强制参数。

旧 HTTP 服务在 full-chat 模式暴露 /control，而新的打包 HTTP 入口不提供该路由。因此完整模式会报告阻塞项，不会偷偷去掉权限。未记录的策略与实际继承环境保持未知。已有版本化 sidecar 布局能够分类，但其生成的 bootstrap 在本阶段尚未独立审计，不得视为源码验证通过。

plist 可能包含环境变量值；输出只包含受限的端点／模式／策略字段及指定配置引用变量是否存在，不读取也不回显引用的 .env、bridge.yaml、setup.yaml、凭据、工作区或审批状态。也不调用 launchd、Bridge controller 或外部服务。

审阅成功仍返回 compatibility_verified=false、ready_for_activation=false、activation_authorized=false。正在运行的程序及生效环境、状态 schema、活动任务、维护准入、受控切换、持久恢复与原客户端回连均需后续验收。重复读取不是原子快照；哈希不是签名。

部署配对目前仅支持 macOS；Linux 的独立环境准备与 Windows 的包安装／HTTP 测试不受影响，但不代表已实现全平台服务迁移。

[English](LAUNCH_REVIEW.md) · [部署配对](DEPLOYMENT_PAIRING_CN.md) · [HTTP Bridge](BRIDGE_HTTP_CN.md)
