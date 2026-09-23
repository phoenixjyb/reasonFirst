# Mental model

ReasonFirst is easiest to understand as five layers connected by a feedback loop.

## 1. Reasoning

The strongest reasoning interface should spend its capacity on decisions where better reasoning changes the result:

- understanding the system;
- finding root causes;
- choosing architecture;
- defining scope and non-goals;
- writing acceptance criteria;
- reviewing implementation and CI evidence.

In the reference workflow, this is normal ChatGPT.

## 2. Task contract

A **TaskSpec** carries approved intent from the reasoning layer into execution. It is bound to the managed workspace identity and persists:

- goal;
- acceptance criteria;
- non-goals;
- project and pinned base identity;
- requested backend.

A later steering instruction can guide an attempt without silently rewriting the original task.

## 3. Control

ReasonFirst controls the boundary around execution:

- isolated managed workspaces;
- backend selection;
- WorkerPolicy;
- protected paths;
- validation commands;
- locking and publication rules;
- review and secret gates.

The control contract should remain stable even when the worker changes.

## 4. Execution

The coding worker does the high-volume implementation loop:

- inspect files;
- edit code;
- run allowed commands;
- react to compiler/test feedback;
- produce a concrete diff.

Supported worker surfaces include Codex CLI, GitHub Copilot CLI, and Codex Desktop/App Server.

## 5. Evidence

The worker's claim that it is "done" is not acceptance.

ReasonFirst returns inspectable evidence:

- status and changed paths;
- bounded diff;
- validation results;
- MR and matching-HEAD CI;
- TaskSpec/attempt metadata;
- recursively redacted **EvidencePack**.

That evidence goes back to ChatGPT and the human for the next decision.

## The invariant

> **Reasoning leads. Workers execute. Evidence returns. Humans decide.**

Changing a worker must not silently change task scope, trust boundaries, validation requirements, or publication policy.
