# Managed HTTP service owner

**Development source; internal, explicit integration.**
`ManagedBridgeService` connects the [controller admission gate](MAINTENANCE_ADMISSION.md)
to one HTTP server that it creates and owns. HTTP requests, controller work,
and maintenance reservation use the same admission object. An optional immutable
configuration binds selected parent policy to that exact owner and controller.
Configured owners also retain the local inputs supplied to their covered child
launches.

The owner lives in `src/gitlab_agent/upgrade/service_managed.py`. It is a Python
embedding API, not a new CLI command, MCP tool, public administration route, or
service-manager installer. Without the optional configuration, startup uses the
ordinary Bridge configuration and state loaders. With it, the controller consumes
the selected policy objects and loads state from the explicit state directory.
The native integration fixture uses fresh synthetic inputs for both routes.

## Ownership and lifecycle

The owner creates a new `ControllerAdmission`, a new `BridgeController` enrolled
with that exact gate, the shared MCP core bound to that controller, an exclusive
TCP socket, and its HTTP server task. It does not accept an existing controller,
PID, saved registration, pairing digest, or disposable startup object as proof
of ownership.

For a configured owner, the controller and shared core must retain the exact
`ManagedServiceConfiguration` object supplied to the owner and the exact child
context captured by that owner. Matching public digests or equal context values
do not authorize substituting another object. The exact admission gate
determines cleanup ownership: a controller using another gate is rejected before
adoption and is not closed. A controller enrolled in this owner's gate remains
its cleanup responsibility even when a configuration, child-context or state-path mismatch
prevents startup.

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
| `capture_service_configuration(settings, bridge_config, *, bridge_config_path, state_dir, ...)` | Detach already-resolved settings, target policy and explicit service options into an immutable selected-policy object. |
| `ManagedBridgeService(launch, *, configuration=None)` | Select an explicit `HTTPLaunch` and optionally the exact selected-policy object; create a new service. |
| `await service.start()` | Capture configured child inputs and runtime observations before the listener, construct the enrolled controller, and publish running only after readiness and binding checks succeed. |
| `service.maintenance_snapshot()` | Return bounded service/ledger diagnostics and retained observation metadata; perform no runtime or executable file reread. |
| `service.try_enter_maintenance()` | Revalidate a configured binding, then atomically reserve the running owner's controller if all tracked activity is known complete. |
| `service.leave_maintenance(lease)` | Revalidate a configured binding, then release the exact current in-process `MaintenanceLease`. |
| `await service.aclose()` | Close admission first, then clean up owned server/controller resources. |

The service uses the original lease implementation from the controller gate.
There is no serialized lease, bearer token, second lease namespace, or automatic
lease transfer. A copied, foreign, stale, or already consumed lease cannot reopen
admission. Close invalidates an outstanding lease permanently.

This embedding pattern takes settings and Bridge policy that the caller has
already resolved deliberately. All configuration references must be absolute
`Path` objects, including the paths in `AgentSettings`. The example does not
select a file, invoke an ordinary loader, or alter the host environment:

