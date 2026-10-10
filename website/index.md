# ReasonFirst

<div class="rf-hero" markdown>

<p class="rf-eyebrow">Reasoning-first coding orchestration</p>

## Strong reasoning plans. Coding agents execute. Evidence comes back for review.

<p class="rf-lead">ReasonFirst keeps ChatGPT (or another deliberately chosen strong reasoning interface) responsible for architecture, diagnosis, scope, acceptance criteria, and review. Replaceable coding agents handle implementation inside a controlled workspace.</p>

[Install ReasonFirst](docs/INSTALL.md){ .md-button .md-button--primary }
[Start in 10 minutes](first-10-minutes.md){ .md-button }
[See the daily workflow](docs/WORKFLOW.md){ .md-button }
[中文](index_cn.md){ .md-button }

</div>

## How ReasonFirst works

<div class="rf-flow">
  <div class="rf-flow-step"><span class="rf-flow-label">1 · Reason</span><strong>ChatGPT</strong><small>Read repository/MR/CI evidence. Diagnose, choose scope, write acceptance criteria.</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">2 · Contract</span><strong>TaskSpec</strong><small>Persist the approved goal, non-goals, acceptance criteria, and pinned workspace identity.</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">3 · Execute</span><strong>Coding worker</strong><small>Codex CLI, Copilot CLI, or Codex Desktop implements the bounded task.</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">4 · Prove</span><strong>EvidencePack + CI</strong><small>Return bounded diff, validation, reviewability, and CI freshness/completeness evidence.</small></div>
  <div class="rf-flow-step"><span class="rf-flow-label">5 · Decide</span><strong>ChatGPT + human</strong><small>Review evidence, continue or revise the task, and make the merge decision.</small></div>
</div>

!!! info "One primary loop"
    ActualCoder can also run directly from a terminal, CI job, IDE, or another client. That is useful for testing, recovery, and automation, but it is a secondary operational capability—not a separate product mode.

## v0.5.2 setup reuse and service checks

v0.5.2 adds clearer controls for existing installations:

- Reuse valid saved configuration with `reasonfirst setup --reuse-existing`; choose `--reconfigure` when you intend to change it.
- Inspect the installed CLI, selected configuration and known saved deployment with `reasonfirst status`. Running-service identity remains a separate check.
- Advanced operators can prepare a separate runtime offline on macOS/Linux and review the known macOS deployment before planning service changes.

**Packaged and source installation remain first-class routes** to the same guided setup and saved state. The advanced service features retain their platform and compatibility limits; installing v0.5.2 does not activate or migrate an existing service.

Use the v0.5.2 wheel only after its GitHub Release assets are published and the public download has been checked. See the release notes for release status and evidence tied to the exact source. Installation checks do not verify provider login or the browser-side ChatGPT connection.

[Read the v0.5.2 release notes](docs/RELEASE_NOTES_0.5.2.md){ .md-button }
[Choose an installation route](docs/INSTALL.md){ .md-button }

## v0.5.0 validated baseline

v0.5.0 brings the architecture shown above together as one tested system: **three worker backends** (`codex-cli`, `copilot-cli`, `codex-desktop`), **two deliberately different MCP surfaces** (read-only GitLab evidence vs. optional Bridge Preview orchestration), persistent **TaskSpec/attempt** state, cross-process workspace mutation locking, bounded **EvidencePack**, shared finish/review gates, and matching-HEAD CI feedback.

The pre-release audit baseline `7d16061061f6337604bd3135c9e4a693ad1fd68a` passed all seven release-critical CI jobs plus the documentation deployment. The main validation job ran **445 tests**; the package job built wheel + sdist, inspected archive paths, clean-installed the wheel, verified version/CLI entry points, and reran source/full-history secret scanning. The published v0.5.0 tag identifies the frozen release, not this earlier audit baseline. This historical test count is not the current suite count.

[Read the v0.5.0 release notes](docs/RELEASE_NOTES_0.5.0.md){ .md-button }
[Review the architecture](docs/ARCHITECTURE.md){ .md-button }

!!! note "Bridge Preview availability"
    The read-only GitLab MCP is the portable evidence connection. Bridge Preview requires a connected client/workspace that permits its more privileged custom-MCP actions. If that surface is unavailable, use terminal ActualCoder; TaskSpec, workspace identity, validation and review gates remain the same.

## Where should I start?

<div class="grid cards" markdown>

-   **I am new to ReasonFirst**

    Start with [Install & update](docs/INSTALL.md). Choose the packaged quick route or the fully supported source/developer route; both converge on `reasonfirst setup`. Then use [First 10 minutes](first-10-minutes.md) for the shortest guided loop.

-   **I already connected ChatGPT to GitLab**

    Follow the [daily workflow](docs/WORKFLOW.md): reason in ChatGPT, hand off a bounded task, then return evidence for review.

-   **I need to configure the coding worker**

    Use the [worker / CLI setup guide](docs/ACTUAL_CODER_QUICKSTART.md). This is the execution-engine reference.

-   **Something is not working**

    Use [troubleshooting](docs/TROUBLESHOOTING.md) and keep API, Git, Tunnel, worker login, workspace, and CI failures separate.

</div>

## Core concepts

| Concept | What it means |
| --- | --- |
| **Reasoning layer** | ChatGPT reads evidence, diagnoses, chooses architecture, constrains scope, and reviews outcomes. |
| **TaskSpec** | Persistent goal, acceptance criteria, non-goals, and workspace/base identity. |
| **Worker** | Replaceable executor: Codex CLI, Copilot CLI, or Codex Desktop/App Server. |
| **WorkerPolicy** | User-owned model/effort/sandbox/approval/network/tool constraints. |
| **EvidencePack** | Bounded, recursively redacted, read-only evidence for returning implementation state to the reasoning layer. |
| **Finish gates** | Validation, reviewability, protected-path, secret/history, and candidate-identity checks before publication. |

## What ReasonFirst is not

ReasonFirst is **not** a model proxy, quota-transfer service, universal security sandbox, or auto-merge bot. It does not make direct model-inference API calls. Coding backends use their own authentication and entitlement.

For the detailed trust model, see [Architecture](docs/ARCHITECTURE.md) and [Security](SECURITY.md).
