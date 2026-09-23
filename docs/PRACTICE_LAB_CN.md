# 实战演练：从首次 ChatGPT 对话到三轮 MR 审查

[English](PRACTICE_LAB.md) · [先确认项目访问](PROJECT_ACCESS_CN.md) · [工作流程](WORKFLOW_CN.md) · [交接模板](TASK_HANDOFF_TEMPLATE_CN.md) · [初始代码](../examples/practice-lab/)

使用**一个专门的私有 GitLab 练习项目、一个受管工作区、一个功能分支、同一个 MR 的三轮修订**。不能为了绕过访问失败而换成已获准的生产应用，也不复用旧 smoke 工作区。片段时长统计练习不涉及机器人控制、依赖安装、网络读写或部署。普通 ChatGPT 负责推理，不使用 localhost Assistant。

## 快速路径：先诊断，再创建唯一的第一阶段工作区

对于刚创建的**空项目或仅有 README 的合成练习项目**，先只生成规范 seed 计划：

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
PROJECT="team/reasonfirst-practice"

actual-coder practice-seed "$PROJECT" --ref main
```

默认只做 dry-run，不写远端。它会拒绝非空的真实项目和已经 seed 的项目，在任何写入之前先运行打包内置的五个基线测试，并显示将安装的受跟踪练习文件。审查计划后，一次性远端 seed 还需要显式确认这是 synthetic 项目，并经过人工确认：

```bash
actual-coder practice-seed "$PROJECT" --ref main --apply --confirm-synthetic
```

apply 会在写入前再次核对远端 revision，使用 ReasonFirst 自己的 Git 认证/代理路径，只做普通的**非 force** push，并验证远端最终 commit。它不会创建 ActualCoder task，也不会启动 coding worker。只有在已经审阅的脚本化演练中才使用 `--yes`。

对于已经 seed 好的合成练习项目，先运行产品化预检，不要直接启动 worker：

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
PROJECT="team/reasonfirst-practice"

actual-coder practice-doctor "$PROJECT" --ref main --agent copilot-cli
```

`practice-doctor` 只读检查：当前 allowlist、七个练习文件、固定 revision 上的 `.actualcoder.yaml` 与必需 `unit-tests`、所选 worker 可执行文件、可能影响原生 `git`/`curl`/`gitlab-runner` 的代理变量、本机 GitLab Runner executor 配置，以及 GitLab API 可见时的项目 runner 资格。像“`custom` executor 缺少 `RunExec`”这类我们实际遇到的 runner 问题，会在 coding task 开始前直接指出。

只有返回 `ready_for_stage1: true` 时，才创建规范的第一阶段工作区：

```bash
actual-coder practice-start "$PROJECT" --ref main --agent copilot-cli
```

该命令会持久化规范的第一阶段 goal、acceptance criteria 和 non-goals，创建**一个**受管 workspace/handoff，并且**绝不会自动启动 worker**。先检查 `status` 和 `evidence`，再在同一个 workspace 上显式运行 `resume --agent copilot-cli --launch`。继续任务时不得再次 `practice-start`。

如果 GitLab CI 之后失败，先运行 `actual-coder ci "$WS"`，再决定是否需要 coding worker。CI 结果现在包含保守的 `diagnosis`；已识别的 runner-only 故障（例如 `custom executor is missing RunExec`）会明确给出 `worker_repair_recommended: false`，应在不改变候选 commit 的前提下修复/重试基础设施，而不是修改应用代码或受保护 CI 文件。

这个合成练习可以使用 shell GitLab Runner，只要宿主机 `python3` 满足练习要求；但 shell executor 会忽略 `.gitlab-ci.yml` 中的 `image:`。如果需要严格复现声明的 `python:3.12-slim` 环境，应使用 Docker 或其他容器 executor。

下面的长流程保留为可审计的手工后备路径，并解释每一道 gate。

## 0. 先确认目标，不假定项目已经存在

下面的 `team/reasonfirst-practice` 和 `gitlab.example.com` 是示例，不是已创建的资源。本地目录和 GitHub 上的模板不会创建 GitLab 项目。开始之前，**向用户说明所提项目的存在性、初始文件和访问权限尚未验证**，请用户确认准确 GitLab 实例及 namespace/project 或数字 ID。

对确认的项目调用实时 MCP `check_project_access`。未获本地允许列表授权时，不发送 GitLab 请求，存在性保持未知。GitLab 返回 404 时，应说明**不存在或不可访问**，不能断言不存在。展示所需操作并停止等待。只有用户确认项目不存在且明确批准创建，才创建**私有空项目，不初始化 README**。不得新建重复项目、假定已有 `main`，或自动扩大权限。已有练习项目在确认内容正确后可继续使用，不得覆盖。