```python
from pathlib import Path
from typing import Any

from gitlab_agent.bridge_http import HTTPLaunch
from gitlab_agent.config import AgentSettings
from gitlab_agent.upgrade.service_configuration import capture_service_configuration
from gitlab_agent.upgrade.service_managed import ManagedBridgeService


async def exercise(
    launch: HTTPLaunch,
    settings: AgentSettings,
    bridge_config: dict[str, Any],
    *,
    bridge_config_path: Path,
    state_dir: Path,
) -> dict:
    configuration = capture_service_configuration(
        settings,
        bridge_config,
        bridge_config_path=bridge_config_path,
        state_dir=state_dir,
        approval_timeout_seconds=300,
        gitlab_auth_mode="auto",
    )
    service = ManagedBridgeService(launch, configuration=configuration)
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

After the configured runtime-file checks, `try_enter_maintenance()` refuses if
requests, operations, turns, or approval replies remain, or readiness is unknown.
It does not wait for work,
interrupt a turn, decide an approval, drain a client, or perform a maintenance
operation. A successful reservation keeps new work closed until exact release.

## Selected parent policy

`capture_service_configuration`, in `upgrade/service_configuration.py`, accepts
already-loaded objects. It does not load `.env` or Bridge files, inspect their
contents, create state directories, or copy values into `os.environ`. The caller
remains responsible for how the original settings were resolved. Explicit file
references identify the selected inputs; they do not prove that current file
contents match those objects.

The captured settings, worker requests, target policy and service options are
immutable. Returned projection and Bridge configuration views are detached.
Later edits to the original collections, compatibility views, environment or
source configuration files do not reload these selected values.

| Selected input | Controller behavior with a configuration binding |
| --- | --- |
| Agent settings and workspace root | Direct helpers and the private ReasonFirst child entry point consume the retained settings, without loading another `.env` file. |
| Codex worker request | Effective policy, session resume and continuation use the retained worker policy. This does not prove backend enforcement. |
| Named and stored execution targets | Named requests resolve from the selected targets. A stored target must match a selected target's canonical record, including its validation policy. |
| `gitlab_auth_mode` | Accepts `auto`, `api` or `git-only`. `auto` uses the retained API credential's presence; the direct mode helper does not reload the ambient override. |
| `approval_timeout_seconds` | Accepts an integer from 30 through 1,800 seconds, default 300; approval handling uses that retained timeout. |
| Experimental remote push | Remains disabled for this owner, including its remote dynamic-tool selection. |

Stored targets use the canonical `ExecutionTarget.to_dict()` shape, including
its flat validation fields. JSON list/tuple representations are equivalent;
scalar types must match exactly, and unknown or missing fields are rejected.
The bound controller keeps the selected backend when handling legacy SSH
records; it does not probe for a missing remote binary and silently migrate the
record to another backend.

A saved session worker policy must match the retained policy's canonical shape.
An absent saved policy uses the exact retained Codex policy. The controller
checks this before connecting or resuming an AppServer and also for continuation
on an already cached session. A stale saved push approval cannot enable the
bound `commit_push` callback: it is rejected before session or manager access,
after the usual check that the request belongs to the current AppServer
generation.

The versioned public projection is
`reasonfirst-selected-service-policy-v1`, scoped to
`selected-parent-service-policy`. Its digest is SHA-256 of compact, sorted-key
UTF-8 JSON. It includes selected non-secret policy, references and the actual
HTTP launch. Credentials, their presence and hashes, Git usernames, author
metadata and arbitrary unused configuration fields are excluded. Two objects
can therefore have the same public digest and different private settings; live
binding requires object identity, not digest equality alone.

The local child-launch binding described below supplies retained process inputs.
Provider state, external configuration files, CA bundle contents, native Git and
SSH configuration, remote runtime state and backend enforcement remain outside
the selected-policy claim. Retaining a CA path or an environment-trust boolean
does not freeze the referenced files.

This object is separate from the disposable startup observation state. A
disposable controller continues to refuse every tool call and cannot be combined
with live admission. Ordinary controllers and owners created without
`configuration` keep their existing loading behavior.

## Selected local child-launch inputs

A configured owner captures a private `ServiceChildContext` before creating its
listener. The context belongs to its creating PID and exact configuration
object, and retains a detached environment, an absolute working directory and
the Python invocation selected for local ReasonFirst children. Returned
environment views are independent copies. Later changes to `os.environ` or the
parent working directory do not replace the captured values, and launching one
owner's work does not temporarily modify process globals for another owner.

Controller operations that use a ReasonFirst CLI child pass already-resolved
settings through the bounded private `upgrade.service_child` entry point. The
child bypasses ordinary settings and `.env` discovery. The environment is
supplied separately to the process; credentials are not placed in command-line
arguments. Python isolated mode excludes the captured working directory and
`PYTHONPATH` from module discovery. Each helper captures its inherited environment
into its own child-local context for descendants; executable caches and process
identity are not transferred across processes. This is an internal execution
channel, not a new user-facing CLI, MCP tool, or service-administration endpoint.

Local Git, history inspection, validation and the locally launched SSH client
receive the retained launch context as well. Executable selection is lazy and
uses that context; covered launches use explicit absolute executable paths.
Selected executable files are observed and checked for drift. Codex configuration
probes and owned AppServer processes use the selected local environment and
working directory. Cached clients must continue to belong to the same context.
The controller checks the selected context on covered use paths; the owner also
revalidates it before publishing running and at both maintenance transitions.
An observed context or executable mismatch closes admission permanently. A later
file restoration or successful child response does not refresh the baseline.

This binds the local inputs supplied by ReasonFirst. It does not establish what
an executable subsequently loads or starts. Codex/provider configuration files,
additional MCP services, native Git hooks, filters, trust stores and included
configuration, SSH configuration and host-key/agent state, remote shell
environments, remote binaries and container images, and already-running Desktop
daemons remain external. A local validation command is still the existing
structured host runner; the context does not turn it into a sandbox. File
observations do not prove loaded-code identity or make process creation atomic.

## Current-process runtime observation

A configured owner captures its runtime observation internally, before creating
the socket. Callers do not supply an expected runtime digest. The observation
binds the current PID, interpreter invocation and prefix, selected interpreter
files, the selected module objects and coherent source roots, their file
metadata and bytes, and the reviewed SDK versions. Source and installed routes
retain their own observed origins.

The owner revalidates that observation before publishing the running state and
before both maintenance entry and lease release. A mismatch closes admission,
invalidates maintenance eligibility and remains a failure for that owner.
Restoring a file or reference does not refresh the baseline or revive a failed
owner. Wrong-process use is rejected before acquiring its inherited owner lock.

These are bounded, repeated observations of selected files and process-local
references. They are not an atomic filesystem snapshot, proof of the code already
loaded in memory, whole-runtime integrity, or the identity of an independently
prepared runtime. There is no background drift watcher or reread of this
current-process file set on every HTTP request. Covered child paths perform their
separate per-use checks. The retained observation shown in diagnostics is
historical; maintenance transitions perform the fresh checks described above.

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

A bound AppServer client becomes an owned cleanup target only after its exact
child context is accepted. A foreign factory result or cache replacement is
rejected and is not closed. Replacing a cache entry does not discard the original
owned client's cleanup handle.

A cleanup timeout or failure remains a separate outcome. Stopping the listener
does not prove that controller callbacks, a coding worker, or a remote container
have finished. Outstanding counters and unknown activity remain visible; cleanup
does not clear them to manufacture idle. The existing controller behavior for
approval decisions and Desktop-owned workers is retained.

Configuration capture errors use finite `ServiceConfigurationError` codes. The
owner reports `configuration_binding_failed` for a failed selected-policy check
and `runtime_binding_failed` for failed runtime capture or revalidation.
`child_binding_failed` covers invalid child context capture, identity mismatch,
selected executable drift and a helper-reported child context failure. Ordinary
child command failures retain their separate classification.
`controller_binding_failed` and `core_binding_failed` identify mismatched startup
objects. A bound controller or gate closed outside the owner also makes the
configuration binding fail permanently. Raw configuration values, runtime paths
and underlying exception text are not reflected in these owner errors.

## Diagnostic scope

The snapshot describes this owner and its nested controller ledger using fixed
schema fields, counts, booleans, and classified reasons. It excludes lease
objects, credentials, request bodies, prompts, workspace paths, worker/session
identifiers, and raw exception text.

| Snapshot field | Meaning |
| --- | --- |
| `resolved_policy_bound` | True only while this configured owner is running with its exact controller/core configuration and viable gate. |
| `current_process_bound` | True only while that live binding also retains its internally captured current-process observation. It is not a fresh disk-integrity result from the snapshot call. |
| `child_inputs_bound` | True only while this running owner, controller and core retain the exact captured child context and a viable gate. It does not verify child runtime or provider configuration. |
| `configuration_digest` | Historical digest of the selected projection and launch, or `null` when none was captured. |
| `runtime_observation` | Historical bounded summary of selected runtime observations, including its digest and module count, or `null` when none was captured. |
| `child_observation` | Bounded `owned-local-child-inputs` summary with retention booleans and selected executable count, or `null` when none was captured. It contains no raw environment, environment digest, paths or credential indicators. |

The child summary leaves `child_runtime_verified`,
`provider_configuration_verified`, `remote_runtime_verified`,
`desktop_daemon_verified` and `activation_authorized` false. Its counts report
selected observations; they do not enumerate a transitive process tree.

Closing the owner makes all three live binding flags false. Successfully captured
digests and observation metadata remain available afterward as historical
evidence. Unconfigured owners leave these binding flags false, the configuration
digest `null` and both observation fields `null`.

The following broader flags remain false even when all three live binding flags are
true: `effective_configuration_verified`, `recovered_state_verified`,
`external_producers_quiesced`, `global_idle_verified`,
`runtime_identity_verified`, `activation_authorized`, `ready_for_activation`
and `existing_service_adopted`.

An observed saved state file retains `recovered_state_unverified`; absence of
that file is not an interprocess ownership lock or proof that another producer
has no work. Selected-policy binding does not establish state recovery, external
producer quiescence, global idle, or activation authorization.

Static deployment records, native loaded-job samples, startup claims, and this
owner's live local reservation remain separate evidence. Existing
[launch-review blockers](LAUNCH_REVIEW.md) are not removed by this integration.
The reviewed `bridge_mcp.py` source pin covers its explicit subscription option
and configuration/child-context reference retention; it does not add this owner to an approved
service launcher.

## Verification

Focused tests cover owner identity and lifecycle, immutable selected-policy
consumption, foreign and stale bindings, runtime drift, real request/response
gaps, protocol rejection, task/lease races, transport uncertainty, and cleanup.
Child integration tests exercise the real controller helper routes, independent
owners, retained settings in preview and direct finish, foreign client cleanup,
sticky child failures, and ordinary controller compatibility. Direct backend
tests cover local Git, validation, approved Python, history and local SSH inputs.
The real-wire fixture in `tests/test_managed_http_integration.py` uses isolated
synthetic configuration, both Bridge modes, and ordinary shared-core catalogs
as its comparison source. It does not contact a coding backend or GitLab.

The installation harness executes the same 14-case fixture from the clean wheel
and the source environment, checking module origins in each route. The fixture
includes configured-owner cases and real `reasonfirst_files`/`reasonfirst_read`
HTTP calls in both modes. These calls continue to use the selected workspace
after `.env`, environment and parent working-directory changes, with a shadow
package in the captured child directory to exercise isolated module discovery.
Four additional report-validator cases enforce the report contract. Its strict
report requires `selected_policy_binding_exercised`,
`runtime_observation_exercised` and `child_launch_binding_exercised` to be exactly
true, together with the existing real-MCP and maintenance-admission evidence;
skipped or incomplete acceptance is not success. `working_service_touched` and
`activation_tested` remain false. These checks exercise a newly owned HTTP
runtime; they do not establish a working user's service adoption, authentication,
reboot recovery, or rollback.

See [controller admission](MAINTENANCE_ADMISSION.md),
[HTTP transport](BRIDGE_HTTP.md), [architecture](ARCHITECTURE.md), and
[中文](MANAGED_HTTP_SERVICE_CN.md).
