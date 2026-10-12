# Platform validation record

[简体中文](PLATFORM_VALIDATION_CN.md) · [Install & update](INSTALL.md) · [Public downloads](RELEASE_DISTRIBUTION.md)

**Updated: 2026-10-12.** ReasonFirst has real-machine validation evidence on
**macOS, native Windows, and Ubuntu/Linux**. The dates, installed sources, and
operations differ across those records. The published **v0.5.2** additionally
passed package/source installation and regression CI on all three platforms.

## Coverage at a glance

| Platform | Real-machine record | Verified scope | Remaining boundary |
| --- | --- | --- | --- |
| macOS | Full chat-based source rehearsal recorded on 2026-09-28; public v0.5.1 local activation on 2026-10-01 | Historical live read/worker/MR/matching-HEAD CI loop; separately, v0.5.1 activation, three local health samples, listener ownership, and read-only Bridge doctor | The local activation does not independently revalidate the remote connection. No fresh v0.5.2 installation or reboot test. See [macOS record](#macos-historical-deployment). |
| Windows 11 Pro x64 | 2026-09-30 read/reconnect and 2026-10-01 normal worker closeout; v0.5.1 candidates with recorded source/asset identities | Guided setup, selected Windows MCP read, managed Git clone/fetch, approved Python, initial worker handoff, 10 existing tests, clean local closeout | No new v0.5.2 field run. Healthy-runtime repair/no-op, controlled restart, reboot, and optional privileged Bridge/write remain in [#88](https://github.com/phoenixjyb/reasonFirst/issues/88). |
| Ubuntu 22.04.5 LTS x86_64 | 2026-10-12; published v0.5.2 wheel, Codex CLI 0.162.1 | Fresh package/setup, verified tunnel-client recovery, live Linux MCP read, managed Git/contract check, Codex read/test execution, 10 existing tests, unchanged tracked files | Automatic tunnel download needed recovery. No repository edit/publication, full-chat Bridge, restart/reboot, or live acceptance of later fixes was performed. |

The concise supported claim is: **“Tested on macOS, Windows and Linux; see the
dated platform record for the version and scope.”** It does not mean every
feature has been exercised on every platform with the same release.

## Evidence types and release identity

This record distinguishes three sources:

- **Live MCP calls:** the review conversation called the selected platform's
  connection and inspected its returned identity, access, and pinned-file data.
- **Operator-supplied terminal evidence:** the operator ran commands on their own
  computer and supplied the output, including the worker report. The reviewer
  did not have a remote shell on that computer.
- **Build/source evidence:** versioned source, public release assets, and GitHub
  Actions results. Synthetic CI does not authenticate a real provider account.

Private hostnames, project namespaces, account details, home paths, workspace
identifiers, tunnel IDs, and credentials are omitted from this public summary.
The opaque practice commit below records the common test base; it is not a
public repository or an instruction to grant access to another project.

The published [v0.5.2 release](https://github.com/phoenixjyb/reasonFirst/releases/tag/v0.5.2)
identifies source
`a4aa63b91109c6ddb8a721d93fe4b9be2543bc23`, tree
`acc799593658c4fcfc7f03531a20f5c77ca42ea4`. Its
[final-source CI](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598934)
passed the Ubuntu/macOS/Windows regression and package/source install jobs.
[Documentation](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598967),
[native tunnel boundaries](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598923),
and [candidate packaging](https://github.com/phoenixjyb/reasonFirst/actions/runs/38041598899)
also passed at that source. Principal CI interpreter coverage is Python 3.12.

The [release asset workflow](https://github.com/phoenixjyb/reasonFirst/actions/runs/38042829135)
succeeded on 2026-10-10. The release records verification of all five anonymous
downloads and isolated Linux/Python 3.12.14 installation outside the source
checkout. That package/inventory check did not authenticate GitLab, start a user
service, or run a coding worker. It is separate from the field rehearsal below.

The v0.5.2 tag and asset bytes remain unchanged. Later source fixes are described
under [Unreleased](../CHANGELOG.md#unreleased); this record does not claim that
those fixes were installed or retested on the user's computer.

## Ubuntu/Linux — 2026-10-12

### Environment and installed artifacts

The operator supplied these versions from the Linux computer:

| Component | Observed version or identity |
| --- | --- |
| OS / architecture | Ubuntu 22.04.5 LTS / x86_64 |
| Git | 2.34.1 |
| uv | 0.9.5 |
| System `python3` | 3.10.12 |
| ReasonFirst tool environment | Python 3.12.12, installed with `uv tool install --python 3.12` |
| ReasonFirst / ActualCoder | 0.5.2, published GitHub wheel |
| Installed MCP / Uvicorn | 2.3.0 / 0.54.0 |
| Codex CLI | 0.162.1; `codex login status` reported ChatGPT login |
| Official tunnel client | 0.0.16, upstream commit `5f99daabd4aa4a77049e6d81d54a0d8c18335397` |
| Mode / selected worker | `standard` / `codex-cli` |
| Practice base commit | `4561c76d57dda1d2fc6e7cd076b6d52638400a78` |

The published ReasonFirst wheel SHA-256 is
`fa23112b8003ce7ee46d072a7fa053b51dc5aaacf0fc9f6a72eb678f5fab1466`, as recorded in
the public release. The Linux transcript confirms installation from that
versioned wheel URL and the resulting 0.5.2 commands; it is not an additional
independent checksum inspection of the user's installed package files.

The official `tunnel-client-v0.0.16-linux-amd64.zip` archive was verified on the
Linux host with SHA-256
`d60cdba019bce451bcc3a15478cc5b9cb11270b049f5b56ea39a80b517f8b117`.
The downloaded `SHA256SUMS.txt` was also checked, with SHA-256
`fcce864a5935655534cc3d981257509579dbb5c71dbaec64c17a157ca9d65e56`.
See the [official upstream release](https://github.com/openai/tunnel-client/releases/tag/v0.0.16).

### Installation and tunnel recovery

The fresh wheel installation completed, and guided setup successfully resolved
the authorized synthetic practice project on `main`. The operator approved the
private configuration and exact single-project grant. Setup selected standard
mode and Codex CLI; ChatGPT tunnel configuration was completed separately.

The first `reasonfirst tunnel connect --install` attempt failed with
`SSL: UNEXPECTED_EOF_WHILE_READING` in the Python download path. The original
message did not identify which download failed, so the transcript does not
establish a specific failing URL, proxy, or certificate cause.

Recovery used `curl` with HTTPS verification enabled to fetch the pinned official
v0.0.16 checksum file and Linux archive. Both hashes were checked, and the
existing packaged installer installed the complete companion bundle through
its injectable download function. Its binary version probe succeeded. The
subsequent ordinary `reasonfirst tunnel connect` discovered that installation
and reported the runtime healthy and ready. The runtime key was entered through
the masked prompt and was not persisted by ReasonFirst.

**The fresh installation succeeded after this verified recovery.** This is not a
claim that v0.5.2's automatic tunnel download completed without intervention.
The download recovery and the diagnostic corrections are explained in
[troubleshooting](TROUBLESHOOTING.md).

### Read access and managed Git

The assistant used the selected **Linux test read connection** for
`gitlab_whoami`, `check_project_access`, and `get_file`. Authentication and the
authorized project's `main` preflight passed. `README.md` was then read at the
newly returned full commit SHA above, rather than at moving `main`. The returned
file revision matched the pinned ref, and the complete 2,488-byte UTF-8 README
was returned without truncation. The previous Mac and Windows connectors were
not used as substitutes.

Separately, the operator ran `actual-coder project-config` with that exact SHA
and `--validate`. The native Git bare clone and fetch completed; the project
contract returned `found: true`, `valid: true`, and empty errors/warnings. Its
required validation was `python3 -m unittest discover -s tests -v`, with a
120-second contract timeout. This established native Git read/authentication and
contract validity, in addition to the MCP's GitLab API evidence.

### Doctor and worker execution

`actual-coder doctor` returned `ok: true`, with **19 passes, two skips, one
warning, and zero failures**. The warning was the absent optional Copilot CLI.
The skips were the old doctor's PATH-only tunnel-client lookup and the absence
of a runtime key in the parent shell. The versioned tunnel bundle and healthy
runtime had already been observed separately; the key had been supplied only
to the tunnel child environment. These skips did not require reinstalling the
client or exporting a key just to change the doctor summary.

The operator then used `actual-coder start` with an explicit `codex-cli` backend,
the same pinned base, and a read/test-only goal. It created one isolated managed
workspace, persisted the task, and launched the ordinary initial handoff. The
worker was asked to read the existing instructions and exercise files, run the
existing suite once, and report without repairing or implementing another stage.

The supplied worker report records:

```text
python3 -m unittest discover -s tests -v
Ran 10 tests
OK
exit code: 0
```

The operator-supplied worker report stated “Final Git status: clean; no tracked
or untracked changes” and “Files modified: none”, with HEAD equal to the pinned
base. This is worker-reported file-state evidence; the reviewer did not
independently run Git status on the Linux host.
After the operator exited the interactive worker, ActualCoder reported
`launch.returncode: 0`. The test report and the enclosing CLI exit are distinct
observations; the outer zero alone would not establish that the tests passed.
Doctor's Python version identifies the ReasonFirst tool environment, not
automatically the interpreter used by the project's `python3` command.

Requested Codex policy was `gpt-5.6-sol`, effort `high`, `workspace-write`,
approval `on-request`, and sandbox network access `false`. The launch evidence
reported `catalog_verified_launch_arguments_encoded` and
`runtime_verified: false`. It confirms accepted catalog/policy inputs and
explicit launch arguments; it does not independently attest to the provider's
resolved model or enforcement of the sandbox/network boundary.

### What this Linux run did not exercise

The task did not edit source, tests, or documentation; create local commits;
push a branch; create or merge an MR; deploy; run the optional full-chat Bridge;
or restart/reboot the host. Those operations are outside this run's acceptance.
No later downloader/doctor code fix was installed on the Linux host as part of
this record. No completion-time measurement supports a “few-minute setup” claim.

## Windows — 2026-09-30 to 2026-10-01

The original host was Windows 11 Pro x64 with PowerShell 5.1, uv 0.12.21, and an
isolated Python 3.12.10 tool environment. The recovery/read checkpoint used the
v0.5.1 candidate source `235ddc81cf12341266d14e68ae94d0729bf0e462`.

The [live-read checkpoint](https://github.com/phoenixjyb/reasonFirst/pull/90#issuecomment-5914490847)
records a successful reconnect and independently repeated `whoami`, project
preflight, and pinned README read through the selected Windows connector. The
[fresh-terminal checkpoint](https://github.com/phoenixjyb/reasonFirst/pull/90#issuecomment-5914743199)
records valid persisted setup and healthy runtime status with `PYTHONUTF8`
absent. It inspected an already-running runtime, not a new launch without that
variable.

The [normal worker/host closeout](https://github.com/phoenixjyb/reasonFirst/pull/94#issuecomment-5925996668)
on 2026-10-01 used the integrated v0.5.1 wheel whose SHA-256 was
`12d5663c44a68a7b417e510028f4780dd363feaf590a609d46de8b19fd21a7e6`.
The [integration record](https://github.com/phoenixjyb/reasonFirst/pull/94#issuecomment-5924958060)
establishes its byte identity with the final source at
`9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b`.

The ordinary initial handoff carried the approved CPython 3.12.10 binding and
worker execution recipe without a corrective prompt. All 10 existing tests
passed; child return code and supervisor exit were zero, output was complete,
and no timeout occurred under the 120-second recipe. The host closeout reported
the original branch, HEAD equal to base, `dirty: false`, and zero local commits
ahead. The local `pushed: false` field was not an independent remote-branch audit.

This operator-supplied worker evidence remains separate from the earlier host
validator and failed diagnostic attempts. The subsequent v0.5.1 publication
does not change its original date or make it a v0.5.2 field run. Outstanding
repair/no-op, controlled restart, reboot, and privileged Bridge/write checks
remain explicitly tracked in [#88](https://github.com/phoenixjyb/reasonFirst/issues/88).

## macOS historical deployment

### Chat-based workflow recorded on 2026-09-28

The [2026-09-28 release checklist record](PUBLIC_RELEASE_CHECKLIST.md) and
[chat-only rehearsal guide](CHAT_ONLY_PRACTICE.md) record a real synthetic
workflow through managed worker execution, reviewed snapshot approval, GitLab
MR publication, successful matching-HEAD unit-test CI, and EvidencePack review.
The macOS launchd proxy inheritance finding from that run is documented in the
rehearsal and troubleshooting guides.

That is historical source-deployment evidence for the full chat-based workflow.
The cited public summary does not establish the exact installed wheel or OS
version for that earlier run.

### v0.5.1 local activation on 2026-10-01

The maintainer's retained deployment record, reviewed on 2026-10-09, identifies a
uv-managed public **0.5.1** CLI and reuse of the existing configuration. During an
approved idle maintenance window, only the saved launch arguments were updated;
the existing working directory was retained. The activation completed without
rollback. Three consecutive local health samples reported version **0.5.1**,
and the local listener ownership check passed. A subsequent read-only Bridge
doctor reported **0.5.1 / Python 3.12.12**.

This is a dated maintainer record of local activation and health, not a new
independent remote-connection test or a published raw host transcript. It does
not by itself establish every loaded module's identity or a new full-chat run
under that package. Private launcher paths and other machine details are omitted.

Current v0.5.2 macOS package/source CI, the earlier provider-connected workflow,
and the v0.5.1 local activation are separate evidence. A fresh v0.5.2 Mac
installation or reboot rehearsal would need its own dated result.
