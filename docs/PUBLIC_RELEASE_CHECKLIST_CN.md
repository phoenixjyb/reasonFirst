# 维护者公开发布检查清单

[English](PUBLIC_RELEASE_CHECKLIST.md) · [中文文档索引](README_CN.md)

<!-- Translation source: docs/PUBLIC_RELEASE_CHECKLIST.md @ a3e33c72c55efef6a0dc3808fb9853c62ad15f9d -->

仓库公开且 CI 通过，不等于已经准备好发布。本页是检查清单，**不是**所有条目均已完成的声明。源码已有 Apache-2.0 许可证；不要在维护文档时重新授权，或顺带公开另一个仓库。

2026-09-28 已观察到的功能证据：合成全聊天链路完成了 managed worker 执行、审阅 snapshot 的人工批准、GitLab MR 发布、matching-HEAD unit-test CI 和 EvidencePack 审查，并且没有自动 merge。这是有效的发布前功能证据，但**不能**单独替代下面的隐私、打包、仓库设置、多平台和 release tag 检查。

## 源码、隐私与许可

- [ ] 确认有权分发源码、示例、资产和贡献内容。审查第三方声明与依赖，保留现有 LICENSE。公开代码不会授予外部编程代理服务的使用许可，也不会赋予私有目标项目的访问权。
- [ ] 用 `scripts/check_repo_secrets.py --history` 检查受跟踪文件及完整的、当前可获得的 HEAD 历史，再单独检查其他分支/tag、已关闭 PR 及评论、Issue、发布资产、CI 日志/产物、截图和附件。内置扫描器不覆盖所有这些位置。
- [ ] 从拟公开材料中删除或脱敏部署专用端点、客户代码/数据、个人路径和标识符。不要把本地迁移验收输出复制进公开文档，应生成合成示例。
- [ ] 首先撤销实际泄露的凭证。协调历史清理；不要认为从 HEAD 删除或创建新分支就会清除旧对象、fork、缓存或评论。已知用于负面测试的假数据不是生产凭证，也不应成为关闭审计的理由。
- [ ] 检查实际发布归档或 wheel，而不只是工作树。排除 `.env`、运行时数据、工作区、迁移日志/备份、私有证书、测试生成的私钥、本地审阅包和无关产物。

## 公开入口

- [ ] README 无需依赖私有聊天即可解释已实现流程、目标用户、配置方法、尚不支持的能力、供应商/认证边界和许可证。
- [ ] 干净的源码检出可在没有生产 GitLab 凭证或付费模型会话的情况下运行文档中的合成测试；真实服务冒烟检查须明确标注。
- [ ] 可复制命令使用纯 URL、明确的占位符，不把说明文字或注释放入会影响交互式 zsh 的代码块。升级时绝不用示例覆盖现有用户配置。
- [ ] 中英文入口和相对链接正确。历史设计及 alpha 阶段说明有明确标记，不被当作已交付能力。
- [ ] CONTRIBUTING、缺陷/功能模板及 PR 模板要求最小化、已脱敏的证据，而不是完整私有日志或凭证。

## 安全与仓库设置

- [ ] 鼓励公众使用前，启用并测试私密漏洞报告通道。存在 SECURITY.md 不代表 GitHub 私密报告已启用。确认报告能送达实际维护者；不要公布无人值守的邮箱或未经承诺的 SLA。
- [ ] 为 `main` 配置合适的分支保护或 ruleset、必需检查和人工审查。这些是仓库设置，不会因添加本清单而自动开启。
- [ ] 审查仓库可用的依赖告警、秘密扫描/推送保护和可选代码扫描。未经检查设置，不要声称这些功能已启用。
- [ ] 审查 Actions 权限与 fork PR 行为。不要把生产凭证暴露给 PR 代码，不要在高权限自托管 runner 上运行不可信 PR 代码，也不要为发布绕过失败检查。
- [ ] 设置准确的仓库描述、topics 和支持预期。只有计划好管理与回复时才启用 Discussions；不要把用户引向不可用的通道。

## 版本、证据与范围

- [ ] 选定准确的发布提交；验证该 SHA 的必需 job 与合并后集成，而不是仅检查更早的分支 head。记录平台/解释器覆盖、跳过项和已知限制。
- [ ] 对齐 tag、包版本、changelog 与发布说明。0.5.0 release candidate 已对齐源码/包版本和预备 release notes；维护者明确创建 tag / GitHub Release 之前，不要把 0.5.0 描述为已发布。
- [ ] 从目标分发形式测试文档中的安装与升级方法。源码 editable 安装与不可变发布包行为不同；打包成功不代表 MCP server 入口已经作为服务包含在内。
- [ ] 说明仍未解决的运行时 TLS/重定向、任务交接、并发/恢复和证据局限。在任何验证结论中，都要区分本地回归测试、真实 API 读取、Git fetch、历史 CI 读取、MCP 重连，以及另行明确批准的新推送。
- [ ] 发布 tag、上传软件包、修改可见性或对外公告均须单独获得授权。不要仅因开发讨论提到其他仓库，就将其公开。

参考：[GitHub 私密漏洞报告](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)、[贡献指南设置](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors)、[GitHub 敏感数据清理](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。
