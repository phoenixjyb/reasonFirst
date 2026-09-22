# Changelog

## Unreleased

These are source changes after v0.3.0, not a new release tag. Package metadata
still reports 0.3.0; identify installations and test evidence by commit SHA.

### Correctness and deployment compatibility

- Add a structured SSH validation runner that executes only exact validation argv already accepted from `.actualcoder.yaml`; arbitrary agent-provided `bash -lc` command strings are not part of the interface. The runner verifies the configured remote workspace root and strips credential-shaped environment variables, while explicitly documenting that repository validation code is not a container sandbox.\n- Add a user-owned named SSH target registry with exact per-target project grants. External/MCP callers can select configured target names but cannot supply ad-hoc host/repository destinations; `actual-coder targets` provides a read-only local inspection path.\n- Add user-owned coding-worker policy controls: Codex defaults to GPT-5.6 Sol / High and receives explicit sandbox/approval/network settings; Copilot can receive explicit model/effort plus allow/deny tool rules, with git push denied by default. Interactive behavior remains the default unless a non-interactive execution mode is explicitly configured.
- Preserve unpublished commits during normal workspace cleanup and abandoned-branch recovery using fresh publication evidence and compare-and-delete refs (#5).
- Propagate failed child commands/timeouts to shell exit status while retaining structured results (#7).
- Scan bounded base-to-HEAD commit-history additions, including merge parents, before controlled finish; remove whole-line placeholder exemptions and sanitize findings/display diffs (#8).
- Share bounded, sanitized failed-job trace evidence between CLI and read-only MCP, with explicit completeness/limit reporting (#9).
- Add explicit local HTTP-to-HTTPS migration with offline preview, reviewed input fingerprint, private backups/journal, forward recovery, and a credential-free TLS probe (#12). No workspace reset/reclone or automatic remote writes.

- Add shared verified Python API/MCP TLS contexts, optional user-level `GITLAB_CA_BUNDLE`, pre-send credential-destination checks, and rejection of all API redirects (#13). Native Git and the migration-only TLS probe retain separate trust behavior.
- Compatibility: Python API/MCP clients reject disabled verification and no longer implicitly use `SSL_CERT_FILE`/`SSL_CERT_DIR`; configure the final API endpoint and explicit CA bundle when required.

### Public documentation and contribution workflow

- Refresh README and current English/Chinese quickstarts for external contributors, keeping real deployment details out of examples.
- Document Git-based PR review, normal editable-install updates, and separate configuration/MCP lifecycle steps; manual archive copying is not required.
- Add English HTTPS migration and runtime TLS guides, a documentation index, CONTRIBUTING, issue/PR templates, and a public-release checklist.
- Reconcile public capability claims with merged API/MCP TLS support; distinguish native Git, the maintenance probe, and legacy helpers instead of claiming a universal transport policy.
- Clarify missing project contracts, manual paths that do not enforce full finish gates, host-execution/concurrency limits, and the distinction between published releases, merged source, and planned capabilities.
- Prefer HTTPS in the example configuration without modifying existing user settings, licenses, runtime behavior, repository visibility, or release metadata.

### Still planned

- Native Git trust/destination-policy integration and layered API-versus-Git diagnostics (#10).
- Consistent task handoffs, general workspace locking/recovery, persistent TaskSpec and EvidencePack (#6).

### Naming (retained)

- Rebrand the project as **ReasonFirst**: reasoning-first coding orchestration.
- Keep **ActualCoder** / `actual-coder` as the local coding orchestration engine and CLI.
- Keep `gitlab-agent` as the current GitLab/workspace control plane.
- Reframe GitLab as the first SCM/CI adapter rather than the product identity.
- Add `docs/DESIGN_PHILOSOPHY.md` describing the Reasoning / Execution / Control / Feedback planes and the subscription-efficient architecture.
- Update README, team/setup documentation, MCP identity, and package description for the ReasonFirst naming.
- Preserve existing CLI names, config locations, workspace paths, and Python distribution name in this branding pass to avoid breaking v0.3 users.

## 0.3.0 - 2026-09-19

- Complete the ActualCoder lifecycle: `doctor → project contract → auto backend → start → finish → GitLab CI feedback → resume`.
- Add repository-local `.actualcoder.yaml` policy with strict schema, bounded input, duplicate-key rejection, conservative refs/paths, and user-level executable/timeout caps.
- Add project-aware `--agent auto` selection for Codex/Copilot without invoking a model during discovery.
- Add high-level `actual-coder start` with doctor preflight, isolated worktree creation, project-aware handoff, and optional interactive launch.
- Add controlled `actual-coder finish` with validation, reviewability gates, protected-path checks, secret scanning, reviewed-state fingerprinting, human confirmation, and create/update MR behavior.
- Add `actual-coder ci` plus stale-safe, bounded, ANSI-cleaned, credential-redacted `resume --from-ci` handoffs.
- Add bounded streaming of failed GitLab CI traces and complete paginated job collection without silent truncation.
- Keep the ChatGPT MCP read-only and preserve zero direct OpenAI model API usage.
- Validate the lifecycle against a real private self-hosted GitLab, including a real MR and matching successful MR pipeline.
- Keep Linux/macOS/native-Windows CI, full-history secret scan, and GitGuardian checks as release gates.
- Document known limitations: host execution is not a sandbox, same-workspace concurrent writers are unsupported in v0.3, and backend presence checks do not verify login/quota.

See [docs/V0.3_RELEASE_AUDIT_CN.md](docs/V0.3_RELEASE_AUDIT_CN.md) for the release architecture/security audit.

## 0.3.0-alpha.6

- Add `actual-coder ci WORKSPACE` to inspect GitLab pipelines for a managed feature branch.
- Prefer a pipeline whose SHA matches the current workspace HEAD; otherwise expose the latest branch pipeline as stale.
- Add pipeline-job summaries with failed vs blocking-failed job distinction.
- Fetch only failed-job trace tails with conservative defaults and hard caps.
- Gracefully retain CI/job metadata when an individual job trace is unavailable.
- Strip ANSI terminal control codes from CI logs.
- Redact high-signal credentials and obvious TOKEN/SECRET/PASSWORD/API_KEY assignments before logs enter CLI output or coding-agent prompts.
- Add structured, explicitly untrusted CI repair context.
- Add `actual-coder resume WORKSPACE --from-ci` to attach current-head CI evidence to the next coding handoff.
- Refuse `resume --from-ci` when no pipeline exists or when the latest available pipeline is stale for the workspace HEAD.
- Warn when the pipeline is still incomplete/running.
- Pin resume project/backend preference lookup to the workspace base SHA.
- Keep the CI loop read-only until the user/coding agent makes a repair; subsequent Git writes still go through `actual-coder finish`.
- Add GitLab pipeline/job/trace API helpers and CI feedback regression tests.

## 0.3.0-alpha.5

- Add `actual-coder finish WORKSPACE` with a separate planning and execution phase.
- Add `--dry-run` to run validation/security/review planning with zero Git writes.
- Load the project contract from the workspace base SHA so finish policy is stable even if the branch changes the contract itself.
- Run configured validation commands through the constrained command runner.
- Add changed-path and complete base-to-working-tree diff inspection helpers.
- Scan added diff lines for high-signal credentials/secrets before remote writes.
- Treat `.actualcoder.yaml` as a built-in protected path and enforce project-declared protected paths.
- Require explicit `--allow-protected` and `--allow-secret-match` overrides for those independent safety gates.
- Require a commit message when dirty changes need committing.
- Derive first-MR title from the commit/latest subject and apply project MR conventions.
- Distinguish first push (`push-mr`) from existing MR update (`push-update`).
- Refuse to guess/create an MR for a branch previously pushed without a recorded MR.
- Require interactive human confirmation before writes unless `--yes` is explicitly supplied.
- Keep `--yes` unable to bypass validation/protected-path/secret gates.
- Add finish planning/execution regression tests.
- Re-read status/diff/changed paths after validation commands so validation-generated changes are included in review/security checks.
- Fingerprint the reviewed post-validation workspace state and refuse commit/push if HEAD, status, push state, or diff changes before execution.
- Read the immutable workspace-base project contract from the existing managed cache without an unnecessary remote fetch.

## 0.3.0-alpha.4

- Add high-level `actual-coder start PROJECT --task ... --goal ...` lifecycle orchestration.
- Run ActualCoder doctor as a preflight gate before creating a start workspace.
- Load and validate the remote `.actualcoder.yaml`, using its base branch when the CLI does not explicitly override it.
- Default `start` to project-aware `--agent auto`.
- Fail before workspace creation when the selected explicit backend is not installed.
- Inject repository instructions, protected paths, and expected validation commands into the generated handoff while explicitly subordinating repository-owned guidance to ActualCoder rules and the user goal.
- Launch Codex with the generated positional initial prompt and Copilot with interactive `-i` initial prompt.
- Add `--no-launch` for zero-model-use preparation/validation and `--offline-doctor` for preflight network control.
- Do not enable broad automatic tool approval or full-auto modes.
- Launch backends through direct subprocess argv without a shell.
- Add regression tests for project-context preparation and mocked backend launching.

## 0.3.0-alpha.3

- Add opt-in `--agent auto` to task, resume, branch recovery, and MR recovery handoffs.
- Read `agents.preferred` from the remote `.actualcoder.yaml` when auto-selection is requested.
- Use `codex → copilot` as the default installed-backend fallback when no project preference is present.
- Fall back to another installed supported backend when preferred backends are unavailable.
- Fail before workspace creation/recovery if no supported coding backend is installed.
- Keep explicit `--agent codex|copilot` behavior backward compatible.
- Report `agent_requested`, selected agent, candidates, installed candidates, project-config provenance, and human-readable selection reason.
- Selection only checks executable presence; it does not invoke a coding model or consume quota.
- Add auto-selection regression coverage and team documentation.

## 0.3.0-alpha.2

- Add optional repository-local `.actualcoder.yaml` project contracts.
- Add `actual-coder project-config PROJECT [--ref REF] [--validate]`.
- Read the project contract from a fetched remote Git ref without creating a worktree.
- Add strict YAML schema validation for base branch, backend preference, validation commands, protected paths, instructions, required executables, and MR conventions.
- Require validation commands to use argv arrays rather than shell strings.
- Prevent repository config from self-authorizing new executables; requested tools must already be approved in `GITLAB_ALLOWED_EXECUTABLES`.
- Cap repository-requested command timeouts at the developer's configured maximum.
- Treat a missing project contract as valid and fall back to user/default settings.
- Add `.actualcoder.example.yaml` and regression tests.

## 0.3.0-alpha.1

- Add `actual-coder doctor` / `gitlab-agent doctor` as a non-destructive readiness diagnostic.
- Check Python, Git, uv, supported coding backends, optional tunnel-client, and accidental `OPENAI_API_KEY` presence.
- Validate config loading, config-file permissions, GitLab URL/token presence, project allowlist, and proxy policy.
- Check workspace-root writability, free disk space, and stale/malformed workspace state.
- Perform a live GitLab `/user` authentication check by default, with `--offline` to skip network access.
- Return structured JSON with `pass` / `warn` / `fail` / `skip` checks and a non-zero exit code on failures.
- Keep the command non-destructive: it does not write GitLab and does not invoke coding-agent models.
- Add cross-platform doctor regression coverage and team onboarding documentation.

## 0.2.1 - 2026-09-18

- Add native Windows PowerShell installer and MCP launcher.
- Add Windows-safe Git `GIT_ASKPASS` handling and runner `USERPROFILE` isolation.
- Make the read MCP/smoke test use the same per-user config lookup as ActualCoder.
- Add Linux/macOS/Windows CI coverage for the packaged CLI/workspace workflow.
- Add `docs/OPENAI_TUNNEL_TEAM_SETUP_CN.md` covering per-user Tunnel ID, Runtime API Key, permissions, platform installs, and credential storage.
- Expand the Chinese onboarding guide and README with native Windows support.
- Document macOS Keychain, Linux protected secret files/Secret Service, and Windows DPAPI/ACL guidance.
- Keep ChatGPT Pro MCP usage read/fetch-only; local GitLab writes remain in ActualCoder.

## 0.2.0 - 2026-09-18

- Rename the user-facing orchestration layer from **CodingAgent** to **ActualCoder**.
- Add the primary `actual-coder` console command.
- Keep `codingagent` as a compatibility alias during the alpha series.
- Update generated agent prompts to say they were selected by ActualCoder.
- Update README, architecture docs, installer output, quickstart, tests, and CI for the new name.
- Keep `gitlab-agent` unchanged as the low-level GitLab/worktree control plane.
- Rewrite the README around the tested ActualCoder/team workflow.
- Add the canonical Chinese onboarding/install/configuration/usage guide (now `docs/ONBOARDING_GUIDE_CN.md`).
- Add dependency-free tracked-file/full-history secret scanning and enforce it in CI.
- Harden `.gitignore`, `.env.example`, security guidance, and troubleshooting documentation for team distribution.

## 0.2.0-alpha.4

- Introduce **CodingAgent** as the agent-neutral user-facing coding layer.
- Add the `codingagent` console command while retaining `gitlab-agent` as the lower-level GitLab/worktree controller.
- Add first-class `--agent codex|copilot` selection for task, resume, branch recovery, and MR recovery handoffs.
- Replace Codex-specific handoff fields with `agent`, `agent_command`, and `agent_prompt`; keep Codex aliases for compatibility.
- Add a CodingAgent quickstart and backend-handoff regression tests.
- Verify both packaged CLIs in CI.
- Make handoff next-step text goal-neutral so inspection-only tasks are not told to modify code.
- Add `codingagent agents` to report installed supported backend CLIs without invoking them or consuming quota.
- Real coding validation already includes a GitHub Copilot CLI C++ change pushed to an existing GitLab MR.

## 0.2.0-alpha.3

- Add `checkout-branch` to reconstruct a managed local worktree from an existing remote feature branch.
- Add `checkout-mr` to resume a same-project GitLab MR after local cleanup/restart.
- Preserve existing MR URL/source branch state so `push-update` continues the same MR.
- Refuse duplicate local worktrees for a branch that is already checked out.
- Add GitLab MR API helper with the same direct-network/proxy-bypass behavior.
- Add tests for branch reconstruction, continued pushes, and MR API lookup behavior.

## 0.2.0-alpha.2

- Add user-level editable installation for `gitlab-agent`.
- Add stable config lookup via `~/.config/gitlab-agent/.env`.
- Add `config`, `task`, `resume`, and `path` helper commands.
- Add Codex-ready handoff prompts from `task` / `resume`.
- Add `push-update` for repeated commit/push cycles on an existing MR branch.
- Add regression tests proving a second commit updates the same remote feature branch.
- Keep zero OpenAI model API usage as a CI-enforced invariant.

## 0.2.0-alpha.1

- Keep the ChatGPT MCP read-only for the personal-Pro workflow.
- Add a reusable local coding engine for Codex/terminal use.
- Add isolated cached Git repositories and per-task worktrees.
- Add safe workspace read/write and unified-patch operations.
- Add allowlisted build/test execution with timeout/output limits and secret scrubbing.
- Add dedicated commit and feature-branch push operations.
- Add first-push GitLab Merge Request creation via Git push options.
- Add explicit project allowlist and branch-prefix safety policy.
- Add `gitlab-agent` JSON CLI.
- Add local Git workflow tests.
- Add CI invariant preventing accidental OpenAI model API integration.
- End-to-end smoke-tested against a real private self-hosted GitLab: isolated worktree → local edit → controlled command → diff → commit → feature-branch push → Merge Request creation.

## 0.1.0 - Initial public version

- Read-only MCP server for self-managed GitLab.
- Repository tree and file reading.
- Project code search.
- Merge request metadata and diffs.
- CI pipeline, job, and job-log inspection.
- Project allowlist.
- Direct-access mode that ignores host proxy variables by default.
- GitLab API smoke test.
- OpenAI Secure MCP Tunnel launcher.
- English and Chinese setup documentation.
- Troubleshooting notes based on an end-to-end working deployment.
- GitHub Actions syntax/dependency validation.
