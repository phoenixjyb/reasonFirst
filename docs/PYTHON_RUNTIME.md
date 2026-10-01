# Explicit project Python on Windows and other hosts

[简体中文](PYTHON_RUNTIME_CN.md) · [Install](INSTALL.md) · [Troubleshooting](TROUBLESHOOTING.md)

A project can require `python3` while Windows only provides `python.exe`. A launcher
can also see a per-user installation in ordinary PowerShell but not in a worker's
execution context. Do not install another Python, relax a sandbox, change global
PATH, or edit the protected project contract merely to work around that difference.

## Approve once for the existing workspace

Use the intended project version, not automatically ReasonFirst's own tool Python.
For a dependency-free project that is meant to use an existing system Python 3.12:

```powershell
$workspace = Read-Host "Existing ReasonFirst workspace ID"
actual-coder python bind $workspace --command python3 --python 3.12
```

This asks uv to find an **already installed system interpreter**, offline, without
project discovery, config discovery, or Python downloads. It does not use `py`.
Review the displayed executable and workspace before confirming the bounded probe
and saving. A project with its own virtual environment should instead select it:

```powershell
$interpreter = Read-Host "Absolute path to this project's existing Python executable"
actual-coder python bind $workspace --command python3 --executable $interpreter
```

These are alternatives, not two commands to run in sequence. The command accepts
only the explicit `python` or `python3` mapping, which must already be on the user's
executable allowlist. It never authorizes arbitrary executable names from the repo.
Noninteractive callers need explicit `--yes`; an existing binding needs `--replace`
and a fresh approval. Without approval no binding is saved. Version discovery can
inspect installed interpreters; the selected executable identity probe runs only
following approval. All probing is local, bounded, and uses a fixed isolated Python
snippet without importing the project or site startup code.

A binding lives under the manager's `python-bindings` directory, outside the
worktree. It is bound to the workspace/project/base/instance/platform and stores
non-secret interpreter identity plus an executable fingerprint. It does not modify
TaskSpec, `.actualcoder.yaml`, the API-token file, Git settings, or Windows credentials.
Preserve venv invocation paths: resolving a python symlink to its base binary can
select the wrong environment. The binding detects binary, symlink-target, and
`pyvenv.cfg` changes before probing/using it again; approve changes explicitly.
The identity comparison accepts canonical parent-directory aliases (including
macOS temporary-directory aliases), but never treats different venvs as equivalent
merely because their Python symlinks share a binary. The approved invocation spelling
is retained, and retargeting its parent directory requires re-approval. Bindings
created by an earlier draft without the directory fingerprint also need re-approval.

## Validate the original pinned contract

```powershell
actual-coder python status $workspace
actual-coder validate $workspace --plan
actual-coder validate $workspace
```

Status/plan verifies approved Python identities and command availability; it does
not run project tests. Validation executes the **pinned base contract**, not a
potentially modified working-copy contract. For example the stored command remains
`["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]`, and only its first
argument resolves to the approved absolute executable. Output records both argv
lists, binding evidence, timeout, and actual child exit code. No configured commands
is not a test pass. Missing/invalid resolution stops before tests; required test
failures remain failures. This command never commits, pushes, or launches a worker,
but project tests can write files and the usual host-runner policy still applies.

For bound Python commands the runner removes inherited Python-home/path and launcher
identity overrides (`PYTHONEXECUTABLE` and `__PYVENV_LAUNCHER__`),
uses UTF-8 pipe output, and suppresses bytecode cache writes. It does not disable
assertions, skip tests, change arguments, or install dependencies. The shared finish
path uses the same resolution and records the binding in its reviewed snapshot;
replacing a binding invalidates that finish plan before publication.

## Worker handoff and evidence boundaries

Resume the **same workspace** after binding. CLI and local Bridge handoffs include
requested/resolved commands and the approved identity. The worker must probe that
executable in its existing sandbox; a successful host probe is not proof of worker
access. Stop rather than escalating or substituting another interpreter when access
fails. Older goals that explicitly demand another interpreter must be clarified by
the operator, not silently overwritten. No new MCP binding/permission tool is exposed.
SSH/container targets do not inherit this host's absolute interpreter path.

The deterministic command runner remains a structured **host runner, not a filesystem
sandbox**. Operator-owned files and hashes do not defend against malicious same-user
code, and hashing an executable/venv config does not attest to every installed package,
DLL, or site customization. Python identity probing does not validate dependencies.
Worker policy, network policy, project authorization, and publication approval remain
separate. Local runtime paths should be omitted from public issue reports.

While a tunnel uses the installed package, test a candidate with the reviewed
`uv tool run --isolated --from` wheel route rather than overwriting its environment.
That isolates the package, not the workspace: the binding deliberately persists for
the existing workspace so the approved interpreter can be reused on the next run.
