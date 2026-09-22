# ReasonFirst

**English** · [简体中文](README_CN.md) · **[First-time setup](docs/GETTING_STARTED.md)** · [Documentation index](docs/README.md)

> **Reasoning-first coding orchestration.**
> Use your strongest reasoning model for reasoning. Let coding agents do the coding.

[![CI](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml/badge.svg)](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

ReasonFirst connects interactive engineering reasoning with replaceable coding agents and a local GitLab workflow. A human and their chosen reasoning interface define the task; **ActualCoder** prepares a Git worktree, hands the task to **Codex CLI** or **GitHub Copilot CLI**, and supplies validation, Merge Request, and CI evidence for review.

The goal is to spend reasoning capacity on architecture, diagnosis, and review while delegating implementation iterations. ReasonFirst is not a model proxy, quota-transfer service, or auto-merge bot. It makes no direct model-inference calls; external coding tools use their own authentication and billing. Cost savings are a design goal, not a measured guarantee.

**Early-stage developer tooling:** use trusted repositories on a trusted development host. A Git worktree is not a security sandbox. Read [SECURITY.md](SECURITY.md) before using real credentials or executing repository code. A private MCP endpoint still returns selected data to the connected reasoning service; obtain the relevant data-sharing approval.

## Start here: get connected before asking ChatGPT to work

**Follow [the complete first-time setup guide](docs/GETTING_STARTED.md) ([中文](docs/GETTING_STARTED_CN.md)) in order.** It includes installation commands, where to obtain each credential, Keychain storage, a local profile, service startup, ChatGPT app selection, and a live repository-read test. You should not need earlier chat messages to reconstruct setup.

| Prepare | Why it is needed |
| --- | --- |
| Git, uv/Python, ReasonFirst and the separate `tunnel-client` binary | The new supervisor does not install the upstream tunnel agent. |
| GitLab URL, confirmed project/ref/file, read token and explicit local allowlist | A proposed project name or local folder does not prove remote existence or access. |
| OpenAI Platform tunnel permissions, tunnel ID and runtime API key | Tunnel identity and runtime authentication are different values. |
| ChatGPT custom-app/developer-mode permission and workspace association | A locally running tunnel is not automatically attached to a conversation. |
| Keychain or another explicit supported secret source | An earlier shell export is not persistent storage. |

Codex/Copilot login, Git write permissions and a CI runner are **later implementation prerequisites**, not prerequisites to reading GitLab in ChatGPT. Current provider permissions/eligibility are linked in the setup guide; no subscription name guarantees them. The tunnel runtime key is still required even though ReasonFirst makes no model API calls.

```text
First time: permissions -> install -> GitLab config -> tunnel ID/key/profile
Each stopped session: start -> local status -> select the app in normal ChatGPT
Each new project: gitlab_whoami -> check_project_access -> files at resolved commit
Approved work: ChatGPT plan -> human handoff -> Codex/ActualCoder -> MR/CI -> review
```

Once configured, use `actual-coder-tunnel start` in Terminal A and `actual-coder-tunnel status` in Terminal B. **Keep Terminal A running.** `ready_for_chatgpt_check` is local readiness, not end-to-end acceptance. Select the existing app in the normal ChatGPT composer and perform the guide's live identity/preflight/file read before starting work.

The **localhost Assistant is not required**. Overview/Logs are optional diagnostics. Omitting the Assistant UI does not uninstall Codex or guarantee that an upstream bundled helper is disabled. Do not disable approval controls to make that optional panel work.

## Guides by task

| Goal | Guide |
| --- | --- |
| First-time ChatGPT connection, from prerequisites to first prompt | **[Complete setup](docs/GETTING_STARTED.md)** · **[中文](docs/GETTING_STARTED_CN.md)** |
| Already configured: start/status/stop/restart | [Tunnel lifecycle](docs/TUNNEL_LIFECYCLE.md) · [中文](docs/TUNNEL_LIFECYCLE_CN.md) |
| Confirm a new project's existence/access and obtain an explicit grant | [Project access](docs/PROJECT_ACCESS.md) · [中文](docs/PROJECT_ACCESS_CN.md) |
| Rehearse the reasoning/worker/MR loop | [Practice lab](docs/PRACTICE_LAB.md) · [中文](docs/PRACTICE_LAB_CN.md) |
| Understand interface responsibilities | [Workflow](docs/WORKFLOW.md) · [中文](docs/WORKFLOW_CN.md) |
| Local-only installation and controlled implementation | [CLI quickstart](docs/ACTUAL_CODER_QUICKSTART.md) · [中文](docs/QUICKSTART_CN.md) |
| Approved requirements and observed results | [Manual handoff template](docs/TASK_HANDOFF_TEMPLATE.md) · [中文](docs/TASK_HANDOFF_TEMPLATE_CN.md) |
| Architecture | [Design philosophy](docs/DESIGN_PHILOSOPHY.md) · [中文](docs/DESIGN_PHILOSOPHY_CN.md) |
| Existing HTTP-to-HTTPS migration | [Migration](docs/HTTPS_MIGRATION.md) · [中文](docs/HTTPS_MIGRATION_CN.md) |
| Certificates, API redirects and native Git boundaries | [Runtime TLS](docs/HTTPS_API_TLS.md) · [中文](docs/HTTPS_API_TLS_CN.md) |
| Review a PR or update the normal checkout | [Local PR review](docs/LOCAL_PR_REVIEW.md) · [中文](docs/LOCAL_PR_REVIEW_CN.md) |
| Unsupported/advanced profiles or manual startup | [Manual operator guide](docs/SETUP_TUTORIAL.md) · [中文/Windows](docs/OPENAI_TUNNEL_TEAM_SETUP_CN.md) |
| Troubleshoot, contribute or report securely | [Troubleshooting](docs/TROUBLESHOOTING.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) |

## How implementation works

```text
Human + reasoning interface: define goal, constraints, acceptance criteria
    -> ActualCoder: prepare a worktree and bounded handoff
    -> Codex/Copilot: inspect, implement, test
    -> Human-reviewed finish: feature branch / GitLab MR
    -> CI evidence and live ChatGPT review
    -> Human decides whether to continue or merge
```

The read-only MCP bridge exposes repository/MR/CI inspection. **It does not submit or execute local coding tasks, or read unpublished local changes.** The user operates the local CLI and carries approved task/result context between interfaces. Persistent TaskSpec and EvidencePack access are planned, not shipped; the handoff template is a writing aid.

After confirming the real project with the [access preflight](docs/PROJECT_ACCESS.md), follow the [CLI quickstart](docs/ACTUAL_CODER_QUICKSTART.md) or [practice lab](docs/PRACTICE_LAB.md). `start --no-launch` creates a real local workspace and handoff without invoking a coding model; it is not a no-side-effect preview. Do not keep calling `start` to continue the same task: retain its workspace ID and use `resume`.

Inspect actual local changes before `finish --dry-run`, then explicitly approve real `finish`. **Dry-run runs configured validation commands; it means no commit/push, not no code execution.** Missing `.actualcoder.yaml` is allowed but supplies no project-specific tests. A green result without real required tests is not adequate task acceptance.

`resume --from-ci` returns matching-HEAD failure evidence, not automatic repair execution. Do not invent changes for successful CI. Low-level `commit`/`push` commands and some existing generated handoffs do not run every finish gate; retain explicit task boundaries and use the reviewed finish flow. See [workflow](docs/WORKFLOW.md).

GitLab is the implemented target SCM/CI integration. Hosting this tool's source on GitHub does not imply a GitHub-target task adapter exists. Coding execution can use `codex` (Codex CLI), `copilot` (GitHub Copilot CLI), or `codex-desktop` (the Codex App Server bundled with Codex/ChatGPT Desktop). `REASONFIRST_WORKER_BACKEND` can pin the user's preferred backend; `auto` preserves project/default selection.

## Try the source without production credentials

For contributors who want only tests/help, install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/). Package metadata requires Python 3.10+; CI uses 3.12. Use a new checkout and stop on errors:

