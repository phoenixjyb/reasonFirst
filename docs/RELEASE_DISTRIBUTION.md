# Versioned public downloads

[Install](INSTALL.md) · [Maintainer checklist](PUBLIC_RELEASE_CHECKLIST.md) · [简体中文](#简体中文)

## What users receive

The intended stable release is **v0.5.2**. Only advertise its download once the
maintainer has published the Release, the asset workflow has succeeded, and the
actual public download/install has been checked. A merged PR or an expiring Actions
candidate is not a public release. Keep source installation supported.

The Release asset set contains:

- `chatgpt_selfhosted_gitlab_mcp-0.5.2-py3-none-any.whl` — shared Python package;
- `chatgpt_selfhosted_gitlab_mcp-0.5.2.tar.gz` — built source distribution;
- `reasonfirst.rb` — checksum-pinned formula, not evidence that a Homebrew tap exists;
- `RELEASE.json` — exact source commit/tree, version, build run/attempt and artifact hashes;
- `SHA256SUMS.txt` — hashes for the wheel, source distribution, formula and manifest.

The source archive GitHub automatically generates for a tag is separate from the
built `.tar.gz` above. A wheel is not a standalone GUI installer: users still need
uv, an appropriate Python, Git for managed repositories, their coding-worker login,
and their own provider authorization. Never distribute an existing user's private
config, workspace, token, or tunnel identity along with the package.

For a locally downloaded wheel, compare its SHA-256 with the entry for that exact
filename in the Release's `SHA256SUMS.txt` before installation:

```powershell
Get-FileHash -Algorithm SHA256 .\chatgpt_selfhosted_gitlab_mcp-0.5.2-py3-none-any.whl
uv tool install .\chatgpt_selfhosted_gitlab_mcp-0.5.2-py3-none-any.whl
reasonfirst setup
```

The hash check detects different/corrupted bytes when compared with a trusted
checksum. A manifest served by the same repository is not a digital signature,
independent security attestation, or proof of live worker/provider readiness.
Installing the package does not update a process already running from an older copy.

## Maintainer publication sequence

1. Complete the release-scope review and relevant acceptance, then freeze an exact
   `main` SHA with successful core, three-platform regression/install, Bridge,
   documentation, tunnel-boundary and source/history checks. Do not promote an older
   candidate's checks to a newer source identity.
2. Confirm package/runtime metadata is 0.5.2, approve tag `v0.5.2` on that exact SHA,
   and publish the Release as a human action. This workflow never creates tags,
   publishes Releases, merges PRs, or uploads to PyPI.
3. The published-Release workflow checks out the event's exact commit (not a moving
   branch), verifies its tag/version and clean tracked source, builds/inspects the
   package, runs packaged/source installation checks and source/history scanning,
   and generates the public manifest/checksums.
4. Immediately before upload, fetch the release by ID, the fully paginated asset
   inventory, and the current tag commit. Refuse mismatched identity, drafts,
   prereleases, immutable published releases, and any colliding target filenames.
   Upload without replacement. The workflow is serialized per Release and never
   deletes assets or changes repository immutability settings.
5. After upload, check the public Release anonymously, download the actual wheel
   and checksums, and install outside a source checkout. Record the final SHA,
   asset hashes and installation result before sharing the versioned download.

The current flow attaches assets **after** human publication, so there can be a
window where the Release exists but its package assets are not ready. The release
page alone is not the final download acceptance. If repository immutability is
already enabled, use a separately reviewed draft-asset publication procedure rather
than disabling it; this post-publication uploader intentionally refuses that case.

## Retry and partial-upload behavior

Reruns are conservative, not automatically idempotent: a target filename already
present blocks the upload even if its bytes look identical. Do not add `--clobber`
or delete the previous asset to make the run green. Preserve the failed run and
existing files; inspect the exact local/remote hashes and source identity, then
prepare an explicitly reviewed recovery or a new version. Building again may
produce different bytes and a different build-run manifest.

GitHub asset uploads are separate requests, not an atomic release transaction.
A network failure or another publisher can leave a partial set. The preflight and
no-replacement upload prevent intentional overwrites; they do not make all five
uploads atomic or establish that nobody can move a tag after the last read.

## Acceptance scope stays explicit

