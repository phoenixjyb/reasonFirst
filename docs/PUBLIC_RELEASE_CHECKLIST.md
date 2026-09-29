# Public-release checklist for maintainers

A public repository and green CI are not the same as a prepared release. This is a checklist, not a claim that every item has been completed. The source already contains Apache-2.0 licensing; do not relicense or expose another repository as part of documentation maintenance.

Observed functional evidence on 2026-09-28: the synthetic chat-only path completed managed worker execution, reviewed snapshot approval, GitLab MR publication, matching-HEAD unit-test CI, and EvidencePack review without auto-merge. This is useful release evidence, but it does **not** by itself satisfy the privacy, packaging, repository-settings, multi-platform, or release-tag checks below.

## Source, privacy, and licensing

- [ ] Confirm authority to distribute the source, examples, assets, and contributions. Review third-party notices and dependencies; preserve the existing LICENSE. Publishing code does not license external coding-agent services or grant rights to private target projects.
- [ ] Review tracked files and full available HEAD history with `scripts/check_repo_secrets.py --history`, then separately inspect other branches/tags, closed PRs/comments, issues, release assets, CI logs/artifacts, screenshots and attachments. The built-in scanner does not cover all of these surfaces.
- [ ] Remove or sanitize deployment-specific endpoints, customer code/data, personal paths and identifiers from intended public material. Do not copy local migration acceptance output into public docs. Generate synthetic examples instead.
- [ ] Revoke any actual exposed credential first. Coordinate any history cleanup; do not assume deletion from HEAD or a new branch erases old objects, forks, caches, or comments. Known fabricated negative-test fixtures are not production credentials and should not motivate disabling the audit.
- [ ] Inspect the actual release archive/wheel, not just the working tree. Exclude `.env`, runtime data, workspaces, migration journals/backups, private certificates, test-generated keys, local review bundles and unrelated artifacts.

## Public entry points

- [ ] README explains the implemented workflow, intended users, setup, unsupported capabilities, provider/authentication boundaries, and license without referring to a private chat.
- [ ] A clean source checkout can run the documented synthetic tests without production GitLab credentials or a paid model session; live smoke checks are clearly labeled.
- [ ] Copyable commands use plain URLs, explicit placeholders and no interactive-zsh prose/comments. Existing user configuration is never overwritten by an example during upgrade.
- [ ] English/Chinese entry points and relative links are correct. Historical design/alpha notes are labeled and not presented as shipped capabilities.
- [ ] CONTRIBUTING, bug/feature templates and the PR template request minimal sanitized evidence, not full private logs or credentials.

## Security and repository settings

- [ ] Enable and test a private vulnerability-reporting channel before encouraging public adoption. SECURITY.md is not proof that GitHub private reporting is enabled. Verify delivery to an actual maintainer; do not advertise an unmonitored email/SLA.
- [ ] Configure appropriate branch protection/rulesets, required checks and human review for `main`. These are repository settings, not enabled by adding this checklist.
- [ ] Review dependency alerts, secret scanning/push protection and optional code scanning available to the repository. Do not claim they are enabled without checking the settings.
- [ ] Review Actions permissions and fork-PR behavior. Do not expose production credentials to PR code, run untrusted PR code on a privileged self-hosted runner, or bypass failing checks for a release.
- [ ] Set an accurate repository description/topics and support expectations. Enable Discussions only when there is a plan to moderate/respond; do not point users at an unavailable channel.

## Version, evidence, and scope

- [ ] Choose an exact release commit; verify required jobs for that SHA and post-merge integration, not just an earlier branch head. Record platform/interpreter coverage, skips and known limitations.
- [ ] Align tag, package version, changelog and release notes. For v0.5.0, keep the final tagged commit aligned with package version and release notes; later `main` commits may move ahead and should be identified by exact SHA.
- [ ] Test documented installation/update from the intended distribution. Source editable installs and immutable release packages have different behavior; packaging success does not prove the MCP server entrypoint is bundled as a service.
- [ ] State outstanding runtime TLS/redirect, handoff, concurrency/recovery and evidence limitations. Separate local regression tests, real API reads, Git fetch, historical CI retrieval, MCP reconnection, and an explicitly approved new push in any validation claim.
- [ ] Authorize any release tag, package upload, visibility change or announcement separately. Do not publish other repositories merely because their names appeared in a development discussion.

References: [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository), [contribution guidelines](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors), [GitHub sensitive-data removal](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
