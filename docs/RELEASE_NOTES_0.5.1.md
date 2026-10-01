# ReasonFirst 0.5.1 release notes

[简体中文](RELEASE_NOTES_0.5.1_CN.md) · [Install & update](INSTALL.md) · [Changelog](../CHANGELOG.md)

ReasonFirst 0.5.1 makes the existing reasoning-first workflow easier to install and configure. It brings the five guided-setup slices together without replacing the supported source/manual route or changing the human review and publication boundary.

**Release identity and availability:** a source version or merged PR does not establish that a release has been published. The `v0.5.1` tag identifies the released commit once a maintainer publishes it. Use the packaged command below only after the Release assets workflow succeeds and the wheel is attached to that Release. The published `v0.5.0` tag remains frozen at `6b3b1983b3dd8ad56f214393d00e7a74c60c7f95`; its [historical release notes](RELEASE_NOTES_0.5.0.md) remain unchanged.

## What changes for users

### One guided entry point

`reasonfirst setup` guides local GitLab, project and worker configuration, then offers the read-only ChatGPT connection and, where eligible, the optional full-chat Bridge. Project access is verified before the explicit local grant is written. Configuration updates preserve unrelated settings and use private backups and atomic replacement.

The standalone surfaces remain available:

```bash
reasonfirst setup --status --json
reasonfirst project list
reasonfirst worker list
reasonfirst tunnel status
reasonfirst bridge inventory --json
```

`setup --status` is detect-only: it does not call providers, start services, or claim that ChatGPT authorization is complete. Finding a worker executable does not prove provider authentication.

### Packaged and source installation are both supported

After publication and successful asset attachment, the normal packaged route is:

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup
```

The wheel contains `reasonfirst`, `reasonfirst-gitlab-mcp`, `reasonfirst-bridge-mcp`, ActualCoder and the compatibility CLIs. Running the packaged services does not require keeping a ReasonFirst source checkout. Git is still needed for managed coding workspaces.

From a reviewed source checkout, contributors and advanced users can instead run:

```bash
uv sync --python 3.12
uv run reasonfirst setup
```

Editable installations remain supported. Both routes use the same private configuration, non-secret SetupState, project grants and recorded tunnel identities. Keep the selected checkout stable when using an editable installation. The [detailed/manual setup guide](GETTING_STARTED.md) is still supported and is not deprecated. PyPI publication is not required for this release.

### Managed read tunnel and optional Bridge

The read MCP is packaged, and the official tunnel-client owns managed runtime supervision rather than a second ReasonFirst service-manager implementation. Its installer verifies the release archive checksum, preserves the companion runtime bundle, and verifies an existing bundle before reuse.

The packaged Bridge is an optional, more privileged stdio/tunnel surface. It requires explicit acknowledgement of a client/workspace supporting write-capable custom MCP actions, a tunnel ID separate from the read-only app, a checked tool inventory, and local Codex App Server capability. Its normal catalog excludes the experimental remote-push authorization tool and the legacy HTTP control-token route.

Copilot CLI remains supported in the terminal ActualCoder workflow. The current full-chat worker controller is Codex App Server-based; it does not claim to steer Copilot CLI.

### Resume and repair without recreating account resources

Normal setup reuses recorded healthy read-tunnel and Bridge runtimes. For recorded local runtime state:

```bash
reasonfirst setup --repair
reasonfirst setup --status
```

Repair does not create GitLab projects, OpenAI tunnels or ChatGPT apps. Reconnecting an unhealthy recorded runtime needs a supplied runtime credential; privileged Bridge repair also requires explicit eligibility acknowledgement. Runtime keys are accepted through the environment or a masked prompt, not literal command-line arguments, and are not persisted in SetupState.

Installing a newer package is not proof that an already-running process has loaded it. Check the running service separately. The new state-driven repair command is not an automatic migration of arbitrary legacy launchd profiles or manually configured services.

## Cross-platform fixes from clean-machine testing

The shared runtime now preserves tunnel executable quoting and decodes native JSON
as UTF-8, isolates managed Git credentials from inherited helpers, and supports an
explicit workspace-approved project Python without changing protected contracts or
PATH. The same interpreter mapping is used for pinned host validation and worker
handoffs. Normal handoffs include a complete PowerShell 5.1/7 or POSIX execution
recipe with exact arguments and truthful child/timeout/output evidence.

Public asset preparation also generates `RELEASE.json` and `SHA256SUMS.txt` and
refuses existing target filenames instead of silently replacing release assets.
See [public downloads and recovery boundaries](RELEASE_DISTRIBUTION.md).

## Validation and its limits

The integrated feature baseline `ee1ed4564e07b52adbbadaea750d9799c43d7d56` passed [all ten CI jobs](https://github.com/phoenixjyb/reasonFirst/actions/runs/36669048813) and [documentation build/deployment](https://github.com/phoenixjyb/reasonFirst/actions/runs/36669048675). This is pre-version-bump evidence, not a substitute for validation of the final release-preparation commit.

The required release gate includes the core regression suite; Ubuntu, macOS and Windows regressions; both Bridge regression jobs; wheel/sdist inspection and source/history scanning; packaged/source install E2E on all three platforms; and documentation build/link checks. Record the final PR head, merged commit and successful run IDs in the release record.

Install E2E builds a wheel, really installs it using `uv tool install`, leaves the source checkout, and exercises version/status and packaged Bridge inventory. It separately exercises `uv sync` / `uv run` from source. It does **not** authenticate a real provider, create browser-side apps, or verify live full-chat setup on all three platforms. CI uses Python 3.12; the broader package metadata range is not a claim of equivalent interpreter coverage.

## Publication and unchanged boundaries

The release-assets workflow runs only after human Release publication. It checks tag/package/runtime alignment, builds and inspects wheel/sdist, exercises installation, scans source/history, generates a checksum-pinned Homebrew formula, and attaches the artifacts to the existing Release. It does not create a tag or Release. Formula generation and Ruby syntax checks are not a tested Homebrew installation; do not advertise a tap until one exists.

Local runtime health does not establish `CHATGPT_READY` or `FULL_CHAT_READY`. Complete the relevant handoff and live acceptance checks. A worktree is not a security sandbox, selected MCP data may leave the host, and coding backends retain their own authentication and billing. No direct model-inference service, unattended merge, new SCM adapter, credential migration, or repository-permission expansion is introduced by release preparation.

See [Security](../SECURITY.md), [Architecture](ARCHITECTURE.md), and the [maintainer release checklist](PUBLIC_RELEASE_CHECKLIST.md) before adoption or publication.
