# 托管 Git 凭证恢复

[English](GIT_CREDENTIAL_RECOVERY.md) · [故障排查](TROUBLESHOOTING_CN.md)

## API 可读，但原生 Git 读取认证失败

MCP/API 读取成功不代表原生 Git 认证已通过。托管 clone 若出现凭证管理器/OAuth
警告或旧 helper 不存在，不要立即更换有效 token、注册 OAuth 应用、重装 Git、
清空 Windows 凭据管理器，或修改全局 Git 配置。

对需要认证的托管 clone/fetch/push，ReasonFirst 仅在本次 Git 调用中清空继承的
credential helper 列表，并为准确的远端 URL 指定已配置的用户名。凭证优先级仍为
`GITLAB_GIT_TOKEN`、`GITLAB_GIT_PASSWORD`、最后回退到 `GITLAB_TOKEN`；
同时指定前两项仍会报错。临时 askpass 传递凭证，不将其放入 argv 或远端 URL。
关闭终端交互提示，但允许打包提供的 askpass；认证子进程不继承 Git 跟踪设置。
不写入持久 Git 配置，不影响其他正在运行的任务。

独立读取探测成功后，保留原凭证，安装经过审查的修正候选包再验收。`ls-remote`
成功只证明当时可以读取引用，不代表完整 clone、推送权限、worker 执行或完整安装
均已通过。部分失败后先确认是否已创建工作区，再决定是否重试 `start`；不要仅为
清除报错而删除已有缓存或工作区。

这只隔离凭证选择，不是原生 Git 的完整安全沙箱，也不重写其 CA、代理、重定向或
URL 重写策略。项目校验契约要求 `python3` 而 Windows 未提供该命令，是独立的
可移植性问题；不要修改受保护的测试/策略，也不要声称 Git 认证修正解决了它。

