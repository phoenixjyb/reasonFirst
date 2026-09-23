# ReasonFirst

**Put strong reasoning at the front of coding. Let coding agents execute.**

ReasonFirst is a reasoning-first coding orchestration system. The reference workflow keeps **ChatGPT (or another deliberately chosen strong reasoning interface)** responsible for architecture, diagnosis, scope, acceptance criteria, and review, while replaceable coding agents handle implementation.

[Get started](docs/GETTING_STARTED.md){ .md-button .md-button--primary }
[Understand the workflow](docs/WORKFLOW.md){ .md-button }
[中文](index_cn.md){ .md-button }

---

## The mental model

~~~text
ChatGPT / strong reasoning interface
        │
        │ read repository / MR / CI evidence
        ▼
architecture · diagnosis · scope · acceptance
        │
        ▼
TaskSpec — durable approved intent
        │
        ▼
ReasonFirst control
workspace · policy · validation · review gates
        │
        ▼
coding worker
codex-cli · copilot-cli · codex-desktop
        │
        ▼
implementation + tests + MR / CI
        │
        ▼
EvidencePack · diff · CI evidence
        │
        └──────────────► ChatGPT + human review
~~~

!!! info "One primary loop"
    ActualCoder can also be invoked directly from a terminal, CI job, IDE, or another client. That is useful for testing, recovery, and automation, but it is a secondary operational capability—not a separate product mode.

## Where should I start?

<div class="grid cards" markdown>

-   **I am new to ReasonFirst**

    Start with the [first-time setup](docs/GETTING_STARTED.md), then run the [practice lab](docs/PRACTICE_LAB.md).

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
