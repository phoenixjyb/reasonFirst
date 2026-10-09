# Managed HTTP service owner

**Development source; internal, explicit integration.**
`ManagedBridgeService` connects the [controller admission gate](MAINTENANCE_ADMISSION.md)
to one HTTP server that it creates and owns. HTTP requests, controller work,
and maintenance reservation use the same admission object. This is the first
service integration of that gate.

The owner lives in `src/gitlab_agent/upgrade/service_managed.py`. It is a Python
embedding API, not a new CLI command, MCP tool, public administration route, or
service-manager installer. Starting it loads the ordinary Bridge configuration
and state. Use deliberately selected configuration and state; the native
integration fixture uses fresh synthetic inputs.

## Ownership and lifecycle

The owner creates a new `ControllerAdmission`, a new `BridgeController` enrolled
with that exact gate, the shared MCP core bound to that controller, an exclusive
TCP socket, and its HTTP server task. It does not accept an existing controller,
PID, saved registration, pairing digest, or disposable startup object as proof
of ownership.

Construction, startup, serving, and cleanup are distinct lifecycle states. The
owner is used once. Startup must finish successfully before it can reserve
maintenance; closing or failed owners cannot be restarted or rebound.

Startup passes the retained socket to the actual server and verifies that the
server is serving that socket at the selected address. Maintenance entry also
requires the retained task and listener to remain live. Neither a successful
TCP connection nor `/healthz` substitutes for this ownership check.

The embedding application owns process signals. The adapter does not install
its own signal handlers, register a daemon, or change a launchd job. Its async
lifecycle belongs to one event loop; its maintenance handle and lease belong
to the creating process.

## Internal API

| Entry point | Behavior |
| --- | --- |
| `ManagedBridgeService(launch)` | Select an explicit `HTTPLaunch`; do not adopt an existing server. |
| `await service.start()` | Construct the enrolled controller and start the owned HTTP listener; return after readiness checks succeed. |
| `service.maintenance_snapshot()` | Return bounded service/ledger diagnostics without using them as an authorization. |
| `service.try_enter_maintenance()` | Atomically reserve the running owner's controller if all tracked activity is known complete. |
| `service.leave_maintenance(lease)` | Release the exact current in-process `MaintenanceLease`. |
| `await service.aclose()` | Close admission first, then clean up owned server/controller resources. |

The service uses the original lease implementation from the controller gate.
There is no serialized lease, bearer token, second lease namespace, or automatic
lease transfer. A copied, foreign, stale, or already consumed lease cannot reopen
admission. Close invalidates an outstanding lease permanently.

This embedding pattern assumes that the caller has deliberately selected its
configuration and unused endpoint before entry:

```python
from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.upgrade.service_managed import ManagedBridgeService


async def exercise(launch: HTTPLaunch) -> dict:
    service = ManagedBridgeService(launch)
    try:
        await service.start()
        lease = service.try_enter_maintenance()
        try:
            return service.maintenance_snapshot()
        finally:
            service.leave_maintenance(lease)
    finally:
        await service.aclose()
```

`try_enter_maintenance()` fails immediately if requests, operations, turns, or
approval replies remain, or if readiness is unknown. It does not wait for work,
interrupt a turn, decide an approval, drain a client, or perform a maintenance
operation. A successful reservation keeps new work closed until exact release.

## HTTP profile and compatibility

The new adapter uses an explicit MCP **2026-07-28** single-request JSON profile.
It configures stateless HTTP, disables subscriptions on the shared MCP core,
and uses Uvicorn's `h11` transport. The reviewed SDK pair is **MCP 2.3.0 and
Uvicorn 0.54.0**. The adapter checks that pair before constructing a controller;
the package's broader dependency range is not evidence that this owner supports
another SDK version.

| Request | Managed owner behavior |
| --- | --- |
| MCP `POST` with the supported protocol and consistent method metadata | Reserve activity before dispatch; hold it through request execution and response handling. |
| MCP `POST` while maintenance is held | Fixed `503` rejection before consuming the body or calling a controller tool. |
| Older, missing, conflicting, or duplicate protocol metadata | Reject before entering an unsupported SDK request path. |
| MCP `GET` or `DELETE` | `405`; this profile has no standing GET stream or stateful session deletion. |
| `subscriptions/listen` | Unsupported; the managed core does not advertise or register subscriptions. |
| `/healthz` | Liveness only; remains separate from work admission and maintenance readiness. |
| `/control` or a maintenance HTTP route | Not exposed. Maintenance stays on the trusted owning Python handle. |

