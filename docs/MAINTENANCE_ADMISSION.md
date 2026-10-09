# Controller maintenance admission

**Development source; internal opt-in.** This gate coordinates enrolled work in
one `BridgeController` instance. A successful maintenance reservation holds
that controller's admission closed until its original lease is released or the
controller closes. It does not establish global idle, adopt an existing service,
or authorize activation, switching, restart, rollback, or publication.

There is no public CLI command, MCP tool, environment variable, setup
transition, or default enrollment.

## Enrollment and scope

An internal caller opts in by constructing a controller with a fresh
`ControllerAdmission`. The admission object can be claimed by one controller
once. Reuse by another controller and use from a foreign process are rejected.
An ordinary controller without this option keeps its existing behavior.

Maintenance admission and the disposable `managed_startup` context are mutually
exclusive. The [disposable startup probe](DISPOSABLE_STARTUP.md) permanently
denies tool execution with `startup_observation_only`; a maintenance lease
cannot open that context's tool admission.

Enrollment covers operations that pass through this controller and the
instrumented app-server lifetime hooks attached to its clients. Independent
CLI commands, other controllers or processes, and Desktop activity initiated
by other clients are outside that set. Shared access to the same target does
not enroll those producers automatically.

If a saved Bridge state file already exists at enrollment, maintenance readiness
becomes unknown before the file is loaded. Persisted session metadata does not
prove that live work or an approval has finished, even when the file's pending
approval list is empty or cleared during loading. This change introduces no
durable state schema, migration, service adoption, or recovery record.

## Internal API

The primitive lives in
`src/gitlab_agent/bridge_preview/admission.py`.
Its controller-facing interface is implemented in
`src/gitlab_agent/bridge_preview/controller.py`:

| Entry point | Result and boundary |
| --- | --- |
| `BridgeController(admission=ControllerAdmission())` | Explicitly enroll one fresh admission object before loading controller state. Supplying `managed_startup` as well fails before state access. |
| `controller.maintenance_snapshot()` | Return the diagnostic dictionary without loading saved state or contacting a worker. |
| `controller.try_enter_maintenance()` | Return the original `MaintenanceLease` on atomic success; otherwise raise `BridgeError` with a fixed admission code. |
| `controller.leave_maintenance(lease)` | Consume that exact current lease; return `None`. |
| `controller.close()` | Permanently deny new admission, invalidate the lease, and close the controller's app clients. Outstanding work evidence is retained. |

Controllers without enrollment reject the three maintenance methods with
`maintenance_admission_not_enabled`. An active lease refuses new work with
`maintenance_active`. Maintenance entry uses `maintenance_busy` for active
work, another held lease, or sticky unknown; inspect the safe snapshot to
distinguish those conditions. Closed admission reports `admission_closed`.
Invalid release reports `invalid_maintenance_lease` without changing state.

This example demonstrates ownership and release only; the gate does not itself
perform a maintenance action:

```python
from gitlab_agent.bridge_preview.admission import ControllerAdmission
from gitlab_agent.bridge_preview.controller import BridgeController

controller = BridgeController(admission=ControllerAdmission())
try:
    lease = controller.try_enter_maintenance()
    try:
        # Keep the lease through a separately authorized internal action.
        pass
    finally:
        controller.leave_maintenance(lease)
finally:
    controller.close()
```

A failed entry does not run the inner block. Do not catch its failure and
continue the maintenance action without a lease. Existing controller work
methods remain subject to admission while the lease is held.

## Maintenance is an atomic reservation

A diagnostic idle observation and a later decision to pause work leave a race
for another request to start. Maintenance entry instead checks the enrolled
state and closes new admission under the same lock used to reserve work.

| Situation at maintenance entry | Result |
| --- | --- |
| Admission is open, all enrolled activity is known and finished, and no lease is held | Reserve maintenance and return a lease. |
| An operation, callback, approval, or turn is still in progress | Fail immediately with `maintenance_busy`; do not wait, queue, cancel, or drain it. |
| Activity or lifecycle coverage is uncertain | Fail immediately with `maintenance_busy`; the snapshot preserves unknown readiness and its fixed reasons. |
| Maintenance is already held, enrollment is invalid, or the controller is closed | Refuse entry; do not replace another lease. |

Work reservations and maintenance entry share one decision point. If work wins
the race, maintenance cannot enter while that reservation remains active. If
maintenance wins, a new operation cannot pass admission. A rejected operation
does not begin the handler's side effects.

The caller must keep the lease for the entire maintenance interval. A snapshot
or a previously successful readiness check is not a lease and cannot reserve
the interval.

## Unknown readiness and closed admission are different

An uncertainty marker is sticky for the enrolled controller. There is no
clear, reset, or successful-snapshot path that removes it. This preserves the
distinction between observed completion and a lost lifecycle signal.

Unknown readiness blocks maintenance. When admission is otherwise open, normal
operations, approval replies, and cancellation remain available. An unresolved
turn retains conflicts for its recorded app/thread identity and workspace
reservation; unrelated work may continue. Unknown readiness must not disable
the actions needed to resolve or stop ongoing work.

The snapshot's `admission_open` field states whether normal admission is open.
Its `state` can be `unknown` while that field is true. If an uncertainty arrives
while a maintenance lease is held, the lease still prevents new admission.
Releasing the valid lease can reopen normal admission, but it cannot make
maintenance readiness known again.

Closing the controller permanently closes admission and invalidates its lease.
Shutdown does not produce a new idle claim or a transferable maintenance grant.

## Lease ownership

