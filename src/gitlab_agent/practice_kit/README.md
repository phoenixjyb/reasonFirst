# Clip summary practice / 片段时长统计演练

This is a deliberately small teaching baseline for ReasonFirst, not production media or robot-control software. Five baseline tests pass, but input validation, filtering and JSON output are incomplete by design. Do not mistake a green baseline pipeline for completion of EXERCISE.md.

这是 ReasonFirst 的教学起点，不是生产影像或机器人控制代码。初始五个测试应当通过；严格输入校验、过滤与 JSON 输出留给演练完成。基线流水线绿色不代表完整需求已满足。

## Run / 运行

Python 3.10+; Python 3.12 matches the sample container. No third-party package is needed.

```bash
python3 -m unittest discover -s tests -v
```

Read [EXERCISE.md](EXERCISE.md) for the staged requirements. A human must approve each stage; the coding agent edits code/tests but does not commit or publish. See [AGENTS.md](AGENTS.md).

按 [EXERCISE.md](EXERCISE.md) 分阶段推进。每阶段由人工批准，编程代理只修改代码与测试，不提交或推送。约束见 [AGENTS.md](AGENTS.md)。

The sample GitLab pipeline runs real unit tests on branch/MR pipelines. It needs an authorized runner able to use the Python image (or an approved shell runner with python3). It does not deploy or require model/GitLab API credentials inside jobs. Adapt runner tags/image before seeding the base, with an administrator, never as a failing-task workaround.

示例 CI 运行真实单元测试，需要可用且获准的 runner。镜像或标签应在初始化基线之前按管理员要求配置；不能为绕过任务失败而修改 CI。演练 job 不需要生产密钥，也没有部署步骤。

Upstream practice guides: [English](https://github.com/phoenixjyb/reasonFirst/blob/main/docs/PRACTICE_LAB.md) / [中文](https://github.com/phoenixjyb/reasonFirst/blob/main/docs/PRACTICE_LAB_CN.md). While the kit PR is unmerged, use the corresponding guide on its branch rather than expecting these main links to exist.
