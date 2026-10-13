# Install ReasonFirst

ReasonFirst v0.5.3 supports **quick packaged installation and building from source** as two first-class routes. They converge on the same `reasonfirst` CLI, private GitLab config, non-secret setup state, tunnel IDs and review/publication policy.

**New user:** choose a packaged or source route below, then complete the [first ChatGPT read](GETTING_STARTED.md). If you want to read and discuss a project before installing a coding worker, see [read-first setup](#read-first-setup) for the **Unreleased source change**; the published v0.5.3 wizard still requires a detected worker.

## Existing installation? Inspect before changing it

**Installing a package does not update every running service.** Keep your existing
config, workspaces, source checkout, legacy runtime and active sidecar directories.
Do not rerun onboarding or create a new tunnel merely because `setup.yaml` is absent.

The following detect-only command works with v0.5.1 and newer. It makes no provider requests, writes, or service changes:

```bash
reasonfirst --version
reasonfirst setup --status --json
```

Use the intended installation's executable when several are on PATH. The effective
config still comes from `GITLAB_AGENT_ENV_FILE`, then the canonical
`~/.config/gitlab-agent/.env`, then cwd `.env`. Existing exported values take
precedence; never paste `.env` contents or tokens into a report.

### Since v0.5.2: reuse existing settings and inspect status

Since v0.5.2, plain `reasonfirst setup` offers one **reuse**
confirmation for existing valid settings. Accepting does not prompt again for the
URL/token/project/worker, narrow the allowlist, write `setup.yaml`, verify logins,
start services or create connections. An explicit noninteractive equivalent is:

```bash
reasonfirst setup --reuse-existing --json
reasonfirst status --json
```

These flags and the `status` alias require v0.5.2 or newer; the frozen v0.5.1
downloads do not include them.
`setup --reconfigure` (or explicit configuration options such as `--project`)
selects the deliberate editor instead; its original preflight, environment-conflict
and approval guards still apply. Declining reuse cancels rather than falling into
that editor. `--reuse-existing` cannot be combined with change options.

Reuse includes custom/cwd config and environment-only settings without copying
secrets into a new, higher-precedence file. Incomplete settings remain intact with
missing fields reported. Malformed, duplicate-key, oversized, unreadable or
symlinked selected config is classified and left untouched, not treated as new
onboarding. This inspection uses the simple single-line assignment dialect of the
existing loader; it does not evaluate shell syntax. The legacy low-level loader's
behavior outside these setup/status paths is unchanged.

`status` and `setup --status` share a static inventory. The installed CLI version,
Python and module directory are separate from the **uninspected** running version.
On macOS only the known user `com.reasonfirst.v4-mcp` LaunchAgent is inspected; its
legacy staged and versioned HTTP-sidecar path layouts are recognized without
executing them. No broad service search, `launchctl`, listener probe or MCP request
runs. A recognized path declares a layout, not a verified transport or code identity.
Windows/Linux service-manager discovery remains explicitly `not_inspected`.
A missing registration means only that the selected registration was not found.

The selected plist is parsed privately and may contain environment values, but the
output omits environment values and arbitrary arguments. The Bridge config is an
existence marker only. Known layouts can report `discovered_legacy`; malformed,
unreadable and unsupported layouts are not absence. `healthy`/`unhealthy` are not
inferred from static files. Missing wizard records with existing config/deployment
evidence recommend **review**, not duplicate tunnel setup. Neither `mode: standard`
(the default without a wizard record) nor `ready: false` proves an old service is
absent. These setup/status paths do not write a deployment registry or adopt a service.

A separate, explicitly approved v0.5.2 command can [record a known macOS
registration snapshot](DEPLOYMENTS.md). It does not install a runtime or activate
a service.

## Route A — quick packaged install (recommended for new users)

Before the **published v0.5.3 wizard**, install Git, uv, and a supported coding
worker such as Codex CLI. Setup detects the worker executable but does not install
it or verify its provider login; complete that login before running a coding task. Have your
GitLab URL, token, and exact project path ready. The [first-time prerequisites](GETTING_STARTED.md)
cover account access and the separate ChatGPT tunnel connection.

For a new installation, use the v0.5.3 wheel with Python 3.12:

```bash
uv tool install --python 3.12 https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.3/chatgpt_selfhosted_gitlab_mcp-0.5.3-py3-none-any.whl
reasonfirst setup
```

Public assets include `SHA256SUMS.txt` and `RELEASE.json`; check the [v0.5.3 Release](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.3) for asset availability and completed public-download verification. See [download verification and release scope](RELEASE_DISTRIBUTION.md). The fresh Ubuntu 22.04.5 field installation on 2026-10-12 used **v0.5.2**, before the installer and doctor fixes in v0.5.3; see the [macOS, Windows, and Linux validation matrix](PLATFORM_VALIDATION.md).

`--python 3.12` selects the tested tool interpreter independently of your system Python. On Linux, an older `python3` command can remain in place; the target project's validation command still chooses its own interpreter. If the installed executables are not on PATH, follow uv's shell-path instructions and open a fresh terminal.

This route does **not** require keeping a ReasonFirst source checkout. The installed wheel includes:

- `reasonfirst`;
- `reasonfirst-gitlab-mcp`;
- `reasonfirst-bridge-mcp` and the explicitly configured `reasonfirst-bridge-http`;
- `actual-coder` and compatibility/expert CLIs.

### Install uv

Use a supported uv installation method. Current upstream options include:

- macOS: `brew install uv`;
- Windows: `winget install --id=astral-sh.uv -e`;
- Linux/macOS/Windows: the official uv installer or another supported package manager.

ReasonFirst itself does not use a `curl | sh` bootstrap as its canonical install path.

Git is still required for managed workspaces and normal coding workflows even though the wheel installation itself does not need a ReasonFirst checkout.

### Then run one wizard

```bash
reasonfirst setup
```

For a non-mutating inventory first:

```bash
reasonfirst setup --status
```

For an eligible write-capable ChatGPT workspace:

```bash
reasonfirst setup --mode full-chat
```

Full-chat remains optional. The portable/default path is the read-only GitLab app plus terminal ActualCoder.

## Route B — build/use from source (fully supported)

Use this route if you are contributing, auditing the implementation, maintaining a private patch, testing a PR, or deliberately want the installed commands to follow a local checkout.

For a released source tag:

```bash
git clone --branch v0.5.3 --depth 1 https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run reasonfirst setup --status
uv run reasonfirst setup
```

To build a wheel and source distribution from the checkout:

```bash
uv build --no-create-gitignore
```

The packages are written to `dist/`. Building them does not configure GitLab,
start services or publish a release.

For a persistent editable user install from that checkout:

```bash
bash scripts/install_user.sh
```

On Windows PowerShell:

```powershell
.\scripts\install_user.ps1
```

The editable install follows the checkout. Keep a long-lived editable checkout on a reviewed branch/tag; use separate worktrees for PR experiments.

<a id="read-first-setup"></a>
## Read in ChatGPT before choosing a worker (Unreleased)

Worker deferral is an **Unreleased source change**. The published v0.5.3 wheel
and v0.5.3 source tag do not support `--worker later`. Use a reviewed source
checkout containing this change; record its commit SHA as well as the package
version. From that checkout, on macOS, Windows PowerShell or Linux:

```bash
uv run reasonfirst setup --mode standard --worker later
```

In standard interactive setup, you can also choose `later` at the worker prompt.
When no worker is detected and no worker choice was supplied, standard setup can
continue with the read connection. `cli-only` and `full-chat` still require their
coding workers and reject `later`.

Deferral retains any existing worker preference and tunnel identity; it does not
store `later` as a backend. The normal GitLab project check and configuration
review still apply. Follow the wizard's tunnel steps, then run
`uv run reasonfirst chatgpt handoff` and complete the returned live-read prompt in
normal ChatGPT. Local setup/status alone does not establish that connection.
Reading and discussing the project does not launch a coding worker.

When you have agreed an implementation task, install or start one supported
worker and complete its provider sign-in. Then inspect and select it:

```bash
uv run reasonfirst worker list
uv run reasonfirst worker use codex-cli
```

Choose `copilot-cli` or `codex-desktop` instead if that is your installed backend.
For a global installation built from the reviewed source, omit `uv run` from
these commands. Worker selection updates the execution preference without
repeating GitLab or tunnel setup; executable discovery does not verify login.
Continue with the [first implementation](GETTING_STARTED.md#add-worker).

## The routes share state

Both routes intentionally use the same persistent user state:

```text
~/.config/gitlab-agent/.env
~/.config/reasonfirst/setup.yaml
~/.local/share/chatgpt-gitlab-mcp/
~/.local/share/reasonfirst/
```

Changing installation route does **not** require recreating approved projects, OpenAI tunnel IDs or ChatGPT apps.

Do not run two different ReasonFirst installations concurrently against the same managed workspace.

Before changing version or installation route, inspect the existing installation.
`reasonfirst setup --repair` repairs only runtimes **already recorded** in valid setup metadata;
it is not a legacy importer and may reconnect services. With no setup record,
inspect the existing deployment instead of running a new wizard to create one.
A credential request during an explicitly approved reconnect is separate from
configuration reuse; ReasonFirst still never persists tunnel runtime API keys.

## Upgrade packaged installs

First check the effective config and which runtime any live service imports. Do not
replace a tool environment while a service depends on it. A version-pinned sidecar
can deliberately reject changed package bytes; updating the CLI alone is not a
coordinated sidecar upgrade.

Once the intended package replacement is safe and approved, use the reviewed
release wheel. The example below targets v0.5.3:

```bash
uv tool install --force --python 3.12 https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.3/chatgpt_selfhosted_gitlab_mcp-0.5.3-py3-none-any.whl
reasonfirst setup --status --json
```

The first command replaces the tool installation; it is not a service activation
transaction. For later releases replace both version occurrences. Reuse existing
settings; inspect recorded runtime health and plan any restart separately. Do not
run `setup --repair` indiscriminately on a busy machine.

## Upgrade source/editable installs

Keep local changes and your existing package-manager ownership. Select a reviewed
tag/commit without resetting or discarding work, update that checkout's environment
with the source-install route, then inspect configuration and service versions.
Do not silently switch a developer's editable installation to a wheel or assume a
staged daemon imports the updated checkout. Never operate two independent mutating
installations on the same managed workspace.

## Adopt or upgrade a legacy background service

An older launchd/staged HTTP deployment can share the GitLab config while importing
a different core/runtime from the CLI. Missing `setup.yaml` is not lost credentials.
Keep existing approved projects, tunnel identities, listener and read/write policy.
Do not substitute the stdio-only packaged Bridge command for an HTTP endpoint.

v0.5.2 provides **inspection, configuration reuse and explicit registration-snapshot
recording**, plus opt-in [runtime preparation](RUNTIME_PREPARATION.md) and
[deployment/runtime pairing](DEPLOYMENT_PAIRING.md). The [managed HTTP service
owner](MANAGED_HTTP_SERVICE.md) is an internal embedding API. These components do
not automatically adopt, switch or upgrade an existing service. A full service
migration, rollback and recovery workflow still needs separate integration and
live acceptance. Do
not fabricate wizard phases or remove old/staged directories to clear status flags.
In particular, retain any active launcher, working directory and private recovery
backup. A stopped/started service and its existing-client roundtrip must be verified
separately from successful package installation; static inventory is not that test.

## Homebrew formula

The versioned release process generates a release-pinned `reasonfirst.rb` formula from the exact protected tag/source archive and SHA256. It is attached to the GitHub Release and can be copied into a maintained Homebrew tap.

The formula is **not** the source of truth for ReasonFirst configuration. It installs the same Python package/entry points; `reasonfirst setup` remains the onboarding contract.

Do not advertise `brew install phoenixjyb/tap/reasonfirst` until the tap actually exists and the generated formula has been reviewed/published there.

## What “installed” does not mean

Successful package/source installation proves only that the local executables exist. It does not prove:

- GitLab identity/project access;
- worker authentication;
- OpenAI tunnel association;
- ChatGPT app connection;
- full-chat write-capable MCP eligibility.

ReasonFirst keeps those as explicit setup/readiness gates rather than declaring READY from installation alone.

For the detailed/manual operator procedure, see [First-time setup](GETTING_STARTED.md). For the architecture and trust boundaries, see [Architecture](ARCHITECTURE.md) and [Security](../SECURITY.md).
