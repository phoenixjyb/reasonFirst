# ReasonFirst security policy and boundaries

ReasonFirst operates near repository code, host files, coding-agent sessions, and GitLab credentials. Use it on trusted development hosts with repositories you are authorized to access. It is early-stage developer tooling, not a sandbox or a multi-tenant execution service.

## Reporting a security issue

Do not disclose vulnerabilities, credentials, private endpoints, or exploit details in public issues, PRs, discussions, or logs.

Use the repository's GitHub **Security -> Advisories -> Report a vulnerability** workflow. Private vulnerability reporting is enabled for the public ReasonFirst repository. If that UI is unavailable to you, request a private contact channel in a public issue **without technical details, secrets, affected private hostnames, or proof-of-concept attachments**, and wait for a private route before sharing the report. Do not invent a security email address.

Privately include the affected commit/version, a synthetic reproduction, impact, and proposed mitigation. Revoke a real exposed credential immediately rather than waiting for a code fix. Maintainers have not committed to an incident-response SLA or long-term backport policy; confirm affected refs rather than assuming all historical tags are supported.

## Credentials and outbound data

Keep `.env`, GitLab/Git tokens, tunnel runtime credentials, private keys/certificates, managed-workspace metadata, and migration backups outside version control. Migration backups include exact copies of configuration and may contain tokens; never upload them as a bug-report bundle.

Prefer a dedicated read-only API identity, a separate Git credential with only necessary repository-write rights, and explicit project allowlists. An unset Git token falls back to the API token; scope is not upgraded. Askpass avoids embedding tokens in Git remote URLs, but the local user/host can still access configured secrets. API-side allowlisting is not a substitute for GitLab permissions.

ReasonFirst itself does not call model-inference endpoints. External coding agents and reasoning clients can send code, prompts, or tool output to their providers under their own settings. A local controller does not make the complete workflow offline or private. Only expose code to those tools when authorized. The optional MCP bridge makes approved read results available to its client; run it behind an appropriate authenticated transport and read-only service identity.

## Control-plane protections and their limits

Managed paths and branches are checked by supported controller operations. The controller creates feature branches, avoids force-push/base-branch publication, and has no MR approve/merge/deploy operation. These controls govern that interface, not arbitrary commands executed by the same local user, editor, coding agent, or build script.

The high-level `finish` path reads project policy from the immutable workspace base, executes configured validations, checks reviewability/protected paths, scans candidate and bounded history additions, then requests confirmation. Low-level `commit`/`push` commands do not run the entire finish gate. Some generated handoffs still describe those manual routes; review the worker's actions rather than assuming the controller intercepts all writes.

`.actualcoder.yaml` cannot add local executable permissions. A missing contract is valid but supplies no project-specific tests. Editing the contract inside a task does not replace the original base policy for that task. Treat policy/CI/build changes as sensitive review items.

## Build/test execution is not a sandbox

An approved `python`, `uv`, `npm`, `make`, or test runner can execute arbitrary repository-controlled code, read other host paths, and make network requests. Stripping obvious environment secrets is not isolation from filesystem credentials or network services. The general runner's displayed output cap is not a guarantee of bounded subprocess buffering; process-tree termination and resource isolation remain follow-up work.

`finish --dry-run` runs validation commands and may change files. It means no commit/push, not no execution. `start --no-launch` creates local state and may fetch repositories; it means no coding-agent launch. The HTTPS maintenance preview is different: it inspects local inputs without changing live endpoint/state/ref files; `--check-tls` explicitly opts into a network probe.

Use an independently secured disposable VM/container for untrusted code, with restricted mounts, credentials, network, and host access. ReasonFirst does not configure that isolation for you. Do not treat a Git worktree as an OS security boundary.

## Prompt injection and evidence

Repository instructions, MRs, build output, and CI logs are untrusted inputs, not authorization to change the goal or weaken tests. Explicit trust labels and shared log sanitization are defense in depth, not a guarantee of model behavior.

