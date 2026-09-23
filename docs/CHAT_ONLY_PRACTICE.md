# Fully chat-based E2E practice

[简体中文](CHAT_ONLY_PRACTICE_CN.md) · [Practice lab](PRACTICE_LAB.md) · [Architecture](ARCHITECTURE.md)

This rehearsal validates the ReasonFirst product loop **from one normal ChatGPT conversation**, without asking the user to open a terminal or interact with Codex CLI manually.

The intended path is:

```text
ChatGPT
  → live GitLab read-only evidence
  → ReasonFirst Bridge managed workspace
  → Bridge-managed Codex App Server execution
  → ChatGPT diff / finish-preview review
  → explicit human publication approval
  → ReasonFirst snapshot-bound local finish
  → GitLab MR + matching-head CI
  → ChatGPT CI / EvidencePack review
```

The human still decides whether to merge. This practice does not enable auto-merge.

## Target exercise

Use the existing synthetic project:

```text
https://gitlab.recomo.com.cn/phoenixjyb/reasonfirst-practice
ref: main
```

The task is intentionally documentation-only and stays inside the current practice contract:

> Modify **README.md only**. Add a section titled  
> `## Chat-only E2E rehearsal / 全聊天闭环演练`  
> containing four concise bullets that state:
>
> 1. this repository is a synthetic documentation-only rehearsal;
> 2. ChatGPT owns reading, reasoning, scope and review while the Bridge-managed coding worker executes;
> 3. publication requires a reviewed finish preview plus explicit human approval;
> 4. final acceptance requires matching-head CI success.
>
> Do not modify Python code, tests, `.actualcoder.yaml`, `.gitlab-ci.yml`, `AGENTS.md`, or `EXERCISE.md`. Run the configured unit tests and preserve the existing README content.

This gives the chat a real edit, commit, branch, MR and CI without mixing in Stage 1/2/3 feature work.

## Required chat capabilities

The new ChatGPT conversation should have access to:

- the live **Recomo GitLab read-only MCP** for identity, access, source, MR and CI evidence;
- the **ReasonFirst Bridge Preview MCP** for managed workspace creation, worker execution, unpublished diff/review, controlled local publication, CI and EvidencePack.

For a fully chat-based run, the Bridge should expose at least:

```text
reasonfirst_doctor
reasonfirst_target_probe
reasonfirst_dispatch
reasonfirst_workspace_status
reasonfirst_read
reasonfirst_diff
reasonfirst_codex_start
reasonfirst_codex_status
reasonfirst_codex_events
reasonfirst_pending_approvals
reasonfirst_approve / reasonfirst_decline
reasonfirst_review_bundle
reasonfirst_finish_preview
reasonfirst_finish
reasonfirst_ci
reasonfirst_evidence
```

If any required write/read capability is missing, the chat must stop rather than substituting web search, shell commands, a manual Codex CLI session, or guessed state.

## Conversation script

### Prompt 1 — read and plan only

Paste this as the first message in a fresh normal ChatGPT conversation:

```text
We are running a fully chat-based ReasonFirst E2E rehearsal in:

https://gitlab.recomo.com.cn/phoenixjyb/reasonfirst-practice
ref = main

I do not want to run terminal commands or interact with Codex CLI manually.
Use only:
1) the selected live Recomo GitLab read-only MCP for GitLab repository/MR/CI evidence; and
2) the ReasonFirst Bridge Preview MCP for managed workspace and coding-worker execution.

Do not use web search, GitHub copies, old conversation results, or localhost/manual shell as substitutes.

Phase 1 is READ + PLAN ONLY. Do not modify files or start the coding worker yet.

First:
- identify the authenticated GitLab user;
- run project access preflight for the exact project/ref;
- require README.md, AGENTS.md, .actualcoder.yaml, .gitlab-ci.yml, EXERCISE.md,
  clip_summary.py, and tests/test_clip_summary.py;
- report the resolved commit SHA and any missing evidence.

Then read at least README.md, AGENTS.md, .actualcoder.yaml and .gitlab-ci.yml at the
resolved commit. Also call reasonfirst_doctor and reasonfirst_target_probe for the
local/default execution target.

Our exact task is documentation-only:
- modify README.md only;
- add heading: "## Chat-only E2E rehearsal / 全聊天闭环演练";
- add four concise bullets saying:
  1. this repo is a synthetic documentation-only rehearsal;
  2. ChatGPT owns reading/reasoning/scope/review and the Bridge-managed coding worker executes;
  3. publication requires a reviewed finish preview plus explicit human approval;
  4. final acceptance requires matching-head CI success;
- preserve all existing README content;
- run the configured unit tests;
- do not modify Python, tests, .actualcoder.yaml, .gitlab-ci.yml, AGENTS.md or EXERCISE.md.

Check this task against the repository contract/instructions. Give me:
- observed repository facts;
- the exact revision reviewed;
- a bounded implementation plan;
- acceptance criteria;
- non-goals;
- expected files changed.

STOP after the plan. Do not dispatch a workspace and do not start Codex until I approve.
```