A maintenance lease belongs to the original admission instance, process, and
reservation generation. The caller must return that same held lease to that
instance. A foreign, stale, malformed, or already consumed lease fails without
changing the state or releasing another caller's maintenance reservation.

The current valid lease can be consumed after readiness becomes unknown. This
releases its own reservation only; the uncertainty remains. Closing the
controller invalidates outstanding leases, and a closed instance cannot be
reopened with an old lease.

Every primitive operation checks process ownership before acquiring the
in-process lock. Copying state across a process boundary does not transfer
enrollment or lease ownership. No lease is serialized into the saved Bridge
state or used as a service-manager credential.

## Track the whole activity lifetime

### Synchronous controller operations

An enrolled outer handler holds its operation reservation from before its first
side effect through its final return or exception cleanup. Nested work within
that handler does not create an unprotected interval. A handler's return can
release its synchronous reservation without declaring an asynchronous turn
complete.

### Asynchronous turns

The outer operation covers preparation and thread creation, including the
interval before a thread identifier is available. A separate turn reservation
is made immediately before `turn/start`, before that external request is sent.
The returned turn identifier binds to the reservation. Returning from the start
RPC does not retire the running turn.

Lifecycle notifications retain starting, running, and stopping work until an
observed terminal event. Requesting a stop does not establish that the turn
stopped. Ambiguous startup failure, lost transport, mismatched lifecycle
identity, or missing completion coverage must preserve uncertainty rather than
manufacture an idle state.

Remote validation also remains uncertain when its report contains `timed_out`
or a validation transport error is caught and reported. The remote container
may outlive the container-engine command. Finishing the parent turn and sending
the callback reply do not remove that uncertainty.

Exact completed identities are retained in a bounded set. Repeated completed
or started notifications for a retired identity are idempotent; they do not
resurrect finished work or erase a different turn. A new turn must reserve room
for its eventual completed identity before `turn/start`. Reaching capacity
refuses further turns with `admission_capacity`, leaving a normally completed,
otherwise known-idle controller eligible for maintenance. There is no eviction
or reset that discards the evidence needed to distinguish duplicates.

The internal bounds are 256 simultaneous operation reservations across all
three operation kinds, 128 retained active turns, 256 completed identities plus
reserved completion slots, and 4,096 characters per activity identity. Nested
handlers may hold more than one operation reservation; counts are reservation
counts, not distinct user requests. These limits bound bookkeeping; they do not
place a timeout or automatic cancellation on existing work.

### Approvals and callbacks

Approval and callback work is reserved before dispatch. An approval remains
active while it is pending and through the actual final app-server reply send.
Removing it from an in-memory pending list or deciding the response is not
enough to establish completion. A failed or uncertain reply send preserves
unknown readiness.

The existing approval policy is unchanged: approval remains a decision made
through the normal workflow. Maintenance entry does not approve, decline,
expire, or otherwise resolve a pending request. Unknown readiness leaves
ordinary admission available when otherwise open so approval and cancellation
work can still proceed.

## Safe diagnostic snapshot

The snapshot reports only bounded schema fields, counters, booleans, and fixed
reason labels. It does not return prompts, tool arguments, approval contents,
credentials, workspace paths, app/thread/turn/request identifiers, arbitrary
exception text, or transport payloads.

| Field | Meaning |
| --- | --- |
| `schema_version`, `scope` | Versioned diagnostic shape; scope is `controller-instance`. |
| `state` | The local enrollment/readiness state: unclaimed, open, maintenance, unknown, or closed. |
| `admission_open` | Whether the gate currently permits new ordinary reservations. |
| `counts` | Operations, callbacks, approvals, and starting, running, stopping, or unknown turns still retained. |
| `unknown_reasons` | Fixed reason labels for sticky uncertainty. |
| `idle_observed` | A diagnostic observation of the enrolled activity at that instant. |
| `maintenance_held` | Whether this instance currently holds a maintenance reservation. |
| `activation_authorized` | Always false. |
| `global_idle_verified` | Always false. |

The snapshot omits the lease and provides no release capability. Its counters
are local observations, not a census of target-wide activity. A caller that
needs exclusive maintenance must attempt the atomic reservation and retain the
returned lease; it cannot act on `idle_observed` alone.

## What remains outside this gate

The separate [managed HTTP owner](MANAGED_HTTP_SERVICE.md) now connects a fresh
controller enrollment to an owned listener and the supported HTTP request
lifetimes. Its optional selected-policy binding adds exact configuration-object
checks and current-process/runtime-file revalidation before maintenance entry
and release. It uses this original lease and leaves the primitive's scope intact;
those observations do not establish whole-runtime or effective-configuration
integrity.

The primitive does not discover or enroll external producers, prove that an
old service has no work, stop a service, change a listener, switch runtimes,
reconnect clients, or perform a maintenance task. Existing work owned by an
uninstrumented producer remains outside its knowledge.

Service adoption still requires an explicit design for existing sessions and
approvals, all relevant producers, configuration and state-schema
compatibility, legacy `/control`, recovery and rollback, client reconnection,
and human authorization. Closing this one controller's admission does not
settle those gates.

The [startup-claims codec](STARTUP_CONFIRMATION.md), disposable startup
observations, runtime preparation, and static launch review keep their
separate evidence and blockers. Neither a maintenance lease nor an idle
snapshot changes their activation or compatibility conclusions.

See [architecture](ARCHITECTURE.md),
[disposable startup](DISPOSABLE_STARTUP.md), and
[中文](MAINTENANCE_ADMISSION_CN.md).
