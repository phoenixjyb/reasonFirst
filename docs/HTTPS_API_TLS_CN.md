# GitLab HTTPS：Python API / MCP 运行时信任与重定向

[English](HTTPS_API_TLS.md) · [当前上手](QUICKSTART_CN.md) · [迁移工具](HTTPS_MIGRATION_CN.md)

本文描述已合入的 Python API/MCP 运行时部分（PR #13）。与 PR #12 的本地
URL/workspace 迁移工具独立；升级代码不会自动更改用户 `.env`、工作区或
Git 远端。该运行时 TLS 行为已包含在 v0.5.0；对可能领先于 release tag 的 editable/源码安装，排障时仍需记录准确源码 commit SHA。

## 配置

对使用受公共 CA 信任证书的 GitLab，只需配置最终 HTTPS 地址并保持校验：

```dotenv
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_VERIFY_SSL=true
GITLAB_TRUST_ENV=false
```

私有 CA 可增加：

```dotenv
GITLAB_CA_BUNDLE=/absolute/path/company-ca.pem
```

也支持 `~/.config/...`，但不接受依赖当前工作目录的相对路径。PEM 文件只放
CA 证书，不放私钥；为空、不可读、无效或超过 4 MiB 时拒绝请求。
它会在公共 certifi 根证书基础上增加指定 CA，不会移除现有公共根。

CLI 和 MCP 都使用相同的显式 SSLContext。`GITLAB_TRUST_ENV` 仍控制代理
继承，不需要为了私有 CA 将它设为 true。Python 客户端不再隐式使用
`SSL_CERT_FILE` / `SSL_CERT_DIR`；曾依赖这些变量的用户应显式设置上述选项。
环境变量仍优先于 `.env`，MCP 进程需要重启后才会读取新配置。

## 三条连接链路不要混淆

| 链路 | CA 配置与范围 |
| --- | --- |
| CLI API / MCP JSON、text、trace | 公共根 + 用户级 `GITLAB_CA_BUNDLE`；拒绝所有 API 3xx |
| 原生 Git clone/fetch/push | Git 独立的 trust store/backend/redirect 策略；不读取上述新设置 |
| 迁移工具 `--check-tls` | 默认信任库，不读取 `GITLAB_CA_BUNDLE`；无凭证、无重定向 |

因此私有 CA 的 API 已成功、但维护 probe 仍失败，并不意味着 token 失效。
浏览器能打开网页也不能证明 Python 与 Git 均信任证书。独立的旧版
`smoke_test.py` 不通过此共享运行时 client factory，不能据其结果宣称请求
守卫已生效。原生 Git 和分层诊断仍在 Issue #10。

## 覆盖范围与兼容性变化

覆盖同步 GitLab API 的 JSON/text/trace，以及异步 MCP 的 JSON/text/job-log
路径。请求发送前核对 scheme、host、port 和配置前缀下的 `/api/v4` 路径。

所有 3xx 响应都被拒绝，包括同源规范化、登录页、跨源和 HTTPS 降级。
拒绝时不读重定向响应体、不追随 Location；即便底层调用意外指定
`follow_redirects=True`，响应 hook 仍会阻止下一次请求。
需要配置最终可直接返回 API 响应的地址，不能依赖 HTTP->HTTPS 跳转。

`GITLAB_VERIFY_SSL=false` 现在会在 Python 客户端建立前报错，不再作为
绕过证书错误的方式。base URL 中的账户密码、query/fragment、特殊编码或
路径穿越会被拒绝。普通 hostname、IPv6、端口及简单 GitLab path prefix 支持。

保留显式 HTTP 配置以兼容尚未迁移的可信内网；它仍是未加密连接，原 doctor
会警告。这个增量没有把 HTTP 变安全，也没有自动升级或回退 endpoint。

## 特别注意：不等于 Native Git 已配置

**本增量的 `GITLAB_CA_BUNDLE` 只配置 Python API/MCP，不会修改 Git 的 CA、
全局 `.gitconfig`、SSL backend、remotes 或代理。** 原生 Git 的 trust store
仍独立；不能把 `actual-coder doctor` 的 API 成功理解为 fetch/push 已验证。

将同一 CA 策略接入 managed Git、验证 Windows Schannel/OpenSSL 行为，以及
正常 doctor 的分层 TLS/Git 诊断，仍是 Issue #10 后续工作。已有 workspaces 的
origin 与 saved MR URL 迁移由 PR #12 处理；它也不等于运行时 CA 配置。

本次不改变 `.actualcoder.yaml` schema，仓库 policy 不能添加 CA 或关闭校验。
不增加 MCP 写操作、模型调用、任务权限、自动 merge 或生产 GitLab 操作。
HTTP 普通错误响应的通用脱敏、OS sandbox、Git URL rewriting 等更广边界未在
本次全面重写。该限制不可被“请求守卫”等同于端到端安全保证所掩盖。

## 验证与上线顺序

测试会临时生成 CA/服务端证书和私钥，只在 loopback 上使用 dummy session，
不提交证书/密钥文件，不连接真实 GitLab。覆盖私有 CA、错误 CA、过期证书、
hostname mismatch、同源/跨源/降级重定向、前缀越界、代理隔离和实际 CLI/MCP 路径。

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

从正常源码安装更新并同步依赖；全局 editable 安装与临时 PR 环境要区分。
已完成 URL 迁移且 preview 为 no-op 时，不要仅因运行时 TLS 更新再执行 apply。
仅当仍有旧 HTTP 状态需要迁移时，按迁移指南在已停止其他 writer 的窗口操作。
随后重启既有 MCP/tunnel launcher，分别验证 API TLS/auth、Git fetch 和 MCP
读取；新 push 仍须经授权。`actual-coder config` 当前不显示 CA-bundle 字段，
不能把此输出当作完整 TLS 状态报告。没有根据单个 API/TLS 成功就推断全部
链路通过。公共 CA 已可信的配置通常无需新增 CA 文件。

## 官方依据

- HTTPX SSL / 显式 SSLContext：https://www.python-httpx.org/advanced/ssl/
- HTTPX 环境变量与 trust_env：https://www.python-httpx.org/environment_variables/
- Git 的独立 TLS 配置：https://git-scm.com/docs/git-config
