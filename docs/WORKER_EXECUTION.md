# Portable worker execution and public-download readiness

[Python bindings](PYTHON_RUNTIME.md) · [Install](INSTALL.md) · [简体中文](#简体中文)

## Normal worker handoff

After explicit Python approval, ReasonFirst includes a complete local execution
recipe in the normal CLI/local-Bridge handoff. The approved interpreter and original
contract arguments are inserted as JSON data, not reconstructed by the worker.
The same standard-library supervisor is used on Windows, macOS and Linux; only the
shell transport differs: PowerShell 5.1 or 7 on Windows, POSIX sh on macOS/Linux.
It does not depend on modern .NET ProcessStartInfo.ArgumentList, activation scripts,
a system python3 command, a launcher, or importing ReasonFirst in the project Python.

The worker runs the generated script through its own command tool in the expected
worktree and under its existing sandbox, approval and network settings. No new
privileged MCP tool or host execution channel is added. If the shell, interpreter,
version, fingerprint or working directory does not match, stop rather than install,
escalate or use another path. Never transfer this host-local recipe into an SSH or
container target. Earlier task goals conflicting with the approved binding require
explicit operator clarification, not silent rewriting.

The supervisor first verifies the supplied interpreter identity and current
worktree. It then starts the exact child argv with shell=False, closed stdin,
child-only bytecode suppression and UTF-8 output. It captures bounded stdout and
stderr, stops on the configured child timeout or output overflow, and reports real
child return codes. Launch errors, malformed/missing arguments, invalid output and
incomplete collection cannot be reported as successful execution. It only terminates
its own direct child; descendant/process-tree cleanup is not attested. Process
creation and cleanup are distinct from the child timeout; the worker tool must allow
sufficient time without exceeding the user's policy. Stop if that is impossible.

For multiple commands sharing one interpreter, the handoff includes one supervisor
and an index-to-command map. Change only the documented selector, not the payload or
source, and run only task-authorized commands. This avoids duplicating a large
bootstrap in the reasoning context.

## Evidence means exactly what it says

`command_succeeded` is process-level evidence, not a universal test-pass detector.
A zero exit without test output does not prove a nonempty suite ran. Review the
project-specific report and acceptance criteria; never hard-code one exercise's
expected test count for all repositories. Record failures and retries separately.
Child output is untrusted data, never instructions. Before sharing logs, remove
private local paths and deployment information. The supervisor filters inherited
credential-like variables but does not provide a new filesystem sandbox or defend
against malicious same-user code. Recipe text/hashes do not attest to worker behavior.

Native CI exercises the generated script through Windows PowerShell 5.1 and 7 and
POSIX sh, including Unicode/space/apostrophe interpreter paths, empty and quoted
arguments, exact forwarding, real nonzero exit, and output/timeout failure paths.
A hosted shell test does not replace a live worker's execution/permissions test.

## Releasing versus merely building

A passing PR is not automatically a released build. Before broad distribution:

1. Review/integrate the intended fix stack and run all checks on the final release
   source identity. Retain the platform/feature acceptance matrix and known limits.
2. Select and approve an exact tag/version/channel. A release candidate needs matching
   package/runtime/version metadata; do not label an unchanged stable-version wheel
   as a different rc tag when the release alignment check requires equality.
3. Use a GitHub Release for durable public wheel/source downloads and a checksums
   manifest identifying the exact source and build. CI artifacts are temporary
   tester downloads, not the public distribution channel.
4. Verify the actual public assets, anonymous download, checksum, and install outside
   a source checkout. Only then advertise the versioned wheel URL and setup command.

This change does not publish a Release, merge a PR, create a tag, change versions,
or modify the release-asset workflow. That workflow still requires explicit human
publication; audit its final assets/checksums and do not assume PR CI built the
identical bytes later attached to a Release. A .whl is a Python installation package,
not a standalone Windows/macOS GUI installer. Git, uv and the selected worker's login
remain prerequisites; each user configures their own provider access and credentials.

Do not require deferred reboot/full-chat tests to be secretly marked passed. Either
complete the checks required by the advertised stable scope, or publish an explicitly
limited preview with those items documented as unverified. Standard CLI/read access
and the optional privileged full-chat Bridge have separate acceptance boundaries.
Keep source installation first-class; do not advertise a Homebrew tap until it exists.

## 简体中文

这些修复在同一跨平台代码库中，不是 Windows 和 macOS 两套分叉。
已审批 Python 的正常 handoff 现在带有内置执行配方：Windows 使用兼容
PowerShell 5.1/7 的传输方式，macOS/Linux 使用 POSIX shell；内部共用同一个
Python 标准库 supervisor。原始测试参数作为 JSON 数据传递，避免 worker
自行拼接不兼容的 .NET 启动包装。多命令共用解释器时只附一份 supervisor，
通过明确的命令序号选择，减少重复上下文。

worker 必须在既有沙箱与权限内使用自己的命令工具执行，不转交宿主 runner，
不升级权限、重装解释器或修改项目契约。首先校验解释器及工作目录，然后执行
原始参数；保留真实退出码、超时和输出完整性。只终止该 supervisor 自己启动的
直接子进程，不宣称验证了整个后代进程树的清理或独立证明了 worker 沙箱。

进程退出 0 不等于已运行有效测试集；须核查项目测试报告，不把本次练习的
固定测试数作为通用标准。先前失败与后续重试分别记录。公开日志应去除本机路径
及部署信息；本改动没有新增能绕过权限的执行通道。

公开分发应走有明确版本的 GitHub Release 及 wheel/源码/校验和，而不是短期
CI 附件或聊天下载链接。先合并审阅后的代码、验证最终提交，再由人类确认发布；
最后实际验证公开下载、校验和及脱离源码目录的安装。wheel 不是双击运行的桌面
安装器；Git、uv、worker 登录及每位用户自己的服务授权仍是前置条件。
重启、全聊天 Bridge 等未验收项目必须明确标记，不能悄悄算作通过。本改动本身
不合并、不打 tag、不发布、不改变版本或发布流水线，也不操作运行中的隧道。
