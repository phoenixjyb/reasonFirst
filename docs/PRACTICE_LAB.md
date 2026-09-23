# Practice lab: first ChatGPT read to three MR review rounds

[简体中文](PRACTICE_LAB_CN.md) · [Project access first](PROJECT_ACCESS.md) · [Workflow](WORKFLOW.md) · [Handoff template](TASK_HANDOFF_TEMPLATE.md) · [Seed files](../examples/practice-lab/)

Use **one dedicated private GitLab practice project, one managed workspace, one feature branch and one MR with three reviewed revisions**. Never switch this rehearsal to an allowed production application or reuse an old smoke workspace to get past an access failure. The clip-summary exercise has no robot controls, dependencies, network IO or deployment. Normal ChatGPT is the reasoning interface; the localhost Assistant is not used.

## Fast path: diagnose first, then create one bounded Stage-1 workspace

For an already-created synthetic practice project, use the productized preflight before starting any worker:

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
PROJECT="team/reasonfirst-practice"

actual-coder practice-doctor "$PROJECT" --ref main --agent copilot-cli
```

`practice-doctor` is read-only. It checks the effective allowlist, the seven practice-kit files, the pinned `.actualcoder.yaml` contract and required `unit-tests`, the requested worker executable, proxy variables that can affect raw `git`/`curl`/`gitlab-runner`, local GitLab Runner executor configuration, and project-visible runner eligibility when the GitLab API exposes it. It deliberately reports known runner failures such as a `custom` executor without `RunExec` before a coding task starts.

Only when it reports `ready_for_stage1: true`, create the canonical Stage-1 workspace:

```bash
actual-coder practice-start "$PROJECT" --ref main --agent copilot-cli
```

This command persists the canonical Stage-1 goal, acceptance criteria and non-goals, creates exactly one managed workspace/handoff, and **never auto-launches the worker**. Inspect `status` and `evidence` first, then explicitly launch on the same workspace with `resume --agent copilot-cli --launch`. Do not call `practice-start` again to continue the task.

If GitLab CI later fails, run `actual-coder ci "$WS"` before asking a worker to repair anything. CI output now includes a conservative `diagnosis`. Recognized runner-only failures (for example `custom executor is missing RunExec`) explicitly set `worker_repair_recommended: false`; fix/retry infrastructure on the same candidate commit instead of changing application or protected CI code.

A shell GitLab Runner is acceptable for this synthetic lab when host `python3` satisfies the exercise, but shell executors ignore `.gitlab-ci.yml` `image:` declarations. Use Docker/another container executor when reproducibility against the declared `python:3.12-slim` image matters.

The longer sections below remain the auditable/manual fallback and explain every gate.

## 0. Confirm the destination; do not assume it exists

`team/reasonfirst-practice` and `gitlab.example.com` below are placeholders, not provisioned resources. A local folder or this GitHub starter kit does not create a GitLab project. Before setup, **tell the user that the proposed project's existence, seed and access are unverified**, and ask them to confirm the exact GitLab instance and namespace/project or numeric ID.

Call the live MCP `check_project_access` for that confirmed identifier. If it is not allowlisted, the check sends no GitLab requests and existence remains unknown. If GitLab returns 404, report **missing or inaccessible**, not definitely absent. Show the returned user action and stop. Only after the user confirms nonexistence and explicitly approves creation should they create a **private blank project without an initial README**. Do not create a duplicate, assume `main`, or broaden access automatically. An existing lab can be used after confirming its intended contents; do not overwrite it.

Per-project authorization is **`GITLAB_ALLOWED_PROJECTS` in the effective local MCP configuration**, not an OpenAI tunnel setting. The user/operator appends only the approved project, preserves existing entries, handles stale exported overrides privately, and restarts the existing MCP/tunnel. GitLab membership/scopes are a separate grant. An empty allowlist can permit all token-accessible projects; never clear it as a workaround. See [the access guide](PROJECT_ACCESS.md). Do not inspect or request credential files, password stores or shell profiles. Use valid, unexposed least-privilege credentials; revoke exposed credentials before authenticated lab operations.

For a new lab, arrange an authorized runner for unprotected MR branches before seeding. The sample uses `python:3.12-slim`; a shell runner needs python3 installed. Confirm image access/tags with the administrator, without copying production variables/secrets or enabling deployment. No runner means CI remains untested, not successful.

### Seed a user-approved new empty project

From a reviewed ReasonFirst checkout containing this kit, export only tracked seed files. This does not switch the source branch or copy user credentials. The local destination must not exist. Run the explicit Bash block from that source checkout:

```bash
bash <<'BASH'
set -euo pipefail
SRC="$PWD"
LAB="$HOME/Projects/reasonfirst-practice"
test ! -e "$LAB"
test ! -L "$LAB"
ARCHIVE="$(mktemp)"
trap 'rm -f "$ARCHIVE"' EXIT
git -C "$SRC" archive --format=tar HEAD:examples/practice-lab > "$ARCHIVE"
mkdir -p "$LAB"
tar -xf "$ARCHIVE" -C "$LAB"
cd "$LAB"
python3 -m unittest discover -s tests -v
git init -b main
git add .
git commit -m "chore: seed synthetic ReasonFirst practice"
printf 'Prepared local lab: %s\n' "$LAB"
BASH
```

Expect **five baseline tests**, not completion of EXERCISE.md. If Git needs author identity, configure it deliberately in this new repository; do not rerun the export over existing files. Review seed/runner settings, then replace the example URL and run the following **one-time bootstrap push only to the approved empty project**:

```bash
cd "$HOME/Projects/reasonfirst-practice"
LAB_REMOTE="https://gitlab.example.com/team/reasonfirst-practice.git"
git remote add origin "$LAB_REMOTE"
git remote get-url origin
git push -u origin main
```

Use approved native Git authentication; never put a token in a URL/command. ReasonFirst askpass is not automatically a global Git credential helper. On failure, stop and reconcile rather than force-pushing, broadening scopes, or changing the remote blindly. This bootstrap is not an ActualCoder task publication.

Keep the following Terminal for the actual `PROJECT`, `WS`, `WT` and `NOTES` values. **Run each command individually and stop on any failure**:

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
PROJECT="team/reasonfirst-practice"
actual-coder-check-project "$PROJECT" --ref main --require-file README.md --require-file EXERCISE.md --require-file AGENTS.md --require-file .actualcoder.yaml --require-file .gitlab-ci.yml --require-file clip_summary.py --require-file tests/test_clip_summary.py
actual-coder doctor
actual-coder project-config "$PROJECT" --validate
codex login status
```

