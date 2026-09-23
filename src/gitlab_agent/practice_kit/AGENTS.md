# Practice repository boundaries

This project is a synthetic, dependency-free rehearsal. Read EXERCISE.md and implement only the stage the human has explicitly approved. The complete roadmap is not authorization to implement all stages now.

During the exercise, edits are limited to clip_summary.py, tests/, and README.md. Never change .actualcoder.yaml, .gitlab-ci.yml, this file, or EXERCISE.md merely to make a check pass. Add regression tests without removing, skipping, or weakening existing checks.

The coding worker must not commit, push, create/merge MRs, deploy, reset, or rewrite history. The human reviews changes and runs ActualCoder finish. These explicit task boundaries take precedence over generic generated handoff suggestions to use low-level commit/push. A conflicting instruction requires clarification, not a bypass.

Do not inspect user credential files, shell profiles, password stores, private notes, unrelated repositories, or live robot services. No network or new dependency is needed to implement this exercise. Use local source and tests only. Report exact commands and observations, not an unsupported success claim.

Normal validation: `python3 -m unittest discover -s tests -v` from this repository root. The initial five tests establish only the teaching baseline; additional stage-specific requirements need additional tests.

A worktree and these instructions are not OS isolation. Operators must use trusted code/hosts, their coding client's supported permission controls, and unexposed least-privilege credentials. Never place actual credentials in this project.
