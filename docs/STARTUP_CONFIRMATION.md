# Managed-startup confirmation: protocol contract first

Development proposal on main `0fcc977a48d91195574c94370f1a966494a04bc4`.
**Not in published v0.5.1. Not an automatic updater.**

## Why this exists

PR #102 deliberately observes only the fields the native macOS API supplies.
Its missing cwd/environment fields stay unknown, and its
`managed_startup_confirmation_not_verified` blocker remains unconditional.
The next evidence source must be the managed runtime's own resolved startup
state, with a separately established relationship to the expected process.
Reading another saved file or accepting `/healthz`'s version is not a substitute.

This slice implements the internal **claim-message codec and one-shot verifier**
in `gitlab_agent.upgrade.startup_protocol`. It does not implement peer binding,
a state collector, an HTTP route, a startup launcher, or service activation.
No existing command imports or enables this module automatically. No public CLI,
MCP tool, dependency, persistence schema or service policy is changed.

## Implemented contract

`PendingStartupChallenge` takes an independently selected `StartupClaims` value
and an expected positive process ID. Its `start()` returns one challenge and can
be used only once. `make_reply()` combines the request's two opaque identifiers,
its own `os.getpid()`, and the caller's separately collected `StartupClaims`.
The request contains **no expected identity values** for the responder to echo.
`finish()` validates a single reply and consumes the pending challenge on success
or failure. `cancel()` consumes it after transport failure or cancellation.

The protocol name is `reasonfirst-startup-claims-v1`. Every message is one UTF-8
JSON object of at most 16 KiB; duplicate names (including nested duplicates),
unknown/missing fields, NaN/infinities, invalid UTF-8, multiple JSON documents,
unsupported protocol/kind values and inappropriate types are rejected. The
protocol module accepts complete bounded bytes; a later transport MUST enforce
its own byte/deadline limits while reading, not buffer arbitrarily and call the
codec afterward. Message errors are fixed codes with chained parser errors
suppressed; raw peer input is never used as an error message.

A challenge has exactly: `protocol`, `kind: challenge`, `launch_id`, `nonce`.
The two identifiers are independently generated 256-bit random hex strings.
A reply has exactly: `protocol`, `kind: reply`, `launch_id`, `nonce`, `pid`, `claims`.
There is no timestamp supplied by the peer, no arbitrary metadata map, and no
`ready`, `authorized`, `signature`, `token` or claimed peer-verification field.

The identity projection contains exactly these nine fields:

| Field | Constraint and meaning |
| --- | --- |
| `runtime_id` | 64 lowercase hex characters; selected prepared-runtime identifier. |
| `manifest_digest` | 64 lowercase hex characters; selected prepared-runtime manifest identifier. |
| `interpreter_digest` | 64 lowercase hex characters; reserved for the independently collected interpreter fingerprint digest. |
| `configuration_digest` | 64 lowercase hex characters; reserved for an approved **non-secret resolved projection**, not `.env` bytes. |
| `host` | Existing packaged HTTP policy: literal IPv4/IPv6 loopback only. |
| `port` | Existing fixed-port policy, integer (not boolean), 1..65535. |
| `path` | Exact path accepted by the existing HTTP validator; never normalized. |
| `mode` | `read-only` or `full-chat`. |
| `control_policy` | `disabled` only; a legacy-control requirement is not silently discarded. |

No configuration/interpreter collector is supplied by this slice. Passing a hash
only claims its identity: it does not verify a file, its ownership, its origins,
loaded memory, or whether the selected non-secret configuration was collected
correctly. The collector's exact projection/version requires subsequent review.
The wire contains identifiers and a local endpoint, so keep it private even
though credentials and raw configuration maps have no supported schema fields.

The fixed lifetime is eight seconds from construction, measured with the
verifier's `time.monotonic_ns()`. Starting late does not renew it. Expiry is checked
before and after reply parsing; equality with the deadline is expired. The
pending object is process-local, not serializable evidence and not a durable
receipt. After supervisor restart, cancellation, suspension/resumption, or a
changed launch expectation, a later integration must discard it and create a
new attempt. This is not a suspend-aware or crash-recovery clock protocol.

A lock allows at most one completed verification attempt; malformed, mismatched,
expired and repeated responses cannot be retried with the same pending object.
Nonce/launch-ID and canonical claim comparisons use `hmac.compare_digest`, which
is a comparison operation here, **not a MAC, signature, or authenticated channel**.