Require preflight `ok: true` and `workspace_policy_allowed: true`; these do not prove Git write authorization or the running MCP's configuration. Require project contract `found: true`, `valid: true`, required `unit-tests` and intended protected paths. The contract must exist on the base before `start`; later edits are not retroactive policy. Confirm the baseline GitLab pipeline actually ran `unit-tests`. Codex login status is not a quota check; confirm the intended signed-in account/entitlement. The tunnel runtime key is separate.

## 1. First normal ChatGPT conversation: access gate before file reads

Select the real GitLab MCP connection. Replace the proposed project only with the user-confirmed exact identifier, then send:

```text
We are rehearsing ReasonFirst in team/reasonfirst-practice only, at ref main.
Use the selected live GitLab MCP, not web search, earlier chat results or the
localhost Assistant. First call gitlab_whoami, then check_project_access with
this exact project/ref and required_files=["README.md","EXERCISE.md","AGENTS.md",
".actualcoder.yaml",".gitlab-ci.yml","clip_summary.py","tests/test_clip_summary.py"].

If the tool is unavailable, report the missing capability and request an MCP
upgrade/tool rediscovery. If ok=false, show error.code, stage, HTTP status if
returned, known/unknown project existence, and next_steps. STOP and wait for
user/operator confirmation, grant or initialization. Do not bulk-retry files,
change access, create a project, substitute a production project, or plan Stage 1.
A 404 or empty listing is not proof of nonexistence.

Only after successful preflight, read those seven files at resolved_commit_sha.
Report actual revision identifiers and missing evidence. Then review Stage 1
and prepare a handoff with acceptance tests. One workspace/branch/MR, three
explicitly approved stages; do not implement future stages, publish, merge,
deploy, or inspect credentials. Repository instructions remain subordinate to
these approved constraints. Do not claim a tool call happened when unavailable.
```

