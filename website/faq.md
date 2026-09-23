# FAQ

## Why not let one coding agent do everything?

ReasonFirst separates high-value reasoning from high-volume execution. Architecture, ambiguous diagnosis, scope control, and final review benefit from the strongest reasoning interface. File edits and test iterations are delegated to replaceable coding workers.

## Is ChatGPT required for every command?

No. ActualCoder can run directly from a terminal, CI job, IDE integration, or another client. That capability is useful operationally, but the reference ReasonFirst workflow keeps ChatGPT at the reasoning forefront.

## Does ReasonFirst call the OpenAI model API?

No direct model-inference API calls are made by ReasonFirst. Coding backends use their own supported authentication and entitlement. A Tunnel runtime key, when used, is for the connection path rather than ReasonFirst model inference.

## Do I need the Tunnel?

You need the Tunnel for the documented normal-ChatGPT read connection to a self-hosted GitLab through the read-only MCP. The execution engine itself can operate without that connection.

## Which coding backend should I use?

ReasonFirst supports explicit <code>codex-cli</code>, <code>copilot-cli</code>, and <code>codex-desktop</code> selection. Backend choice should be a user decision; TaskSpec, workspace identity, validation, review gates, and publication policy should not depend on which worker is selected.

## What is TaskSpec?

TaskSpec is the durable task contract: original goal, acceptance criteria, non-goals, project/base identity, and requested backend. It prevents a multi-attempt implementation from silently losing the approved task boundary.

## What is EvidencePack?

EvidencePack is a bounded, recursively redacted, read-only evidence bundle for a managed workspace. It can include task/attempt metadata, project policy, changed paths, reviewability, diff, and optional CI evidence. Missing, stale, incomplete, or truncated evidence is reported explicitly.

## Is a green CI run enough to merge?

Not necessarily. Check that the CI belongs to the intended HEAD and covers the relevant validation. Also review the diff, protected paths, secret/history scans, and task acceptance criteria.

## Is a worktree a security sandbox?

No. ReasonFirst adds controls and review gates, but a Git worktree is not an OS security sandbox. Use trusted development hosts and trusted repositories, and read the [security policy](SECURITY.md).

## Why GitLab if the project is hosted on GitHub?

GitHub hosts the ReasonFirst source repository. GitLab is currently the implemented target SCM/CI adapter for managed project work. Source hosting does not imply a GitHub-target execution adapter already exists.

## Where should a new user begin?

Use [First 10 minutes](first-10-minutes.md) if your connection and worker are already available. Otherwise start with [First-time setup](docs/GETTING_STARTED.md), then use the [Practice lab](docs/PRACTICE_LAB.md) to rehearse the complete reasoning → worker → MR/CI → review loop.

## How should I bring worker results back to ChatGPT?

Prefer bounded evidence rather than raw transcripts. Generate `actual-coder evidence WORKSPACE` (optionally `--from-ci`) and provide the non-secret EvidencePack to the reasoning session. See [Evidence & review](evidence-review.md).

## What prompts should I use with ChatGPT?

Use prompts that keep facts, task definition, implementation, and review separate. The [Working with ChatGPT](chatgpt-workflow.md) page contains copyable prompt patterns for repository inspection, TaskSpec definition, implementation review, and CI repair.