## Precisely what success means

A successful `finish()` returns `fresh_reply_claims_match: true` and
`pid_claim_matches: true`. It does not return raw claims, paths, nonce or launch ID.
All of these remain false:

```text
peer_identity_verified
running_code_verified
effective_configuration_verified
managed_startup_confirmation_verified
compatibility_verified
activation_authorized
ready_for_activation
service_changed
```

The report always includes `managed_startup_confirmation_not_verified`,
`startup_peer_identity_not_verified` and `startup_claims_collection_not_verified`.
There is no parameter by which a caller can change those flags to true. The #102
observer and all its blockers remain unchanged. A copied or forged response can
match public expected values and still provide no independent peer proof; the
regression suite explicitly demonstrates that distinction.

## Next integration, before claiming managed-startup confirmation

A future supervisor must first revalidate the prepared runtime, reviewed launch
and configuration compatibility. It must own a fresh private communication
channel to the particular process it launches, retain that process handle and
read its PID from the OS, and prevent the channel from leaking to unrelated
workers. A PID from a file, a reply, or an arbitrary CLI option is not that handle.

The runtime-side collector must produce claims from the actual resolved objects
used to configure the server, not the supervisor's expected values. It must
confirm when the listener really exists; codec success says nothing about bind
success, listener ownership, tool catalog, health or client reconnection.

For the initial macOS/Linux integration, evaluate dedicated inherited anonymous
pipes rather than a public HTTP route or a persistent socket path. Such a channel
binds an exchange to holders of the inherited handles, not cryptographically to
one binary. Socket-pair peer credentials may describe the process that created
the pair; do not assume they identify the later child. Add native process/channel
and handle-inheritance tests before describing the association as verified.
Windows needs its own handle-inheritance and process-binding tests; this codec's
portability does not imply a Windows service updater.

A fresh channel, child process, collected identity, readiness observation and
independent runtime review must be combined as separately named evidence. Never
silently fill old uninstrumented service state from a saved registration. Such
services require an explicit maintenance adoption path with retained recovery.

Even a fully integrated startup confirmation must NOT clear maintenance/work
admission, configuration-schema, legacy-control, rollback or human-authorization
gates. Those are separate from confirming what a new process reports.

## Validation for this slice

Tests cover strict schemas, all selected claims, fresh/expired/replayed replies,
nonce reuse, wrong PID, exact expiry boundaries, clock failure, concurrent calls,
fixed nonrevealing errors, optional-policy rejection, and immutable expectations.
A real disposable Python child reads one bounded challenge from its private stdin
and writes one reply to stdout; no Bridge, listener, provider, registry or service
manager is started. The child exits before verification, intentionally proving
that successful message comparison is not liveness evidence. No test is skipped
on platform grounds by the new test module.

The process fixture launches the running interpreter's explicit base executable
on Windows, where a virtual-environment executable may be a redirector with a
different PID. This is test-only selection before launch, not production fallback.
The child must match the parent's Python version and import the reviewed source.
Expected PID still comes from `Popen.pid`, never from a probe or the reply. A
separate original-executable probe reports only whether those PIDs coincide; a
real relay-process negative test requires rejection when they do not. No missing
base interpreter, PID mismatch or failed challenge is retried with weaker checks.
The fixture does not validate an installed Windows venv or a service launcher.
See the corresponding [CPython Windows spawning implementation](https://github.com/python/cpython/blob/v3.12.10/Lib/multiprocessing/popen_spawn_win32.py).

This is not a native installed-wheel, macOS API, managed daemon, full effective
configuration, IPC peer credential, lifecycle, rollback or reboot test. The
existing three-platform CI remains required for any proposed PR; local results
must be reported separately. No existing test or activation invariant is weakened.

## References

Repository baseline: [partial native observation](LOADED_SERVICE.md),
[packaged HTTP transport](BRIDGE_HTTP.md), [runtime preparation](RUNTIME_PREPARATION.md).

Python primary documentation: [secrets](https://docs.python.org/3/library/secrets.html),
[monotonic clocks](https://docs.python.org/3/library/time.html#time.monotonic_ns),
[JSON duplicate-name behavior](https://docs.python.org/3/library/json.html#repeated-names-within-an-object),
[process handles and pipe transport](https://docs.python.org/3/library/subprocess.html).

[中文](STARTUP_CONFIRMATION_CN.md)
