# First 10 minutes

**Goal:** experience the ReasonFirst loop once without losing the reasoning boundary.

!!! note "This is the short path"
    This page assumes the repository connection and at least one coding worker are already available. If they are not, use the [first-time setup](docs/GETTING_STARTED.md) first.

## 1. Read before you code

In normal ChatGPT, select the intended GitLab connection and start with a read-only request:

~~~text
Use the selected GitLab connection for team/project-a.
First identify the authenticated GitLab user and run the project-access preflight.
If access succeeds, read README.md and the files relevant to the problem at the resolved commit.
Separate observed facts from proposals. Do not modify anything yet.
~~~

The important outcome is not a long summary. It is a grounded understanding of the real repository revision you are about to change.

## 2. Turn the conversation into a bounded task

Before implementation, ask the reasoning layer to make the boundary explicit:

~~~text
Turn this into an implementation contract with:
- one goal;
- concrete acceptance criteria;
- explicit non-goals;
- likely affected components;
- risks or assumptions;
- evidence we should require before accepting the change.
Do not broaden the task beyond the evidence we have read.
~~~

A good task is small enough that a worker can execute it without making product or architecture decisions on its own.

## 3. Persist that intent as TaskSpec

From the ReasonFirst source checkout, prepare a managed workspace without launching a model:

~~~bash
uv run actual-coder start team/project-a \
  --task fix-timeout \
  --goal "Fix the timeout bug" \
  --acceptance "Timeout regression test passes" \
  --acceptance "Existing API remains compatible" \
  --non-goal "No unrelated refactor" \
  --no-launch
~~~

`--no-launch` still creates a real managed workspace. Save the returned workspace ID. The goal, acceptance criteria, and non-goals are persisted in the workspace TaskSpec.

!!! warning "Do not call start again to continue the same task"
    Keep the workspace ID. Use the returned handoff, `resume`, or `continue` for later attempts.

## 4. Let the worker execute

Review the returned workspace path, branch, backend selection, WorkerPolicy, and handoff. Then use the returned backend command/prompt to start the chosen worker.

The worker may inspect, edit, and run allowed validation, but it should not redefine the task. If it discovers a scope-changing issue, bring that evidence back to the reasoning layer instead of silently expanding the implementation.

## 5. Inspect evidence before publication

Set `WS` to the real returned workspace ID:

~~~bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder evidence "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
~~~

Check the diff, reviewability, validation, protected paths, secret/history coverage, and candidate identity. A worker saying “done” is not acceptance.

If the dry-run is intentionally clean and unblocked:

~~~bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
uv run actual-coder evidence "$WS" --from-ci
~~~

## 6. Return the evidence to ChatGPT

Bring the bounded evidence back to the reasoning session—through a connected orchestration surface when available, or by pasting/attaching the non-secret EvidencePack output.

Use a review request like:

~~~text
Review this implementation against the original TaskSpec.
Check each acceptance criterion and non-goal.
Distinguish verified evidence from missing, stale, incomplete, or truncated evidence.
Do not expand scope.
If the current HEAD has a real CI failure, identify the root cause and propose the smallest repair task.
Otherwise tell me whether more evidence is needed before I make the merge decision.
~~~

## You have completed the loop when…

<div class="rf-checklist" markdown>

- ChatGPT read the intended repository revision before implementation.
- The task has an explicit goal, acceptance criteria, and non-goals.
- One managed workspace owns the task.
- A replaceable worker executed within that boundary.
- ReasonFirst produced reviewable diff/validation evidence.
- Matching-HEAD CI evidence is understood rather than assumed.
- ChatGPT and the human made the next decision from evidence, not from the worker's success claim.

</div>

Next: [Working with ChatGPT](chatgpt-workflow.md) · [Evidence & review](evidence-review.md) · [Full daily workflow](docs/WORKFLOW.md)
