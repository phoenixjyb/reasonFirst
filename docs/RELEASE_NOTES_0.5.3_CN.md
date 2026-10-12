# ReasonFirst 0.5.3 发布说明

[English](RELEASE_NOTES_0.5.3.md) · [安装与更新](INSTALL_CN.md) · [平台验证](PLATFORM_VALIDATION_CN.md) · [变更记录](../CHANGELOG.md)

ReasonFirst 0.5.3 汇集 Ubuntu 全新安装促成的安装器与诊断修复，并整理带日期的
macOS、Windows 和 Linux 实测记录。主流程保持不变：在普通 ChatGPT 对话里读代码、
讨论任务，让编程 worker 负责实现与测试，再把证据带回对话审阅。

**快速打包安装和从源码构建都支持。**
[v0.5.3 Release](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.3)
记录最终源码、附件工作流、公开文件哈希及下载/安装验收；仅有版本号并不证明发布已完成。

## 本次变化

- **恢复符合条件的隧道下载中断。** 安装器先使用 Python HTTPS；符合条件的传输
  错误发生且系统提供 curl 时，对元数据、校验文件或 archive 进行一次有时限的
  恢复尝试。TLS 验证、仅 HTTPS 重定向、SHA-256 和完整 bundle 校验继续生效。
  证书校验失败、HTTP 状态错误、不安全 URL 和无效内容不会触发该恢复。错误会指出
  下载阶段和脱敏 URL，不暴露代理凭证。
- **Doctor 能找到实际安装的客户端。** `actual-coder doctor` 复用 setup 的查找
  逻辑，包含 PATH 之外的 ReasonFirst 受管版本目录。消息区分可执行文件/socket
  发现、当前进程的 runtime-key 环境与服务健康或认证。连接状态仍需通过
  `reasonfirst tunnel status` 和 ChatGPT 实时读取核验。
- **更容易找到平台记录和安装入口。** 中英文平台矩阵、故障排查、README 和网站
  记录 Linux 结果，保留原 Mac/Windows 版本与范围。安装示例使用 Python 3.12，
  同时覆盖打包安装和源码路径。

这些变化来自 [#111](https://github.com/phoenixjyb/reasonFirst/pull/111)。
已发布的 v0.5.2 tag 和附件保持不变。

## 安装 v0.5.3

先准备 Git、uv 和一个受支持的编程 worker。运行编程任务前完成 worker 自身的登录；
引导配置不会创建服务商账号。GitLab 与 ChatGPT 权限见[首次接入准备](GETTING_STARTED_CN.md)。

### 快速打包安装

```bash
uv tool install --python 3.12 https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.3/chatgpt_selfhosted_gitlab_mcp-0.5.3-py3-none-any.whl
reasonfirst setup
```

这条路径不要求长期保留 ReasonFirst 源码目录。Release 提供 wheel、构建的源码包、
`reasonfirst.rb`、`RELEASE.json` 和 `SHA256SUMS.txt`。使用前核对实际附件与校验和，
详见[公开下载](RELEASE_DISTRIBUTION.md#简体中文)。

### 从源码构建/使用

```bash
git clone --branch v0.5.3 --depth 1 https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run reasonfirst setup
```

需要构建 wheel 与源码包时，在该目录运行 `uv build --no-create-gitignore`。
Editable 安装及基于已审阅提交的开发继续支持；两条路径共用原有配置、工作区状态、
项目授权和隧道身份。

已有用户先运行 `reasonfirst --version` 和 `reasonfirst setup --status --json`。
替换运行中服务依赖的软件包之前，按[更新说明](INSTALL_CN.md)检查。此补丁不会
自动重启服务、迁移部署或重新创建连接。

## 验证与范围

**2026-10-12 的 Ubuntu 22.04.5 实机演练使用已发布的 v0.5.2 wheel。**
它完成全新配置、经过校验的隧道下载恢复、Linux MCP 实时读取、受管 Git clone/fetch、
项目契约检查，以及 Codex CLI worker 执行 **10 项既有测试全部通过**。
Worker 报告工作区干净、文件未修改，没有进行仓库编辑或发布。这不是新版 v0.5.3
自动恢复或 doctor 行为的现场复测。

macOS 和 Windows 实机记录覆盖较早源码及 v0.5.1 安装。因此，**已在 macOS、Windows
和 Linux 上测试**指[平台记录](PLATFORM_VALIDATION_CN.md)中的具体日期、版本与操作，
不表示同一版本的所有功能都在三个平台完成验收。CI 回归、独立打包/源码安装与真实
服务商认证测试分别计入证据。

发布要求准确源码上的核心与三平台回归/安装、Bridge、原生 tunnel 边界、文档、打包和
源码/历史扫描全部满足门槛。版本化 Release 记录该源码及随后实际公开下载的验证结果；
较早集成提交的绿灯不能替代这些检查。

健康 runtime 的 repair/no-op、Windows 受控重启与系统重启恢复，以及可选高权限
Windows Bridge/写入验收继续保留 [#88](https://github.com/phoenixjyb/reasonFirst/issues/88)
中的范围限制。此补丁不改变服务激活、worker 权限或发布策略。