```bash
git clone https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run actual-coder --help
uv run actual-coder-tunnel --help
uv run actual-coder-check-project --help
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
```

Tests use temporary repositories, mocked services and loopback fixtures, not production credentials or a real tunnel. Installing dependencies accesses package indexes. Local `uv sync` can create an untracked `uv.lock` at this source baseline; preserve it rather than ignoring/resetting unrelated files. MCP/tunnel setup is unnecessary for this local-only test path.

## What is available on main

The last documented release is **v0.3.0**; merged source changes need not be in that tag, and package metadata still reads `0.3.0`. Record the commit SHA as well as version in bug reports. See [CHANGELOG.md](CHANGELOG.md).

| Capability | Current scope |
| --- | --- |
| Managed workspaces | Local Git caches/worktrees, feature branches, MR creation/update/recovery |
| Controlled finish | Base-policy tests, reviewability/protected paths, candidate/bounded-history secret checks, human confirmation |
| Publication safety | Fresh remote evidence before ordinary cleanup; unpublished abandoned work preserved |
| Command/CI evidence | Nonzero failures propagate; matching-HEAD CI and bounded/sanitized job logs |
| Worker policy | User-owned Codex/Copilot model, reasoning effort, execution mode and permission controls are passed explicitly to the selected CLI |
| Project-access preflight | Local allowlist -> GitLab project/ref/files; actionable diagnostics and pinned revision; no automatic grant |
| Tunnel lifecycle | Existing-profile configure/start/status/stop/restart on macOS/Linux, optional exact Keychain lookup, owned process cleanup; foreground only |
| HTTPS migration | Offline preview and confirmed local URL updates, private backups and forward recovery |
| API/MCP transport | Verified roots plus optional Python-only private CA, destination checks and no API redirects |
| Read-only MCP | Files, projects, MRs, pipelines and jobs; no local task-execution endpoint |

**Still planned:** native Git trust/destination integration, uniform project context on every handoff route, general workspace locking/transactional recovery, persistent TaskSpec/attempt records and EvidencePack access. Migration and tunnel-owner locks do not provide general workspace concurrency protection. [Issue #6](https://github.com/phoenixjyb/reasonFirst/issues/6) and [Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10) track task-loop/HTTPS work.

API/MCP clients reject disabled TLS verification and every API redirect. Configure the final endpoint. `GITLAB_CA_BUNDLE` adds Python API/MCP trust, not native Git or the migration `--check-tls` probe. Keep those scopes distinct; see [runtime TLS](docs/HTTPS_API_TLS.md).

## Names, compatibility and contribution

**ReasonFirst** is the project; **ActualCoder** (`actual-coder`) is the high-level CLI. `gitlab-agent` is lower-level and `codingagent` is a compatibility alias. Distribution `chatgpt-selfhosted-gitlab-mcp`, Python package `gitlab_agent`, private config `~/.config/gitlab-agent/.env` and existing workspace paths are intentionally retained. Do not rename managed directories during an update.

Global editable commands follow their source checkout; use a separate worktree for PR experiments. Contributions and reproducible English/Chinese reports are welcome: [Contributing](CONTRIBUTING.md), [Security reporting](SECURITY.md#reporting-a-security-issue), [release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md). Never publish credential files, private source, migration backups or unreviewed logs in issues.

Licensed under **Apache License 2.0**; see [LICENSE](LICENSE). External tools have their own licenses/terms. This is an independent project, not an official OpenAI, GitHub or GitLab product.
