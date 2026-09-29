# Migrate an existing GitLab endpoint from HTTP to HTTPS

[中文指南](HTTPS_MIGRATION_CN.md) · [Quickstart](ACTUAL_CODER_QUICKSTART.md) · [Security](../SECURITY.md)

The `actual-coder-migrate-https` command upgrades **local** configuration, cached Git URLs, and matching saved MR links. It does not configure GitLab's server, change token scopes, or install trust material. Python API/MCP private-CA support and redirect protection are described separately in [runtime TLS](HTTPS_API_TLS.md); native Git retains its own configuration. This command is included in v0.5.0; for editable/source installs that may move ahead, record the exact source commit.

## Supported scope

The same GitLab instance, hostname (DNS or IPv4), repository URL prefix, and default HTTP/HTTPS ports. Explicit `:80`/`:443` forms are accepted when stored URLs match the approved mapping exactly. Changed hostnames/prefixes, non-default ports, URL rewrites/includes, extra/inherited/multiple remotes, and per-worktree Git config require separate review and stop automatic migration.

Run on a trusted personal local filesystem. Stop other writers before apply; the migration lock does not lock coding agents, editors, ordinary Git commands, or MCP. Confirm the server endpoint and certificate chain with its operator first. Never fix TLS by disabling certificate verification.

## 1. Identify the effective config

Run `actual-coder config` from the normal installation, or the PR environment's executable from the original source directory. File lookup can depend on that directory. Inspect locally; do not publish the full output.

The usual config is `~/.config/gitlab-agent/.env`; an explicit `GITLAB_AGENT_ENV_FILE` can select another file. The managed root is a different directory, normally `~/.local/share/chatgpt-gitlab-mcp`. This is not the ReasonFirst source clone's GitHub origin.

Exported variables override `.env`. Update stale launcher/service settings separately; the tool changes only the selected file. Choose the actual config and operator-approved endpoints, not the examples below.

## 2. Offline preview

From the source checkout on macOS/Linux:

```bash
(
set -eu
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
unset GITLAB_BASE_URL
uv run actual-coder-migrate-https --config-file "$GITLAB_AGENT_ENV_FILE" --from-url "http://gitlab.example.com" --to-url "https://gitlab.example.com"
)
```

On PowerShell, run in a dedicated terminal whose temporary environment settings you control:

```powershell
$env:GITLAB_AGENT_ENV_FILE = Join-Path $HOME ".config\gitlab-agent\.env"
Remove-Item Env:GITLAB_BASE_URL -ErrorAction SilentlyContinue
uv run actual-coder-migrate-https --config-file "$env:GITLAB_AGENT_ENV_FILE" --from-url "http://gitlab.example.com" --to-url "https://gitlab.example.com"
```

There is no `--dry-run` flag: preview is already the default. It makes no live endpoint-file/state/ref changes. Private scratch Git-config copies are prepared and removed. Output contains paths, counts, hashes, changes, and `plan_digest`, not raw configuration/credentials.

Review `config_file`, `workspace_root`, `projects`, `workspace_count`, and every proposed change. Six inspected workspaces need not mean six metadata changes: only saved MR links needing an upgrade change. An allowlisted project need not be cached. Missing/stale/malformed state is an error to investigate, not permission to delete a workspace or expand the allowlist.

## 3. Verify the HTTPS handshake separately

Repeat the same preview command with `--check-tls`. Optionally add `--plan-digest` with the exact digest copied locally from your preview. This sends one unauthenticated HTTPS request, reads response headers only, verifies certificates using HTTPX's default trust, ignores environment proxies/credentials, and refuses redirects.

| Probe result | Interpretation |
| --- | --- |
| Certificate verified, HTTP 401/403 | The TLS connection worked; authentication is intentionally untested |
| Certificate verified, HTTP 404/500 | TLS worked, but investigate API path/service readiness |
| Redirect | Check the canonical endpoint/reverse proxy; no redirect is followed |
| Verification/connectivity error | Investigate CA/chain, hostname, expiry, DNS, routing or VPN; do not disable verification |