All MCP POSTs share the reservation boundary, including discovery and catalog
requests. Clients can enumerate the catalog while admission is open and again
after a valid maintenance release. During a held lease, they receive the same
maintenance rejection as other MCP requests.

The ordinary [packaged HTTP command](BRIDGE_HTTP.md), stdio Bridge, and
[disposable startup probe](DISPOSABLE_STARTUP.md) keep their existing protocol
selection. The managed owner is a separate profile, not a compatibility claim
about clients currently attached to a legacy service. Client reconnection and
protocol compatibility must be established before any later service transition.

Both `read-only` and `full-chat` modes use the shared core's exact tool schemas
and annotations. Disabling subscriptions changes the corresponding discovery
capabilities for this owner. Omitting the new shared-core subscription option
preserves the SDK default for ordinary callers.

## Why the transport boundary matters

In the reviewed SDK, legacy stateful and legacy stateless HTTP paths may have
session/dispatcher tasks that outlive the ASGI request. Merely setting
`stateless_http=True` does not join all of that work. Likewise, JSON responses
alone do not remove the default subscription handler and its standing stream.

The selected modern JSON profile awaits its request execution. The outer
adapter acquires admission before dispatch and retains it through the final
response handling and request-local cleanup. The controller's existing turn and
approval reservations continue to cover work that legitimately outlives a tool
response.

A returned ASGI send is not proof that the remote peer received a reply.
Observed disconnects, cancellation, failed sends, and incomplete request cleanup
retain uncertainty. There is no peer-acknowledgement or remote delivery claim.

## Cleanup and failure

Shutdown closes admission and invalidates leases before awaiting any transport
cleanup. Unexpected server exit also closes admission. Only owned sockets,
tasks, and controller clients are cleanup targets; an occupied port or another
listener is never taken over.

A cleanup timeout or failure remains a separate outcome. Stopping the listener
does not prove that controller callbacks, a coding worker, or a remote container
have finished. Outstanding counters and unknown activity remain visible; cleanup
does not clear them to manufacture idle. The existing controller behavior for
approval decisions and Desktop-owned workers is retained.

## Diagnostic scope

The snapshot describes this owner and its nested controller ledger using fixed
schema fields, counts, booleans, and classified reasons. It excludes lease
objects, credentials, request bodies, prompts, workspace paths, worker/session
identifiers, and raw exception text.

The immutable HTTP launch describes the transport arguments this owner consumes.
It is not a full configuration digest. Ordinary controller operations still
resolve application policy, environment inputs, and subprocess configuration
through existing loaders.

Configuration binding, state recovery, external-producer quiescence, global
idle, and activation authorization remain unverified or false. An observed
saved state file retains `recovered_state_unverified`; absence of that file is
not an interprocess ownership lock or proof that another producer has no work.

Static deployment records, native loaded-job samples, startup claims, and this
owner's live local reservation remain separate evidence. Existing
[launch-review blockers](LAUNCH_REVIEW.md) are not removed by this integration.
The reviewed `bridge_mcp.py` source pin is refreshed only for its explicit
subscription opt-out; it does not add this owner to an approved service launcher.

## Verification

Focused tests cover owner identity and lifecycle, real request/response gaps,
protocol rejection, task/lease races, transport uncertainty, and cleanup.
The real-wire fixture in `tests/test_managed_http_integration.py` uses isolated
synthetic configuration, both Bridge modes, and ordinary shared-core catalogs
as its comparison source. It does not contact a coding backend or GitLab.

The installation harness executes the same fixture from the clean wheel and
the source environment, checking module origins in each route. These checks
exercise a newly owned HTTP runtime; they do not establish a working user's
service adoption, authentication, reboot recovery, or rollback.

See [controller admission](MAINTENANCE_ADMISSION.md),
[HTTP transport](BRIDGE_HTTP.md), [architecture](ARCHITECTURE.md), and
[中文](MANAGED_HTTP_SERVICE_CN.md).