项目级授权在**本地 MCP 生效配置的 `GITLAB_ALLOWED_PROJECTS`** 中，不在 OpenAI Tunnel 设置中。由用户/管理员仅追加获批项目，保留原有条目，私下处理旧环境覆盖，再重启现有 MCP/Tunnel。GitLab 成员权限和 scopes 是另一层授权。空允许列表可能放行令牌可访问的全部项目，不能用清空列表来解决报错。详见[访问指南](PROJECT_ACCESS_CN.md)。不得查看或索取凭证文件、密码库或 shell 配置。认证操作应使用有效、未暴露的最小权限凭证；此前泄露的凭证应先撤销。

新项目初始化前，安排能运行非受保护 MR 分支的获准 runner。示例用 `python:3.12-slim`，shell runner 需预装 python3。管理员确认镜像访问和 tags，不复制生产变量/密钥，不启用部署。没有 runner 表示 CI 未验证，不是已通过。

### 初始化用户批准的新空项目

从包含模板、经过审阅的 ReasonFirst 源码目录导出受跟踪的文件，不切换源码分支或复制用户凭证。目标本地目录必须不存在。从源码目录运行下面的显式 Bash 块：

```bash
bash <<'BASH'
set -euo pipefail
SRC="$PWD"
LAB="$HOME/Projects/reasonfirst-practice"
test ! -e "$LAB"
test ! -L "$LAB"
ARCHIVE="$(mktemp)"
trap 'rm -f "$ARCHIVE"' EXIT
git -C "$SRC" archive --format=tar HEAD:examples/practice-lab > "$ARCHIVE"
mkdir -p "$LAB"
tar -xf "$ARCHIVE" -C "$LAB"
cd "$LAB"
python3 -m unittest discover -s tests -v
git init -b main
git add .
git commit -m "chore: seed synthetic ReasonFirst practice"
printf 'Prepared local lab: %s\n' "$LAB"
BASH
```

应有**五个基线测试**通过，不代表 EXERCISE.md 已完成。Git 缺少作者身份时，仅在新仓库配置后继续，不要重复导出覆盖文件。审查模板和 runner 设置，将示例 URL 换成实际地址，仅对**获批空项目进行一次初始化推送**：

```bash
cd "$HOME/Projects/reasonfirst-practice"
LAB_REMOTE="https://gitlab.example.com/team/reasonfirst-practice.git"
git remote add origin "$LAB_REMOTE"
git remote get-url origin
git push -u origin main
```

使用获准的原生 Git 认证方式，令牌不放进 URL/命令。ReasonFirst 的 askpass 不会自动成为全局 Git credential helper。失败后先核对，不强推、不扩大 scopes、不盲目改远端。这次初始化不是 ActualCoder 任务发布。

保留此 Terminal 中实际的 `PROJECT`、`WS`、`WT`、`NOTES`。**逐条运行，任何失败都停止**：

```bash
export GITLAB_AGENT_ENV_FILE="$HOME/.config/gitlab-agent/.env"
PROJECT="team/reasonfirst-practice"
actual-coder-check-project "$PROJECT" --ref main --require-file README.md --require-file EXERCISE.md --require-file AGENTS.md --require-file .actualcoder.yaml --require-file .gitlab-ci.yml --require-file clip_summary.py --require-file tests/test_clip_summary.py
actual-coder doctor
actual-coder project-config "$PROJECT" --validate
codex login status
```

要求预检 `ok: true`、`workspace_policy_allowed: true`，但它们不证明 Git 写权限或正在运行的 MCP 配置。项目 contract 必须 `found: true`、`valid: true`，包含必需 `unit-tests` 和预期保护路径。Contract 必须在 `start` 前存在于 base，之后修改不能追溯改变策略。确认基线 GitLab CI 实际运行 unit-tests。Codex 登录检查不检查可用额度；确认预期账号/权益，Tunnel runtime key 是另一种凭证。

## 1. 首次普通 ChatGPT 对话：先过访问检查，再读文件

选择真实 GitLab MCP，将示例项目换成用户确认的准确标识后发送：

