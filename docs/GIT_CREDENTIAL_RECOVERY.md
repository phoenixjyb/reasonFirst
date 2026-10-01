# Managed Git credential recovery

[简体中文](GIT_CREDENTIAL_RECOVERY_CN.md) · [Troubleshooting](TROUBLESHOOTING.md)

## Git read fails despite working API access

A successful MCP/API read does not prove native Git authentication. If a managed
clone reports a credential-manager/OAuth warning or a missing legacy helper, do
not immediately rotate a valid token, register an OAuth application, reinstall
Git, clear Windows Credential Manager, or change global Git configuration.

For authenticated managed clone/fetch/push operations, ReasonFirst now resets
inherited credential helpers for that Git invocation and pins the configured
username for the exact remote. The configured Git credential still comes from
`GITLAB_GIT_TOKEN`, then `GITLAB_GIT_PASSWORD`, then the existing `GITLAB_TOKEN`
fallback; conflicting explicit Git token/password settings remain an error.
The temporary askpass supplies the credential without placing it in argv or a
remote URL. Terminal prompting is disabled, while the packaged askpass is allowed.
Inherited Git tracing is removed from the authenticated child environment. This
does not edit persistent Git settings or affect other running jobs.

After an isolated read probe succeeds, keep that credential and retest a reviewed
replacement package. `ls-remote` success proves reference-read access at that
moment, not a completed clone, push rights, worker execution, or a complete setup.
Do not rerun `start` after a partial failure until checking whether a workspace was
created; do not delete an existing cache/worktree merely to clear an error.

This is credential-selection isolation, not a general native-Git sandbox or a
rewrite of its CA/proxy/redirect/URL-rewriting policy. The missing Windows
`python3` command in a project validation contract is a separate portability
issue; do not edit protected tests/policy or claim it fixed by a Git-auth change.

