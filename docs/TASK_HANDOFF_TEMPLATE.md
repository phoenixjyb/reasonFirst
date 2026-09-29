# Manual task handoff and evidence template

[Workflow](WORKFLOW.md) · [Quickstart](ACTUAL_CODER_QUICKSTART.md)

This is a **human-maintained Markdown planning template**, not the machine TaskSpec/EvidencePack schema, a configuration file, or an automatically loaded instruction file. ActualCoder now implements persistent TaskSpec/attempt records and a bounded read-only EvidencePack; it does not discover or parse this Markdown page. Use the template when a human-readable planning/review note is useful, then keep the machine TaskSpec and EvidencePack as the authoritative managed-workspace state/evidence.

Keep private task material only in an approved private location. Do not add filled examples, credentials, credential-file contents, or private logs to the public ReasonFirst repository. A reference to a private credential file is not permission for the worker to read or print it.

## Before implementation

Copy and fill this section in the reasoning conversation or an approved private task note:

```text
Task title:
Task revision (manual):
Approved by / date:
Target GitLab project:
Reviewed base ref and full commit SHA:

Goal:
Non-goals:
Allowed components / paths:
Sensitive or prohibited changes:

Approved approach and concise engineering rationale:
Relevant source files and observed facts:
Open questions requiring a human decision:

Acceptance criteria:
  AC-1: Observable result; exact check that establishes it.
  AC-2: Regression behavior; exact check that establishes it.

Required validation commands and execution environment:
What cannot be validated in this environment:

Stop and ask when:
  Scope, public interfaces, dependencies, credentials, or security policy
  need to change beyond the approved task.

Publication boundary:
  Do not publish until the human has reviewed the finish plan.
  Do not merge, deploy, rewrite history, or use bypass flags without
  separate explicit authorization.
```

The approach is a short, reviewable engineering summary, not private model reasoning. The publication text is guidance for the worker, not an OS security mechanism. Runtime validation/protected-path policy comes from the target project's `.actualcoder.yaml` at the workspace base and the user's executable permissions; this note cannot override it.

After ActualCoder creates the worktree, add its actual workspace ID, worktree path, branch, and base SHA. Never invent these values or reuse an example ID. On a backend switch, keep the same approved task revision and workspace, note what the previous attempt changed, and inspect the newly returned handoff for lost constraints. A scope change needs a newly approved manual revision.

## After an implementation attempt

```text
Task title / manual revision:
Workspace ID / branch:
Base SHA:
Current HEAD:
Dirty tracked/untracked changes remaining:
Worker / attempt label (manual):

Observed changes:
  Paths changed and why; reviewed diff reference.

Observed validations:
  Exact argv / working directory / environment.
  Exit status / timeout / timestamp.
  Sanitized output reference and any truncation or missing evidence.

Acceptance:
  AC-1: passed / failed / not established; supporting evidence.
  AC-2: passed / failed / not established; supporting evidence.

Publication:
  Not published, or explicitly approved commit/push and existing MR URL.

CI:
  Pipeline ID / SHA / status / jobs actually covered.
  Does SHA match HEAD? Are there later uncommitted changes?
  Was this a new run, historical evidence, or docs-only detection?

Worker claims not independently verified:
Remaining risks / unanswered questions:
Human decision needed next:
```

Separate observed command results from a worker's claim that tests passed. Do not mark missing evidence as success, infer a full build from a detection-only job, or infer write rights from a successful read. Review outgoing material for secrets even when a sanitizer ran.

For unpublished local changes, the **read-only GitLab MCP** cannot fetch the worktree's local diff. Use `actual-coder evidence WORKSPACE` (optionally `--from-ci`) for the bounded EvidencePack, or use Bridge Preview for unpublished workspace review when deliberately enabled and available. Share only reviewed excerpts manually when neither path is available; do not publish a branch merely to make a read test pass.
