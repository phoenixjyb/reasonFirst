# Read-only launch compatibility review

This development-only B3b slice follows deployment/runtime pairing. It is not part of public v0.5.1 and never starts, stops, installs, restarts or migrates a service.

Commands:

```text
reasonfirst-runtime launch-plan --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --json
reasonfirst-runtime launch-check --runtime-id RUNTIME_ID --expect-pairing-digest PAIRING_DIGEST --expect-digest LAUNCH_DIGEST --json
```

The first command revalidates the saved macOS registration and prepared runtime, reads four specific legacy launcher sources, compares known source fingerprints, checks the selected packaged HTTP/MCP source profile, and reports selected saved policy fields. The second repeats those checks and requires an exact review digest. Neither permits approval flags or writes a saved association.

Legacy full-chat HTTP exposes /control. The packaged HTTP entry point does not. A saved full-chat policy therefore yields a blocker, not silent privilege loss. Unknown or inherited policy remains unknown. An existing versioned sidecar is recognized but its bootstrap is not independently audited in this slice, so its source cannot be accepted as verified.

The inspected plist may contain credentials and environment values: only constrained policy fields and configuration-reference presence are reported; actual values of referenced files are not opened or printed. The commands never read application .env, bridge.yaml, setup.yaml, workspaces or approval state, and do not contact launchd, any controller or external provider.

A successful review still returns compatibility_verified=false, activation_authorized=false and ready_for_activation=false. Running service identity, actual effective environment, schema compatibility, pending work, controlled activation, recovery and existing-client acceptance remain unresolved. Repeated reads are not an atomic snapshot, and hashes are not signatures.

macOS is the only supported deployment-pairing platform. Linux runtime preparation and Windows package/HTTP installation support are separate and unchanged.

[中文](LAUNCH_REVIEW_CN.md) · [Pairing](DEPLOYMENT_PAIRING.md) · [HTTP Bridge](BRIDGE_HTTP.md)
