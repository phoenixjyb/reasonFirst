# Packaged Bridge HTTP transport

**Included in v0.5.2; not available in the v0.5.1 wheel.**
This is the HTTP/shared-core portion of B2, not side-by-side runtime preparation,
legacy-service activation, or a complete updater. The normal packaged
`reasonfirst-bridge-mcp` remains stdio. No existing launcher is rewritten.

## Supported boundary

`reasonfirst-bridge-http` uses `gitlab_agent.bridge_mcp.build_server` and the
packaged controller, rather than a copied legacy server/PYTHONPATH sidecar. It
accepts an explicit fixed endpoint and either `read-only` or `full-chat`. These
are Bridge tool-surface policies, not the separate read-only GitLab MCP app.
The full-chat tool set and annotations match the normal packaged Bridge; the
read-only set matches its strict compatibility mode. Neither exposes experimental
remote-push authorization or `/control`.

**This is not a drop-in replacement for a legacy deployment that uses `/control`.**
The historical HTTP server can expose that route even without an active relay.
It must not be removed implicitly during migration. An explicit
`--control-policy legacy`, conflicting mode environment, or enabled experimental
remote-push extension is rejected before controller construction. Keep that
installation unchanged until a separately reviewed compatibility path exists.
A registration recorded by [deployment adopt](DEPLOYMENTS.md) does not establish
that its actual endpoint or control policy is compatible with this wrapper.

Host must be the literal `127.0.0.1` or `::1`; wildcard binds and hostnames are not
supported. Port must be in 1..65535, never dynamic port 0. Paths use slash-separated
ASCII letters/digits/underscore/hyphen; no trailing slash, query, fragment, percent
encoding, dot segments, or `control`/`healthz` first segment. Unsupported inputs
fail rather than being normalized to another endpoint.

This wrapper adds **no HTTP authentication**. Loopback binding does not protect
against other local users/processes, and a full-chat listener is privileged.
Remote access requires a separately secured authenticated transport. No tunnel,
app, grant, token, authentication policy or service-manager setup is performed.
Do not start two servers against one live Bridge state directory.

## Inspect without starting

All endpoint and policy fields are required. This example only describes its
arguments; it does not discover or verify an existing deployment:

```bash
reasonfirst-bridge-http --inspect --host 127.0.0.1 --port 8765 --path /mcp --mode read-only --control-policy disabled
```

`--inspect` emits one JSON object without importing the controller or MCP SDK,
reading application configuration/state, creating directories, probing a listener,
or executing commands. It reports `configuration_inspected: false`,
`package_integrity_verified: false`, `server_started: false`, and
`ready_for_activation: false`. It is not `deployment plan`, a runtime manifest,
an approval digest, or service/credential readiness evidence. `--help` likewise
requires no configuration or controller.

## Foreground execution is a separate action

An operator-controlled development launch must use `--serve` instead of
`--inspect`, with the exact same explicitly reviewed endpoint/policy arguments.
There is no default action or implicit start. No restart/install/repair/doctor
option is accepted. Use a disposable configuration/state directory for tests;
do not repoint a working launchd job at this development command.

Serving loads the existing Bridge configuration and state and can create/chmod
its state directory through the existing controller. Read-only **tool exposure**
does not mean startup is a filesystem-free operation. Controller lifecycle/state
semantics have not been replaced by an upgrade transaction or admission gate.
Existing config references and working directory are not copied, synthesized or
changed by the wrapper. The caller still owns their correct selection.

Nonempty `PYTHONPATH` or `PYTHONHOME` is rejected, not silently removed. Run from
a reviewed package environment with isolated Python imports where appropriate;
argument validation is not an attestation against code already imported by Python.
Explicit `RF_MCP_READ_ONLY` must agree with `--mode`. Present policy variables
must contain a recognized boolean; empty/unknown values are errors. The wrapper
removes inherited tunnel/admin runtime credentials before constructing the
controller. Inspection does not alter the environment.

A server failure returns nonzero and closes the controller when available;
cleanup failure is also nonzero. The wrapper never kills an unrelated listener,
retries a port bind, calls a service manager, restores state, or claims rollback.
`/healthz` retains the existing liveness schema; it does not prove external-client
acceptance, listener ownership, worker execution, or an upgrade's success.

## Validation and next step

The separate internal [managed HTTP owner](MANAGED_HTTP_SERVICE.md) adds a
controller admission gate, actual listener/task ownership, and a modern JSON
request profile with subscriptions disabled. Its optional explicit configuration
binds selected parent policy and an internally observed process/runtime-file
baseline; it does not verify all effective configuration or a prepared runtime.
It is a Python embedding API; this foreground command keeps its existing launch
and protocol behavior.

Tests separate static validation/mocked registration from real SDK/HTTP behavior.
Native regressions use disposable homes and test ports, compare complete HTTP tool
schemas to the shared core in both modern and legacy client modes, check both
Bridge modes and missing `/control`, and verify occupied-port failure without
stopping the original listener. No coding worker or external provider is used.
The install harness additionally runs those tests with the clean wheel's own
interpreter outside the checkout and checks packaged module origins. Source and
packaged install routes remain separate, supported paths.

These are synthetic transport tests, not an existing ChatGPT connection test,
authentication audit, native service-manager migration, reboot or rollback test.
Current CI exercises Python 3.12; the package's broader Python/SDK dependency range
is not a complete compatibility matrix. IPv6 is validated as an accepted argument;
the native wire-level fixtures currently exercise IPv4 loopback only.

[Side-by-side runtime preparation](RUNTIME_PREPARATION.md) pins the reviewed
wheel, interpreter and resolved dependencies at a separate final runtime path.
It and the managed HTTP owner remain separate components: endpoint, application
configuration, client/control compatibility, work ownership, and durable recovery
must be established together before a later service transition.

[中文](BRIDGE_HTTP_CN.md) · [Install/update](INSTALL.md)
