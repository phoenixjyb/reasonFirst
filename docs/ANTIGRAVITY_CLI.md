# Antigravity CLI worker (Unreleased)

[中文](ANTIGRAVITY_CLI_CN.md)

This source-only adapter is **not in the released v0.5.3 wheel**. It adds optional,
interactive terminal execution through `agy`, alongside the existing Codex and
Copilot routes. The launch interface is based on the maintainer-supplied `agy
1.3.4 --help`. That observation proves option availability on that Mac, not login,
sandbox enforcement, execution success, or live three-platform acceptance.

## Select and launch

Use your existing Antigravity installation and signed-in account. ReasonFirst
neither installs/updates Antigravity nor reads its credential/settings files. No
new model API key is required by this adapter. Use an authorized repository and
review provider data-sharing and tool permissions before submitting code.

From a reviewed source checkout with the normal project dependencies installed:

```bash
uv run actual-coder agents
uv run reasonfirst worker list
uv run actual-coder start team/project \
  --agent antigravity-cli \
  --task agy-smoke \
  --goal 'Read the project instructions and run its existing tests. Report the exact command, test count, exit code, HEAD and final Git status. Stop on failure without repairing it.' \
  --acceptance 'Existing tests pass and tracked files remain unchanged.' \
  --non-goal 'Do not edit files, install dependencies, inspect credentials, commit, push, create MRs, deploy or start another coding worker.'
```

Replace `team/project` with an already-authorized project. `start` creates a managed
worktree and launches the interactive terminal worker; `--no-launch` prepares the
handoff without starting the provider. Both `antigravity-cli` and `agy` are accepted
by `--agent`. The automatic fallback order remains Codex then Copilot. Installing
`agy` alone does not select it automatically; an explicit user default or pinned
repository preference can opt in.

For a source trial alongside an older installed CLI or daemon, prefer per-task
`--agent`. Do not save the new backend name into configuration shared with an
older runtime: v0.5.3 and earlier reject that name. Coordinate supported runtime
versions separately; no service upgrade is part of this trial.

To save the user-owned default for subsequent terminal tasks:

```bash
uv run reasonfirst worker use antigravity-cli
```

That command changes the existing private ReasonFirst worker preference and
setup progress; it preserves project grants and connection identity. It does not
update Antigravity settings or reconfigure/restart services. `worker use agy`
saves the canonical name `antigravity-cli`.

After setting `WS` to an existing ReasonFirst workspace ID, resume explicitly:

```bash
uv run actual-coder resume "$WS" --agent antigravity-cli --launch
```

This preserves the **ReasonFirst task and worktree**, supplying a new complete
handoff. It does not resume the provider's internal conversation. No `--continue`
or `--conversation` flag is added. Remembered-worker behavior is the separate
PR #114 change; use explicit `--agent` on this main-based candidate.

## Launch contract

The executable is found on PATH and converted to an absolute invocation path
before changing `cwd`. Arguments are passed directly without a shell:

```text
agy --sandbox [--model=VALUE] [--effort=VALUE] --prompt-interactive TASK_PROMPT
cwd = the ReasonFirst-managed worktree
```

Both stdin and stdout must be terminals at launch. Noninteractive invocation
fails; it never falls back to print/headless execution or another backend. Missing
`agy` or rejected provider flags fail rather than retrying with fewer protections.
Presence checks in agents/doctor/setup do not execute `agy` or establish a login.

Optional settings in the existing ReasonFirst configuration:

```bash
REASONFIRST_ANTIGRAVITY_MODEL=
REASONFIRST_ANTIGRAVITY_REASONING_EFFORT=
```

Blank values inherit Antigravity's configuration. Effort values are `low`, `medium`,
`high`, `xhigh`, `max`; other values are rejected. Model values are passed as one
argument, not interpolated into a shell. ReasonFirst does not claim to validate
model availability or verify the provider-resolved model.

No `--mode` is forced. No permission-skipping flag, extra workspace directory,
project ID, internal agent, log-file override, remote control, provider conversation
ID or headless flag is added. Antigravity's `--project` is not a directory argument,
and ReasonFirst's `--agent` selector is not forwarded as Antigravity's `--agent`.

## Evidence and limits

`worker_policy.backend` is `antigravity`; the evidence surface is
`antigravity-cli`. `sandbox_requested: true` means `--sandbox` was encoded. Both
`runtime_verified` and `permissions_verified` remain false. Network/approval
policy is unknown (`null`), not silently equated to Codex's restrictions. Existing
provider permissions, MCP servers, hooks, modes and external-directory or
unsandboxed grants remain in effect; ReasonFirst does not inspect or rewrite them.
A worktree is not an OS security boundary.

The returned code is the CLI's process exit status, **not** proof that tools ran,
tests passed or acceptance was met. Inspect real test output and Git changes;
use the existing reviewed validation/finish flow before publication. A denied or
unfinished task must not be treated as accepted because the CLI returned zero.

This adapter supports standard and CLI-only terminal flows. Selecting it in
full-chat setup is rejected. The Bridge remains on its existing worker routes.
New optional fields are recognized by the strict service settings inventory, but
are intentionally absent from the existing versioned Bridge `worker_requests`
projection; they do not make Antigravity a managed service worker. No persisted
schema, dependency, version or service lifecycle change is introduced.

Not included: headless/streaming transport, provider conversation-ID persistence,
remote worker control, automatic MCP disabling, or independently verified sandbox
policy. Synthetic tests and native CI are distinct from live Antigravity trials.

## References and installation routes

Both [packaged installation and building from source](INSTALL.md) remain supported;
this unreleased change currently requires reviewed source. See
[the task workflow](WORKFLOW.md) and [security boundaries](../SECURITY.md).
Provider references: [CLI use](https://antigravity.google/docs/cli-using),
[settings](https://antigravity.google/docs/settings), and
[terminal sandbox](https://antigravity.google/docs/sandbox-mode).
Provider defaults can change; this adapter explicitly requests its documented flag
and does not infer enforcement from online documentation alone.
