# Working with ChatGPT

ReasonFirst works best when ChatGPT remains the **reasoning and review layer**, rather than becoming an invisible wrapper around a coding agent.

## Who owns what?

| Layer | Primary responsibility |
| --- | --- |
| **ChatGPT / reasoning interface** | Read evidence, diagnose, choose architecture, define scope/non-goals/acceptance, review outcomes. |
| **ReasonFirst** | Persist TaskSpec, control workspace/backend policy, run review gates, produce bounded evidence. |
| **Coding worker** | Inspect files, edit code, run allowed commands, iterate on implementation feedback. |
| **Human** | Approve scope changes, publication, and final merge/deployment decisions. |

Changing the worker should not change the approved task.

## Prompt 1 — inspect before proposing

Use this when introducing a repository or a new problem:

~~~text
Use the selected GitLab connection only.
First confirm the authenticated identity and project access.
Read the relevant source at the resolved revision.
Separate:
1. facts directly supported by files / MR / CI;
2. uncertainties or missing evidence;
3. proposed changes.
Do not implement anything yet.
~~~

Why this matters: architecture discussions become much more reliable when the reasoning layer is pinned to actual source rather than filenames, summaries, or old chat context.

## Prompt 2 — define a worker-safe task

Before implementation:

~~~text
Convert the agreed change into a bounded implementation contract.

Return:
- Goal
- Acceptance criteria
- Non-goals
- Affected components
- Constraints / compatibility requirements
- Validation we expect
- Evidence required before acceptance

Do not delegate unresolved product or architecture decisions to the coding worker.
~~~

Then persist that boundary with `actual-coder start ... --acceptance ... --non-goal ...`.

## Prompt 3 — review an implementation

After ReasonFirst produces an EvidencePack or finish preview:

~~~text
Review this implementation against the original TaskSpec.

For every acceptance criterion:
- identify the exact supporting evidence;
- mark it unverified if the evidence is missing, stale, incomplete, or truncated.

Check non-goals for scope creep.
Check whether the diff matches the stated root cause.
Check validation and matching-HEAD CI separately.
Do not treat the worker's own summary as proof.

Return:
1. verified findings;
2. blockers / missing evidence;
3. the smallest next action, if any.
~~~

## Prompt 4 — repair real CI failure

Only use this when CI evidence belongs to the current workspace HEAD:

~~~text
The attached CI evidence is for the current HEAD.
Identify the root cause of the failing job.
Keep the original TaskSpec unchanged unless I explicitly approve a scope change.
Propose the smallest repair attempt and the validation that should prove it.
Do not change code merely because older or unrelated CI failed.
~~~

Then use `resume --from-ci` or `continue --from-ci` for that same workspace.

## When ChatGPT should stop and ask

ChatGPT should not silently hand work to the worker when:

- the project/ref was not actually read;
- the task has no testable acceptance criteria;
- a new finding materially changes scope or architecture;
- repository policy or protected paths conflict with the proposal;
- CI evidence is stale for the current HEAD;
- evidence is truncated or reviewability is incomplete;
- credentials or private configuration would need to enter the prompt.

## Keep prompts small and evidence-rich

A useful reasoning session does not need the entire repository or full raw logs. Prefer:

- pinned relevant files;
- TaskSpec;
- bounded changed paths/diff;
- validation results;
- sanitized failing-job evidence;
- matching-HEAD pipeline status.

This is exactly why ReasonFirst persists task intent and produces bounded EvidencePack output.

Next: [First 10 minutes](first-10-minutes.md) · [Evidence & review](evidence-review.md)
