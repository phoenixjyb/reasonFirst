# ReasonFirst

<div class="rf-hero" markdown>

<p class="rf-eyebrow">Reasoning-first coding orchestration</p>

## Strong reasoning plans. Coding agents execute. Evidence comes back for review.

<p class="rf-lead">ReasonFirst keeps ChatGPT (or another deliberately chosen strong reasoning interface) responsible for architecture, diagnosis, scope, acceptance criteria, and review. Replaceable coding agents handle implementation inside a controlled workspace.</p>

[Start in 10 minutes](first-10-minutes.md){ .md-button .md-button--primary }
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

## Where should I start?

<div class="grid cards" markdown>

-   **I am new to ReasonFirst**

    Use [First 10 minutes](first-10-minutes.md) for the shortest guided path, then run the [practice lab](docs/PRACTICE_LAB.md) when you want a complete rehearsal.

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