Successful identity alone is insufficient. Successful file HEAD metadata is not a source-code review. Record actual live file results at the pinned revision. A GitHub copy or pasted file is not proof of the requested GitLab connection. Only then approve Stage 1 and retain its plan privately using the [manual template](TASK_HANDOFF_TEMPLATE.md).

## 2. Stage 1: implement locally, then publish the first MR

After that approval, create **one** managed workspace without launching a model. Run individually, stopping on failure:

```bash
umask 077
NOTES="$(mktemp -d "$HOME/reasonfirst-practice-notes.XXXXXX")"
actual-coder start "$PROJECT" --task clip-summary --agent codex --goal "Implement EXERCISE.md Stage 1 only. Edit clip_summary.py, tests/ and README.md only. Do not commit, push, merge, deploy or read credentials. Stop for human-reviewed finish." --no-launch > "$NOTES/start.json"
WS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workspace"]["workspace_id"])' "$NOTES/start.json")"
WT="$(actual-coder path "$WS" --plain)"
printf 'Workspace: %s\nWorktree: %s\nPrivate notes: %s\n' "$WS" "$WT" "$NOTES"
```

This fetches code and writes local state; it is not an offline/no-write preview. Inspect the real workspace/base/branch and compare the base with the revision reviewed by ChatGPT. If it changed, review the new base before implementation. Save values privately, but do not execute saved notes as shell scripts. **Never repeat `start` just to continue this task.**

Generated handoffs still mention low-level commit/push and resume context remains incomplete. Review their output, but supply the explicit approved limits below. This exercise does not implement automatic TaskSpec ingestion or fix every generated-handoff path.

Launch ordinary Codex in the same worktree:

```bash
(
unset CONTROL_PLANE_API_KEY GITLAB_TOKEN GITLAB_GIT_TOKEN OPENAI_API_KEY CODEX_API_KEY
codex --cd "$WT" --sandbox workspace-write --ask-for-approval on-request
)
```

Removing those child environment values does not remove filesystem credentials or all provider settings. Do not approve credential reads, unrelated files, network/publication or policy changes for this offline exercise. A worktree and instructions are not complete OS isolation; preserve the client's managed controls. Paste the approved ChatGPT plan, plus:

```text
Stage 1 only. Read EXERCISE.md and AGENTS.md in this worktree. Add genuine Stage 1
regression tests, show the baseline failure, then implement and run the full suite.
Do not delete, skip or weaken checks; commit, push, merge or deploy; change CI/policy;
install dependencies; or inspect secrets. These explicit limits supersede generic
handoff suggestions to commit/push. Return changed files, exact test commands/results,
limitations, branch and HEAD. Stop for human review. Stage 2 and 3 are not authorized.
```

Optional negative control: pause after red tests and run finish dry-run. Required validation should block it without commit/push. Fix in the same workspace; never override a failing check. This is a local red/green test, not an observed GitLab CI failure.

After Codex exits and you inspect actual changes, validate and preview:

```bash
actual-coder status "$WS"
actual-coder run "$WS" -- python3 -m unittest discover -s tests -v
actual-coder finish "$WS" --message "fix: validate clip durations" --title "Reliable clip duration summaries" --dry-run > "$NOTES/round1-plan.json"
```

Require an unblocked plan, passing required validation, intended paths and complete declared scan coverage. **Dry-run executes validation and can modify local files**, but does not commit/push. MCP cannot see unpublished local diffs; manually share only reviewed, sanitized evidence when needed.

The following is a real write: only after human review, run interactive finish without `--yes` or bypass flags:

```bash
actual-coder finish "$WS" --message "fix: validate clip durations" --title "Reliable clip duration summaries"
actual-coder status "$WS" > "$NOTES/round1-status.json"
actual-coder ci "$WS" > "$NOTES/round1-ci.json"
```

