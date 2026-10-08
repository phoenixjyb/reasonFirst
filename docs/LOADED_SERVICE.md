# Observe the loaded macOS job without switching it

**Development-source feature, not included in the published v0.5.1 wheel.**
This is an explicit native read, separate from static `launch-plan`/`launch-check`.
It never loads, unloads, restarts, signals or registers a ReasonFirst service.

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
unreported fields or no running PID. `selected_launch_fields_match` covers only
those four fields. It does not cover all launch options or all launchd defaults.
`PID` is an observation, not independent executable identity or listener ownership;
`LastExitStatus` is historical, not current health. The exposed environment block
is not the process's complete inherited environment or the shell's transformed
state. In-memory metadata from the API is not proof of imported Python code.

All prior compatibility blockers are retained, including the legacy `/control`
mismatch and unaudited generated launcher. `effective_environment_verified`,
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
changes only its saved temporary plist, verifies the loaded job still differs,
and unloads that exact fixture. It never uses the production service label or a
ReasonFirst server. The fixture tests the native adapter/comparator, not the
public command's complete pairing/review path or a service-upgrade transaction.
Missing native capability fails that CI test rather than silently skipping it.
The fixture emits one classified JSON result only after its cleanup attempt. It
keeps the original failure stage and a separate cleanup outcome, and exposes only
fixed codes and field-match classifications, never raw native data or stderr.
The parent validates this bounded report before logging it; a failed fixture or
inconsistent exit status remains a CI failure. Missing fields and saved/loaded
drift checks are not relaxed merely to obtain diagnostics.
No native fixture is run by normal setup/status or on maintainer machines.

Application configuration/schema compatibility, generated-sidecar source support,
complete effective environment and process identity, work admission, controlled
switch/recovery and existing-client acceptance remain separate requirements.

[Launch review](LAUNCH_REVIEW.md) · [中文](LOADED_SERVICE_CN.md)
