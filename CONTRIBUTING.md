# Contributing to ReasonFirst

Thank you for helping improve ReasonFirst. English and Chinese issues and pull requests are welcome. Read the [design philosophy](docs/DESIGN_PHILOSOPHY.md), [current quickstart](docs/ACTUAL_CODER_QUICKSTART.md), and [security boundaries](SECURITY.md) first.

## Choose a focused contribution

Useful contributions include reproducible bugs, regression tests, clearer documentation, portability fixes, and small steps in the [task-loop roadmap](https://github.com/phoenixjyb/reasonFirst/issues/6) or [HTTPS work](https://github.com/phoenixjyb/reasonFirst/issues/10). Discuss large interface, dependency, persistence-format, or backend changes before implementation.

The core boundary is deliberate: reasoning defines intent, coding agents implement, deterministic tools govern supported transitions, and humans authorize consequential writes/merge. Do not add a hidden model-inference service, automatic merge, permission expansion, or unrestricted remote shell as an incidental change.

## Report bugs safely

Use the bug-report template with a minimal synthetic example, exact commit SHA, OS, Python/Git/uv versions, command, expected behavior, and observed behavior. Redact endpoints, usernames, filesystem paths, tokens, and customer code. Do not attach full `.env` files, raw CI traces, private certificates, migration backups, or managed workspaces.

For a suspected security issue, follow [SECURITY.md](SECURITY.md#reporting-a-security-issue), not a public bug report. If a test cannot be reproduced without private assets, describe the boundary first; do not publish those assets.

## Develop from a branch or fork

Fork the repository if you do not have push access. Clone your fork and add this repository as `upstream`, or create a branch in your authorized clone. Keep credentials and managed GitLab workspaces outside the source tree. Use [local PR review](docs/LOCAL_PR_REVIEW.md) rather than downloading patches manually.

From the source root:

```bash
uv sync --python 3.12
uv run python -m compileall -q server.py smoke_test.py src/gitlab_agent tests
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
git diff --check
```

The normal unit/integration suite should not need a real GitLab account, production token, or paid model session. It creates temporary Git repositories and ephemeral local TLS fixtures. Dependency installation may use package indexes. `smoke_test.py` is different: it is a live API check requiring your own configured service; do not run it against a third party or attach its raw output without review.

Current CI exercises Python 3.12 on Ubuntu/macOS/Windows. Package metadata's broader Python range is not a claim that every interpreter is tested. A platform-specific skip needs a documented reason, not a hidden weakening of an invariant.

## Tests and implementation expectations

For a fix, reproduce the failure before changing behavior where feasible and add a regression test. Test real temporary Git operations for state/ref semantics; use mocks at transport or unavailable-service boundaries, not to claim end-to-end coverage. Bind CI claims to exact SHAs and say which jobs/tests actually ran. A successful docs-only pipeline is not an application build.

Preserve existing CLI compatibility and workspace records. Destructive operations require explicit intent and preservation/recovery tests. Same-user hostile code is outside the host runner's isolation boundary; do not describe an allowlist or fingerprint as a sandbox or transaction.

Generate fabricated credential-shaped values at test runtime and generate TLS keys only in temporary directories. Do not commit realistic literal credentials even for negative tests. Do not add broad scanner exceptions to make CI green. If a real credential is exposed, revoke it first and coordinate remediation; do not force-push other contributors' work.

For documentation, test command flags against the current CLI, use plain URLs inside code blocks, avoid unquoted `<placeholder>` shell syntax, and put explanations outside pasteable shell blocks. Distinguish source checkout, global editable install, user config, managed workspace, and external service. Updating source does not automatically restart MCP or reverse a configuration migration.

## Submit a pull request

Keep one reviewable purpose. Include the problem, implementation, compatibility/security effects, tests actually executed, evidence limitations, and related issue. Update user-facing guides and the Unreleased changelog when behavior changes. Do not bump versions, create tags, or announce a release unless that is the approved task.

AI-assisted contributions are welcome, but the contributor remains responsible for understanding, reviewing, testing, and having permission to submit the result. Summarize relevant assistance and verification; do not upload private prompts, internal reasoning traces, credentials, or confidential source. Do not claim a coding agent's assertion is independently observed evidence.

Submit only material you have the right to contribute and retain required third-party notices. The repository's existing [Apache-2.0 license](https://github.com/phoenixjyb/reasonFirst/blob/main/LICENSE) remains the license; this guide does not introduce a new CLA or change ownership. Be respectful, specific, and constructive in review. Maintainer acceptance and merge are separate from a green CI run.
