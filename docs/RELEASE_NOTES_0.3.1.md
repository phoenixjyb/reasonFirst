# ReasonFirst 0.3.1 release candidate

This document summarizes the prepared post-v0.3.0 release candidate. It is **not**
a release announcement: no tag, GitHub Release, or external package publication
is authorized by this file.

## Why 0.3.1

0.3.1 is a compatibility, safety, and public-usability release over the 0.3.0
ActualCoder lifecycle. It keeps the same product direction—ChatGPT/strong
reasoning first, replaceable coding workers second—while making the complete
chat-only path reproducible for external users.

## Main user-visible changes

- Full Bridge-managed chat-only loop: pinned TaskSpec → one managed workspace →
  Codex App Server execution → reviewed diff → snapshot-bound finish preview →
  explicit human publication approval → GitLab MR → matching-HEAD CI →
  EvidencePack.
- Current Codex App Server worker-start compatibility fixes, including policy
  wire values and safe handling when a ReasonFirst MCP entry is absent.
- macOS launchd proxy synchronization for environments where outbound model
  traffic needs an HTTP(S) proxy, without writing proxy values into source or
  LaunchAgent plists.
- Health polling after MCP installation/restart to avoid false-negative startup
  races.
- Durable TaskSpec/attempt records, bounded EvidencePack, worker-policy evidence,
  explicit approval mediation, project practice tooling, CI failure
  classification, and safer finish gates accumulated since v0.3.0.
- Public bilingual chat-only rehearsal, onboarding, troubleshooting,
  architecture, contribution, and release-maintainer documentation.

## Observed end-to-end acceptance

The 2026-09-28 synthetic GitLab rehearsal observed all of the following on one
managed task:

- live GitLab source grounding at a pinned revision;
- one managed workspace and feature branch;
- Bridge-managed Codex App Server worker execution;
- a README-only reviewed diff;
- successful configured unit tests;
- a concrete finish-preview snapshot digest;
- explicit human approval of that exact snapshot;
- controlled commit/push and Merge Request creation;
- matching-HEAD GitLab CI with the real unit-test job successful;
- complete EvidencePack review;
- no automatic merge.

This acceptance proves the exercised path, not every deployment topology.

## Known boundaries

- GitLab is the implemented SCM/CI target adapter; hosting ReasonFirst itself on
  GitHub does not provide a GitHub-target task adapter.
- Worktrees and worker subprocess controls are not an OS security sandbox.
- Native Git trust/destination policy remains distinct from Python API/MCP TLS
  handling.
- The Bridge Preview MCP is more privileged than the read-oriented GitLab MCP
  and should be enabled deliberately.
- macOS launchd does not automatically inherit an interactive shell's proxy
  environment; use the documented session-scoped sync helper when required.
- Final merge remains a human decision. ReasonFirst does not auto-merge.
- External coding tools use their own authentication, quotas, and billing;
  ReasonFirst makes no direct model-inference API calls.

## Release-candidate verification

Before publishing 0.3.1:

1. require green exact-head GitHub CI on the release candidate;
2. build wheel and sdist and inspect archive member names;
3. install the built wheel in a clean virtual environment and verify package
   version plus CLI entry points;
4. run tracked-file/full-history secret scanning;
5. review public repository surfaces and repository security settings separately;
6. obtain explicit maintainer approval before creating a tag, GitHub Release, or
   external package upload.

See `docs/PUBLIC_RELEASE_CHECKLIST.md` for the full maintainer checklist.
