# Observe the loaded macOS job without switching it

**Development-source feature, not included in the published v0.5.1 wheel.**
This is an explicit native read, separate from static `launch-plan`/`launch-check`.
It never loads, unloads, restarts, signals or registers a ReasonFirst service.

The approved feature contract is **`partial-selected-fields-v1`**. Observation can
complete while loaded configuration remains incomplete and activation is blocked.
This is an explicit scope correction after native investigation, not a claim that
the original four-field native acceptance succeeded.

After reviewing the existing deployment pairing and launch-source digests:

```text
reasonfirst-runtime loaded-inspect --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

Replace placeholders with the exact reviewed values. There is no arbitrary label,
host, PID, command or alternate-domain option, and no `--yes` or activation flag.
The command always queries `com.reasonfirst.v4-mcp` in the caller's **user-level
launchd domain**. It rejects Windows/Linux, root and setuid contexts. Running this
in another login context is not a way to prove the GUI domain's service is absent.

## What is measured

The existing source/policy and prepared-runtime review is revalidated before and
after two native queries. Selected fields are compared privately: Program,
ProgramArguments, WorkingDirectory and EnvironmentVariables. The report contains
only match/difference/missing-field classifications, a reported PID and last exit
status, and the reviewed launch digest. Arbitrary native environment/argument
values and native stderr are not returned or written to a report file.

An absent Program may be derived from argv[0]. No other missing loaded field is
filled from the saved plist: it becomes `not_reported`, not a match. A reported
empty environment block can match a saved absence, but an absent native block is
still unknown. Two changed PID/selected-field samples or changed reviewed files
cause failure without returning a partial successful comparison.

`ok: true` means the observation/comparison completed; it can contain mismatches,
unreported fields or no running PID. `field_coverage` is `complete`, `partial`, or
`none`; `unreported_fields` names only the known fields absent from that response.
`reported_fields_match` requires at least one reported field and every reported
field to match; it cannot be used as configuration equivalence. The original
`selected_launch_fields_match` gate is unchanged: **all four** fields must match,
so any `not_reported` field keeps it false. Even `complete` covers only those
four selected fields, not all launch options or all launchd defaults.
`PID` is an observation, not independent executable identity or listener ownership;
`LastExitStatus` is historical, not current health. The exposed environment block
is not the process's complete inherited environment or the shell's transformed
state. In-memory metadata from the API is not proof of imported Python code.

All prior compatibility blockers are retained, including the legacy `/control`
mismatch and unaudited generated launcher. The new
`managed_startup_confirmation_not_verified` blocker is unconditional, even when
all four fields match. `managed_startup_confirmation_verified` remains false;
there is no startup-confirmation protocol implemented by this command.
`effective_environment_verified`,
`running_code_verified`, `compatibility_verified`, `activation_authorized` and
`ready_for_activation` stay false. No registry, runtime, wizard state, application
configuration or task/approval data is modified or read beyond the preceding
review's existing allowed source/metadata reads. Configuration **references** may
be present in native data but their files are not opened.

## Native adapter and limits

The adapter uses Apple's structured `SMJobCopyDictionary(kSMDomainUserLaunchd)`
read API, not a parser for `launchctl print` diagnostic text. **Apple marks this
API deprecated.** A missing symbol/framework, null result, malformed/oversized
response, crash or timeout fails closed; there is no undocumented fallback or
request for administrator access. A null response is classified as
`job_unavailable_or_query_failed`, not proof that the service does not exist.
See Apple's [job-copy documentation](https://developer.apple.com/documentation/servicemanagement/smjobcopydictionary(_:_:))
and [user-domain constant](https://developer.apple.com/documentation/servicemanagement/ksmdomainuserlaunchd).

A fixed standard-library-only script runs through the current Python with
`-I -S -B`, absolute framework paths and a minimal child environment. This prevents
user-site/.pth/loader-environment injection into that helper; it does not attest
the already-running CLI/interpreter/OS. Each query is bounded to eight seconds,
256 KiB stdout and 16 KiB stderr, plus up to two seconds of helper termination
cleanup. Only that helper can be terminated by this read operation. No server,
worker, application controller, HTTP endpoint or credential provider is invoked.
Repeated reads are not an atomic snapshot, maintenance lock, ABA protection,
PID-reuse defense or a signature.

## Validation and remaining work

Portable tests cover redaction, missing fields, mismatches, PID changes, review
rechecks, errors and the public CLI boundary. POSIX tests exercise actual bounded
helper pipes and timeouts. An explicit macOS Actions-only harness loads one
random-label `/bin/sleep` job, queries it with the **prepared runtime's Python**,
changes only its saved temporary plist (including the sleep argument from `90`
to `91`), verifies the still-loaded program arguments differ from that new saved
value, and unloads that exact fixture. Program/ProgramArguments must be reported
and match initially; missing essential fields still fail. Any returned cwd/env
must match initially and differ after the saved change. Unreported cwd/env must
remain `not_reported` in both comparisons, with the full four-field gate false
and the incomplete-comparison and startup-confirmation blockers retained.
The native selected projection and PID must remain unchanged across queries. It never uses the production service label or a
ReasonFirst server. The fixture tests the native adapter/comparator, not the
public command's complete pairing/review path or a service-upgrade transaction.
Missing native capability fails that CI test rather than silently skipping it.
The fixture emits one classified JSON result only after its cleanup attempt. It
keeps the original failure stage and a separate cleanup outcome, and exposes only
fixed codes and field-match classifications, never raw native data or stderr.
The parent validates this bounded report before logging it; a failed fixture or
inconsistent exit status remains a CI failure. Reports identify the revised
`observation_contract`, coverage, original four-field result, and activation
blockers. The parent rejects any attempt to claim full comparison from missing
fields, remove the startup blocker, skip reported-argument drift, or authorize
activation. Test success means this narrower observation contract and cleanup
passed, **not** that full loaded configuration was verified.
No native fixture is run by normal setup/status or on maintainer machines.

Application configuration/schema compatibility, generated-sidecar source support,
complete effective environment and process identity, work admission, controlled
switch/recovery and existing-client acceptance remain separate requirements.

[Launch review](LAUNCH_REVIEW.md) · [中文](LOADED_SERVICE_CN.md)

## Native field-availability investigation

CI at `1ce151e8` successfully queried and cleaned up its synthetic job, but the
returned dictionary did not report `WorkingDirectory` or `EnvironmentVariables`
at the expected top level. That is not proof of an absent launch setting and is
not a successful four-field comparison. No native field is filled from the plist.

The CI-only fixture now adds `native_shape`: fixed top-level type classifications
and a bounded recursive count of the two known keys and its own synthetic cwd/env
strings anywhere in the returned dictionary. It emits no arbitrary native key,
value, label, path or stderr. At most 4096 nodes and depth 16 are inspected; an
incomplete traversal is explicitly reported and must not support an absence
claim. This diagnostic is never used to make comparison/cleanup acceptance pass.
It does not introduce another query, a new production API, or a fallback parser.

Apple's [historical launchd-842.1.4 `job_export` implementation](https://github.com/apple-oss-distributions/launchd/blob/d448a1c8f70a61202f8705f94337f686b87c30c4/src/core.c#L985)
exports a selected dictionary including program/argv and process status rather
than the whole saved plist; its export function does not include cwd/environment.
That old source helps explain a possible API limit, but it is not evidence of
which binary implementation runs on today's macOS. The native fixture's actual
field/shape report is the evidence for its particular runner. A renamed/nested
field must not be accepted through a guessed alias.

The follow-up at `8e1e4862`, CI **37781648284**, completed a bounded traversal of
that runner's response: neither the two field names nor the exact synthetic
cwd/env strings occurred anywhere in it. That run remains a **failed original
four-field acceptance**, and its saved-file drift phase was not reached. Cleanup
was confirmed. These observations are not a guarantee about every OS/API version.

The operator explicitly approved revising #102 to partial observation. Native
acceptance now proves available program/argument evidence, unknown-field handling,
reported-argument drift detection, and cleanup; it does not manufacture the
missing evidence. Tests that previously required unavailable cwd/environment to
exist were explicitly revised under this scope decision. Their underlying
four-field comparison assertions and automatic-activation blocks were retained,
and new regressions enforce the distinction. This is not an unchanged full
verification suite being made green by relabeling a failure.

A separate managed-startup confirmation must establish freshness and local
process/peer binding before selected non-secret effective configuration or runtime
identity can be relied on. A self-report alone is neither independent process
attestation nor authorization. Older uninstrumented services require a reviewed
maintenance-adoption path. None of that protocol, activation or recovery is
implemented in this partial-observation change.

## Next protocol layer

See [managed-startup claim messages](STARTUP_CONFIRMATION.md). This internal
codec/verifier does not bind a live peer or collect effective configuration.
It does not remove this observer's unconditional startup-confirmation blocker.
