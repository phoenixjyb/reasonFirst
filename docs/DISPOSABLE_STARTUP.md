# Disposable managed startup

**Development source; not in the published v0.5.1 wheel.** This internal
macOS/Linux adapter observes a newly launched, disposable prepared runtime. It
does not adopt, replace, restart, or authorize an existing service. There is no
new public CLI, MCP tool, setup transition, release, or activation command.

## Scope and entry point

`gitlab_agent.upgrade.startup_managed.probe_disposable` accepts a selected
prepared runtime and an explicit `HTTPLaunch`. It revalidates the runtime,
creates a fresh synthetic home/configuration/state area, and launches the
candidate with that runtime's interpreter. It checks the private startup reply
and uses an owned catalog helper to check actual HTTP behavior, then cleans up
the resources owned by that attempt.

The synthetic configuration uses an `.invalid` GitLab hostname, empty
credentials, and a local target. No maintainer configuration, credential,
workspace, approval, tunnel, or working listener is selected. The candidate
registers the shared core's normal read-only or full-chat catalog, but **every
tool call is blocked** with `startup_observation_only`. MCP initialization,
catalog enumeration, and the existing health route remain available. The
production probe enumerates tools; it does not invoke coding tools.

This is an observation harness for trusted, reviewed installed artifacts, not
a sandbox for hostile executable code or a general service launcher. There is
no transition that opens tool admission in this disposable context.

## Configuration checkpoint: selected policy, with provenance

The configuration identifier is SHA-256 of compact, sorted-key UTF-8 JSON
with `allow_nan=False`, using the exact projection version
`reasonfirst-selected-startup-policy-v1`. The projection is bounded to 64 KiB.
Only explicitly named fields enter this version; adding a dataclass field does
not automatically extend the hashed schema.

| Projection group | Exact selected fields and source |
| --- | --- |
| `protocol`, `scope` | Projection version above; scope `disposable-startup-observation`. |
| `transport` | `transport`, `host`, `port`, `path`, `mode`, `control_policy`, `remote_push_exposed`, `tool_admission`. The actual `HTTPLaunch` configures the shared MCP application and owned listener; admission is closed. |
| `references` | `agent_config_file`, `bridge_config_file`, `bridge_state_dir`, `bridge_state_file`, `workspace_root`, `api_ca_bundle`. Canonical references in the captured settings/controller context; file contents are not hashed here. |
| `agent_policy` | `gitlab_base_url`, `api_verify_ssl`, `api_trust_env`, `git_trust_env`, `allowed_projects`, `require_write_allowlist`, `branch_prefix`, `default_base_ref`, `allowed_executables`, `command_timeout_seconds`, `max_output_bytes`, `max_file_bytes`, `default_backend`. Values from the loaded `AgentSettings`; sets have stable ordering. |
| `bridge_policy` | Loaded version 4; resolved `default_target`, `default_codex_backend`, and named `targets`. Each target has `configured_name`, `type`, `name`, `host`, `repo`, `codex_backend`, `remote_codex`, `ssh_connect_timeout`, `network_access`, `validation_engine`, `validation_image`, `validation_allowed_executables`, `validation_network_access`. Resolution reuses the existing target resolver. |
| `worker_requests` | Separate resolved Codex and Copilot requests: `backend`, `model`, `reasoning_effort`, `execution_mode`, `sandbox_mode`, `approval_policy`, `network_access`, `disable_builtin_mcps`, `allow_tools`, `deny_tools`. Values come from `resolve_worker_policy` over the captured settings. These are requested policy, not provider enforcement evidence. |

The parent builds expected objects directly from explicit synthetic inputs and
writes the corresponding ordinary configuration files. It never loads a
maintainer `.env` or modifies its own process environment. The child runs the
existing loaders under a clean, explicitly constructed environment, validates
transport policy again after env-file loading, and captures those loaded
objects. It receives ordinary launch inputs, not expected claim digests.

The captured settings detach mutable sets into `frozenset` values. Resolved
targets and worker policies are immutable values; resolver-compatible Bridge
views are detached copies. The managed controller receives this snapshot,
uses its selected state directory/targets/settings/worker request, and exposes
that same context to the collector. Default constructors and unmanaged
configuration reload behavior retain their existing semantics.

**Excluded:** credentials and their presence/hashes; Git usernames and author
identity; arbitrary environment or YAML entries, including legacy `control`;
raw `.env` bytes; CA-file contents; per-repository `.actualcoder.yaml` contracts;
resumed-session policy; provider/global Codex settings; actual sandbox/network
enforcement; local CLI subprocess settings; and later work-admission decisions.
Agent limits are the loaded settings, not a claim that every downstream
consumer's clamped limits were observed. Closed tool admission prevents this
disposable context from crossing those unverified work boundaries.

## Listener checkpoint: binding and serving are separate

The reviewed adapter supports the actual inspected pair **MCP 2.3.0 and
Uvicorn 0.54.0**. It rejects another pair; it does not install, upgrade, or
downgrade dependencies. The project's existing dependency range and CI policy
are unchanged. A future supported pair requires source/lifecycle review and
native regression evidence.

MCP's `MCPServer.run_streamable_http_async` constructs its ASGI application and
calls Uvicorn. Uvicorn starts ASGI lifespan **before** binding/serving sockets.
An ASGI startup callback therefore cannot prove a listening endpoint. The
managed path uses `streamable_http_app` from the same shared server and
supplies an owned socket to Uvicorn's supported `serve(sockets=...)` path.

