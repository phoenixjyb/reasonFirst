# ReasonFirst

**English** · [简体中文](README_CN.md) · **[First-time setup](docs/GETTING_STARTED.md)** · [Documentation index](docs/README.md)

> **Reasoning-first coding orchestration.**
> Use your strongest reasoning model for reasoning. Let coding agents do the coding.

[![CI](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml/badge.svg)](https://github.com/phoenixjyb/reasonFirst/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

ReasonFirst connects interactive engineering reasoning with replaceable coding agents and a local GitLab workflow. A human and their chosen reasoning interface define the task; **ActualCoder** prepares a Git worktree, hands the task to a user-selected **Codex CLI**, **GitHub Copilot CLI**, or **Codex Desktop/App Server** backend, and supplies validation, Merge Request, and CI evidence for review.

The goal is to spend reasoning capacity on architecture, diagnosis, and review while delegating implementation iterations. ReasonFirst is not a model proxy, quota-transfer service, or auto-merge bot. It makes no direct model-inference calls; external coding tools use their own authentication and billing. Cost savings are a design goal, not a measured guarantee.

**Early-stage developer tooling:** use trusted repositories on a trusted development host. A Git worktree is not a security sandbox. Read [SECURITY.md](SECURITY.md) before using real credentials or executing repository code. A private MCP endpoint still returns selected data to the connected reasoning service; obtain the relevant data-sharing approval.

## Start here: choose the path you actually need

ReasonFirst does not require the same setup for every use case.

| Goal | Recommended entry point | Tunnel required? |
| --- | --- | --- |
| Local controlled coding with `codex-cli`, `copilot-cli`, or `codex-desktop` | **[CLI quickstart](docs/ACTUAL_CODER_QUICKSTART.md)** | No |
| Normal ChatGPT reads/reviews an approved GitLab repository, MR or CI | **[First-time ChatGPT connection](docs/GETTING_STARTED.md)** | Yes for this path |
| App Server orchestration, explicit approvals, named SSH workspaces, remote validation / finish preview | **[Architecture](docs/ARCHITECTURE.md)** then the Bridge Preview guide | Optional / deployment-specific |

For a ChatGPT read connection, follow the complete first-time setup guide in order. For local-only coding, skip Tunnel provisioning entirely and use ActualCoder directly. The optional Bridge Preview is more privileged than the read-only GitLab connector; enable it deliberately rather than treating it as a prerequisite.

```text
Local coding:
install -> user config -> actual-coder doctor -> start/resume -> worker -> finish -> MR/CI

ChatGPT read connection:
permissions -> install -> GitLab config -> tunnel -> select app -> live identity/project/file read

Bridge Preview:
explicit local setup -> configured targets/policy -> App Server -> approvals -> finish preview
```

The **localhost Assistant is not required** for any of these paths. Overview/Logs remain optional diagnostics.

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
| Current architecture | **[Architecture](docs/ARCHITECTURE.md)** · **[中文](docs/ARCHITECTURE_CN.md)** |
| Design rationale | [Design philosophy](docs/DESIGN_PHILOSOPHY.md) · [中文](docs/DESIGN_PHILOSOPHY_CN.md) |
| Existing HTTP-to-HTTPS migration | [Migration](docs/HTTPS_MIGRATION.md) · [中文](docs/HTTPS_MIGRATION_CN.md) |
| Certificates, API redirects and native Git boundaries | [Runtime TLS](docs/HTTPS_API_TLS.md) · [中文](docs/HTTPS_API_TLS_CN.md) |
| Review a PR or update the normal checkout | [Local PR review](docs/LOCAL_PR_REVIEW.md) · [中文](docs/LOCAL_PR_REVIEW_CN.md) |
| Unsupported/advanced profiles or manual startup | [Manual operator guide](docs/SETUP_TUTORIAL.md) · [中文/Windows](docs/OPENAI_TUNNEL_TEAM_SETUP_CN.md) |
| Troubleshoot, contribute or report securely | [Troubleshooting](docs/TROUBLESHOOTING.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) |

## How implementation works

```text
Human + reasoning interface: define goal, constraints, acceptance criteria
    -> ReasonFirst control: ActualCoder CLI or Bridge Preview
    -> selected worker: codex-cli / copilot-cli / codex-desktop
    -> isolated local worktree or configured SSH workspace
    -> validation + shared review/secret/protected-path gates
    -> reviewed feature branch / GitLab MR / matching-HEAD CI
    -> human decides whether to continue or merge
```

ReasonFirst now has **two distinct MCP surfaces**. The original GitLab MCP remains read-oriented for repository/MR/CI inspection. The optional **Bridge Preview** is a more privileged local orchestration surface: it can prepare managed workspaces, control Codex App Server sessions, expose pending approvals, review unpublished managed diffs/artifacts, and run complete finish previews. SSH mutation/validation is restricted to user-configured targets; arbitrary remote shell execution is not exposed. Experimental remote publication is hidden and disabled by default. See the [architecture reference](docs/ARCHITECTURE.md). Persistent core TaskSpec/attempt/EvidencePack support is still not on `main`.

After confirming the real project with the [access preflight](docs/PROJECT_ACCESS.md), follow the [CLI quickstart](docs/ACTUAL_CODER_QUICKSTART.md) or [practice lab](docs/PRACTICE_LAB.md). `start --no-launch` creates a real local workspace and handoff without invoking a coding model; it is not a no-side-effect preview. Do not keep calling `start` to continue the same task: retain its workspace ID and use `resume`.

Inspect actual local changes before `finish --dry-run`, then explicitly approve real `finish`. **Dry-run runs configured validation commands; it means no commit/push, not no code execution.** Missing `.actualcoder.yaml` is allowed but supplies no project-specific tests. A green result without real required tests is not adequate task acceptance.

`resume --from-ci` returns matching-HEAD failure evidence, not automatic repair execution. Do not invent changes for successful CI. Low-level `commit`/`push` commands and some existing generated handoffs do not run every finish gate; retain explicit task boundaries and use the reviewed finish flow. See [workflow](docs/WORKFLOW.md).

GitLab is the implemented target SCM/CI integration. Hosting this tool's source on GitHub does not imply a GitHub-target task adapter exists. Canonical explicit coding backends are `codex-cli`, `copilot-cli`, and `codex-desktop`; historical `codex` / `copilot` remain compatibility aliases.

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
| Managed workspaces | Local Git caches/worktrees plus user-configured SSH workspaces; feature branches, recovery and cross-process mutation locking |
| Controlled finish | Shared local/remote review gates: validation, reviewability, protected paths, candidate/history secret checks and exact candidate identity |
| Publication safety | Local reviewed finish is the normal path; SSH publication is exact-tree/destination bound, experimental and default-off |
| Remote validation | Structured argv execution only inside a user-configured container policy; no arbitrary remote shell and no implicit image pull |
| Command/CI evidence | Nonzero failures propagate; matching-HEAD CI and bounded/sanitized logs/artifacts |
| Worker policy | User-owned default backend plus model, effort, sandbox/network and permission controls across Codex CLI, Copilot CLI and Codex Desktop/App Server; Desktop verifies resolved policy and records reroutes |
| Approval mediation | Codex Desktop approvals are explicit: terminal prompt locally or bounded pending/approve/decline MCP flow; timeout defaults to deny |
| Project-access preflight | Local allowlist -> GitLab project/ref/files; actionable diagnostics and pinned revision; no automatic grant |
| Tunnel lifecycle | Existing-profile configure/start/status/stop/restart on macOS/Linux, optional exact Keychain lookup, owned process cleanup; foreground only |
| HTTPS migration | Offline preview and confirmed local URL updates, private backups and forward recovery |
| MCP surfaces | Read-oriented GitLab MCP plus optional local Bridge Preview orchestration MCP; see architecture for trust boundaries |

**Still planned / not on `main`:** persistent core TaskSpec/attempt records and bounded EvidencePack access (currently tracked separately), plus further native Git trust/destination-policy work. Workspace mutation locking and shared local/SSH finish gates are already implemented on `main`. See [Architecture](docs/ARCHITECTURE.md) for the current boundary.

API/MCP clients reject disabled TLS verification and every API redirect. Configure the final endpoint. `GITLAB_CA_BUNDLE` adds Python API/MCP trust, not native Git or the migration `--check-tls` probe. Keep those scopes distinct; see [runtime TLS](docs/HTTPS_API_TLS.md).

## Names, compatibility and contribution

**ReasonFirst** is the project; **ActualCoder** (`actual-coder`) is the high-level CLI. `gitlab-agent` is lower-level and `codingagent` is a compatibility alias. Distribution `chatgpt-selfhosted-gitlab-mcp`, Python package `gitlab_agent`, private config `~/.config/gitlab-agent/.env` and existing workspace paths are intentionally retained. Do not rename managed directories during an update.

Global editable commands follow their source checkout; use a separate worktree for PR experiments. Contributions and reproducible English/Chinese reports are welcome: [Contributing](CONTRIBUTING.md), [Security reporting](SECURITY.md#reporting-a-security-issue), [release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md). Never publish credential files, private source, migration backups or unreviewed logs in issues.

Licensed under **Apache License 2.0**; see [LICENSE](LICENSE). External tools have their own licenses/terms. This is an independent project, not an official OpenAI, GitHub or GitLab product.
