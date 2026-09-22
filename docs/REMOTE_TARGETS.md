# User-owned SSH execution targets

ReasonFirst treats an SSH execution destination as a **local trust grant**, not as a repository setting or an MCP/tool argument.

By default the registry is:

```text
~/.config/reasonfirst/targets.yaml
```

Override only with a trusted local path:

```bash
export REASONFIRST_TARGETS_FILE="$HOME/.config/reasonfirst/targets.yaml"
```

Example:

```yaml
version: 1
targets:
  gpu-a:
    type: ssh
    host: gpu-a
    repo: /srv/recomo/app
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects:
      - recomo/app
    ssh_connect_timeout: 8

  build-box:
    type: ssh
    host: build-box
    repo: /srv/cloud-pipeline
    workspace_root: /srv/reasonfirst/worktrees
    allowed_projects:
      - phoenixjyb/cloud-pipeline
```

On POSIX systems the file must not be writable by group or others. A typical setup is:

```bash
mkdir -p "$HOME/.config/reasonfirst"
chmod 700 "$HOME/.config/reasonfirst"
chmod 600 "$HOME/.config/reasonfirst/targets.yaml"
```

Inspect the effective non-secret grants without connecting to any host:

```bash
actual-coder targets
```

## Trust rule

An external reasoning client or MCP request may select a target by **name**:

```json
{"execution": "gpu-a"}
```

It must not be allowed to create a new trust destination by sending an inline host/repository object or shorthand such as:

```text
gpu-a:/srv/recomo/app
```

The bridge/executor must resolve the supplied name with `resolve_remote_target(..., project=...)`. That resolution also checks that the exact GitLab `group/project` is allowlisted for the target.

Repository-owned `.actualcoder.yaml` may narrow task behavior, but it must never add an SSH target or expand `allowed_projects`.

This registry only establishes **where** ReasonFirst is permitted to operate. It is not a process sandbox and does not itself authorize arbitrary remote shell commands or remote publication. Those require separate execution and finish policies.


## Remote validation execution

A configured SSH target does not authorize arbitrary shell execution.

The hardened remote execution primitive is `RemoteValidationRunner`. It accepts a **validation name**, resolves the exact argv from the already policy-checked `.actualcoder.yaml`, and sends that argv as JSON stdin to a fixed SSH-side Python runner.

For example, if the project contract contains:

```yaml
validation:
  commands:
    - name: unit
      argv: [pytest, -q]
```

the reasoning/coding worker may request `unit`; it may not replace that with `bash -lc ...`, `python -c ...`, or another arbitrary command.

The remote helper:

- verifies the managed worktree resolves under the target's configured `workspace_root`;
- verifies the path is the actual Git worktree root;
- executes the exact argv with `shell=False`;
- strips credential-shaped environment variables and SSH agent forwarding from the validation process;
- enforces the contract timeout and returns bounded stdout/stderr.

This is an **execution-policy boundary, not a container sandbox**. A validation command can execute repository code, and repository code should be treated as code the user chose to run on that target. Strong filesystem/network isolation, when required, should be supplied by a container/VM/OS sandbox rather than a command denylist.