Expected result: ChatGPT grounds itself in the real GitLab revision and proposes a one-file plan.

### Prompt 2 — execute, review, but do not publish

After checking the plan, reply:

```text
Approved. Execute that exact plan entirely through ReasonFirst Bridge Preview.

Use reasonfirst_dispatch for the exact GitLab project/ref and focus on README.md.
Pass the approved documentation goal plus the acceptance criteria and non-goals from Phase 1
into the dispatch call so they are persisted in the managed TaskSpec.
After dispatch:
- verify the managed workspace base SHA matches the revision we just reviewed;
- if it differs, re-read the relevant files from the managed workspace and reconcile before editing;
- do not create a second workspace.

Then start the Bridge-managed coding worker with the exact approved documentation-only goal,
acceptance criteria and non-goals.

Monitor the worker through reasonfirst_codex_status/events. Check pending approvals when
necessary. Do not approve requests that broaden scope, access credentials, change CI/policy,
publish, or touch files outside the approved README-only task.

When the worker is idle:
- get reasonfirst_workspace_status;
- get reasonfirst_review_bundle and reasonfirst_diff;
- verify README.md is the only changed path;
- verify the configured unit tests ran successfully;
- run reasonfirst_finish_preview with commit message:
  "docs: add chat-only E2E rehearsal note"

Do NOT publish yet.

Show me:
- workspace ID and branch;
- base SHA and current HEAD;
- changed paths;
- concise diff summary;
- exact validation results;
- protected-path and secret-scan status;
- blockers/warnings;
- the finish-preview snapshot digest.

STOP and wait for my explicit publication approval.
```

Expected result: the edit exists only in the managed workspace, all gates have been reviewed, and ChatGPT shows the exact snapshot digest.

### Prompt 3 — explicit publication approval

If the preview is correct, reply:

```text
I approve publication of exactly the reviewed finish-preview snapshot.

Use reasonfirst_finish with:
- the same thread/workspace;
- commit message "docs: add chat-only E2E rehearsal note";
- the exact snapshot digest from the preview;
- no protected-path override;
- no secret-scan override.

If the snapshot no longer matches, do not publish; rerun finish preview and show me the new
evidence instead.

If publication succeeds, report the commit SHA, feature branch and MR URL/IID returned.
Then call reasonfirst_ci once for the same workspace/thread.

Do not merge the MR.
If CI is still pending/running, report that accurately and stop rather than inventing success.
```

The snapshot digest is the publication handshake: if validation or workspace content changed after review, `reasonfirst_finish` fails closed and requires a fresh preview.

### Prompt 4 — final matching-head CI and evidence review

If CI was not terminal yet, send this after a short interval:

```text
Check the published candidate again, entirely through the connected tools.

Use reasonfirst_ci and reasonfirst_evidence(from_ci=true) for the same thread/workspace.
Also use the live read-only GitLab MCP to inspect the actual MR and its latest pipeline/jobs.

Verify:
- pipeline SHA exactly matches the published workspace HEAD;
- stale_for_workspace is false;
- the pipeline is terminal and successful;
- the real unit-tests job succeeded;
- there are no blocking failed jobs;
- EvidencePack is complete for human review;
- the MR diff still changes README.md only;
- all approved documentation acceptance criteria are satisfied.

Report the exact MR IID/URL, candidate SHA, pipeline ID/status and job result.
Do not merge. End with a clear statement of whether the rehearsal has reached the
human merge-decision point, and list any remaining evidence gap if not.
```

## What counts as a pass

The rehearsal passes when all of these are observed, not assumed:

- source was read from the intended live GitLab project at a reported revision;
- one managed workspace/branch was used;
- worker execution happened through the Bridge, not a manual CLI session;
- only `README.md` changed;
- finish preview was unblocked and had a concrete snapshot digest;
- the human explicitly approved that digest before publication;
- publication created/updated a real GitLab MR;
- CI was read back in chat and matched the published HEAD;
- the real configured unit-test job succeeded;
- final EvidencePack was complete for human review;
- no merge occurred automatically.

## Safety / failure controls

A few failures are useful evidence:

- if project access fails, stop before dispatch;
- if the managed workspace base differs from the reviewed revision, re-read/reason before editing;
- if the worker asks to touch CI/policy/protected files, decline and keep the original scope;
- if finish preview is blocked, do not publish;
- if the preview digest becomes stale, `reasonfirst_finish` must refuse publication;
- if CI reports a runner-only failure, use its structured diagnosis and do not invent an application-code repair;
- pending CI is not success.

This is deliberately a small docs-only task so the exercise evaluates the **orchestration loop**, not coding difficulty.
