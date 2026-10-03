# Record an existing deployment registration

**Development-source feature; not available in the published v0.5.1 wheel.**
This is the first part of the deployment-identity work, not a general updater.
The existing [install and update guide](INSTALL.md) still applies.

## What this records, and what it does not

`reasonfirst deployment` can explicitly record the **saved registration** of the
known macOS user LaunchAgent `com.reasonfirst.v4-mcp`. It supports the staged
`v4-service` launcher layout and the versioned `uv-http-vX.Y.Z-…` HTTP-sidecar
layout. It does not search unrelated services. Linux and Windows return
`unsupported_platform` before filesystem inspection; no systemd or Windows service
layout is assumed. Other macOS layouts have no recording path in this version.

Discovery remains separate from recording. `reasonfirst status` and
`setup --status` remain nonmutating; neither writes this new registry. The registry
is independent of the wizard's `setup.yaml` and does not manufacture completed
onboarding phases. Use `deployment status` to inspect the new record.

The saved record has a strict versioned schema. It holds the known service label,
registration path and SHA-256, recognized launcher/cwd/transport layout, review
digest, timestamp, and the version of the CLI that recorded it. That CLI version
is **not the running service version**. Runtime identity, application configuration
bindings, credential validity, listener ownership and health remain `not_inspected`.
`runtime_id` and `running_version` are null; `activation_authorized` is false.

The full plist is read locally and may contain environment values. Its hash binds
all bytes, but raw environment values, extra arguments and plist contents are not
printed or copied into the record. Referenced launcher scripts, `.env`,
`bridge.yaml`, setup state, credentials, workspace and approval state are not read.
No `launchctl`, HTTP, MCP, worker, package manager or provider call is made.

The recognized path and `streamable-http` label are **static layout evidence**, not
verification of the actual transport or loaded code. The digest is a change
detector, not a signature, authentication token or future restart permission.
Keep metadata/reports private: they still include local paths and fingerprints.

## Review, explicitly record, then inspect

From a development build containing this feature:

```bash
reasonfirst deployment plan --json
```

This prints a plan and `plan_digest`; it does not save a plan or create directories.
Review the exact service, source path, launcher, working directory and proposed
`record_path`. An unsupported/invalid registration or conflicting record stops the
operation. Then copy the digest from that reviewed plan into the command below:

```bash
reasonfirst deployment adopt --expect-digest "REPLACE_WITH_REVIEWED_DIGEST" --yes --json
reasonfirst deployment status --json
```

Replace `REPLACE_WITH_REVIEWED_DIGEST` rather than executing that placeholder. `adopt`
here means **record this registration snapshot only**. It is not activation or
adoption of a runtime, application configuration or tunnel. There is no implicit
prompt approval: both the exact digest and `--yes` are required. The command
rechecks the source before writing, including changes to unprinted plist fields.
A previously recorded identical snapshot is a no-op that preserves record bytes
and timestamp. No existing record is replaced, even with `--yes`.

The only persistent write is the new record (plus missing private parent
directories and a temporary sibling during creation):

```text
~/.config/reasonfirst/deployments/com.reasonfirst.v4-mcp.json
```

The implementation validates ownership and modes, rejects symlinked paths,
nonregular/hardlinked files and untrusted directories, writes privately, and uses
create-if-absent publication rather than overwriting. Existing directories are not
chmodded. A concurrent recorder cannot replace the winner; inspect status instead
of retrying blindly. This is not protection against a malicious same-user process
with full access to your home, or an assertion of macOS ACL validation.

## Evidence and failures

`deployment status` compares the current saved plist to the recorded snapshot:

| Result | Meaning |
| --- | --- |
| `not_recorded` | No registry record; not a claim that no service exists. |
| `matches_record` | The inspected registration matches the snapshot at this check. |
| `drifted` | The saved plist changed; the old snapshot is retained, not overwritten. |
| `unavailable_or_unsupported` | A current registration comparison could not be completed. |

A valid status response can have `ok: true` while showing `drifted`: status
inspection succeeded, not service acceptance. `ready_for_activation` stays false.
The service manager's **loaded** definition can differ from the saved plist; it is
not inspected here. Editing a script without changing its path also requires later
runtime-code verification; this snapshot does not cover script contents.

An adoption error may report `record_written: null`: a failed final flush/readback
cannot prove whether creation committed. Inspect `deployment status` before any
retry. Power loss/forced termination can leave a private `.pending-…` file or an
extra hard link; no automatic deletion, overwrite, rollback or recovery is offered.
A partial/invalid existing record is rejected. Preserve it for review; do not remove
it just to make the command succeed. No service was stopped, so record repair does
not require restarting a daemon. Normal commands do not modify setup records,
application configuration, workspace state or release artifacts.

## Next implementation stages

A subsequent runtime-preparation plan must independently inspect configuration
references, package/dependency/interpreter identity, transport, endpoint and
privilege compatibility. A recorded snapshot is only one input and must be
revalidated. Side-by-side runtime installation, packaged HTTP compatibility,
maintenance gating, service switching, recovery and rollback are **not implemented
by these commands**. Do not update or restart a working machine to test recording.

The separate [packaged HTTP transport](BRIDGE_HTTP.md) is a development-only
shared-core wrapper. It does not make this registration command an updater and
is not compatible with deployments requiring the legacy `/control` route.

[中文说明](DEPLOYMENTS_CN.md)
