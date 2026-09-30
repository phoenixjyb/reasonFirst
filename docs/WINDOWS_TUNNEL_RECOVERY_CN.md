# Windows 隧道启动恢复——v0.5.1 测试候选包

[English](WINDOWS_TUNNEL_RECOVERY.md) · [安装](INSTALL_CN.md)

原候选包给 tunnel-client v0.0.15 传入 MCP 命令时可能丢失 Windows 路径反斜杠；
按系统区域设置解码又用 `UnicodeDecodeError: gbk` 与 `NoneType ... replace`
二次异常掩盖原始错误（#88）。修正包按接收端解析器转义，在所有系统以 bytes 捕获
原生输出，严格解析 UTF-8 JSON，并单独处理诊断。只读 MCP 与 Bridge 共用修正路径。

## 保留已有状态

不要重新创建 Windows tunnel、复制 Mac 配置、更换有效 token、添加 admin key、
关闭 TLS 或删除已有 profile。安装经过审查与校验和验证的替代候选包后，移除会话级
临时诊断设置，再查状态：

```powershell
Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
reasonfirst tunnel status --alias reasonfirst-gitlab --json
```

`status_query_ok` 区分查询失败与成功查询到停止状态。检查 `runtime_state` 和有界、
脱敏的 `diagnostics`。诊断时用 JSON；原普通摘要不足以解释查询错误。查询失败不代表
可以安全再启动一个进程。

确认同一个 Windows alias 已停止后，重连它：

```powershell
reasonfirst tunnel connect --alias reasonfirst-gitlab
```

在提示中填写已有 Windows 只读 tunnel ID，只在隐藏输入提示中填写有效 runtime key。
ID 也可通过 `--tunnel-id` 提供，但 key 不能放入命令参数。该命令更新本地 runtime
profile，不重建远端账号资源；保留 GitLab 配置，仅在 readiness 成功后记录 tunnel
阶段完成。若原向导在记录该阶段之前中断，`setup --repair` 无法猜测缺失的身份，
应先显式 `tunnel connect`。若进程在运行但状态未记录，应先核对身份与状态，不要盲目
再启动。保持 Mac 连接与高权限 Bridge 连接独立。

## 仍须现场验收

子进程回归与固定版本上游解析器/原生进程启动 CI，不是实际 tunnel/provider 测试。
在 Windows 上移除 UTF-8 临时设置后验收修正包，再按
`reasonfirst chatgpt handoff --open` 指引，仅选 Windows app 执行一次新的实际读取。
本地健康不代表 ChatGPT 项目访问成功。正式发布仍须等到现场演练及重启/恢复验收通过。
