# GitLab HTTP → HTTPS：现有 ReasonFirst 工作区迁移

本增量提供独立维护命令 `actual-coder-migrate-https`，不会改变日常
`actual-coder` / `gitlab-agent` 命令，也不会修改业务 GitLab 服务端。
对应 Issue #10 的**显式本地 URL 迁移**部分，不表示完整 TLS 改造已完成。

## 1. 范围与前提

支持同一 GitLab 实例、同一 DNS 主机名（或 IPv4 地址）、同一仓库 URL 前缀，
从默认 HTTP 端口升级到默认 HTTPS 端口。`http://host:80` → `https://host:443`
也可以显式指定，但本地记录必须匹配提供的精确 URL。非默认端口、改主机名、
改前缀、URL rewriting、Git include/includeIf、额外远端或每 worktree 配置，
均停止自动处理，需要独立审阅，不会猜测。

GitLab 已有 HTTPS 能力不等于每个客户端已准备好。先由运维验证服务端域名、
证书链和网络访问；不要通过关闭验证解决证书问题。本工具保持
`GITLAB_VERIFY_SSL=true`，并拒绝检查到的 Git `sslVerify=false`。

**适用环境是可信的个人开发机、已停止其他写入者的维护窗口。**
本工具的进程锁只协调同类迁移，不会锁住 coding agent、Git、编辑器或 MCP。
它不是 sandbox，也不是跨多个文件的原子事务。

## 2. 确认使用的配置文件

先查看当前全局工具的配置来源：

```bash
actual-coder config
```

维护命令要求明确的 `--config-file`，不从目标仓库猜测 `.env`。
团队通常使用 `~/.config/gitlab-agent/.env`；也可能由
`GITLAB_AGENT_ENV_FILE` 显式指定。只能选择实际生效的用户配置。
**本工具只改所选文件，不会扫描或修改所有可能的 shell / 服务 / MCP 配置。**

当前进程中导出的变量优先于 `.env`：若 `GITLAB_BASE_URL` 仍为旧 HTTP 值，
先从当前 shell 取消该导出，并检查 shell profile、服务配置和 MCP launcher。

macOS/Linux：

```bash
unset GITLAB_BASE_URL
```

PowerShell：

```powershell
Remove-Item Env:GITLAB_BASE_URL -ErrorAction SilentlyContinue
```

若设置了 `GITLAB_AGENT_ENV_FILE`，它必须指向所选文件。
工作区根目录必须为绝对路径；迁移要求明确的 `GITLAB_ALLOWED_PROJECTS`，
并且检查到的每个缓存项目都在其中。不符合时保留本地状态并停止。

## 3. 默认预览（不改 live 配置、状态或 Git refs）

安装包含此增量的版本并在仓库根目录运行 `uv sync` 后：

```bash
uv run actual-coder-migrate-https \
  --config-file "$HOME/.config/gitlab-agent/.env" \
  --from-url http://gitlab.example.com \
  --to-url https://gitlab.example.com
```

以上域名是示例，替换为运维确认的实际 endpoint。不要将 PAT 写进 URL。
已更新全局安装后，也可以直接使用 `actual-coder-migrate-https`。
源码入口是 `python -m gitlab_agent.https_migration`。

预览输出含配置文件位置、工作区根目录、项目、工作区数量、每个待改文件的
类型与 before/after SHA-256，以及 `plan_digest`。不输出 `.env` 内容、
Git credential helper 值或源码 diff。准备 Git config 修改时使用私有临时
副本，随后清理；不会在 live 工作区写入缓存、index、ref 或状态文件。

工具检查所有当前缓存和工作区记录，包括没有活跃 worktree 的仓库缓存。
存在 malformed/stale metadata、意外 origin/push URL、未批准的 URL 形式、
Git 配置覆盖变量、symlink 输入等，会拒绝而不是忽略。先明确处理这些状态。

## 4. 可选的无凭证 HTTPS 探测

预览时附加：

```text
--check-tls
```

它使用 HTTPX 默认可信 CA、证书验证开启、`trust_env=False`，向新的
`/api/v4/version` 发一个不带 PAT 的 HTTPS GET；只读取响应头，不跟随重定向。
默认离线预览和 apply 均不需要网络；只有该显式选项发起 HTTPS 探测。

结果分别说明 `certificate_verified`、HTTP status、`api_auth_checked=false`
和 `git_tls_checked=false`。例如 401 可以证明这次 TLS 成功，但**不能证明
API 认证成功**。重定向会停止该 probe，需要检查 API/reverse proxy 配置；
不能把登录网页重定向当作 API 就绪证据。探测失败时本次 apply 不执行。

