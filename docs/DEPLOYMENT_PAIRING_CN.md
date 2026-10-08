# 联合检查已有部署登记与候选运行环境

**开发源码功能；已发布的 v0.5.1 wheel 不包含这些命令。** 本阶段 B3a
把部署登记与离线运行环境准备的身份信息合并为一个可复查摘要，
不代表二者兼容，也不会切换任何服务。

## 输入与平台范围

当前联合检查仅支持 **macOS** 的已知用户 LaunchAgent
`com.reasonfirst.v4-mcp`。需要已经存在且仍匹配保存 plist 的
[部署登记](DEPLOYMENTS_CN.md)，以及通过完整 runtime ID 明确选择、
已准备且未发生变化的[候选运行环境](RUNTIME_PREPARATION_CN.md)。
缺少输入时不会自动登记、修复、下载、安装或创建关联。

Linux 和 Windows 在查找 HOME 或读取存储前返回
`unsupported_pairing_platform`。此限制不影响既有 macOS/Linux 运行环境准备，
也不撤销 Windows 的包安装与源码安装支持。本阶段不推断 systemd、
Windows 服务/ACL 或其他部署布局。

## 检查与复查

使用包含此功能的开发版本 CLI。把下面大写占位符替换为实际检查过的标识，
不要填版本号、分支名或旧部署登记摘要。

```bash
reasonfirst-runtime deployment-plan --runtime-id PREPARED_RUNTIME_ID --json
reasonfirst-runtime deployment-check --runtime-id PREPARED_RUNTIME_ID --expect-digest PAIRING_PLAN_DIGEST --json
```

`deployment-plan` 校验保存的登记与当前磁盘 plist 是否匹配，复用完整的
运行环境文件树及基础解释器校验，并要求准备环境的平台与架构匹配当前机器。
返回前再次检查两组证据；不写计划文件、不保存关联、不修改原登记中为空的
`runtime_id`。

新的 `plan_digest` 绑定独立的版本化 scope、HOME、平台/架构、登记文件原始字节、
当前登记快照及其摘要、所选 runtime ID、runtime.json 原始字节及 manifest 摘要。
manifest 继续绑定包/依赖/解释器身份与安装文件树。元数据只改空白也会使联合摘要失效。
单独的 runtime 摘要或登记摘要不能替代联合摘要。哈希是变更检测，**not a signature**，
不是签名、来源可信认证或权限令牌。

`deployment-check` 重做全部只读检查，要求与先前审核的联合摘要完全一致。
不接受 `--yes`、`--force`、启动服务或修复选项。复查不持有维护锁，
也不批准执行；后续有副作用的阶段仍须重新检查并取得明确授权。

## 成功不代表可以升级

成功检查返回退出码 0、`ok: true` 和 `pairing_verified: true`，但始终保留：

```json
{
  "compatibility": "not_established",
  "compatibility_verified": false,
  "legacy_control_requirement": "unknown",
  "ready_for_activation": false,
  "activation_authorized": false,
  "live_service_verified": false,
  "commands_executed": false,
  "mutating": false,
  "proposed_actions": []
}
```

输出列出七项未解决的前提：磁盘登记与已加载服务是否一致、当前启动器与配置绑定、
端点/模式/工具权限、旧 `/control` 是否必需、目标配置/状态兼容性、活动任务准入，
以及受控激活与持久恢复。这些是阻塞项，不能通过本命令忽略。

磁盘 plist 不等于 launchd 已加载定义；可识别的启动路径不证明运行代码身份。
通过包导入检查也不证明端点、权限或状态处理与旧服务一致。
当前目标 HTTP 包装器不实现旧 `/control`，不能据此推断旧服务不需要该路由。
这里不检查真实服务健康、客户端、worker、provider、重启恢复、切换或回滚。

## 失败与证据边界

缺失登记/manifest、登记变化、安装树或基础解释器变化、主机不匹配、无效或不安全
元数据、审核摘要变化均返回退出码 1。失败不输出部分身份或可复用摘要；
错误使用固定分类，不回显原始异常、环境值或源文件内容，不更改现有文件。

继续使用已有的所有者/权限、no-follow、硬链接限制。仅检查已知登记与 plist 路径，
以及明确选择的运行环境。不读取启动脚本、应用 `.env`、`bridge.yaml`、`setup.yaml`、
凭据库、workspace/task/approval 状态或真实服务。plist 可能带有环境值：
所有字节参与哈希，但输出仅包含已有校验快照字段，不包含环境值。
报告仍包含本地路径和摘要，请私下保存。

重复读取用于发现检查期间观察到的变化，不是原子快照、ABA 防护、同用户恶意进程
隔离或持久事务；检查结束后文件仍可能变化。源码测试使用临时真实记录和模拟准备执行；
原生 macOS 安装测试另外使用真正准备的运行环境运行新 CLI，配合临时合成的
LaunchAgent 登记，不加载该服务。Linux/Windows 验证明确的不支持边界。

[部署登记](DEPLOYMENTS_CN.md) · [运行环境准备](RUNTIME_PREPARATION_CN.md) · [English](DEPLOYMENT_PAIRING.md)

[下一步：已保存启动器与 HTTP 兼容性审阅](LAUNCH_REVIEW_CN.md)
