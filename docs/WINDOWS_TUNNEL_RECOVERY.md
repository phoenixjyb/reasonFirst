# Windows tunnel startup recovery — v0.5.1 test candidate

[简体中文](WINDOWS_TUNNEL_RECOVERY_CN.md) · [Installation](INSTALL.md)

The original candidate could lose Windows path separators when passing an MCP
executable to tunnel-client v0.0.15. Locale-dependent decoding then hid the native
startup error behind `UnicodeDecodeError: gbk` and `NoneType ... replace` (#88).
The corrected candidate quotes for the receiving parser on every OS and captures
native output as bytes, with strict UTF-8 JSON parsing and separate diagnostics.
Both packaged read MCP and Bridge use the shared corrected launch path.

## Preserve existing state

Do not recreate the Windows tunnel, copy Mac configuration, rotate valid tokens,
add an admin key, disable TLS, or delete the existing profile. After installing the
reviewed checksummed replacement, remove the session-only diagnostic workaround:

```powershell
Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
reasonfirst tunnel status --alias reasonfirst-gitlab --json
```

`status_query_ok` distinguishes query failure from a successfully queried stopped
runtime. Inspect `runtime_state` and the bounded, scrubbed `diagnostics` entries.
Use JSON for diagnostics; the older human summary alone is not enough to diagnose
a query error. A failed query is not evidence that a process can safely be started.

After the same Windows alias is confirmed stopped, reconnect it:

```powershell
reasonfirst tunnel connect --alias reasonfirst-gitlab
```

Enter the existing Windows read tunnel ID when prompted, and the authorized
runtime key only in the masked prompt. A tunnel ID can also be supplied through
`--tunnel-id`; never put a runtime key in command arguments. This updates the local
runtime profile, not the remote account resources. It preserves GitLab config and
records the completed tunnel phase only after readiness succeeds.

When the original wizard crashed before recording that phase, `setup --repair`
cannot infer the missing identity: use explicit `tunnel connect` first. A running
but unrecorded runtime needs identity/status review, not a blind second start.
Keep the Mac and privileged Bridge connections separate.

## Acceptance remains live

Local subprocess regressions and the pinned upstream parser/native child-launch
CI check are not a live tunnel/provider test. Recheck this corrected candidate on
the Windows computer without the UTF-8 workaround. Then follow
`reasonfirst chatgpt handoff --open` and perform a fresh read using only the Windows
app. Local readiness is not proof of ChatGPT project access. Stable publication
remains separate until this rehearsal and restart/recovery acceptance succeed.
