# 并行准备独立服务运行环境

**v0.5.2 包含此功能；v0.5.1 wheel 不包含。** 这是 B2b 离线包环境准备，
不是旧服务迁移或激活引擎；不要用此入口替换正在工作的启动器。

首个持久存储适配支持 macOS/Linux 本地 POSIX 文件系统。Windows 在文件系统访问前
拒绝 plan/prepare/status，待后续 ACL/reparse-point 适配。共享 HTTP 入口依然在三平台测试；
包可以安装不等于存在 systemd/Windows service 集成。

## 输入与审批

提供一个只含审阅过的 ReasonFirst wheel 及目标系统/Python 全部依赖 wheel 的目录，
每个 distribution 仅一个版本。不接受源码包、editable 目录、符号链接、硬链接和 URL 依赖。
下载并汇集完整 wheel 集合是独立开发/分发步骤，当前命令不实现；尚不是面向普通用户的
一条命令下载升级。包 SHA-256 来自审阅产物；plan 还绑定每个依赖的字节摘要。
摘要用于检测改变，**not a signature（不是签名）**，不证明依赖可信或对应某个源码 commit。

显式选择已有 uv 与基础 CPython 的绝对路径，不用 py、shell、PATH 版本选择器或工具虚拟环境。
准备后仍依赖该基础解释器；不会复制冻结整个标准库、安装 Python 或允许删除旧解释器。
新环境与 CLI 的可变 uv tool 包环境独立，但基础 Python 被替换后仍需重新审阅。

```bash
reasonfirst-runtime plan --wheelhouse /ABSOLUTE/REVIEWED_WHEELS --python /ABSOLUTE/python3 --uv /ABSOLUTE/uv --package-sha256 REVIEWED_PACKAGE_SHA256 --json
```

替换所有大写占位符。计划只读输入文件及元数据，不运行 Python/uv/controller/网络服务，
不建目录、不保存计划。plan_digest 绑定完整 wheel 集合、解释器/uv 指纹、路径、平台、架构及
目标 home。必须审阅全部依赖而非只看版本号；不需要业务配置或密钥。

```bash
reasonfirst-runtime prepare --wheelhouse /ABSOLUTE/REVIEWED_WHEELS --python /ABSOLUTE/python3 --uv /ABSOLUTE/uv --package-sha256 REVIEWED_PACKAGE_SHA256 --expect-digest REVIEWED_PLAN_DIGEST --yes --json
reasonfirst-runtime status --runtime-id REVIEWED_PLAN_DIGEST --json
```

prepare 同时要求 --yes 与精确摘要，重新检查后只新建
`~/.local/share/reasonfirst/runtimes/<摘要>/`，包含 intent.json、inputs、build-home、venv、runtime.json。
在最终路径创建 venv，不移动/复制既有虚拟环境。不改旧 runtime、CLI、服务注册、setup.yaml、
业务配置、凭据、连接、工作区或审批；不停服务，不启动新监听器。

uv 使用 `--no-config`、`--offline`、`--no-cache`、禁止下载 Python、禁止 index、只用 wheel、
精确版本与 `--require-hashes`，以 copy 模式安装，再运行 `uv pip check`。
没有网络重试、自动换 Python、源码构建或依赖版本兜底。子进程使用隔离配置/环境，
但依赖导入仍是执行代码，不是安全沙箱。

隔离 Python 探测核对完整已安装 distribution 集合、版本、包导入位置、console entry point、
MCP SDK 和 HTTP 调用签名，不构造 controller。仅证明导入/签名检查，不证明 HTTP 握手、
worker、旧客户端回连、身份认证或服务健康；CI 合成传输测试是另外一层证据。

## 状态和失败

runtime.json 私密、仅创建，记录输入与已安装环境指纹。runtime_id 是审阅输入身份，
manifest_digest 还绑定实际观察的安装树。status 不执行命令、不访问在线服务，检查安装文件、
允许的解释器链接和保留的基础 Python 指纹，区分 not_found、preparation_incomplete、
prepared_matches_record、drifted。状态查询成功不代表准备成功；prepared 为 true 也始终
ready_for_activation 为 false，不授予重启权。

既有目标目录不自动复用、覆盖、修复或清理。失败/中断保留新目录，先检查再考虑下一步。
强制结束可留下部分文件；缺少 runtime.json 为未完成，格式错误则拒绝检查。
不要删除证据换取成功结果。旧服务从未停止，但这不等于已有崩溃安全服务事务或自动恢复。

使用目录句柄/no-follow、仅创建写入、所有权/权限检查；不隔离恶意同用户进程，不声称
POSIX ACL/网络盘支持。命令时限及返回输出上限不是磁盘配额或完整进程树隔离。
报告含本地路径和依赖指纹，私下保管。

后续切换仍需独立绑定真实部署、配置/状态引用、传输端点、权限、维护门控、schema 兼容及
恢复身份。打包 HTTP 不含旧 `/control`，需要该路由的部署仍阻塞，不能用准备成功绕过。
激活、恢复、清理和发布是后续独立步骤；不需在维护者工作机器上重装或重启来验收此 PR。

原生安装测试在 macOS/Linux 临时 home 使用实际 uv 离线安装候选 wheel，再使用新解释器跑
既有 HTTP 合成测试。Windows 检查不支持的存储边界，保留原有安装/HTTP 测试。
按实际 commit/CI 分别报告，不等同维护者机器的迁移证明。

[English](RUNTIME_PREPARATION.md) · [HTTP](BRIDGE_HTTP_CN.md)

[与已有部署登记联合只读检查](DEPLOYMENT_PAIRING_CN.md)
