# Assess known legacy HTTP launch sources and saved policy

**Development-source feature, not in the published v0.5.1 wheel.** This follows
[static pairing](DEPLOYMENT_PAIRING.md). It inspects actual named source files and
the selected saved Bridge YAML, rather than assuming that a familiar launcher path
or version number establishes compatibility. It does not execute those files,
probe a real service, change registrations or activate a replacement.

## Read-only commands

After independently reviewing/recording the registration and preparing a runtime:

```bash
reasonfirst-runtime deployment-assess --runtime-id "REPLACE_WITH_RUNTIME_ID" --json
reasonfirst-runtime deployment-assess-check --runtime-id "REPLACE_WITH_RUNTIME_ID" --expect-digest "REPLACE_WITH_ASSESSMENT_DIGEST" --json
```

Replace both placeholders with the actual runtime ID and the **assessment** digest.
A pairing/adoption/preparation digest is not interchangeable. Neither command
accepts `--yes`, `--force`, `--activate`, configuration writes or automatic repair.
They revalidate the existing pairing, named source bytes, saved plist and Bridge
config before returning. A failed check returns no partial evidence/digest.
The digest is a change detector, **not a signature**, lock or restart authorization.
Repeated reads are not an atomic snapshot, ABA defense or hostile-same-user isolation.

## Supported source profile

macOS only; Linux and Windows reject before discovering HOME or reading storage.
The previous Linux runtime-preparation and cross-platform installation support
remain unchanged. The known **legacy staged HTTP** layout is inspected under
`~/.local/share/reasonfirst/v4-service/tools/codex_web_bridge`:

- Both launch scripts, `set_local_no_proxy.sh`, and the HTTP server must match the
  reviewed retained legacy source hashes.
- The controller/config modules may be the known full implementations or the
  exact forwarding aliases with the corresponding known staged implementations.
- The paired target runtime must still pass the existing complete tree/interpreter
  validator. Its HTTP wrapper, MCP core, controller and config-parser source hashes
  must match the reviewed packaged profile; the same reported version is insufficient.

Unknown or modified sources stop with a classified error. In particular, the
one-off `uv-http-v…` sidecar layout returns `unsupported_launcher_profile`, even
though registration/pairing can recognize its path. Its bootstrap and mutable uv
import arrangement need their own source-profile adapter; they are **not** guessed
from the retained legacy wrapper. This feature cannot upgrade that sidecar.
Do not modify a working installation to make it fit the accepted profile.

Source profiles are intentionally conservative and must be reviewed and retested
when the implementation changes. These checks are not a complete Python import
closure or proof that the saved source is what the running process loaded.

## Saved policy and configuration evidence

The exact known shell source supplies literal loopback `/mcp`, an inherited/default
port, and the v4 runtime reference. The saved plist contributes explicit
`RF_MCP_READ_ONLY`, `RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH`, `RF_MCP_PORT` and configuration
references. Unset values are labeled `source_default_if_not_inherited`, **not**
actual launchd/process environment. Shell/Python code-injection overrides, conflicting
HOME, invalid booleans/ports, outside-home paths and unsafe filesystem objects fail.
Unknown plist fields remain bound by its hash but are not claimed to be interpreted.

Bridge config is selected only from the saved reference or known default. Custom
absolute/`~/` YAML paths inside the same HOME are allowed through private no-follow
reads. Its whole bytes are fingerprinted. The assessment recognizes only the known
v3/v4 top-level mapping shape; duplicate keys, aliases/anchors, custom tags, excessive
size/depth and unsupported shapes fail. It reports schema version, target count,
and whether legacy control configuration is present, without reporting config values.
It does **not** validate target semantics, state migration, project credentials or
worker permissions. Missing YAML is reported as absent, not created or declared valid.

**The saved plist and Bridge YAML can contain credentials.** They are read privately
but neither their contents nor arbitrary environment values are echoed. The code
never opens `.env`, token stores, `auth.json`, `config.toml`, `control-token`, workspace
state or approval records; `credential_store_files_read` and `workspace_state_read`
remain false. A GitLab config reference is not proof of which file the service
actually loaded. Keep reports private because they include local paths/hashes.

## The important full-chat incompatibility

The known legacy server exposes `/control` whenever its mode is not read-only,
regardless of whether a relay is currently active or `control` YAML is empty. The
packaged HTTP target omits this route. Therefore the default/explicit legacy
full-chat policy returns `legacy_control_route_not_supported_by_target`. It is not
silently narrowed to the new API. Experimental remote-push opt-in also blocks the
target, including a read-only source where that particular tool is not exposed.

`ok: true` means the **assessment completed**, not the deployment is compatible.
`assessment_status: blocked_by_declared_policy` reports known mismatches. Explicit
read-only policy can give `declared_surface_match: true` and
`assessment_status: requires_live_compatibility_checks`; this is only a conditional
saved-policy comparison. In both cases **`compatibility_verified`,
`ready_for_activation`, `activation_authorized` and `live_service_verified` are false**.
The result proposes no commands or writes. The known launcher's proxy-environment
side effect is reported, not executed or silently recreated.

Still required: loaded-service identity and inherited environment, actual import
closure/interpreter, endpoint/authentication/listener ownership, target state and
workspace compatibility, idle/admission checks, controlled activation and durable
recovery. Hash success cannot substitute for any of those checks.

## Tests

Unit tests use real private POSIX file fixtures and retained source bytes, but mock
runtime creation. The macOS install harness additionally invokes the assessment
through a genuinely prepared runtime's isolated Python. It creates only synthetic
saved registrations/sources/config, verifies `/control` blocking and matching-digest
recheck, rejects YAML/source drift, and checks that assessment does not mutate fixture
files. No LaunchAgent is loaded; no worker, provider or maintainer machine is touched.
Native tests are not a live deployment, authentication, activation or rollback test.

[中文](DEPLOYMENT_COMPATIBILITY_CN.md) · [Runtime preparation](RUNTIME_PREPARATION.md)
