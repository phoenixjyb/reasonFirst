# ReasonFirst architecture

[简体中文](ARCHITECTURE_CN.md) · [Project README](../README.md) · [Workflow](WORKFLOW.md) · [CLI quickstart](ACTUAL_CODER_QUICKSTART.md)

ReasonFirst is a **ChatGPT-first reasoning, worker-execution coding orchestration system**. Normal ChatGPT is the default reasoning surface: architecture, diagnosis, scope, acceptance criteria and final review stay there. Implementation is delegated to a user-selected coding backend under explicit workspace, policy, validation and publication controls.

This document describes the current `main` architecture. It is intentionally more concrete than the design-philosophy document: every major box below maps to code that exists today.

## 1. System overview

```text
                  ┌──────────────────────────────┐
                  │ ChatGPT / strong reasoning  │
                  │ architecture · diagnosis    │
                  │ scope · acceptance · review │
                  └──────────────┬───────────────┘
                                 │ TaskSpec / intent
                                 ▼
                    ┌──────────────────────────┐
                    │ ReasonFirst control      │
                    │ policy · workspace       │
                    │ validation · evidence    │
                    └────────────┬─────────────┘
                                 │ bounded handoff
                                 ▼
                    ┌──────────────────────────┐
                    │ Coding worker            │
                    │ codex / copilot / desktop│
                    └────────────┬─────────────┘
                                 │ implementation
                                 ▼
                    managed worktree / SSH workspace
                                 │
                                 ▼
                    diff · EvidencePack · MR · CI
                                 │
                                 └──────────────► ChatGPT + human review
```

The important property is that **the reasoning layer leads, the worker executes, and the worker is replaceable**. Backend choice must not change the user-owned task scope, workspace identity, review gates or publication policy. Direct standalone use of ActualCoder remains useful for testing, recovery and automation, but it is an execution-engine capability rather than a separate product architecture.

## 2. The two MCP surfaces

ReasonFirst currently has two different MCP roles. They should not be treated as interchangeable.

### A. Read-only GitLab MCP

The original GitLab MCP exposes bounded repository, MR, pipeline and job inspection. It is suitable for connecting a reasoning client to GitLab through the supported tunnel flow.

Properties:

- read-oriented GitLab API access;
- project allowlist and TLS controls;
- no local coding-worker execution;
- no unpublished local-worktree access;
- useful for architecture review, code reading and CI/MR inspection.

### B. Bridge Preview orchestration MCP

The Bridge Preview under `tools/reasonfirst_v4_0_3/` is a more privileged **local orchestration surface**. The directory name is retained for compatibility and is not the product version.

It can:

- prepare managed local or configured SSH workspaces;
- start/continue/steer/interrupt Codex App Server work;
- expose bounded workspace reads, diffs and artifacts;
- surface pending App Server approvals;
- explicitly approve or decline individual requests;
- run complete finish-preview review gates;
- use structured remote validation when a user-owned container policy is configured.

Remote publication is intentionally more conservative: it is hidden and disabled by default and requires explicit `RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH=true`.

## 3. Worker backend model

The user owns the backend selection.

Canonical explicit names:

```text
codex-cli
copilot-cli
codex-desktop
```

Historical `codex` and `copilot` remain compatibility aliases.

Selection precedence:

```text
explicit --agent
      ↓
REASONFIRST_DEFAULT_BACKEND when non-auto
      ↓
repository agents.preferred when auto
      ↓
installed-backend fallback
```

The current fallback order remains Codex CLI then Copilot CLI unless a higher-priority preference applies.

### Shared WorkerPolicy

Codex CLI and Codex Desktop share one Codex policy model. Copilot maps the same higher-level intent into provider-specific controls where supported.

Typical Codex policy:

```text
model
reasoning_effort
execution_mode
sandbox_mode
approval_policy
network_access
```

Typical Copilot policy:

```text
model / reasoning_effort when explicitly pinned
execution_mode
disable_builtin_mcps
allow_tools
deny_tools
```

Policy values are user-owned configuration, not repository-granted permissions.

## 4. Policy verification

ReasonFirst distinguishes **requested policy**, **encoded policy** and **runtime-verified policy**.

### CLI workers

For CLI surfaces, ReasonFirst can prove that requested settings were encoded into direct subprocess argv. It does not claim provider-level model attestation when the CLI does not expose it.

Codex CLI additionally uses a no-model-inference App Server preflight for model/effort/admin-policy compatibility.

### Codex Desktop / App Server

The App Server adapter validates:

- requested model exists in the live model catalog;
- requested reasoning effort is supported;
- admin approval/sandbox restrictions allow the requested policy;
- `thread/start` resolves the requested model/effort and compatible approval/sandbox values.

A mismatch fails closed with `WORKER_POLICY_UNSATISFIED`.

Runtime model-reroute events are recorded as policy violations.

Current App Server does not independently expose the provider response-envelope model, so ReasonFirst does not claim provider-level model attestation beyond the resolved App Server thread state.

## 5. Approval model

For `codex-desktop` with `approval_policy=on-request`:

### ActualCoder CLI

A command/file/permission request is shown in the terminal. The user explicitly approves or declines. The default is deny.

### Bridge MCP

Approval requests become pending records. The reasoning client can inspect them through:

```text
reasonfirst_pending_approvals
reasonfirst_approve
reasonfirst_decline
```

Requests time out to deny. Permission grants default to **turn scope**, not session scope.

No approval path silently upgrades to unrestricted/full-access execution.

