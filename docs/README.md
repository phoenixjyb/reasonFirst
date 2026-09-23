# Documentation index

**English** · [简体中文](README_CN.md)

These guides describe the source revision containing them, not necessarily the last release tag. See [the main README](../README.md) and [Unreleased changes](../CHANGELOG.md).

## Start with the reasoning-first workflow

**New user:** follow **[First-time setup: prerequisites to a real ChatGPT GitLab read](GETTING_STARTED.md)**. This is the complete macOS/Keychain path, with installation, GitLab configuration, OpenAI tunnel/permissions/runtime key, local profile, startup, ChatGPT app selection and acceptance checkpoints. Its [Chinese version](GETTING_STARTED_CN.md) follows the same executable steps.

**Primary product loop:** normal ChatGPT reads and reasons over repository/MR/CI evidence, defines the task and acceptance criteria, ReasonFirst hands the approved task to a coding worker, and bounded implementation/CI evidence returns to ChatGPT + human review. Use the [CLI quickstart](ACTUAL_CODER_QUICKSTART.md) as the execution-engine/operator reference inside that loop. Direct CLI-only use remains useful for testing, recovery and automation, but is secondary. The optional **Bridge Preview** is a more privileged orchestration surface; read [Architecture](ARCHITECTURE.md) before enabling it. **Already connected:** use [daily start/status/stop/restart](TUNNEL_LIFECYCLE.md). **Advanced/manual/Windows:** use [the manual guide](SETUP_TUTORIAL.md) and [Windows procedure](OPENAI_TUNNEL_TEAM_SETUP_CN.md#windows-powershell).

## Current entry points

| Topic | English | 简体中文 |
| --- | --- | --- |
| Complete first-time setup and first normal ChatGPT prompt | **[Start here](GETTING_STARTED.md)** | **[首次完整接入](GETTING_STARTED_CN.md)** |
| Product and supported capabilities | [ReasonFirst](../README.md) | [项目说明](../README_CN.md) |
| Start the required service and manage its lifecycle | [Tunnel lifecycle](TUNNEL_LIFECYCLE.md) | [隧道生命周期](TUNNEL_LIFECYCLE_CN.md) |
| Before using a newly proposed GitLab project | [Access preflight and user grants](PROJECT_ACCESS.md) | [项目预检与用户授权](PROJECT_ACCESS_CN.md) |
| Rehearse ChatGPT, Codex and three rounds of one MR | [Practice lab](PRACTICE_LAB.md) | [实战演练](PRACTICE_LAB_CN.md) |
| Interface responsibilities and evidence | [Workflow](WORKFLOW.md) | [工作流程](WORKFLOW_CN.md) |
| Execution-engine setup and controlled implementation | [CLI quickstart](ACTUAL_CODER_QUICKSTART.md) | [快速上手](QUICKSTART_CN.md) |
| Manual/advanced startup, credential alternatives | [Operator guide](SETUP_TUTORIAL.md) | [手工接入/Windows](OPENAI_TUNNEL_TEAM_SETUP_CN.md) |
| Manual approved-task handoff and result evidence | [Writing template, not a runtime API](TASK_HANDOFF_TEMPLATE.md) | [人工交接与证据模板](TASK_HANDOFF_TEMPLATE_CN.md) |
| Current architecture and trust boundaries | **[Architecture](ARCHITECTURE.md)** | **[架构说明](ARCHITECTURE_CN.md)** |
| Architectural intent / rationale | [Design philosophy](DESIGN_PHILOSOPHY.md) | [设计理念](DESIGN_PHILOSOPHY_CN.md) |
| HTTP-to-HTTPS migration | [Migration](HTTPS_MIGRATION.md) | [迁移指南](HTTPS_MIGRATION_CN.md) |
| API/MCP private CA, redirects and native Git boundaries | [Runtime TLS](HTTPS_API_TLS.md) | [运行时 TLS](HTTPS_API_TLS_CN.md) |
| PR checkout, tests and source updates | [Local PR review](LOCAL_PR_REVIEW.md) | [本地 PR 审阅](LOCAL_PR_REVIEW_CN.md) |
| Diagnosis without weakening controls | [Troubleshooting](TROUBLESHOOTING.md) | [分层故障排查](TROUBLESHOOTING_CN.md) |
| Contribution process | [Contributing](../CONTRIBUTING.md) | [贡献指南](../CONTRIBUTING_CN.md) |
| Security reporting and limitations | [Security policy](../SECURITY.md) | [安全策略](../SECURITY_CN.md) |
| Before a public release | [Maintainer checklist](PUBLIC_RELEASE_CHECKLIST.md) | [公开发布清单](PUBLIC_RELEASE_CHECKLIST_CN.md) |

## Required before the first ChatGPT prompt

Complete prerequisites, start the selected tunnel/MCP, keep its Terminal running, check local readiness, and select the actual app in the normal ChatGPT composer. Then call `gitlab_whoami`, `check_project_access` and read files at `resolved_commit_sha`. Local health, identity alone, or the name of a connector in a message is not end-to-end project-read acceptance.

Keychain is the recommended runtime-key source in the Mac guide, not a mandatory component for every platform. The OpenAI tunnel key, tunnel ID, GitLab token, local allowlist and coding-agent login are different things. Platform tunnel permissions, ChatGPT workspace permissions and GitLab/local project grants are also separate. The first-time guide identifies the responsible user/admin at each stop point. Never assume an earlier shell export survived a new Terminal.

## New-project access gate

A project name/local folder is not evidence of a remote repository or authorization. On a newly introduced project, call `check_project_access` before bulk reads or a handoff. On `ok: false`, show the diagnostic and wait for the user/operator. A 404 or empty filtered listing cannot distinguish absent from inaccessible.

Per-project authorization is `GITLAB_ALLOWED_PROJECTS` in the local MCP configuration, not OpenAI tunnel settings. Only after explicit approval, append the intended exact project while preserving existing entries, then restart the existing MCP. The helper never creates projects or grants access. This also applies before following older first-task examples.

## One reasoning interface, not another required chatbot

Normal ChatGPT is the reasoning/review interface. The read-only GitLab Tunnel/MCP supplies repository/MR/CI reads; ActualCoder implements approved local work with `codex-cli`, `copilot-cli`, or `codex-desktop`. The optional Bridge Preview is a separate, more privileged local orchestration surface and should not be confused with the read-only connector. The localhost Assistant, Codex tunnel plugin and Inspector are not prerequisites or acceptance gates. Overview/Logs are optional diagnostics. Not using the Assistant does not disable an upstream bundled helper or uninstall Codex.

The standard GitLab read connector still has no local task-execution role. The optional Bridge Preview exposes local orchestration tools, approvals, finish preview and configured SSH operations. Persistent core TaskSpec/attempt records and bounded EvidencePack are now on `main` and form the durable boundary between reasoning intent and worker evidence. A successful read does not prove Git push, model login or full application CI works; test those during an approved implementation.

## Translation scope and maintenance

Keep English and Chinese current guides together, including executable examples, stop conditions, permissions and evidence limits. The new first-time pair records its source baseline and provider-check date; tests check command parity and safe bootstrap behavior. Existing paired quickstart, lifecycle, access, migration and TLS guides remain their topic references.

Historical notes/CHANGELOG are records, and LICENSE is unchanged. Documentation does not create a release, alter permissions, or claim unsupported features. Provider UI/availability can change; consult the primary references linked from the first-time/manual guides.

## Historical designs and detailed legacy recipes

[Old Chinese onboarding](ONBOARDING_GUIDE_CN.md) and [old Chinese setup tutorial](SETUP_TUTORIAL_CN.md) are historical, not the required checklist. Do not execute old token-bearing examples, overwrite a working `.env`, or install optional components merely because a legacy page mentions them.

[V0.2 design](V0.2_WRITE_ACCESS_DESIGN.md), [V0.2 Codex guide](V0.2_CODEX_QUICKSTART.md), [CodingAgent compatibility guide](CODINGAGENT_QUICKSTART.md), [V0.3 roadmap](V0.3_ROADMAP_CN.md) and [V0.3 audit](V0.3_RELEASE_AUDIT_CN.md) provide historical context.

Focused implementation notes cover [publication safety](V0.3.1_PUBLICATION_SAFETY_CN.md), [exit status](V0.3.1_COMMAND_EXIT_CN.md), [history scanning](V0.3.1_SECRET_HISTORY_CN.md) and [log evidence](V0.3.1_SHARED_LOG_EVIDENCE_CN.md). Their filenames do not announce a v0.3.1 release. [Issue #6](https://github.com/phoenixjyb/reasonFirst/issues/6) now tracks richer TaskSpec/EvidencePack lifecycle and cross-interface work beyond the merged core; [Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10) tracks remaining native-Git trust/destination-policy work.
