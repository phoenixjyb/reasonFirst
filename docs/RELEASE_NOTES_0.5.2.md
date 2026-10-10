# ReasonFirst 0.5.2 release notes

[简体中文](RELEASE_NOTES_0.5.2_CN.md) · [Install & update](INSTALL.md) · [Changelog](../CHANGELOG.md)

ReasonFirst 0.5.2 brings the improvements made after v0.5.1 into a versioned
package. Existing users can reuse their configuration and inspect installation
state more clearly. Advanced operators also receive explicit deployment/runtime
inspection and preparation tools, with opt-in service safeguards kept separate
from ordinary setup.

The workflow stays the same: read and reason in ChatGPT, hand a reviewed task to
the coding backend, then review the result before publication. Both packaged and
source installation remain supported.

## What changes for users

### Reuse an existing installation

When valid settings already exist, `reasonfirst setup` offers reuse before asking
for GitLab, project and worker configuration again. The explicit commands are:

```bash
reasonfirst setup --reuse-existing --json
reasonfirst status --json
```

Reuse preserves existing configuration bytes, project grants and exported-variable
precedence. It does not start services, create tunnels or apps, verify logins, or
write completed setup phases. Use `setup --reconfigure` for an intentional
configuration change; its verification and approval requirements still apply.
Malformed, unreadable or unsupported selected configuration is reported and left
intact. [PR #96](https://github.com/phoenixjyb/reasonFirst/pull/96)

`status` separates the installed CLI from the running service, whose version may
remain uninspected. It recognizes the known macOS legacy/HTTP-sidecar registration
as static evidence. A missing `setup.yaml` no longer implies that an existing
installation needs a duplicate tunnel. Windows/Linux service-manager discovery
remains uninspected; static files are not a health check.

### Explicit operator tools

The package adds a loopback-only `reasonfirst-bridge-http` command with explicit
endpoint and read-only/full-chat mode. It uses the packaged Bridge core; the
normal `reasonfirst-bridge-mcp` command remains stdio. The HTTP wrapper provides
no authentication or legacy `/control` route, so it is not a drop-in replacement
for every existing HTTP deployment. See [HTTP transport](BRIDGE_HTTP.md).

Advanced deployment work now has separate, reviewable steps:

- On macOS, record a known saved LaunchAgent registration using
  `deployment plan/adopt/status`. Recording requires the reviewed digest and
  `--yes`; it creates a registration snapshot, not a service migration.
- On macOS/Linux, prepare a separate runtime from a complete, reviewed offline
  wheel set. This does not download dependencies or replace the running service.
- On macOS, compare a saved deployment, prepared runtime and recognized launcher,
  and inspect selected fields of the loaded job. Unavailable fields and unresolved
  compatibility remain explicit blockers.

These commands are optional operator tools. They do not implement an automatic
updater, service switch, restart, rollback or recovery transaction. Windows
runtime preparation and Linux/Windows deployment pairing are unsupported. See
[deployment records](DEPLOYMENTS.md), [runtime preparation](RUNTIME_PREPARATION.md)
and [loaded-job observation](LOADED_SERVICE.md). These changes were integrated in
[PR #97](https://github.com/phoenixjyb/reasonFirst/pull/97) through
[PR #102](https://github.com/phoenixjyb/reasonFirst/pull/102).

## Internal service reliability work

The new opt-in managed HTTP owner tracks request, worker-turn and approval
lifetimes, and admits maintenance only through its owning local lease. Configured
owners retain selected policy, current-process observations, child-launch inputs
and GitLab API CA material. Observed binding drift closes admission for that owner; failed
transport or cleanup keeps uncertainty visible.

This owner uses an explicitly checked MCP 2026-07-28 JSON profile with
subscriptions disabled and the reviewed MCP 2.3.0/Uvicorn 0.54.0 pair. It is not
automatically selected by existing launchers. Its bounded input checks do not
establish whole-runtime integrity, all provider/native Git/SSH configuration,
global idle, or permission to activate a replacement service.

The separate disposable startup probe always denies tool execution. Its startup
claims and catalog checks do not turn it into a live service. See
[managed HTTP service](MANAGED_HTTP_SERVICE.md) and
[disposable startup](DISPOSABLE_STARTUP.md) for the exact opt-in scope introduced
by [PR #103](https://github.com/phoenixjyb/reasonFirst/pull/103) through
[PR #109](https://github.com/phoenixjyb/reasonFirst/pull/109).

## Install or update

Use the following package URL only after `v0.5.2` is published, its asset workflow
has succeeded, and the wheel/checksums are available:

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.2/chatgpt_selfhosted_gitlab_mcp-0.5.2-py3-none-any.whl
reasonfirst setup
```

For an existing packaged installation, first inspect which environment any live
service imports and arrange the package replacement safely. Then install the
reviewed wheel with `uv tool install --force` and inspect
`reasonfirst setup --status --json`. Installing package files does not make an
already running process load them. Reuse existing settings and keep the existing
source checkout, service directories, workspace state and tunnel identities.

Source users can select the reviewed `v0.5.2` tag after publication and run
`uv sync --python 3.12`; editable installation remains supported. Follow the
[install and update guide](INSTALL.md) for commands and service-specific limits.

Release builds now use `uv build --no-create-gitignore`, fixing the staging-marker
failure without weakening the exact input allowlist or asset collision guards
([PR #95](https://github.com/phoenixjyb/reasonFirst/pull/95)). The frozen v0.5.1 tag
and artifacts are unchanged. Its one-version recovery workflow must not be reused
for v0.5.2. The new Release uses the normal asset workflow and provides a wheel,
sdist, formula, `RELEASE.json` and `SHA256SUMS.txt`. A generated formula does not
establish that a Homebrew tap exists.

## Validation and material limits

The pre-version-bump integration baseline
[`d02314a1681907b772ce9ec4527fb588dd5f18ac`](https://github.com/phoenixjyb/reasonFirst/commit/d02314a1681907b772ce9ec4527fb588dd5f18ac)
passed [CI](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444782),
[documentation](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444779),
[native tunnel-boundary checks](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444786)
and [candidate packaging](https://github.com/phoenixjyb/reasonFirst/actions/runs/38019444788).
This is integration evidence, not acceptance of a later 0.5.2 release commit.
The version-bumped candidate and final merged release SHA require their own
successful checks, followed by actual public-download/checksum/install acceptance.

Regressions cover configuration reuse without writes, deployment/runtime drift,
partial native observation, maintenance races, request/cleanup uncertainty,
retained child inputs and GitLab API trust. The clean wheel/source installation
fixtures exercise both Bridge modes, real MCP requests and synthetic loopback
HTTPS; they do not authenticate a real GitLab or coding-provider account.
Three-platform CI uses Python 3.12; broader package metadata is not equal test
coverage for every interpreter version.

The recorded Windows rehearsal has passed setup/read access, managed clone/fetch,
approved-interpreter host validation, normal initial worker handoff and clean
closeout. **Windows repair/restart/reboot recovery and optional privileged
Bridge/write acceptance remain unverified.** Those earlier machine results do
not establish a new 0.5.2 live-machine acceptance. See the
[recorded acceptance scope](RELEASE_DISTRIBUTION.md#v051-post-publication-staging-recovery)
and [remaining Windows acceptance](https://github.com/phoenixjyb/reasonFirst/issues/88).

Guided setup is available, but a few-minute first-use completion time has not been
measured. [Issue #80](https://github.com/phoenixjyb/reasonFirst/issues/80) tracks
remaining onboarding and distribution criteria; no tested Homebrew tap or package
registry installation is claimed.

Windows tunnel quoting/UTF-8 decoding, managed Git credential isolation,
workspace Python approval and portable worker handoff fixes were already in the
published v0.5.1 source. They remain included; they are not new 0.5.2 fixes.

Local installation or health is still separate from ChatGPT app authorization
and full-chat eligibility. Full-chat worker control uses Codex App Server;
Copilot CLI remains supported through the terminal ActualCoder path. No new
backend authentication, unattended publication or automatic merge is introduced.
