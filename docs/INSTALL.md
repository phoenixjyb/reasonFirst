# Install ReasonFirst

ReasonFirst v0.5.1 supports **two first-class installation routes**. They converge on the same `reasonfirst` CLI, private GitLab config, non-secret setup state, tunnel IDs and review/publication policy.

## Route A — packaged install (recommended for normal users)

After v0.5.1 is published, install the reviewed wheel attached to the GitHub Release:

```bash
uv tool install https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup
```

Public assets include `SHA256SUMS.txt` and `RELEASE.json`; see [download verification and release scope](RELEASE_DISTRIBUTION.md). These files become available after the release-asset workflow succeeds.

This route does **not** require keeping a ReasonFirst source checkout. The installed wheel includes:

- `reasonfirst`;
- `reasonfirst-gitlab-mcp`;
- `reasonfirst-bridge-mcp`;
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
git clone --branch v0.5.1 --depth 1 https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run reasonfirst setup --status
uv run reasonfirst setup
```

For a persistent editable user install from that checkout:

```bash
bash scripts/install_user.sh
```

On Windows PowerShell:

```powershell
.\scripts\install_user.ps1
```

The editable install follows the checkout. Keep a long-lived editable checkout on a reviewed branch/tag; use separate worktrees for PR experiments.

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

After changing version or installation route:

```bash
reasonfirst setup --repair
reasonfirst setup --status
```

`--repair` only repairs **recorded local runtime state**. It does not create GitLab projects, OpenAI tunnels or ChatGPT apps. If a runtime credential is needed, it is taken from `CONTROL_PLANE_API_KEY` or a masked interactive prompt and is not persisted by ReasonFirst.

## Upgrade packaged installs

Download/install the wheel for the new reviewed release:

```bash
uv tool install --force https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.1/chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
reasonfirst setup --repair
```

For later releases, replace both occurrences of `0.5.1` with the target reviewed release version.

## Upgrade source installs

Preserve local changes, move the checkout to the reviewed tag/commit, then:

```bash
uv sync --python 3.12
bash scripts/install_user.sh
reasonfirst setup --repair
```

On Windows use `scripts/install_user.ps1`.

## Homebrew formula

The v0.5.1 release process generates a release-pinned `reasonfirst.rb` formula from the exact protected tag/source archive and SHA256. It is attached to the GitHub Release and can be copied into a maintained Homebrew tap.

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