Historical Windows acceptance for v0.5.1 separately verified setup/read access,
managed clone/fetch, approved-interpreter host validation, and the normal initial
worker handoff followed by clean host closeout. See the checkpoints in [#90](https://github.com/phoenixjyb/reasonFirst/pull/90)
and [#94](https://github.com/phoenixjyb/reasonFirst/pull/94). Those records retain their
own source revisions; they are not a fresh Windows live run of v0.5.2.

Healthy-runtime repair/no-op, controlled restart without the temporary UTF-8
workaround, reboot recovery, and optional privileged Windows Bridge/write live
acceptance remain open in [#88](https://github.com/phoenixjyb/reasonFirst/issues/88).
A fresh terminal's status of an already-running runtime does not prove a new start.
The v0.5.2 automated gates must run against its exact release source. Record the
actual platform and live-acceptance limits in the release decision; do not mark
unverified checks passed or remove release guards.

## 简体中文

目标是 **v0.5.2 正式版**，不是改名的候选包。正常公开分发使用版本化 GitHub
Release，而不是聊天附件或会过期的 Actions 附件。发布人确认准确 main 提交及验收
范围后，人工创建 tag/发布 Release；流水线只构建和上传已有 Release 的资源。

资源包括共享 wheel、构建的源码包、Homebrew formula、`RELEASE.json` 和
`SHA256SUMS.txt`。manifest 记录源提交、源树、构建运行及文件哈希；校验和并不是
数字签名或独立安全证明。wheel 不包含用户配置、工作区、账号凭证或隧道身份，也
不是双击安装的桌面应用。源码安装继续支持。

上传前核对 Release ID、tag 的实时提交和完整分页资源列表。任何目标文件名已存在
都停止，包括内容相同的情况；不使用覆盖选项，不自动删除既有文件。多个上传请求
不是事务，部分失败需保留证据并人工审阅恢复方案，不能简单覆盖后声称成功。
此发布后上传流程不支持已经锁定的 immutable Release；不得为此关闭仓库安全设置。

只有最终公开下载、哈希和脱离源码目录的安装验证通过后才分享下载入口。已有进程
不会因为磁盘上安装了新包而自动更新。v0.5.1 的 Windows 标准读路径、宿主验证、
正常初次 worker handoff 与宿主收尾已有现场记录；这些记录保留各自的源码版本，
不能写成 v0.5.2 的新现场验收。健康 runtime 的 repair/no-op、无临时 UTF-8 绕过的
受控重启、机器重启恢复与可选高权限 Bridge 写入仍在 #88 跟踪。v0.5.2 的自动化
检查必须绑定最终发布提交，实际支持范围和未完成验收如实记录。

## v0.5.1 post-publication staging recovery

**Historical recovery record.** The five v0.5.1 assets are already attached to the
[published release](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.1).
The procedure below documents that completed recovery and is not a command to
rerun for v0.5.2. Use the normal publication sequence above for new versions.


The normal initial-handoff Windows acceptance and host closeout have since passed,
as recorded in #94; that supersedes the historical pending-handoff item above.
Deferred privileged Windows write and restart/reboot checks remain unverified.

The human-published `v0.5.1` tag stays frozen at
`9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b`. The first Release assets run
`36827292184` passed build, install, scanning and formula checks, but failed the
strict staging input-set check before upload. `uv build` creates a hidden
`dist/.gitignore` marker by default; that marker is not a release asset. The
synthetic bundle tests originally missed the real build-directory integration.

Future normal builds use `uv build --no-create-gitignore`. The input allowlist,
tracked-source checks, tag checks and no-replacement policy stay unchanged. Do not
ignore arbitrary hidden files, remove the guard, move the published tag, delete the
Release, or rerun the old publication helper. A rerun of the original tagged
workflow still uses that frozen workflow; it does not pick up this main-branch fix.

The one-version **Release staging recovery** workflow builds the unchanged source
commit in a separate checkout and runs the original tagged staging/upload guards.
PR and main-push runs stage a downloadable five-file bundle only, with read-only
repository permissions and no asset attachment. A real offline-uv regression also
reproduces the marker failure and verifies the flag resolves it without accepting
other unexpected files. Native hosted staging tests the full real source build.

After reviewing the merged recovery workflow and its staging result, the maintainer
can select that workflow on `main` and manually supply `ATTACH v0.5.1` in its
`confirmation` input. A blank input stages only. The attachment job is reachable
only for that explicit manual invocation on main; it downloads this run's exact
staged bundle, verifies provenance and the already accepted wheel hash, rereads the
existing Release ID/tag and full asset inventory, then uploads without replacement.
Any collision, immutable release, identity change or uncertain upload outcome stops
for review. It does not create/edit/delete a Release, retag, mark Latest or configure
any user's installation. Only the published release's first asset attachment is
being recovered; do not reuse this one-version workflow for a future version.

`RELEASE.json` continues identifying the frozen package source. Additional
`release_tooling_commit` and `recovery_of_workflow_run` fields distinguish the newer
recovery orchestration from that unchanged source; the checksum file covers the
updated manifest. Review downloads/install after attachment before sharing or
marking Latest. A staging success alone is not a public-download acceptance.

### 发布后恢复说明

`v0.5.1` 的 tag 与包源码保持不动。首次发布流水线在上传前失败，原因是 `uv build`
默认在输出目录生成 `.gitignore`，而发布打包器只允许准确的三个输入文件。修复仅让
构建使用 `--no-create-gitignore`，不放宽白名单，不删除未知文件，不移动 tag 或重建
Release。旧 tag 的流水线重跑不会自动采用 main 中的新编排。

专用恢复流水线从原固定提交构建，PR/main 推送只验证并保留五文件包。维护者审阅后，
在 main 上手动输入 `ATTACH v0.5.1` 才允许向已有 Release 首次上传；空输入不上传。
上传前复核本次包、源与编排身份、原 Release、tag 和完整附件列表，遇同名文件停止，
不覆盖或删除。manifest 分别记录原包源码与恢复编排提交，失败记录保留。真实公开
下载、校验和及安装仍在附件完成后验收；不改用户机器、隧道或账号授权。
