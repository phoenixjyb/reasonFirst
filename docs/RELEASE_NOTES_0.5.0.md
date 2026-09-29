# ReasonFirst 0.5.0 release notes

This document summarizes ReasonFirst v0.5.0. The GitHub release/tag identifies
the exact released commit; source checkouts can move ahead, so use the exact
commit SHA when reporting reproducibility evidence.

## Why 0.5.0

0.5.0 is a compatibility, safety, and public-usability release over the 0.3.0
ActualCoder lifecycle. It keeps the same product direction—ChatGPT/strong
reasoning first, replaceable coding workers second—while making the complete
chat-only path reproducible for external users.

The 0.5.0 version line is intentional: prior development had already used the
0.4 line, so this release avoids reusing that version family.

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
- Final release documentation/Pages audit: current guides are reconciled with the implemented architecture, historical v0.2/v0.3 notes are labeled, rendered internal links are CI-checked, and the bilingual site exposes the v0.5.0 architecture/validation baseline.

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
  and should be enabled deliberately. Whether its write-capable tools are
  available through ChatGPT depends on the connected client/workspace; terminal
  ActualCoder remains the portable execution fallback.
- macOS launchd does not automatically inherit an interactive shell's proxy
  environment; use the documented session-scoped sync helper when required.
- Final merge remains a human decision. ReasonFirst does not auto-merge.
- External coding tools use their own authentication, quotas, and billing;
  ReasonFirst makes no direct model-inference API calls.

## Release validation

The v0.5.0 release gate requires exact-head success for:

- `validate` (full Python unit/integration suite);
- Ubuntu, macOS, and Windows cross-platform jobs;
- Ubuntu and macOS Bridge regression jobs;
- `package-release-candidate` (wheel + sdist build, archive-path inspection,
  clean-wheel install/version/CLI checks, and source/full-history secret scan);
- the documentation-site build/deploy when documentation changes are present.

At the pre-release audit baseline `7d16061061f6337604bd3135c9e4a693ad1fd68a`,
all seven CI jobs and the Docs site passed; `validate` ran **445 tests**.
The final GitHub Release records the exact tagged SHA and must be published only
after the same exact-head gate passes on the final documentation commit.

See `docs/PUBLIC_RELEASE_CHECKLIST.md` for the full maintainer checklist.
