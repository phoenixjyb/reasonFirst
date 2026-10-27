# Service inventory and planning (Slice 1)

Slice 1 adds read-only service evidence and a deterministic login-start plan:

```text
reasonfirst service status --json
reasonfirst service plan --autostart login --json
reasonfirst service repair --dry-run --json
```

Status reports the installed Python package separately from service definitions,
setup metadata, and possible deployment roles. Inventory is limited to known
static macOS ReasonFirst definitions. A definition or recorded runtime reference
does not establish native registration, a running process, process ownership,
readiness, or end-to-end acceptance. Those live observations remain
`not_checked`; timestamps are absent when no observation occurred. Linux and
Windows native adapters remain `not_inspected`, and unsupported platforms are
reported explicitly. The legacy `ownership_verified` inventory field applies
only to checked static definition metadata; it is not runtime authority.

Plans preserve known deployment topology and state the proposed OS owner,
prerequisites, blockers, handover evidence, and future rollback requirements.
Slice 1 always returns a blocked, non-executable plan. Later authorized work
must establish identity, ownership, stable runtime, configuration, credential
reference, and platform evidence. For stdio, the
upstream tunnel runtime retains ownership of its MCP child. For HTTP, Bridge and
tunnel roles remain separate. The planner does not infer running instances from
static or recorded roles and does not claim native platform acceptance.

Every command in this slice is non-mutating: it does not install or control
services, change setup/configuration, read credential contents, or execute a
plan. Credential references without reviewed metadata remain blockers. The
Windows secure state reader is unsupported where its required no-follow and
descriptor guarantees are unavailable. A source checkout is not evidence of a
stable installed runtime.

Exit status: `status` returns 0 when evidence collection completes, including
when individual evidence is invalid or unavailable. A blocked plan or dry-run
repair returns 1. Repair without `--dry-run`, boot/unattended startup, and other
unsupported arguments return 2; JSON-requested errors are redacted. This slice
does not implement repair application or service lifecycle commands.

An internal collection or planning failure returns 1 with the fixed
`service_inspection_failed` error. This differs from a blocked plan, which has
`plan_status: blocked` and evidence without a top-level error. Human output is
summary-only; use `--json` for evidence states and fixed reader reason codes.
Observed file disappearance or directory replacement discards the bytes and
reports changed evidence. Parent checks compare directory identities, allowing
unrelated sibling activity. These consistency checks are not an atomic snapshot.

For the Chinese guide, see [服务清点与计划](SERVICE_PLANNING_CN.md).


## Slice 2A: opt-in native manager observations (unreleased)

Default service status remains static and never invokes an OS manager. To
request read-only service-manager evidence explicitly:

    reasonfirst service status --native --json

The observer queries only TWO fixed candidate identifiers per supported OS:
macOS GUI user domain: com.reasonfirst.v4-mcp and com.reasonfirst.v4-tunnel.
Linux systemd user: reasonfirst-v4-mcp.service and reasonfirst-v4-tunnel.service.
Windows Scheduled Tasks under the ReasonFirst task folder: V4 MCP and V4 Tunnel.
These candidate names are NOT proof that any installed service uses them.
A negative result means only "not found at a known identifier", not a
whole-host absence. If the GUI domain, systemd user manager, or Windows task
module is inaccessible, the observer reports unknown/unavailable, not healthy.

Native stdout/stderr, installed command arguments, arbitrary target paths,
environment values, and credentials are never returned. Manager activity is
not verified process identity, runtime ownership, health, version, actual login
startup, or successful end-to-end connection. Login settings are only
configuration indicators. No process enumeration, service adoption, installed
service modification, registration, start/stop/repair, or credential access.
A query timeout can clean up ONLY the short-lived query subprocess, not a
ReasonFirst or tunnel service. Plans remain blocked and non-executable.
Actual platform acceptance and ownership verification remain outstanding.