1. The supervisor binds an exclusive, non-listening TCP socket to the explicit
   fixed loopback endpoint and retains its descriptor. It does not release the
   port and does not call `listen()`.
2. Only its newly launched child receives that socket and the two private pipe
   endpoints through explicit `pass_fds`. Unrelated descriptors are closed.
3. The child marks inherited endpoints noninheritable, independently validates
   its installed runtime and loaded policy, and builds the shared MCP server.
4. After Uvicorn's real `startup()` returns, the child checks the actual
   `asyncio.Server` objects, serving state, and socket identity before replying.
5. The parent checks `SO_ACCEPTCONN` on its retained socket and checks its owned
   child is alive. A separate owned helper checks the exact MCP endpoint/catalog;
   the parent monitors both processes and checks the listener again afterward.
   An occupied/decoy endpoint fails without stopping its owner.

The HTTP probe disables ambient proxy use, forbids redirects away from the
exact endpoint, and bounds response bytes and elapsed time. Catalog checking
has its own explicitly bounded post-reply budget; it does not extend the
startup codec's eight-second lifetime.

The catalog helper runs the supervisor's installed interpreter in isolated mode,
loads the same ordinary synthetic configuration, and checks its import origins.
It receives no startup pipe/socket descriptors or expected claim digests. SDK
logging and logger configuration stay in that process; its ordinary stdout and
stderr are discarded. The supervisor accepts only a fixed exit outcome and
requires the helper to be reaped with confirmed controller cleanup.
Individual catalog/network failures become the public `catalog_probe_failed`
category across this boundary. A helper controller-cleanup failure remains a
separate cleanup code; the detailed catalog subcause is not transported.

Primary lifecycle sources inspected for this adapter:
[MCP publishing commit](https://github.com/modelcontextprotocol/python-sdk/commit/2118f14f8a19bc158d8a1cf90af58d85d187f849)
and [Uvicorn publishing commit](https://github.com/Kludex/uvicorn/commit/3eb9a9ab9af69005c687728097461c1fd2a95db9).
Installed-source blobs were also compared during implementation review.

## Private channel and process ownership

`startup_channel` frames each message as a four-byte unsigned big-endian length,
1–16,384 payload bytes, then EOF. It accepts one frame only. Nonblocking I/O
checks bounds during reads/writes, with polling at most every 50 ms. Oversize,
truncation, trailing bytes, missing EOF, stalled peers, cancellation, broken
pipes, early child exit, and deadline equality fail explicitly.

One absolute monotonic transport deadline starts before process launch and no
later than the protocol attempt. No retry renews it. Expected PID comes from
the retained `Popen` object, never a reply or a saved PID file. Failure consumes
the one-shot challenge. Neither challenge nor reply appears on HTTP, ordinary
stdout/stderr, command arguments, logs, or a persistent channel path. Only
descriptor numbers and ordinary launch arguments are process arguments.

Cleanup closes owned endpoints, shuts down/reaps only the owned server child
and catalog helper, and removes only its new disposable configuration area.
Original failure and cleanup failure remain separate; success is not emitted
before cleanup. An observed server exit before supervisor-requested shutdown
fails even with exit status zero. Cancellation is checked before launch, after
catalog observation, and before the final success decision. A forced kill is
not evidence of graceful controller cleanup. Descendant-process
containment and protection against a malicious same-user process are not
claimed.

## Runtime and interpreter evidence

The supervisor uses the existing prepared-runtime validator and selected
manifest. The child derives its runtime from its actual `sys.prefix`, checks
the installed distribution set/import origins, and fingerprints its actual
invoked interpreter and retained base interpreter. Venv selection and the
`pyvenv.cfg` fingerprint are preserved; it does not substitute a base
interpreter for the selected venv launcher. Missing, inconsistent, or changed
runtime evidence fails without a fallback.

Runtime/manifest identifiers are prepared-artifact identifiers. Interpreter
fingerprints and repeated installed-file checks establish selected disk
identity and change detection, not independent attestation of loaded memory.
The external base Python remains a dependency and must not be removed or
replaced on the assumption that the venv is self-contained.

## Evidence and remaining gates

Private-channel completion, owned-child observations, selected-policy
collection, retained-socket listening, endpoint/catalog comparison, and cleanup
are separately reported. The existing claim codec and its tests are unchanged.
Its successful result still leaves these flags false:

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

The codec's startup/peer/collection blockers remain scoped to that message
primitive. Added integration observations do not rewrite the codec's result or
remove the loaded-service/pairing compatibility blockers. Maintenance/idle
admission, existing work and approvals, state/schema compatibility, required
legacy `/control`, durable recovery/rollback, client reconnection, and human
authorization remain future gates.

The installation harness exercises a clean-wheel supervisor and a separately
prepared-runtime supervisor, outside the checkout, against a prepared child
in both modes. Source, installed Linux, native macOS CI, Windows unsupported
behavior, PR CI, and post-merge CI must be reported separately by exact source
identity. Windows rejects this POSIX adapter before filesystem/socket/process
side effects; its existing codec and packaged HTTP coverage remain required.

[Protocol contract](STARTUP_CONFIRMATION.md) ·
[Runtime preparation](RUNTIME_PREPARATION.md) ·
[中文](DISPOSABLE_STARTUP_CN.md)
