# Review the saved HTTP launch contract

**Development-source feature; not in the published v0.5.1 wheel.** This extends
[deployment/runtime pairing](DEPLOYMENT_PAIRING.md) with measured launcher source
and saved endpoint/policy evidence. It is not a service switch or complete
compatibility check. Do not update a working installation just to run this review.

## What is now inspected

After verifying the caller's exact pairing digest, `reasonfirst-runtime launch-plan`
reads the selected launch sources. Two narrowly defined macOS inputs are supported:

- The known `v4-service/tools/codex_web_bridge` launcher chain, including the shell
  entry, HTTP launcher, local proxy helper, and legacy HTTP server.
- The v0.5.1 isolated HTTP sidecar produced during the original maintainer migration:
  canonical quoted shell launcher, fixed bootstrap, stage manifest, and five
  compatibility sources. Its recorded package claims are not current-core proof.

Actual file bytes must match reviewed source profiles; a recognizable pathname is
not enough. Unknown/modified launchers, unsafe storage, missing files, wrong host,
invalid pairing, and unsupported target profiles stop review. No script, bootstrap,
controller, interpreter, shell, `launchctl`, HTTP/MCP client, or package manager is
executed. The bootstrap reference under `tests/fixtures` is inert test input, never
installed or used to generate a new launcher.

The target is the fully revalidated prepared runtime. Its actual packaged HTTP
entry and shared MCP core must match the known no-control source profile. Version
metadata alone is not enough. New source profiles require an explicit code review,
not a command-line trust override. This restriction is intentional for this slice.

Saved `RF_MCP_PORT`, `RF_MCP_READ_ONLY` and experimental-push policy are interpreted
without changing them. Explicit configuration/state/runtime paths are returned as
**references**, not opened. Paths currently must be absolute, unambiguous and under
the selected home; other layouts fail instead of guessing relative-path semantics.
Missing variables remain unknown: launchd may have supplied inherited values, so
an absent saved variable is not proof that a launcher's default was used.

Full-chat in the known legacy HTTP server exposes `/control`, while the known
packaged target does not. A resolved full-chat saved policy therefore reports
`legacy_control_not_supported_by_target`. Read-only has no control route or remote
push regardless of an otherwise enabled push flag. Unknown policy is not treated
as read-only. `/control` is not probed, disabled or automatically added. The old
proxy helper's launchd-environment side effect is reported, never executed.

## Commands

Use the matching `plan_digest` from a fresh `deployment-plan` as the pairing digest.
The arguments below are placeholders, not executable values:

```text
reasonfirst-runtime launch-plan --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --json
reasonfirst-runtime launch-check --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

Both commands are read-only. `launch-plan` returns one new digest binding the pair,
actual source bytes/modes, sidecar manifest (when applicable), saved policy and
checked target HTTP profile. `launch-check` repeats all observations and requires
that digest; a pairing digest cannot substitute for a launch digest. Neither saves
a plan nor accepts install/restart/repair/force approval flags.

`ok: true` means inspection succeeded, **not** that all blockers are cleared.
`launcher_sources_verified` describes the named files. `static_control_compatible`
only compares the identified control-route policies. `compatibility_verified`,
`activation_authorized`, and `ready_for_activation` always remain false, and
`proposed_actions` remains empty. An error returns no partial identity/digest or raw
parser exception. Keep reports private: local paths and fingerprints remain present.

## What remains not_inspected

The service manager's loaded definition, effective/inherited process environment,
current imported application core, application configuration contents and state
schema are **not_inspected**. The new digest is not a maintenance lease, atomic
snapshot, signature, authority to restart, or protection against a malicious same-user
process. Repeated reads can notice changes, not prove absence of ABA races.

Plists and sidecar manifests are privately read and can contain environment values;
raw contents and unrelated environment values are not printed or persisted. `.env`,
`bridge.yaml`, workspace/approval state and credential stores are not opened.
A known launcher still may fail on startup, and its imported dependency closure is
not attested by these source profiles. Active-work admission and durable
activation/recovery must be implemented before a supported switch.

macOS is the only pairing/launch-review platform currently supported. Linux runtime
preparation and Windows packaged/source use retain their existing support; these
new operations reject unsupported pairing before home/source inspection. Tests
separate inert POSIX file fixtures from the native macOS clean/prepared-runtime CLI
check. No real maintainer service is a test target and no launchd migration is claimed.

[中文](LAUNCH_REVIEW_CN.md) · [HTTP transport](BRIDGE_HTTP.md)
