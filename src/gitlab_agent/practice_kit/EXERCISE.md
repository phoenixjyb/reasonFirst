# Reliable clip-duration summary / 可靠的片段时长统计

One task, one workspace, one feature branch and one MR, with three staged review rounds. The seed is intentionally incomplete. Do not invent failures or implement future stages before human approval. Requirements below are fixed before the first coding round; adding a new stage is explicit scope approval, not a reviewer secretly moving the goalposts.

一个任务、一个工作区、一个功能分支、一个 MR，分三轮审查。初始实现刻意不完整。不得编造失败或抢先实现后续阶段；下列标准在第一轮之前已公开，后续阶段须人工批准。

## Stage 1: strict summary / 第一阶段：严格统计

Keep `summarize_clips(durations)` and the keys `count`, `total_seconds`, `mean_seconds`. Accept a finite iterable (including a one-shot generator) of Python int/float values, excluding bool. Validate each value: nonnegative, finite, representable as a float. Invalid items, an unrepresentable number or a nonfinite aggregate must raise ValueError. Do not coerce numeric strings or silently drop invalid items. No input mutation; consume a one-shot iterable only once.

Empty input returns count 0, total_seconds 0.0, mean_seconds 0.0. Nonempty output has integer count and float total/mean. Ordinary examples must remain compatible. A non-iterable input may raise TypeError. Iterable size is bounded by the caller; streaming memory optimizations are not required.

保持函数名和三个字段不变。支持有限可迭代输入及一次性生成器；元素仅允许 int/float，不允许 bool、负数、NaN、无穷、字符串或无法表示为 float 的数字。无效元素或非有限汇总抛 ValueError；不修改输入、不重复消费生成器。空输入输出 0 / 0.0 / 0.0；非空输出 count 为 int，总时长和均值为 float。非可迭代输入允许 TypeError。本练习不要求无限流或常量内存算法。

Acceptance examples: [1, 2.5, 0] -> count 3, total 3.5, mean 3.5/3; [] -> zeros; generator and list agree; [True], [-1], ["2"], [None], [NaN], [infinity], and [1e308, 1e308] are rejected. Add tests for these requirements, not only the original five happy-path cases.

## Stage 2: validated filtering / 第二阶段：经过校验的过滤

After human approval, extend to `summarize_clips(durations, *, min_seconds=0.0)`. The threshold has the same numeric validity rules and is checked even for empty input. Include values greater than or equal to the threshold. Validate ALL input values before deciding whether they are filtered out; [-1, 2] with threshold 1 must fail, not hide the invalid negative. Count/total/mean describe only included clips. All-filtered input returns zeros. The default preserves Stage 1 behavior.

人工批准后增加仅限关键字参数 min_seconds，默认 0.0。阈值采用相同数值规则，空输入也校验阈值。保留大于等于阈值的片段；被过滤项也必须先通过校验，不能静默隐藏负数。输出只统计保留项，全过滤时返回零值，默认行为兼容第一阶段。

Acceptance: [0, 1, 2] with min_seconds=1 -> count 2, total 3.0, mean 1.5; equality is included; a one-shot generator works; empty input with an invalid threshold raises ValueError; invalid below-threshold data still fails.

## Stage 3: deterministic JSON / 第三阶段：确定性 JSON

After human approval, add `summary_json(durations, *, min_seconds=0.0) -> str`. Reuse summary semantics; output valid JSON with sorted keys, compact separators and exactly one trailing newline. Forbid NaN/infinity in serialization. Do not add timestamps, paths, file/network IO, packages or a CLI. Equivalent list/generator input gives identical text. Preserve both earlier stages and update README with examples.

人工批准后增加 summary_json，复用统计语义。JSON 键排序、紧凑分隔符、末尾恰好一个换行，不允许 NaN/无穷；不增加时间戳、路径、文件/网络读写、依赖或 CLI。列表与生成器输出文本一致，前两阶段行为不回退，并补充 README 示例。

For [1, 2], exact output is:

```text
{"count":2,"mean_seconds":1.5,"total_seconds":3.0}
```

The returned string also ends with one newline. Empty output uses float zeros. Add exact-output, repeated-call, generator, invalid-input and filter tests.

## Review rules / 审查要求

At each stage, record workspace ID, base SHA, current HEAD, changed paths, actual test outcomes, MR IID and pipeline SHA. CI must run `unit-tests`, not just a documentation detector. Do not claim failures when the stage is already correct. Review comments can approve the current stage and explicitly authorize the next. Never weaken tests/CI or use finish bypass flags to manufacture a green result.

每轮记录工作区、base/HEAD、修改路径、测试结果、MR IID 与流水线 SHA。CI 必须运行 unit-tests。实现正确时应批准本阶段，不编造缺陷；下一阶段另行明确授权。不得弱化测试/CI 或使用绕过参数凑绿色结果。
