# Prepare a separate service runtime

**Development-source feature; not in the published v0.5.1 wheel.** This is B2b:
an offline package-environment preparer, not an existing-service migration or
activation engine. It must not be used to replace a working service launcher.

## Scope and inputs

The first persistent-storage adapter supports **macOS and Linux on local POSIX
filesystems**. Windows rejects plan/prepare/status before filesystem inspection;
Windows ACL/reparse-point installation and service-manager adapters remain future
work. The shared packaged HTTP transport is still tested on all three platforms.
Do not infer systemd or Windows-service integration from package support.

Provide a dedicated directory containing only the reviewed ReasonFirst wheel and
one wheel for each selected dependency/version for the intended OS and Python.
No source distributions, directories, editable checkouts, duplicate distributions,
symlinks, hardlinks or direct-URL dependencies are accepted. Obtaining the complete
wheel set is a separate developer/distribution step, **not implemented by this
command**. This is not yet a one-command public download-and-upgrade experience.

The package's SHA-256 must come from the reviewed artifact. Plan hashes and shows
**every dependency wheel**, so the operator approves those exact bytes too. Hashes
are change detection, **not a signature**, trust assessment or independent source
attestation. A version string does not prove a source commit. A matching set may
still be incomplete/incompatible; actual dependency resolution is checked later.

Select absolute paths to existing `uv` and a base CPython executable, not `py`, a
shell, a PATH version selector or a tool virtual environment. Preparation retains
that interpreter as an external dependency; it does not copy/freeze the entire
Python standard library, install a new Python, or permit deleting the old one.
The runtime is independent of the convenient CLI's mutable uv tool environment,
but not immune to replacement of its underlying base interpreter.

## Inspect, review and prepare

Use the development CLI containing this feature. Replace every uppercase example
value before execution; no command here targets a current live service.

```bash
reasonfirst-runtime plan --wheelhouse /ABSOLUTE/REVIEWED_WHEELS --python /ABSOLUTE/python3 --uv /ABSOLUTE/uv --package-sha256 REVIEWED_PACKAGE_SHA256 --json
```

The plan performs bounded file/metadata inspection only. It does not run Python,
uv, a controller, a listener or provider; it creates no directory or saved plan.
It binds the exact wheel set, paths, interpreter/uv fingerprints, host platform,
architecture and destination home in `plan_digest`. Inspect the complete input
list, not only the ReasonFirst version. No credentials or application configs are
required. The result remains `ready_for_activation: false`.

```bash
reasonfirst-runtime prepare --wheelhouse /ABSOLUTE/REVIEWED_WHEELS --python /ABSOLUTE/python3 --uv /ABSOLUTE/uv --package-sha256 REVIEWED_PACKAGE_SHA256 --expect-digest REVIEWED_PLAN_DIGEST --yes --json
reasonfirst-runtime status --runtime-id REVIEWED_PLAN_DIGEST --json
```

Both `--yes` and the exact digest are mandatory. The source inputs are checked
again, then only a **new** directory is created beneath:

```text
~/.local/share/reasonfirst/runtimes/<reviewed-plan-digest>/
    intent.json
    inputs/
    build-home/
    venv/
    runtime.json
```

The environment is installed at that final path; no venv is moved or copied.
The old runtime, CLI environment, saved service registrations, `setup.yaml`,
application configuration, credentials, tunnels, workspaces and approvals are not
selected for modification. No service is stopped and no new listener is started.

Preparation copies the reviewed wheels privately, probes the approved CPython,
then invokes uv with `--no-config`, `--offline`, `--no-cache`, no Python downloads,
no index, wheel-only inputs and `--require-hashes`. Resolution uses the complete
exact-pinned input set, installation uses copy mode, and `uv pip check` runs before
import validation. There is no alternate interpreter, network retry, source build
or fallback to another dependency version. Child configuration/environment is
isolated from application settings; code execution is still **not a sandbox**.

The final isolated-Python probe checks exact installed distributions, package
version, package module origins, console entry points, MCP SDK version and the
HTTP run signature without constructing the controller. This is import/signature
acceptance, not HTTP handshakes, worker execution, existing-client reconnection,
authentication or service health. Synthetic CI wire tests are separate evidence.

## Identity, status and failures

`runtime.json` records the full input identity, observed interpreter/dependencies,
import evidence and installed-file fingerprint. The runtime ID identifies the
reviewed inputs; `manifest_digest` additionally binds the observed installed tree.
The record is create-only and private. Status is static, runs no commands, checks
installed files/allowed interpreter links and the retained base Python fingerprint,
and never calls live services. Results distinguish `not_found`,
`preparation_incomplete`, `prepared_matches_record`, and `drifted`. A successful
status inspection with `drifted` is not successful preparation. `prepared: true`
never sets `ready_for_activation: true` or authorizes a restart.

No existing destination is reused, overwritten, repaired or cleaned automatically,
even if it appears identical. A failed/interrupted preparation preserves its new
directory and records an inspection-before-retry result where possible. Forced
termination can leave partial files: absence of `runtime.json` means incomplete;
a malformed record fails inspection. Do not delete evidence to make a retry pass.
No rollback is needed to restore an old service because none was stopped, but
this is **not** a crash-safe service transaction or automatic recovery feature.

Directory handles/no-follow checks, exclusive creation and owner/mode checks
protect cooperating operations; they do not isolate a malicious same-user process
or attest POSIX ACLs/network filesystems. Command time/returned-output bounds are
not a hard disk quota or complete descendant-process containment. Keep reports
private: they contain local paths, fingerprints and dependency identities.

## Later stages

Before activation, a separate plan must bind the actual deployment, config/state
references, transport, endpoint, tool privileges, maintenance admission, state
compatibility and recovery identity. In particular the packaged HTTP wrapper does
not implement legacy `/control`; deployments requiring it remain blocked. Runtime
preparation cannot remove that compatibility requirement or manufacture onboarding
history. Activation, durable recovery, cleanup and release publication are separate.

The native installation harness exercises actual offline uv preparation from a
built wheel in disposable macOS/Linux homes, then uses the prepared interpreter
for the existing HTTP fixture tests. Windows tests explicit unsupported storage
behavior without skipping the existing packaged/source or HTTP tests. CI coverage
must be reported by exact commit and separately from a maintainer-machine migration.

[HTTP transport](BRIDGE_HTTP.md) · [Deployment registration](DEPLOYMENTS.md) · [中文](RUNTIME_PREPARATION_CN.md)

[Read-only pairing with a saved deployment](DEPLOYMENT_PAIRING.md)
