# ReasonFirst 0.5.3 release notes

[简体中文](RELEASE_NOTES_0.5.3_CN.md) · [Install & update](INSTALL.md) · [Platform validation](PLATFORM_VALIDATION.md) · [Changelog](../CHANGELOG.md)

ReasonFirst 0.5.3 packages the installation and diagnostic fixes prompted by a
fresh Ubuntu setup, together with a dated record of macOS, Windows and Linux
validation. The workflow stays the same: read code and discuss the task in normal
ChatGPT, use a coding worker for implementation and testing, and return the
evidence for review.

**Quick packaged installation and building from source are both available.**
The [v0.5.3 Release](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.3)
records the final source, asset workflow, public file hashes and download/install
verification. Version metadata alone is not evidence of completed publication.

## What changes

- **Recover eligible tunnel download interruptions.** The installer tries Python
  HTTPS first. If a qualifying transport failure occurs and curl is available,
  it makes one bounded recovery attempt for release metadata, checksums or the
  archive. TLS verification, HTTPS-only redirects, SHA-256 checks and complete
  bundle validation remain enforced. Certificate failures, HTTP errors, unsafe
  URLs and invalid content do not trigger that fallback. Errors identify the
  download phase and a sanitized URL without exposing proxy credentials.
- **Find the installed tunnel client in doctor.** `actual-coder doctor` now uses
  setup's resolver, including ReasonFirst-managed version directories outside
  PATH. The messages distinguish executable/socket discovery and the current
  process's runtime-key environment from service health or authentication.
  Use `reasonfirst tunnel status` and a live ChatGPT read to check the connection.
- **Make platform evidence and installation choices easier to find.** The
  bilingual platform matrix, troubleshooting guides, README and website record
  the Linux result while retaining the original Mac/Windows versions and scopes.
  Installation examples use Python 3.12 and cover both packaged and source routes.

These changes come from [#111](https://github.com/phoenixjyb/reasonFirst/pull/111).
The published v0.5.2 tag and assets are unchanged.

## Install v0.5.3

Have Git, uv and a supported coding worker ready. Complete the worker's own login
before running a coding task; guided setup does not create provider accounts.
See [first-time prerequisites](GETTING_STARTED.md) for GitLab and ChatGPT access.

### Quick packaged installation

```bash
uv tool install --python 3.12 https://github.com/phoenixjyb/reasonFirst/releases/download/v0.5.3/chatgpt_selfhosted_gitlab_mcp-0.5.3-py3-none-any.whl
reasonfirst setup
```

This route does not require retaining a ReasonFirst source checkout. The Release
provides the wheel, built source distribution, `reasonfirst.rb`, `RELEASE.json`
and `SHA256SUMS.txt`. Check the actual assets and their checksums before use;
see [public downloads](RELEASE_DISTRIBUTION.md).

### Build/use from source

```bash
git clone --branch v0.5.3 --depth 1 https://github.com/phoenixjyb/reasonFirst.git
cd reasonFirst
uv sync --python 3.12
uv run reasonfirst setup
```

To build wheel and source packages from that checkout, run
`uv build --no-create-gitignore`. Editable installation and development on a
reviewed commit remain supported; both routes share the same configuration,
workspace state, project grants and tunnel identities.

Existing users should first run `reasonfirst --version` and
`reasonfirst setup --status --json`. Follow the [upgrade instructions](INSTALL.md)
before replacing a package used by a running service. This patch does not
automatically restart services, migrate a deployment or recreate a connection.

## Validation and scope

The **2026-10-12 Ubuntu 22.04.5 field run used the published v0.5.2 wheel**. It
completed fresh setup, verified tunnel download recovery, live Linux MCP reads,
managed Git clone/fetch, project-contract validation and a Codex CLI worker
running **10 existing tests successfully**. The worker reported a clean worktree
and no file changes. It performed no repository edits or publication. This is
not a live retest of the new v0.5.3 automatic recovery or doctor behavior.

The macOS and Windows real-machine records cover earlier sources and v0.5.1
installations. **Tested on macOS, Windows and Linux** therefore means the dated
versions and operations in the [platform record](PLATFORM_VALIDATION.md), not
every feature on every platform in one release. CI regression and isolated
package/source installs are separate from authenticated provider tests.

The release decision requires successful checks at its exact source: core and
three-platform regression/install, Bridge, native tunnel boundaries,
documentation, packaging and source/history scans. The versioned Release records
that source and the subsequent public-download verification. Earlier green
integration checks do not replace those gates.

Healthy-runtime repair/no-op, controlled Windows restart and reboot recovery,
and optional privileged Windows Bridge/write acceptance retain their existing
limits in [#88](https://github.com/phoenixjyb/reasonFirst/issues/88). This patch
does not change service activation, worker permissions or publication policy.
