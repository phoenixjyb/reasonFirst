# GitLab API/MCP TLS, private CAs, and redirects

[中文](HTTPS_API_TLS_CN.md) · [Quickstart](ACTUAL_CODER_QUICKSTART.md) · [Migration](HTTPS_MIGRATION.md) · [Security](../SECURITY.md)

ReasonFirst's synchronous GitLab API client and asynchronous read-only MCP client share a verified TLS context and credential-destination checks. This behavior is included in v0.5.0. For editable/source installations that may move ahead of a release tag, record the exact source commit when diagnosing an installation.

## Know which client you are configuring

| Path | Certificate trust | Redirect behavior | What it establishes |
| --- | --- | --- | --- |
| CLI API and MCP JSON/text/job traces | Public certifi roots plus optional `GITLAB_CA_BUNDLE` | Every API 3xx response is rejected | API transport and, on an authenticated request, that API operation |
| Native Git clone/fetch/push | Git's own trust configuration/backend | Git's independent policy | The Git operation actually executed; a fetch does not test push rights |
| Migration `--check-tls` | HTTPX default trust; does **not** use `GITLAB_CA_BUNDLE` | No redirects | One credential-free TLS connection and HTTP status, not API authentication or Git trust |

The maintenance command changes approved local URLs, not trust stores. The separate legacy `smoke_test.py` helper is not part of the shared runtime client factory; do not use it as proof of these request guards. A browser's trust configuration is not proof that either Python or Git will accept the certificate.

## Publicly trusted GitLab certificate

Set the final base URL directly in your private, effective user configuration:

```dotenv
GITLAB_BASE_URL=https://gitlab.example.com
GITLAB_VERIFY_SSL=true
GITLAB_TRUST_ENV=false
```

Replace the illustrative endpoint with your authorized service. The base URL is the GitLab root, or its simple installation prefix such as `https://gitlab.example.com/gitlab`, **not** an API route ending in `/api/v4`. Publicly trusted deployments normally need no additional CA setting. This does not prove the endpoint is reachable or your token is valid.

`GITLAB_VERIFY_SSL=false` is rejected by Python API/MCP clients. An HTTPS error is not a reason to disable verification, alter token scopes, or silently fall back to HTTP.

## Add an operator-approved private CA

Only when your service uses a private CA, obtain its trusted PEM CA certificates through an authorized channel and add:

```dotenv
GITLAB_CA_BUNDLE=/absolute/path/company-ca.pem
```

An absolute path or `~/...` is required. Repository-relative paths are rejected. The setting augments public certifi roots; it does not remove them, install certificates into the operating system, or modify `.gitconfig`. The bundle must be nonempty, readable PEM certificate material, at most 4 MiB, with no private keys. Never copy server private keys into a client trust bundle or upload local trust files with a public issue.

Use a private user configuration, not `.actualcoder.yaml`: repository-owned task policy cannot add this setting or disable TLS. Exported values still take precedence over `.env`; restart the long-running MCP process after configuration changes.

CA trust is independent of proxy inheritance. `GITLAB_TRUST_ENV=false` can remain in place with a private CA. Even with proxy inheritance enabled, `SSL_CERT_FILE` and `SSL_CERT_DIR` do not implicitly select the shared Python clients' trust; use the explicit setting instead. `GITLAB_GIT_TRUST_ENV` is not a Python CA option.

**This setting does not configure native Git.** Native Git may need separate administrator-approved trust configuration, and its TLS backend can differ by platform. A successful Python API request must not be described as a successful Git fetch or push.

## Canonical endpoints and rejected redirects

Before a shared-client request is sent, the guard checks its scheme, hostname, port, Host header, and path under the configured `/api/v4` prefix. Off-origin or unsafe-path requests are refused. Every 3xx response is rejected before its body is read or another request is followed, including same-origin canonicalization, login-page redirects, and HTTPS-to-HTTP downgrades.

Configure the endpoint that returns API responses directly. A redirect is not authorization to send a token to its target. Ask the GitLab/reverse-proxy operator to diagnose canonical URLs and API routing; do not weaken the guard just to follow a login page.

Explicit legacy HTTP configuration is still accepted for compatibility, but remains **unencrypted**. Its initial token-bearing request is not protected by a later redirect. No automatic HTTPS upgrade or HTTP fallback is performed. For existing HTTP caches and saved links, use the separate [migration workflow](HTTPS_MIGRATION.md).

These checks constrain the configured client, not arbitrary code on the same host. They are not an OS sandbox or a general audit of native Git URL rewriting or every diagnostic message.

## Verify each layer after a source/configuration update

First update the intended source environment and restart processes importing it. Existing global editable tools may use a separate environment; follow [installation updates](LOCAL_PR_REVIEW.md). Do not overwrite a working `.env` with `.env.example`, or rerun a completed URL migration solely because the Python TLS implementation changed.

From the source checkout, run one command at a time, using your intended private configuration and an authorized project:

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
uv run actual-coder config
uv run actual-coder doctor
uv run actual-coder project-config team/project-a --validate
```

`config` identifies the effective configuration, not a complete TLS-readiness report; it does not currently display the CA-bundle field. Inspect that setting locally without posting credentials. `doctor` checks API authentication. `project-config` fetches/reads Git and validates the project contract without creating a worktree or pushing. Contract absence is a policy gap, not an HTTPS failure. Restart the existing MCP/tunnel launcher and verify a read through that client separately. A new push requires its own approval and test-branch plan.

### Common results

| Result | Interpretation and next action |
| --- | --- |
| API works, Git reports certificate failure | Python and Git have separate trust; inspect the native Git configuration without disabling verification |
| Private-CA API works, migration `--check-tls` fails | The maintenance probe does not consume `GITLAB_CA_BUNDLE`; this is not evidence of token expiry |
| Credential-free probe returns 401/403 after verified TLS | The handshake worked; authenticated access remains untested |
| Runtime API returns a redirect error | Inspect the final base URL and reverse-proxy API routing; redirects are deliberately refused |
| Invalid/empty/relative CA bundle | Correct the explicit, trusted certificate file; do not substitute a key or repository-owned file |
| Source updated but MCP behaves as before | Check which checkout/interpreter the launcher imports and restart that process; Git branch changes do not restart services |

## Development verification and remaining scope

The tests use generated temporary certificates and local loopback servers, not production GitLab credentials:

```bash
uv run python -m unittest discover -s tests -p 'test_tls_policy.py' -v
uv run python -m unittest discover -s tests -p 'test_runtime_tls.py' -v
uv run python -m unittest discover -s tests -v
```

Native Git trust/destination integration and layered API-versus-Git diagnostics remain in [Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10). The migration lock is not general workspace locking. Provider eligibility, code-execution isolation, token scopes, and release approval are separate concerns.

Implementation reference: [`tls.py`](https://github.com/phoenixjyb/reasonFirst/blob/main/src/gitlab_agent/tls.py). Primary library references: [HTTPX SSL](https://www.python-httpx.org/advanced/ssl/), [HTTPX environment variables](https://www.python-httpx.org/environment_variables/), and [Git configuration](https://git-scm.com/docs/git-config).
