# 为 ReasonFirst 作出贡献

[English](CONTRIBUTING.md) · [中文文档索引](docs/README_CN.md)

<!-- Translation source: CONTRIBUTING.md @ a3e33c72c55efef6a0dc3808fb9853c62ad15f9d -->

感谢你帮助改进 ReasonFirst。欢迎使用中文或英文提交 Issue 和 Pull Request。请先阅读[设计理念](docs/DESIGN_PHILOSOPHY_CN.md)、[当前快速上手](docs/QUICKSTART_CN.md)和[安全边界](SECURITY_CN.md)。

## 选择聚焦、可审阅的贡献

有价值的贡献包括可复现的缺陷、回归测试、更清晰的文档、跨平台修复，以及[任务循环路线图](https://github.com/phoenixjyb/reasonFirst/issues/6)或 [HTTPS 工作](https://github.com/phoenixjyb/reasonFirst/issues/10)中的小步改进。涉及较大的接口、依赖、持久化格式或后端变更时，先讨论再实现。

核心边界是有意设计的：推理明确意图，编程代理负责实现，确定性工具管理其支持的状态转换，人工授权重要写入与合并。不要把隐藏的模型推理服务、自动合并、权限扩展或无限制远程 shell 顺带加入其他变更。

## 安全地报告缺陷

使用缺陷报告模板，提供最小化的合成示例、准确 commit SHA、操作系统、Python/Git/uv 版本、命令、预期行为和实际行为。对端点、用户名、文件路径、令牌与客户代码脱敏。不要附上完整 `.env`、原始 CI 日志、私有证书、迁移备份或受管工作区。

怀疑存在安全问题时，遵循 [SECURITY_CN.md](SECURITY_CN.md)，不要提交公开缺陷报告。如果没有私有资产就无法复现测试，应先说明这一限制，不要公开这些资产。

## 从分支或 fork 开发

没有推送权限时，先 fork 仓库。克隆自己的 fork，并将本仓库添加为 `upstream`；或者，在有权限的克隆中创建分支。凭证和受管 GitLab 工作区应放在源码树之外。使用[本地 PR 审阅流程](docs/LOCAL_PR_REVIEW_CN.md)，不必手工下载补丁。

从源码根目录运行：

```bash
uv sync --python 3.12
uv run python -m compileall -q server.py smoke_test.py src/gitlab_agent tests
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
git diff --check
```

常规单元与集成测试不应需要真实 GitLab 账号、生产令牌或付费模型会话。它们创建临时 Git 仓库和短期本地 TLS 测试环境。安装依赖可能访问软件包索引。`smoke_test.py` 不同：它是针对真实 API 的检查，需要你自己的服务配置；不要对第三方服务运行，也不要未经审查分享原始输出。

当前 CI 在 Ubuntu/macOS/Windows 上测试 Python 3.12。包元数据支持更广的 Python 范围，不代表每个解释器版本都经过测试。平台相关的跳过项必须说明原因，不能暗中弱化不变量。

## 测试与实现要求

修复缺陷时，在可行情况下先复现原问题，再改变行为，并增加回归测试。涉及 Git 状态或引用语义时，使用真实的临时 Git 操作；mock 应放在传输层或不可用服务边界，不能据此声称端到端覆盖。CI 结论必须绑定准确 SHA，并说明实际运行的 job 和测试。成功的 docs-only 流水线不等于应用构建成功。

保留现有 CLI 兼容性和工作区记录。破坏性操作必须有明确意图，并具备保全与恢复测试。以同一用户身份运行的恶意代码超出宿主机执行器的隔离边界；不要把允许列表或指纹称为沙箱或事务。

在测试运行时拼接构造凭证形状的假值，仅在临时目录中生成 TLS 私钥。即便用于负面测试，也不要提交看似真实的字面量凭证。不要为让 CI 变绿而增加宽泛的扫描例外。如果真实凭证泄露，应先撤销并协调修复，不要强制推送覆盖其他贡献者的工作。

编写文档时，对照当前 CLI 检查命令参数，代码块使用纯 URL，避免未加引号的 `<placeholder>` shell 语法，将解释放在可粘贴的 shell 代码块之外。区分源码检出目录、全局 editable 安装、用户配置、受管工作区和外部服务。更新源码不会自动重启 MCP，也不会撤销配置迁移。

## 提交 Pull Request

每个 PR 保持一个清晰、可审阅的目的。说明问题、实现方式、兼容性与安全影响、实际执行的测试、证据局限和关联 Issue。行为变化时更新用户指南及 Unreleased 更新记录。除非任务明确获得批准，否则不要提升版本、创建 tag 或宣布发布。

欢迎 AI 辅助贡献，但贡献者仍须负责理解、审查、测试，并确保有权提交结果。概述相关辅助方式与验证过程即可；不要上传私有提示词、内部推理记录、凭证或保密源码。不要把编程代理的说法描述为独立观察到的证据。

仅提交你有权贡献的材料，并保留必要的第三方声明。仓库现有的 [Apache-2.0 许可证](https://github.com/phoenixjyb/reasonFirst/blob/main/LICENSE)保持不变；本指南不引入新的 CLA，也不改变所有权。审查时保持尊重、具体和建设性。维护者接受并合并 PR，与 CI 变绿是不同的决定。
