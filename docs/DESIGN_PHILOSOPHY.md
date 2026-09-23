# ReasonFirst Design Philosophy

[Current architecture](ARCHITECTURE.md) · [Workflow](WORKFLOW.md) · [Security](../SECURITY.md)

> **Reasoning-first coding orchestration**
>
> Use your strongest reasoning model for reasoning. Let coding agents do the coding.

## 1. The core idea

Modern software work contains two very different kinds of computation.

The first is **high-value reasoning**:

- understanding an unfamiliar system;
- researching alternatives;
- deciding architecture;
- decomposing an ambiguous task;
- identifying the real root cause;
- deciding what *not* to change;
- reviewing a proposed implementation;
- interpreting CI and review evidence.

The second is **high-volume execution**:

- opening files;
- searching symbols;
- making edits;
- generating boilerplate;
- running tests;
- fixing formatting;
- iterating on compiler/test feedback;
- producing a concrete diff.

ReasonFirst deliberately does not treat these as the same job.

A strong conversational reasoning model should spend its capacity on the decisions where better reasoning changes the outcome. Coding agents should handle the repetitive implementation loop they are optimized for.

That is the meaning of **reasoning-first**.

In the reference ReasonFirst workflow, **normal ChatGPT is the default Reasoning Plane**. The point is not merely to launch a coding agent safely; it is to keep the strongest conversational reasoning capability at the front of the engineering loop and make the coding agent a replaceable executor. Direct standalone ActualCoder use is intentionally possible for testing, recovery and automation, but it is not the design center.

## 2. The economic idea

ReasonFirst is also about using existing subscriptions efficiently.

Many developers already have access to more than one AI product:

- a high-capability conversational/reasoning experience;
- a coding-agent subscription or included coding quota;
- local development tools and CI.

Using the strongest reasoning experience for every file edit and test iteration can waste scarce/high-value reasoning capacity. Conversely, asking a lower-cost coding agent to make architectural or ambiguous product decisions can reduce quality.

ReasonFirst separates those roles:

```text
high-value reasoning capacity
        ↓
architecture / planning / debugging / review

coding-agent entitlement / quota
        ↓
inspection / editing / tests / iteration

local machine + SCM + CI
        ↓
deterministic execution and evidence
```

The goal is not billing arbitrage and ReasonFirst does not proxy model APIs.

The goal is simple:

> **Spend reasoning capacity on reasoning. Spend coding-agent quota on coding.**

Each backend continues to use the user's own authenticated session, subscription, or entitlement.

## 3. Four planes

### 3.1 Reasoning Plane

The Reasoning Plane is where the strongest available conversational model works with the human.

Typical responsibilities:

- research;
- system understanding;
- architecture;
- task specification;
- decomposition;
- risk analysis;
- debugging strategy;
- code review;
- CI interpretation;
- deciding the next task.

ChatGPT is the first-class reasoning interface for the current system, but the architecture should not require ReasonFirst to be permanently tied to one model family.

The Reasoning Plane should be **interactive**. It benefits from dialogue, clarification, context, and human judgment.

### 3.2 Execution Plane

The Execution Plane contains replaceable coding surfaces such as:

- Codex CLI (`codex-cli`);
- GitHub Copilot CLI (`copilot-cli`);
- Codex Desktop / App Server (`codex-desktop`);
- future local or subscription coding agents.

These agents receive a bounded task context and work inside an isolated workspace.

Typical responsibilities:

- repository inspection;
- code edits;
- local test/build loops;
- implementation iteration;
- preparing a candidate change.

Coding backends are **replaceable workers**, not the source of truth for project policy or Git lifecycle.

ReasonFirst should make switching execution backends cheap.

### 3.3 Control Plane

The Control Plane turns reasoning into safe, reproducible software work.

Today this is implemented by **ActualCoder + gitlab-agent**.

Responsibilities include:

- backend selection;
- project policy;
- isolated worktree creation;
- task handoff;
- executable allowlists;
- validation;
- diff/review gates;
- protected paths;
- credential/secret checks;
- commit;
- feature-branch push;
- Merge Request lifecycle;
- workspace recovery;
- cross-process workspace mutation locking;
- explicit worker approval mediation;
- structured remote validation for configured SSH targets;
- shared local/remote review gates.

The key principle is:

> **The model may propose and execute inside a controlled workspace; the control plane owns state transitions.**

This is why ReasonFirst does not simply give a coding model unrestricted shell/Git access and hope for the best.

### 3.4 Feedback Plane

Software development is a loop, not a one-shot generation task.

The Feedback Plane returns real evidence to reasoning:

- local test results;
- compiler/build failures;
- Git diff;
- Merge Request state;
- GitLab pipeline status;
- failed CI job logs;
- later: review discussions and other engineering signals.

The loop is:

```text
reason
  ↓
specify
  ↓
delegate
  ↓
implement
  ↓
validate
  ↓
observe evidence
  ↓
reason again
```

ReasonFirst should prefer **evidence-driven iteration** over autonomous retry loops.

## 4. Human agency is part of the architecture

ReasonFirst is designed to help humans make better engineering decisions, not to remove them from important state transitions.

The system may:

- inspect;
- reason;
- edit;
- test;
- prepare a diff;
- commit after controlled review;
- create/update its feature-branch MR;
- read CI evidence.

The system should not silently:

- push directly to the base branch;
- force-push;
- approve its own MR;
- merge its own MR;
- weaken tests merely to get green CI;
- deploy production changes.

