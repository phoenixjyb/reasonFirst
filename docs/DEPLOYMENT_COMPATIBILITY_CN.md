# 检查已知旧 HTTP 启动源码与保存的策略

**开发源码功能，不在已发布 v0.5.1 wheel 中。** 这是[静态配对](DEPLOYMENT_PAIRING_CN.md)
之后的检查：读取具体的已知启动源码和所选 Bridge YAML，而不是凭路径或版本号认定兼容。
不执行被检查的文件，不访问运行中服务，不修改注册、不激活新运行环境。

## 只读命令

先分别审阅登记和已准备的运行环境，再使用：

```bash
reasonfirst-runtime deployment-assess --runtime-id "REPLACE_WITH_RUNTIME_ID" --json
reasonfirst-runtime deployment-assess-check --runtime-id "REPLACE_WITH_RUNTIME_ID" --expect-digest "REPLACE_WITH_ASSESSMENT_DIGEST" --json
```

将占位符替换为实际 runtime ID 和本次 **assessment** 的摘要，不能使用 pairing/adoption/prepare
摘要代替。不接受 `--yes`、`--force`、`--activate`，不自动修复或改写配置。返回前重新验证
配对、所选源码、plist 和配置；失败不返回部分证据或有效摘要。摘要只是变更检测，
**not a signature**（不是签名），也不是锁或重启许可。多次读取不是原子快照，不防 ABA
变化，也不是针对恶意同用户进程的安全隔离。

## 支持的源码形式

仅支持 macOS；Windows/Linux 在寻找 HOME 或读取存储前拒绝；原有 Linux runtime preparation
和跨平台安装路径不受影响。当前检查已知的 legacy staged HTTP 目录：
`~/.local/share/reasonfirst/v4-service/tools/codex_web_bridge`。
两个启动脚本、`set_local_no_proxy.sh`、HTTP server 必须匹配审阅过的源码指纹。
controller/config 可为已知完整实现，也可为精确的转发模块及对应 staged 完整实现。
目标 runtime 必须通过既有完整目录和解释器校验，其 HTTP 入口、MCP 核心、controller 和
配置解析模块也必须匹配审阅过的指纹；版本号相同不能替代源码身份。

未知或修改过的源码会停止检查。特别是一次性 `uv-http-v…` sidecar 虽可被登记及静态配对
识别，此检查仍返回 `unsupported_launcher_profile`，不猜测其 bootstrap 或可变 uv 导入布局。
它需要独立的源码适配器；本功能不能升级该 sidecar。不要为通过检查而改动工作安装。
指纹规则随实现变化必须另行审阅和测试；它不覆盖全部 Python 导入闭包，也不能证明后台
进程实际加载了这份保存源码。

## 保存的策略与配置

已知 shell 指定回环地址、`/mcp`、环境/默认端口和 v4 runtime 路径；保存的 plist 提供
`RF_MCP_READ_ONLY`、`RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH`、`RF_MCP_PORT` 及配置引用。
缺失项标记为 `source_default_if_not_inherited`，**不能当作 launchd 或运行中进程的实际环境**。
可能注入 shell/Python 代码的环境覆盖、不一致 HOME、错误布尔值/端口、HOME 外路径、
不安全文件对象都被拒绝。未知 plist 字段仍被整体摘要绑定，但不声称已解释其语义。

Bridge YAML 仅按保存引用或已知默认路径选取，允许 HOME 内绝对路径或 `~/` 路径。
通过私有、禁止跟随链接的读取核对全部字节。只检查已知 v3/v4 顶层 mapping 形状；
重复键、别名/锚点、自定义 tag、过大/过深及不支持结构被拒绝。输出版本、target 数量和
legacy control 配置是否存在，不输出具体值。**不验证 target 语义、状态迁移、项目凭据
或 worker 权限**。缺文件明确报告，不创建或认定有效。

**plist 和 Bridge YAML 自身也可能含凭据。** 它们被私下读取，但原文和任意环境值不输出。
不会打开 `.env`、token store、`auth.json`、`config.toml`、`control-token`、工作区或审批记录；
`credential_store_files_read`、`workspace_state_read` 保持 false。GitLab 配置引用不等于运行中
服务实际选择了哪个文件；输出含本地路径/指纹，应私下保存。

## 完整模式下实际存在的不兼容

已知旧 server 只要不是 read-only 模式，就暴露 `/control`，与 relay 是否正在使用或 control
YAML 是否为空无关。打包 HTTP 目标没有该路由，因此旧默认/显式 full-chat 返回
`legacy_control_route_not_supported_by_target`，不悄悄缩减接口。实验性 remote-push 环境 opt-in
也会阻塞目标，包括旧 read-only 模式下该工具实际未暴露的情况。

`ok: true` 仅表示**检查完成**。已知冲突显示 `assessment_status: blocked_by_declared_policy`。
显式只读策略可得到 `declared_surface_match: true` 与 `requires_live_compatibility_checks`，
但这只是保存策略的条件比较。不论哪种结果，**`compatibility_verified`、`ready_for_activation`、
`activation_authorized`、`live_service_verified` 始终 false**，没有待执行命令或写入计划。
已知旧启动器会修改服务管理器的 proxy 环境，此副作用被明确报告，不执行、不静默复制。

尚需验证已加载服务和继承环境、实际导入闭包/解释器、端点/认证/监听器归属、目标状态与工作区
兼容性、活跃任务及准入限制、受控切换和持久恢复；任何摘要匹配都不能替代这些要求。

## 测试边界

单测使用真实私有 POSIX 文件及保留的源码，运行环境创建仍为模拟。macOS 安装测试则在真正
已准备的 runtime 内，用隔离 Python 运行新命令，使用临时保存注册、源码和 YAML，核对
`/control` 阻塞、匹配摘要复查、配置/源码变更拒绝以及检查前后文件不变。不会加载 LaunchAgent、
启动 worker 或访问维护者机器。原生测试不等于真实部署、身份认证、激活或回滚验收。

[English](DEPLOYMENT_COMPATIBILITY.md) · [运行环境准备](RUNTIME_PREPARATION_CN.md)