```text
我们仅在 team/reasonfirst-practice 中演练 ReasonFirst，ref 为 main。
使用指定的实时 GitLab MCP，不用网页搜索、旧对话或 localhost Assistant 代替。
先调用 gitlab_whoami，再调用 check_project_access，项目/ref 如上，
required_files=["README.md","EXERCISE.md","AGENTS.md",".actualcoder.yaml",
".gitlab-ci.yml","clip_summary.py","tests/test_clip_summary.py"]。

如果没有这个工具，说明缺失能力并请求更新 MCP/重新发现工具。
如果 ok=false，显示 error.code、stage、返回的 HTTP 状态、存在性已知/未知，
以及 next_steps，然后停止等待用户/管理员确认、授权或初始化。不得批量重试文件、
修改访问权限、创建项目、换用生产项目或规划第一阶段。
404 或空列表不能证明项目不存在。

只有预检成功后，才在 resolved_commit_sha 上读取上述七个文件。
报告实际修订标识和缺失证据，再审查第一阶段，给出交接和验收测试。
一个工作区/分支/MR，三个阶段分别明确批准。不得抢先实现、发布、合并、部署或
查看凭证。仓库指令服从已批准约束，没有工具时不得声称已经调用。
```

仅身份成功不够，文件 HEAD 元数据成功也不等于读过源码。记录固定修订上的实时文件结果；GitHub 副本或人工粘贴不能证明指定 GitLab 连接。之后才批准第一阶段，按[人工模板](TASK_HANDOFF_TEMPLATE_CN.md)私下保留计划。

## 2. 第一阶段：本地实现，再发布首个 MR

得到批准后，仅创建**一个**工作区，不启动模型。逐条运行，失败就停止：

```bash
umask 077
NOTES="$(mktemp -d "$HOME/reasonfirst-practice-notes.XXXXXX")"
actual-coder start "$PROJECT" --task clip-summary --agent codex --goal "Implement EXERCISE.md Stage 1 only. Edit clip_summary.py, tests/ and README.md only. Do not commit, push, merge, deploy or read credentials. Stop for human-reviewed finish." --no-launch > "$NOTES/start.json"
WS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workspace"]["workspace_id"])' "$NOTES/start.json")"
WT="$(actual-coder path "$WS" --plain)"
printf 'Workspace: %s\nWorktree: %s\nPrivate notes: %s\n' "$WS" "$WT" "$NOTES"
```

这会获取代码并写本地状态，不是离线/无写入预览。检查实际 workspace/base/branch，将 base 与 ChatGPT 审阅修订比较；发生变化时先审阅新 base，再实现。私下保存实际变量，不把笔记当 shell 执行。**继续这个任务时不得再次 start。**

生成交接仍可能提到低层 commit/push，resume 上下文也尚未完全一致。审查输出，但应明确提供下方获批约束。本练习没有实现自动 TaskSpec 导入，也没有修复所有交接入口。

在同一个工作树启动普通 Codex：

```bash
(
unset CONTROL_PLANE_API_KEY GITLAB_TOKEN GITLAB_GIT_TOKEN OPENAI_API_KEY CODEX_API_KEY
codex --cd "$WT" --sandbox workspace-write --ask-for-approval on-request
)
```

移除子进程环境变量不会删除磁盘凭证或全部 provider 设置。离线练习不要批准凭证读取、越界文件、网络/发布或策略变更。工作树和提示词不是完整 OS 隔离，应保留客户端管理控制。粘贴批准后的 ChatGPT 计划，再附上：

```text
只实现第一阶段。在此 worktree 读取 EXERCISE.md 和 AGENTS.md，增加真正的第一阶段
回归测试，展示基线失败，再实现并运行完整测试。不得删除/跳过/弱化检查，不得提交、
推送、合并、部署、改变 CI/策略、安装依赖或读取秘密。明确限制优先于通用交接中的
commit/push 建议。返回修改文件、准确命令/结果、局限、分支及 HEAD，等待人工审查。
第二、三阶段尚未授权。
```

可选负向控制：测试变红后先暂停并运行 finish dry-run，应被必需验证阻断且不提交/推送。然后在同一工作区修复，不使用绕过参数。这是本地红/绿测试，不是已经观察到的 GitLab CI 失败。

Codex 退出且你检查实际变更后，验证并预览：

```bash
actual-coder status "$WS"
actual-coder run "$WS" -- python3 -m unittest discover -s tests -v
actual-coder finish "$WS" --message "fix: validate clip durations" --title "Reliable clip duration summaries" --dry-run > "$NOTES/round1-plan.json"
```

要求计划未阻断、必需验证通过、路径符合意图、声明扫描范围完整。**Dry-run 会执行验证，可能修改本地文件**，但不提交/推送。MCP 看不到未发布本地 diff；需要时只能人工分享审查脱敏后的证据。