## 6. Workspace model

### Local workspaces

ActualCoder uses managed Git caches and isolated worktrees under the configured workspace root. A task owns a feature branch and pinned base identity.

### SSH workspaces

Remote execution is allowed only through **user-configured named targets**. MCP callers cannot introduce arbitrary SSH hosts or repository paths inline.

A configured target can carry:

- SSH host;
- repository root;
- Codex execution topology;
- network policy;
- optional structured validation policy.

### Workspace locks

ReasonFirst now has re-entrant cross-process file locking for mutation-critical paths. Local workspace mutations, validation steps and bridge-managed remote mutations are serialized instead of relying only on convention.

Locks reduce races but are not a general OS sandbox.

## 7. Remote validation

Arbitrary remote `bash -lc` execution is not part of the worker tool surface.

Remote validation is exposed only when the user configured a structured container policy, including an approved engine/image and executable allowlist.

The worker submits an argv array, not a shell string.

The runner enforces the configured worktree/cwd boundary and validation policy. The current design forbids implicit image pulls during validation, so a missing image fails instead of silently contacting a registry.

The container runner is a validation boundary, not permission to use arbitrary remote administration commands.

## 8. Review and finish gates

Local and remote publication review share the transport-neutral gate implementation in `src/gitlab_agent/review_gates.py`.

Evidence collected before publication includes, as applicable:

- required project validation results;
- changed paths;
- reviewability/completeness;
- full bounded review diff;
- protected-path changes;
- candidate secret scan;
- bounded commit-history secret scan;
- exact base/HEAD/candidate identity.

Publication is blocked when required validation fails, review/secret coverage is incomplete, protected changes lack explicit approval, or secret findings are unresolved.

`finish --dry-run` / bridge finish preview can run validation code. “Dry run” means no commit/push, not no execution.

## 9. Publication model

### Local publication — normal path

The default supported path remains:

```text
workspace
  → finish preview / review
  → human confirmation
  → feature branch
  → GitLab MR
  → matching-HEAD CI
  → human merge decision
```

### SSH remote publication — experimental

Remote publication is default-off.

When explicitly enabled, authorization binds:

- project;
- execution target;
- pinned base SHA;
- HEAD;
- branch;
- canonical origin and push URL;
- exact candidate Git tree;
- reviewed finish result.

The candidate tree and destination are rechecked immediately before publication.

The normal local finish path remains preferred unless the operator intentionally enables the experimental remote route.

## 10. Project contracts

Repository-owned `.actualcoder.yaml` can describe:

- base branch;
- backend preferences;
- required validation commands;
- protected paths;
- implementation instructions;
- required executables;
- MR conventions.

Repository configuration cannot expand the user's executable or project trust boundary.

Finish policy is pinned to the workspace base revision so the feature branch cannot silently rewrite its own publication rules.

## 11. Security boundaries

ReasonFirst is control software, not a universal sandbox.

Trust boundaries to keep distinct:

- reasoning service vs local host;
- read-only GitLab MCP vs Bridge Preview orchestration MCP;
- repository-owned project contract vs user-owned policy;
- coding-worker login/quota vs ReasonFirst configuration;
- local workspace vs user-configured SSH target;
- structured validation container vs arbitrary host execution;
- review evidence vs final human merge decision.

Secrets should never be passed as model prompts/tool arguments. The repository itself is protected by tracked-file/full-history secret scanning in CI.

## 12. Source map

| Concern | Current implementation |
| --- | --- |
| User config and backend defaults | `src/gitlab_agent/config.py` |
| Backend selection / CLI orchestration | `src/gitlab_agent/cli.py` |
| Worker policy / argv mapping | `src/gitlab_agent/worker_policy.py` |
| Codex App Server client | `src/gitlab_agent/codex_app_server.py` |
| Local workspace lifecycle | `src/gitlab_agent/workspace.py` |
| Cross-process locking | `src/gitlab_agent/locking.py` |
| Shared finish/review gates | `src/gitlab_agent/review_gates.py` |
| Local finish | `src/gitlab_agent/finish.py` |
| CI evidence | `src/gitlab_agent/ci_feedback.py`, `log_evidence.py` |
| Project contract | `src/gitlab_agent/project_config.py` |
| Durable TaskSpec / attempts | `src/gitlab_agent/task_state.py` + workspace state integration |
| Bounded EvidencePack | `src/gitlab_agent/evidence.py` |
| GitLab API | `src/gitlab_agent/gitlab_api.py` |
| Bridge Preview controller | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/controller.py` |
| SSH workspace / remote validation / reviewed push | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/remote_workspace.py` |
| Bridge target config | `tools/reasonfirst_v4_0_3/reasonfirst_codex_bridge/bridge_config.py` |
| Bridge MCP tools | `tools/reasonfirst_v4_0_3/reasonfirst_mcp_server.py` |

## 13. Current boundary and next work

Persistent TaskSpec/attempt records and the core bounded EvidencePack are now on `main`. The task contract is bound to workspace project/base identity, attempt history is bounded, and evidence generation is read-only and recursively redacted.

Similarly, GitLab is the implemented SCM/CI adapter today. Hosting ReasonFirst on GitHub does not imply a GitHub-target implementation workflow.

For philosophy and rationale, see [Design philosophy](DESIGN_PHILOSOPHY.md). For executable use, see [Getting started](GETTING_STARTED.md) and the [CLI quickstart](ACTUAL_CODER_QUICKSTART.md).
