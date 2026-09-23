# One reasoning interface, one controlled implementation workflow

[README](../README.md) · [Architecture](ARCHITECTURE.md) · [CLI quickstart](ACTUAL_CODER_QUICKSTART.md) · [Task handoff template](TASK_HANDOFF_TEMPLATE.md)

**Use ChatGPT as the reasoning forefront and keep coding agents in the execution role.** Normal ChatGPT is the default surface for reading, architecture, diagnosis, scope, acceptance criteria and review. ActualCoder or the optional Bridge Preview turns that approved intent into a controlled handoff for `codex-cli`, `copilot-cli`, or `codex-desktop`. The localhost Assistant is not required.

## Where each action belongs

| Surface | Role | Required? |
| --- | --- | --- |
| Normal ChatGPT conversation | Read code through the selected connection, diagnose, define scope, review results | Default reasoning interface |
| Read-only GitLab MCP + tunnel | Repository/MR/CI inspection only | Needed only for the ChatGPT read-connection path |
| Terminal: ActualCoder | Prepare/manage worktrees, select/launch workers, validate and publish through reviewed finish | Execution engine for approved implementation |
| Bridge Preview MCP | Optional local orchestration: App Server control, approvals, managed SSH workspaces, finish preview | Optional and more privileged than the read connector |
| Coding worker | `codex-cli`, `copilot-cli`, or `codex-desktop` executes the approved implementation | Required when a task is actually implemented |
| GitLab MR/CI UI | Inspect repository evidence and make the human merge decision | Used when reviewing/publishing changes |
| Local dashboard Overview/Logs | Diagnose the tunnel | Optional |
| Local dashboard Assistant (`/ui#codex`) | A separate upstream Codex interface | **Not required; not an acceptance gate** |
| Codex tunnel plugin or MCP Inspector | Separate integration/debugging tools | Optional; neither is needed for the standard ChatGPT read test |

Not using the localhost Assistant does not mean uninstalling Codex: its CLI can still be the implementation worker. Closing the dashboard does not stop the tunnel. It also does not necessarily disable the upstream client's bundled background helper. This documentation does not change or claim to disable that helper.

## One primary loop, several supporting surfaces

```text
Normal ChatGPT
  -> inspect repository / MR / CI
  -> reason about architecture and root cause
  -> define goal, non-goals and acceptance criteria
  -> TaskSpec
  -> ReasonFirst control
  -> codex-cli / copilot-cli / codex-desktop
  -> controlled implementation and validation
  -> diff / EvidencePack / MR / matching-HEAD CI
  -> Normal ChatGPT + human review
  -> continue, revise, or merge
```

The interfaces below support different parts of that same loop; they are **not three peer product modes**. The read-only GitLab MCP supplies evidence to ChatGPT. ActualCoder is the normal execution engine for approved work. Bridge Preview is an optional, more privileged orchestration surface for App Server control, explicit approvals, named SSH workspaces and finish preview.

ActualCoder can also be driven directly from a terminal, CI repair job, IDE or another client. Keep that capability for testing, recovery and automation, but treat it as a secondary operational surface rather than the defining ReasonFirst workflow.

Bridge SSH targets are user-configured names, not caller-supplied arbitrary hosts. Remote build/test execution is available only through a configured structured container-validation policy; arbitrary remote shell execution is not exposed.

## Daily task loop

### 1. Read and decide in normal ChatGPT

Select the intended GitLab connection in the conversation. Ask it to inspect actual files and distinguish observations from proposals. Agree on the goal, non-goals, affected components, acceptance criteria, and stop conditions. Do not include tokens, private configuration files, or credential-bearing reference notes in the task.

Use the manual template to capture the approved requirements. A task-specific backend change is not permission to change its goal or broaden its scope.

### 2. Prepare and implement in Terminal

For a genuinely approved new task, from the source checkout, replace the illustrative project and goal:

```bash
uv run actual-coder start team/project-a --task fix-timeout --goal "Fix the timeout bug; preserve the API and add regression coverage" --no-launch
```

This fetches context and creates a worktree; it is not an offline/no-write preview. Review the returned workspace ID, path, branch, and handoff, then use its returned backend launch command/prompt. Do not call `start` again just to continue that workspace. A new task can omit `--no-launch` to launch interactively. Worker login and quota are checked through the worker's own supported flow, not inferred from an installed executable.

### 3. Validate and review before publication

Set `WS` to the real returned ID; the value below is illustrative:

```bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage" --dry-run
```

Inspect the diff, validation results, secret coverage, protected paths, and target MR/branch. **Finish dry-run runs configured validation commands and can change local files; it does not commit or push.** No `.actualcoder.yaml` means no project-specific validations were loaded. Define actual project tests before calling this a test gate.

Only after an unblocked, intentional review:

```bash
uv run actual-coder finish "$WS" --message "fix: handle timeout and add regression coverage"
uv run actual-coder ci "$WS"
```

Finish asks for confirmation. Inspect worker proposals: low-level commit/push commands, including those still mentioned by some generated prompts, do not enforce all finish checks. Do not treat instructions in this guide as OS-level enforcement. See [security boundaries](../SECURITY.md).

### 4. Return evidence, not just an agent's success claim

Record base and HEAD, pending changes, exact validation commands/outcomes, MR link, and CI SHA/coverage. A successful docs-only pipeline is not a full build; a historical matching pipeline is not evidence of a new push. Uncommitted changes are not covered by HEAD CI.

For a genuine matching-head CI failure, `resume --from-ci` prepares a repair handoff. Use `resume --launch` or `actual-coder continue` only when you intentionally want the selected backend launched for that existing workspace. Do not invent changes when CI is already successful. Human review and merge remain separate operations.

## Acceptance is per layer

| Evidence | What it proves | What it does not prove |
| --- | --- | --- |
| `actual-coder doctor` API success | CLI API authentication in that environment | Git push rights, coding-agent login, tunnel availability |
| Successful `project-config` fetch | That managed Git read succeeded | App tests passed or writes are authorized |
| Tunnel started / metadata fetched | Startup and reported control-plane operation succeeded | ChatGPT discovered or called GitLab tools |
| Local health/readiness response | The installed tunnel version's health conditions | A particular GitLab tool call succeeded |
| Live identity and file reads in normal ChatGPT | The actual ChatGPT read connection | Local task execution or a new build/push |
| Local Assistant approval error | Failure in that optional Codex session | Failure of the independent normal ChatGPT connection |

The final read test is in [ChatGPT MCP setup](SETUP_TUTORIAL.md#accept-the-read-connection), not in `/ui#codex`. For existing installations, use the [restart procedure](SETUP_TUTORIAL.md#restart-an-existing-profile) rather than repeating installation or migration.

## Next product work

Preserve the reasoning/control/execution/evidence separation as the project evolves. Workspace locking, resume launch, remote container validation and shared local/SSH review gates are already implemented. Persistent TaskSpec/attempt records and bounded EvidencePack are now implemented; next work should deepen cross-interface exchange and result lifecycle without weakening the reasoning/control/execution/evidence separation.