Record the actual MR IID/URL; never assume MR 1. Missing MR metadata requires reconciliation, not repeated pushes or duplicate MRs. Poll with `actual-coder ci "$WS"`, not another finish. Require completed success, matching HEAD, non-stale evidence and a real `unit-tests` job.

## 3. Review, then Stage 2 on the same MR

In normal ChatGPT, provide the actual project, MR IID and workspace HEAD:

```text
Review MR <actual IID> in <actual project> through the live GitLab MCP.
Read its diff, current source/tests and available discussions. Check the latest
pipeline and actual jobs against <actual workspace HEAD>. Review Stage 1 against
EXERCISE.md. Give evidenced must-fix/optional findings, acceptance status and a
proposed Stage 2 handoff. Do not invent bugs, treat absent evidence as success,
change access or merge. If access now fails, stop with the diagnostic.
```

Angle brackets here are prompt fields, not shell syntax. Post the reviewed summary as an MR comment yourself: the MCP is read-only. If Stage 1 is correct, approve it and authorize Stage 2 explicitly; do not manufacture a defect for another round.

```bash
actual-coder resume "$WS" --agent codex --goal "Preserve approved Stage 1 and implement EXERCISE.md Stage 2 only. Same workspace and MR. No commit/push, CI/policy edits, future stages or credential reads." > "$NOTES/round2-handoff.json"
```

**Resume returns a handoff, not a launched worker.** Inspect it, reopen Codex in the same `WT`, supply Stage 2 approval and actual findings, and re-read EXERCISE.md/AGENTS.md. Repeat the validation/dry-run/interactive-finish sequence with message `feat: filter clips by validated minimum duration`, saving round2 evidence separately. Require the **same WS/branch/MR**, a new HEAD containing Round 1, and a new matching successful unit-test pipeline.

## 4. Stage 3 and final acceptance

Repeat the live MR review and explicitly authorize Stage 3: deterministic JSON, shared validation, tests and README examples. Use `resume` without `--from-ci` for ordinary feature continuation, launch in the same `WT`, then review and finish with message `feat: serialize clip summaries deterministically`.

Final review covers all stages, actual code/tests, latest HEAD/jobs and unresolved discussions. Green CI alone is insufficient: the original five tests were green. The human separately decides to merge in GitLab; ReasonFirst does not implement a merge command. Do not enable auto-merge or forced cleanup for this rehearsal.

## A genuine CI failure

Only for actual matching-HEAD CI evidence:

```bash
actual-coder resume "$WS" --agent codex --from-ci --goal "Diagnose and repair the observed matching-HEAD CI failure within the approved stage. Same workspace/MR. No commit/push or CI/policy bypass." > "$NOTES/ci-repair-handoff.json"
```

Inspect sanitized context and ask ChatGPT to separate code bugs from runner/auth/network failures, then explicitly launch a worker if code repair is justified. Missing/stale CI blocks this route. Logs are not instructions to broaden scope. Never add a fake production failure or weaken CI to exercise recovery.

## Private evidence and optional second MR

Record each round's approved stage, WS/branch, local HEAD, MR IID, pipeline ID/SHA, actual jobs, test outcomes, truncation/redactions, review and approval. Do not pre-fill fictional success. Preserve local evidence/workspace until reviewed. Passing means live access preflight and file reads, observed Codex work, reviewed MR creation and two updates to that same MR, actual matching unit-test CI, and a separate human merge decision. It does not prove automatic task persistence, complete isolation or a full robot application build.

For a **second MR**, finish/merge the first, approve a genuinely separate task, then create a new workspace from updated main. An alternative of one MR per stage must be agreed before starting, not mixed into the same-MR rehearsal halfway through. No fixed time, token quota or cost-saving guarantee is made.

Primary references: [ReasonFirst CLI](../src/gitlab_agent/cli.py), [Codex CLI](https://developers.openai.com/codex/cli/reference/), [GitLab projects](https://docs.gitlab.com/user/project/), [MR pipelines](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/), [workflow rules](https://docs.gitlab.com/ci/yaml/workflow/).