`ok: true` is not end-to-end readiness. `api_auth_checked` and `git_tls_checked` remain false. A private CA working in a browser does not prove Python/Git trust. This maintenance probe has no custom-CA CLI option and does not read `GITLAB_CA_BUNDLE`. Python API/MCP clients now support that setting, so a private-CA API request can work while this default-trust probe still fails. Do not disable verification to reconcile them. See [runtime TLS](HTTPS_API_TLS.md); native Git integration remains in [Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10).

## 4. Apply only the reviewed local changes

Record the relevant workspace HEAD/branch/status privately and stop coding workers, Git/editor tasks, and the MCP process. Replace the digest placeholder with the full value from your own preview:

```bash
(
set -eu
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
unset GITLAB_BASE_URL
PLAN_DIGEST="REPLACE_WITH_YOUR_REVIEWED_DIGEST"
uv run actual-coder-migrate-https --config-file "$GITLAB_AGENT_ENV_FILE" --from-url "http://gitlab.example.com" --to-url "https://gitlab.example.com" --plan-digest "$PLAN_DIGEST" --apply --workers-stopped
)
```

The tool checks the digest, prints the plan, and asks for interactive confirmation. Scripted apply requires `--yes`, the exact prior `--plan-digest`, and `--workers-stopped`; that acknowledgement does not stop processes for you. Changed inputs invalidate the preview. Do not drop the check merely to bypass a mismatch.

Apply changes only the selected `.env` base URL, inspected matching cache origin/push URLs, and matching saved MR URLs. It preserves other metadata and performs no reset, cleanup, reclone, fetch, push, source edit, history rewrite, or MR recreation.

Private before/after backups and a per-file journal are created under the managed root. **They may contain tokens from `.env`; never upload them.** POSIX backups use restricted modes; Windows uses current-user ACLs, which may differ from previous inherited ACLs. Shared/network filesystems are not supported.

## 5. Acceptance: state, API, Git, then MCP

Repeat the original offline preview with the same old/new mapping but **without the old digest**. A completed migration produces `changes: []` and `no_op: true`; the digest has legitimately changed. General warnings are reminders, not pending operations.

From an environment using the updated config:

```bash
uv run actual-coder config
uv run actual-coder doctor
uv run actual-coder project-config team/project-a --validate
```

Use your actual project. `doctor` verifies API authentication, not Git push rights. `project-config` fetches/reads Git and validates policy, without creating a worktree or pushing. A missing `.actualcoder.yaml` is a policy gap, not a transport failure or successful test run.

Inspect a pre-existing workspace, replacing the illustrative ID:

```bash
WS="012345abcdef"
uv run actual-coder status "$WS"
uv run actual-coder ci "$WS"
```

Compare identity, branch, HEAD/base and pending files with your saved observations; check the saved MR URL is HTTPS. Matching successful historical CI proves retrieval and SHA association, not a new push or a complete build. A new test-branch write requires its own authorization.

Restart the **existing** MCP/tunnel launcher with the updated config and no stale HTTP environment override. Verify a read through that client separately. Do not replace a tunnel deployment with a bare `run_mcp.sh` process and assume connectivity is restored.

## Interrupted apply

Preserve the journal and all existing work; keep other writers stopped. Inspect current versus before/after hashes locally, then create a new preview using the same approved old/new mapping. Mixed old/new state is supported for forward completion after review; a completed repeat is a no-op. Do not blindly restore HTTP or clean/reclone workspaces.

This is per-file recovery, not an atomic transaction. Fingerprints cover configuration/metadata/ref/status, not every dirty-file byte or future external change. Keep backups private until recovery and retention decisions are complete.

## Development checks and remaining work

```bash
uv run python -m unittest discover -s tests -p 'test_https_migration*.py' -v
uv run python -m unittest discover -s tests -v
uv run python scripts/check_repo_secrets.py --history
```

The tests use temporary repositories and freshly generated loopback TLS certificates. Python API/MCP private-CA support and rejection of all API redirects are now implemented separately; native Git trust/destination-policy integration and layered diagnostics remain unfinished. The migration probe's behavior must not be generalized to every client. A successful migration on one host does not certify other platforms, instances, or trust backends.
