# Versioned public downloads

[Install](INSTALL.md) · [Maintainer checklist](PUBLIC_RELEASE_CHECKLIST.md) · [简体中文](#简体中文)

## What users receive

The intended stable release is **v0.5.1**. Only advertise its download once the
maintainer has published the Release, the asset workflow has succeeded, and the
actual public download/install has been checked. A merged PR or an expiring Actions
candidate is not a public release. Keep source installation supported.

The Release asset set contains:

- `chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl` — shared Python package;
- `chatgpt_selfhosted_gitlab_mcp-0.5.1.tar.gz` — built source distribution;
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
Get-FileHash -Algorithm SHA256 .\chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
uv tool install .\chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl
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
2. Confirm package/runtime metadata is 0.5.1, approve tag `v0.5.1` on that exact SHA,
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

The Windows rehearsal separately verified setup/read access, managed clone/fetch,
the approved-interpreter host validation, and a worker retry followed by clean
closeout. The newly integrated initial-handoff recipe still needs its live
acceptance; CI shell execution is separate evidence. Restart/reboot recovery and
new Windows privileged Bridge/write acceptance remain unverified at this preparation
checkpoint. Do not mark them passed to clear a release checklist. Record the actual
status and advertised support level in the final release decision; do not silently
turn a stable release request into a preview or remove existing safety checks.

## 简体中文

目标是 **v0.5.1 正式版**，不是改名的候选包。正常公开分发使用版本化 GitHub
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
不会因为磁盘上安装了新包而自动更新。已完成的 Windows 宿主/worker 验收与尚未
完成的新 handoff 现场验收、重启恢复、Windows 全聊天写入验收分别记录，不夸大。