CI results are associated with a reported SHA. `resume --from-ci` rejects stale pipeline evidence. Matching HEAD alone does not validate later dirty files, prove all jobs ran, or establish a full build when the pipeline was docs-only. Never claim complete logs when the reader reports incomplete/limited coverage.

## Secret-scan scope

Finish checks candidate diff additions and added text in `base_sha..HEAD`, including separate merge-parent diffs. The history scan is bounded; unsupported/incomplete history blocks finish even with a secret-match override. It is not an audit of pre-base history, commit messages, external LFS content, arbitrary secret encodings, or every candidate blob/filter/index interaction. `--allow-secret-match` is for explicitly reviewed false positives, not real credentials or missing evidence.

Findings omit offending source lines, and displayed finish diffs are sanitized. Raw state fingerprints still bind the reviewed input. A redaction heuristic cannot guarantee removal of every secret from every diagnostic; review all material before public posting.

The repository audit is a separate development tool:

```bash
uv run python scripts/check_repo_secrets.py --history
```

It checks tracked text files and full **available HEAD history**. It does not audit every remote branch/tag, PR comment, release asset, external service, or dependency. Known historical test-fixture exceptions are not permission to add real credentials. A green check is useful evidence, not a full security certification. See the [public-release checklist](docs/PUBLIC_RELEASE_CHECKLIST.md).

## HTTPS and certificate trust

Configure HTTPS directly. An HTTP-to-HTTPS redirect cannot encrypt a token already sent in the first HTTP request. Keep certificate verification enabled; never use disabled verification or silent HTTP fallback as a repair.

Python API/MCP clients share an explicit verified SSLContext for JSON, text, and trace requests. Public certifi roots are retained; the user-level `GITLAB_CA_BUNDLE` optionally adds PEM certificates. Disabled verification is rejected before client requests. `SSL_CERT_FILE`/`SSL_CERT_DIR` do not implicitly select trust, even with proxy inheritance enabled. Request hooks restrict credentials to the configured origin and API prefix; response hooks reject every 3xx response, including same-origin redirects, before following it or reading its body. Explicit legacy HTTP remains unencrypted; there is no automatic HTTPS upgrade or HTTP fallback.

**Native Git is separate:** these hooks and `GITLAB_CA_BUNDLE` do not configure Git’s trust store, redirect behavior, URL rewriting, or credential destination. The maintenance command upgrades local URLs; its optional unauthenticated `--check-tls` probe uses default trust and does not consume `GITLAB_CA_BUNDLE`. Do not generalize the runtime policy to the separate legacy `smoke_test.py` helper. See [runtime TLS and its scope](docs/HTTPS_API_TLS.md). Native Git integration and layered diagnostics remain in [Issue #10](https://github.com/phoenixjyb/reasonFirst/issues/10).

See [HTTPS migration](docs/HTTPS_MIGRATION.md) for separate local-state, certificate, API-auth, Git-read, and MCP acceptance steps. One working deployment is not proof for every TLS backend or private CA.

## Concurrency and recovery

ReasonFirst implements re-entrant cross-process mutation locks for managed workspace state and Bridge-managed mutation-critical paths. These locks serialize cooperating ReasonFirst writers, but they are **not** transactional crash recovery, an OS sandbox, or protection from arbitrary same-user processes. Do not deliberately run independent/unmanaged mutating tools against the same workspace.

Finish fingerprints the reviewed post-validation state and rechecks it before writes. This detects certain changes; it does **not** close every race window or make commit/push/metadata persistence atomic. Normal cleanup uses fresh publication evidence rather than a historical pushed flag, but remote state can change later. Failed compare-and-delete can leave a preserved branch/metadata needing explicit worktree recovery.

The migration lock coordinates migration processes only. Apply requires other writers stopped, records private before/after files and a journal, and supports inspected forward recovery; it is not an atomic multi-file transaction or a hostile-same-user defense. Windows replacement files use current-user ACLs. Shared/network filesystems are outside this maintenance tool's supported scope.
