# Evidence & review

ReasonFirst treats **evidence as a first-class output**. A coding worker's completion message is useful context, but it is not proof that the task is acceptable.

## Generate evidence

At any point:

~~~bash
uv run actual-coder evidence "$WS"
~~~

To attach sanitized CI evidence:

~~~bash
uv run actual-coder evidence "$WS" --from-ci
~~~

Building an EvidencePack is read-only: it does not run repository code, mutate task state, commit, or push.

## What an EvidencePack contains

The current core shape includes:

| Field | Review purpose |
| --- | --- |
| `workspace` | Project/base/branch/HEAD identity, dirty/push/MR state. |
| `task` | Persistent TaskSpec and bounded attempt history. |
| `project_config` | Pinned-base `.actualcoder.yaml` policy/effective config. |
| `review.changed_paths` | What changed. |
| `review.reviewability` | Whether changed content is fully reviewable as bounded text. |
| `review.diff` | Bounded, redacted human-facing diff plus truncation metadata. |
| `ci` | Optional sanitized pipeline/job evidence. |
| `redactions` | Categories of sensitive text removed from exported evidence. |
| `warnings` | Missing/stale/incomplete/truncated evidence warnings. |
| `complete_for_human_review` | Whether core reviewability/diff/attached-CI completeness conditions are satisfied. |

!!! warning "Complete does not mean correct"
    `complete_for_human_review=true` means the evidence bundle is sufficiently complete for review under those checks. It does **not** prove business correctness, satisfy every acceptance criterion automatically, or authorize merge.

## Review in six passes

### 1. Identity

Confirm the intended project, pinned base, current HEAD, branch, and MR. Do not review a beautiful diff against the wrong revision.

### 2. Task boundary

Read the original goal, acceptance criteria, and non-goals. Later attempt steering should not silently rewrite them.

### 3. Diff coverage

Check changed paths, reviewability, and whether the diff is truncated. Incomplete review coverage is a blocker, not a minor warning.

### 4. Validation

Verify that the expected project validation actually ran and that the commands are appropriate for the project. A missing `.actualcoder.yaml` means there may be no project-specific test contract.

### 5. Security and publication gates

Check protected paths, candidate secret scan, bounded history scan, destination/candidate identity, and any explicit overrides.

### 6. CI freshness

CI should match the intended HEAD. Missing, stale, running, scheduled, or otherwise incomplete CI should stay visibly incomplete rather than being summarized as success.

## A compact review checklist

<div class="rf-checklist" markdown>

- **Identity:** correct project/base/HEAD/branch/MR?
- **Task:** goal + acceptance + non-goals unchanged?
- **Coverage:** all changed paths reviewable, diff not truncated?
- **Validation:** expected tests/commands actually ran?
- **Security:** protected paths and secret/history findings resolved?
- **CI:** matching HEAD and complete enough for the claim being made?
- **Decision:** continue, revise, publish, or merge—based on evidence?

</div>

## Review prompt for ChatGPT

~~~text
Review this EvidencePack against the persistent TaskSpec.
Do not treat complete_for_human_review as automatic acceptance.
For each acceptance criterion, cite the exact supporting evidence or mark it unverified.
Flag scope creep against non-goals.
Flag truncated, stale, missing, incomplete, or non-matching evidence.
Separate code-quality concerns from hard acceptance blockers.
Recommend only the smallest next action needed for a human merge decision.
~~~

## What evidence cannot prove by itself

EvidencePack does not automatically prove:

- product requirements were the right requirements;
- every relevant test in the world was run;
- a reviewer understood the domain semantics;
- deployment is safe;
- merge is authorized.

Those remain reasoning and human decisions.

Next: [Working with ChatGPT](chatgpt-workflow.md) · [Daily workflow](docs/WORKFLOW.md) · [Security](SECURITY.md)