**限制：此 probe 不实现新的私有 CA 配置，不验证 Git TLS / PAT / MR / CI。
不依赖浏览器信任库，也不通过环境变量开放全部代理/CA 设置。**
已有企业私有 CA、Windows Schannel/OpenSSL 差异、同步/异步 API 和 Git 的统一
CA 配置、运行时 authenticated redirect policy 仍是后续独立改造。

## 5. 停止 worker 后确认应用

先停止 coding agent、其他 Git/编辑器写入者和长期运行的 MCP 进程。

```bash
uv run actual-coder-migrate-https \
  --config-file "$HOME/.config/gitlab-agent/.env" \
  --from-url http://gitlab.example.com \
  --to-url https://gitlab.example.com \
  --apply --workers-stopped
```

工具会打印本次计划并要求 TTY 上输入确认。要将应用严格绑定到之前的预览，
附加 `--plan-digest <刚才的摘要>`。
脚本化应用必须同时提供 `--yes --plan-digest <摘要> --workers-stopped`；
不能仅用 `--yes` 跳过未知计划。旧 preview 后，配置、状态文件、Git refs、
worktree HEAD/branch/status 或相关环境发生变化时会拒绝，要求重新预览。

应用只做以下改动：

- 所选 `.env` 中 `GITLAB_BASE_URL` 升级；其余值和注释保留；
- 已批准缓存的 `remote.origin.url` 及匹配的显式 `pushurl` 升级；
- 状态文件内与该项目精确匹配的 `merge_request_url` 升级；
- 创建私有 before/after 备份和操作 journal。

保持 workspace ID、base SHA、HEAD、branch、MR IID、tracked/untracked 文件，
不进行 clone/fetch/push、reset、cleanup、rebase、分支或 MR 重建。
未知 JSON 字段保留；不会根据 `pushed: true` 判断能否删除工作。
此处没有任何删除未发布工作的步骤。

## 6. 备份与中断恢复

备份位于所选 workspace root 下 `migrations/https-<id>/`。
**备份包含所选 `.env` 的凭证，禁止上传 GitHub、工单或团队群。**
POSIX 目录 0700、备份文件 0600；被修改文件保留原 POSIX mode。
Windows 在写入前用原生 ACL 将备份和临时替换文件限制到当前用户；应用后的
目标文件也为当前用户访问，而不是保留更宽的继承 ACL。原生权限工具失败时停止。
共享文件/网络文件系统不在此个人维护工具的支持范围内。

每次写入前会核对原始字节，操作过程更新 manifest。应用后的检查再次核对
Git refs 和 worktree HEAD/branch/status。重复完成后的迁移是 no-op。

如过程中磁盘写入失败或进程中断：保留现有工作区及 journal，停止其他写入者，
人工检查 before/after hashes 和当前文件后，使用**相同 old/new 映射重新预览**。
工具接受已迁移与未迁移记录的混合状态，新的确认会只完成剩余升级。
不要强制清理、重新 clone 或盲目恢复旧 HTTP 网络设置。

这是可恢复的逐文件应用，不是对任意崩溃、远端变更或恶意同用户进程的原子保证。
指纹覆盖配置/metadata/ref/status，不宣称锁住或快照所有已有 dirty 文件字节。
工具本身从不写入源码文件；维护窗口依然是必要前提。

## 7. 应用后独立验收

重新打开使用正确配置的 shell，重启 MCP，检查：

```bash
actual-coder config
actual-coder doctor
actual-coder list
actual-coder status <workspace-id>
actual-coder ci <workspace-id>
```

确认基准 URL 为 HTTPS，并在本地核对一个现有缓存的 fetch/push origin。
然后分别通过正常受控流程验证 API 认证、Git fetch、CI/MR 读取；获人工授权后
才进行测试分支的写入验证。`doctor` 的 API 成功不等于 Git TLS 已成功。
不要为了让认证/TLS测试通过使用 `--force` 或关闭证书验证。

## 8. 开发验证与后续

新增真实临时 Git 工作区测试、生产 WorkspaceManager 衔接测试、打包入口测试，
以及动态生成证书的 loopback HTTPS 测试。TLS 证书与私钥只在测试临时目录存在；
`cryptography` 是 dev dependency，不新增 production/model-inference 依赖。

```bash
uv run python -m unittest discover -s tests -v
uv run actual-coder-migrate-https --help
```

本增量不修改运行时 API/Git 的统一私有 CA 或 redirect policy，不修改常规
`doctor`，也不实现整个 workspace locking。Issue #10 的这些项仍须继续。
之后回到 Issue #6 的 handoff、一致状态、TaskSpec 和 EvidencePack。

主要规范参考：HTTPX SSL / environment variables 文档、Git git-config 文档。