The intended boundary is:

```text
AI reasons / implements / validates / proposes
                    ↓
             feature branch + MR
                    ↓
             CI + human review
                    ↓
          human/team decides merge
```

## 5. Strong reasoning should stay outside the repetitive inner loop

A central ReasonFirst principle is that the highest-capability model does **not** need to perform every mechanical coding iteration.

For example:

```text
Reasoning Plane:
"Trace this failure through the architecture.
The likely invariant is violated in component X.
Change Y, preserve Z, and add regression case Q."

Execution Plane:
inspect X
→ edit Y
→ run Q
→ fix compile issue
→ rerun tests
→ return diff/result
```

Then the Reasoning Plane can review the evidence instead of spending its capacity on every keystroke.

This creates a natural hierarchy:

```text
strong reasoning model = architect / debugger / reviewer / task lead
coding agent           = implementation specialist
ActualCoder            = execution governor
Git/CI                 = evidence and state
human                  = owner of intent and merge decision
```

## 6. Model- and provider-neutral by design

ReasonFirst currently has strong GitLab integration because that is where the system was first validated.

That should not define the product.

Likewise, Codex and Copilot are current execution backends, not permanent architectural assumptions.

The abstraction should remain:

```text
Reasoning Interface
        ↓
ReasonFirst
        ↓
Coding Backend(s)
        ↓
Workspace / SCM / CI adapters
```

Today:

```text
ChatGPT
   ↓
ReasonFirst
   ↓
ActualCoder
   ├── Codex
   └── Copilot
   ↓
GitLab
```

Possible future directions:

```text
other reasoning interfaces
other coding agents
GitHub
other SCM/CI systems
container/VM execution
review-discussion feedback
```

The core thesis should survive all of those substitutions.

## 7. Separation of intelligence from authority

ReasonFirst intentionally separates **intelligence** from **authority**.

A model can be highly capable without needing unlimited authority over:

- the filesystem;
- credentials;
- branches;
- remote repositories;
- CI configuration;
- deployment.

This separation is fundamental.

The Reasoning Plane can be smart.
The Execution Plane can be productive.
The Control Plane should still be deterministic and restrictive.

## 8. Repository and CI content are data, not authority

Repository files, project instructions, dependency scripts, MR text, and CI logs may contain instructions that conflict with the user's intent.

ReasonFirst therefore treats them as scoped inputs:

- repository instructions cannot elevate local executable permissions;
- project policy is bounded and validated;
- protected policy files receive additional review;
- CI logs are treated as untrusted diagnostic data;
- stale CI is not attached to a newer workspace state;
- remote evidence cannot silently override the user's goal.

This is a general rule:

> **Content can inform reasoning; content does not automatically gain authority.**

## 9. Deterministic state over agent memory

ReasonFirst should not rely on a coding agent remembering what branch, MR, or validation state it was using.

Important state belongs in explicit structures:

- workspace ID;
- base SHA;
- feature branch;
- project contract;
- pushed state;
- MR URL;
- reviewed-state fingerprint;
- CI pipeline SHA.

The coding backend can be replaced mid-task without changing that state.

This allows:

```text
Codex
  ↓
same workspace
  ↓
Copilot
  ↓
same branch / HEAD / MR
```

The agent is replaceable; the workflow state is durable.

## 10. Subscription-efficient, not backend-maximalist

ReasonFirst does not assume the most capable model should do everything.

It asks a different question:

> **Which available capability is the best use of each stage of the workflow?**

That often means:

- strongest chat/reasoning model for architecture and ambiguous decisions;
- cheaper or subscription-included coding agent for repetitive implementation;
- deterministic local tools for tests and Git state;
- CI for independent integration evidence;
- humans for intent, risk acceptance, and merge.

This combination can produce a better quality/cost balance than treating a single model as the entire software-development system.

## 11. No hidden model API dependency

ReasonFirst itself should remain usable without introducing a second, hidden model-inference bill.

The current implementation intentionally makes no direct OpenAI model API calls.

The Reasoning Plane uses the user's chosen chat experience.
The Execution Plane uses the coding backend's existing authenticated CLI/session/entitlement.

This is an architectural feature, not an incidental implementation detail.

## 12. The product boundary

ReasonFirst is **not**:

- an autonomous software company;
- a general unrestricted shell agent;
- a model proxy;
- a model API router;
- a replacement for Git/CI;
- an auto-merge bot.

ReasonFirst **is**:

> **A reasoning-first orchestration layer that connects high-capability interactive reasoning to controlled coding-agent execution and real software-engineering evidence.**

## 13. Design principles

When adding a feature, prefer designs that preserve these principles:

1. **Reason before execution.**
2. **Delegate mechanical work.**
3. **Keep backends replaceable.**
4. **Keep state explicit and durable.**
5. **Separate intelligence from authority.**
6. **Treat repository/CI text as scoped data, not authority.**
7. **Prefer evidence loops over blind autonomous retries.**
8. **Keep remote writes controlled and reviewable.**
9. **Use existing subscription entitlements efficiently.**
10. **Keep humans in control of high-impact decisions.**
11. **Avoid hidden model-API cost.**
12. **Stay provider-neutral at the architectural level.**

## 14. One-sentence definition

> **ReasonFirst is reasoning-first coding orchestration: it lets a high-capability conversational model lead software work while replaceable coding agents execute inside a controlled, evidence-driven development workflow.**
