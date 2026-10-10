# Review a saved deployment and prepared runtime together

**Included in v0.5.2; not available in the v0.5.1 wheel.** This B3a
preflight joins the identities established by deployment recording and offline
runtime preparation. It does not establish their compatibility or switch a service.

## Inputs and platform boundary

The initial pairing adapter supports **macOS only**, using the one known saved
user LaunchAgent `com.reasonfirst.v4-mcp`. It requires an existing, matching
[deployment record](DEPLOYMENTS.md) and an existing, unchanged
[prepared runtime](RUNTIME_PREPARATION.md) selected by its exact runtime ID.
Neither missing input is created, adopted, repaired, downloaded or installed.

Linux and Windows return `unsupported_pairing_platform` before HOME discovery
or storage inspection. This narrower limit does not remove macOS/Linux runtime
preparation or the existing Windows packaged/source installation support.
Systemd, Windows services/ACLs and other deployment layouts are not inferred.

## Plan and recheck

Run the development CLI that contains these commands. Replace the uppercase
values with the actual reviewed identifiers, not package versions or branch names.

```bash
reasonfirst-runtime deployment-plan --runtime-id PREPARED_RUNTIME_ID --json
reasonfirst-runtime deployment-check --runtime-id PREPARED_RUNTIME_ID --expect-digest PAIRING_PLAN_DIGEST --json
```

`deployment-plan` validates the saved record against the current saved plist and
reuses the strict prepared-runtime file/interpreter validator. The runtime must
match the current host platform and architecture. It measures both inputs again
before returning a single `plan_digest`. No plan file or persistent association
is written. The original registry's `runtime_id` remains null.

The digest binds a separate schema/scope, destination HOME, host, exact registry
bytes, current registration snapshot and adoption digest, selected runtime ID,
exact runtime-manifest bytes and manifest digest. The latter binds the recorded
wheel/dependency/interpreter identity and installed tree. Even a whitespace-only
metadata change invalidates the pairing digest. Runtime or adoption digests cannot
substitute for this pairing digest. Hashes detect changes; **not a signature**,
attestation, source-trust assessment or permission token.

`deployment-check` performs the same fresh inspection and requires an exact match
to the reviewed pairing digest. It has no `--yes`, `--force`, service-start or
repair option. A recheck is not a maintenance lock or execution approval. Any
future mutating stage must make its own fresh observations and obtain approval.

## What success means

A successful inspection returns exit code 0 and `ok: true`, with:

```json
{
  "scope": "static-deployment-runtime-pairing-v1",
  "pairing_verified": true,
  "compatibility": "not_established",
  "compatibility_verified": false,
  "legacy_control_requirement": "unknown",
  "ready_for_activation": false,
  "activation_authorized": false,
  "live_service_verified": false,
  "commands_executed": false,
  "mutating": false,
  "proposed_actions": []
}
```

The output lists seven unresolved requirements: saved versus loaded service
identity; current launcher and configuration bindings; endpoint/mode/tool
privileges; whether legacy `/control` is needed; target configuration/state
compatibility; active-work admission; and controlled activation/durable recovery.
These are blockers, not warnings that a caller can override using this command.

The saved plist may differ from launchd's loaded definition. A recognized launcher
path does not identify the running code. An import-validated package does not
prove that its endpoint, privileges or state handling match the old deployment.
The target HTTP wrapper does not implement legacy `/control`; this command never
infers that the old deployment does not need it. No live health, client, worker,
provider, reboot, switching or rollback acceptance is claimed.

## Failures and evidence limits

A missing record/manifest, registration change, modified installed tree or base
interpreter, host mismatch, invalid/unsafe metadata or changed review digest
returns exit code 1. No partial pairing identity or digest is returned. Fixed error
codes distinguish these cases without exposing source contents or raw exceptions.
Existing records, files and failed-preparation evidence are left untouched.

Both validators retain their private owner/mode, no-follow and hardlink checks.
Only known registry/registration paths and the explicitly selected runtime are
inspected. Referenced launchers, application `.env`, `bridge.yaml`, `setup.yaml`,
credential stores, workspace/task/approval state and live services are not read.
The plist can contain environment values; all its bytes are hashed, but only the
existing validated snapshot fields are returned, never raw environment values.
Reports still contain local paths and hashes; keep them private.

Repeated reads detect changes observed during inspection, not an atomic snapshot,
ABA protection, malicious-same-user isolation or a durable transaction. Files may
change immediately afterward. Source-tree tests use disposable records and mocked
preparation execution; the native macOS install harness separately tests the new
CLI using a genuinely prepared runtime and a synthetic saved LaunchAgent. It
never loads that LaunchAgent. Linux/Windows test the explicit unsupported boundary.

[Deployment records](DEPLOYMENTS.md) · [Runtime preparation](RUNTIME_PREPARATION.md) · [中文](DEPLOYMENT_PAIRING_CN.md)

[Next: saved launcher and HTTP compatibility review](LAUNCH_REVIEW.md)