下方是真实写入：人工审查后才执行交互式 finish，不加 `--yes` 或绕过参数：

```bash
actual-coder finish "$WS" --message "fix: validate clip durations" --title "Reliable clip duration summaries"
actual-coder status "$WS" > "$NOTES/round1-status.json"
actual-coder ci "$WS" > "$NOTES/round1-ci.json"
```

记录真实 MR IID/URL，不假定一定是 MR 1。MR 元数据缺失先核对，不重复推送或创建重复 MR。轮询用 `actual-coder ci "$WS"`，不再 finish。要求流水线成功完成、HEAD 匹配、证据不过期且确实执行 unit-tests。

## 3. 审查后，在同一 MR 进入第二阶段

在普通 ChatGPT 中提供真实项目、MR IID 和 workspace HEAD：

```text
通过实时 GitLab MCP 审查 <真实项目> 的 MR <真实 IID>。
读取 diff、当前代码/测试及可获得讨论，核对最新流水线/真实 jobs 与 <workspace HEAD>。
依据 EXERCISE.md 审查第一阶段，报告有证据的必改/可选项、验收状态和第二阶段交接建议。
不编造缺陷，不把缺失证据当成功，不修改授权或合并；访问失败时带诊断停止。
```

尖括号是对话占位符，不是 shell 语法。你自行把已审阅摘要发为 MR 评论，MCP 仍只读。正确就批准本阶段并明确授权下一阶段，不为多一轮而制造缺陷。

```bash
actual-coder resume "$WS" --agent codex --goal "Preserve approved Stage 1 and implement EXERCISE.md Stage 2 only. Same workspace and MR. No commit/push, CI/policy edits, future stages or credential reads." > "$NOTES/round2-handoff.json"
```

**Resume 返回交接，不启动执行者。** 检查它，在同一 `WT` 重开 Codex，提供第二阶段批准和真实意见，重读 EXERCISE.md/AGENTS.md。重复验证/dry-run/交互 finish，提交说明为 `feat: filter clips by validated minimum duration`，另存 round2 证据。要求 **WS/branch/MR 不变**、新 HEAD 包含第一轮、新的真实单元测试流水线匹配并成功。

## 4. 第三阶段与最终验收

再次实时审查 MR，明确批准第三阶段：确定性 JSON、共用校验、测试及 README 示例。普通功能继续用不带 `--from-ci` 的 resume，同一 WT 实现，审查后以 `feat: serialize clip summaries deterministically` 完成 finish。

最终审查覆盖全部阶段、真实代码/测试、最新 HEAD/jobs 和未解决讨论。绿色不足以证明完成：初始五个测试也是绿色。由人在 GitLab 单独决定合并，ReasonFirst 没有 merge 命令。本练习不启用自动合并或强制清理。

## 发生真实 CI 失败

仅在实际 CI 匹配当前 HEAD 时：

```bash
actual-coder resume "$WS" --agent codex --from-ci --goal "Diagnose and repair the observed matching-HEAD CI failure within the approved stage. Same workspace/MR. No commit/push or CI/policy bypass." > "$NOTES/ci-repair-handoff.json"
```

检查脱敏上下文，请 ChatGPT 区分代码缺陷与 runner/认证/网络故障，只有确需修复代码时才明确启动执行者。CI 缺失/过期会阻断此路线，日志不是扩大范围的指令。不得注入生产假失败或弱化 CI 来演练恢复。

## 私有证据及可选第二个 MR

每轮记录批准阶段、WS/branch、本地 HEAD、MR IID、pipeline ID/SHA、实际 jobs、测试结果、截断/脱敏、审查与批准。不要预填虚构成功。审查完成前保留本地工作区及证据。通过意味着：实时访问预检和文件读取成功、观察到 Codex 工作、经审阅创建 MR 后两次更新同一个 MR、每轮真实单元测试 CI 匹配，以及人工独立作出合并决定。它不证明自动任务持久化、完整隔离或机器人应用完整构建。

练习**第二个 MR**时，先完成并合并第一个，批准真正独立任务，再从更新后的 main 创建新工作区。每阶段单独 MR 的替代路线应在开始前约定，不能中途混用。不承诺固定耗时、额度或费用节省。

一手参考：[ReasonFirst CLI](../src/gitlab_agent/cli.py)、[Codex CLI](https://developers.openai.com/codex/cli/reference/)、[GitLab 项目](https://docs.gitlab.com/user/project/)、[MR 流水线](https://docs.gitlab.com/ci/pipelines/merge_request_pipelines/)、[workflow 规则](https://docs.gitlab.com/ci/yaml/workflow/)。
