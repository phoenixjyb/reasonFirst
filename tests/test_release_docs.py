from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_DOCS = [
    ROOT / "README.md",
    ROOT / "README_CN.md",
    ROOT / "SECURITY.md",
    ROOT / "SECURITY_CN.md",
    ROOT / "docs/GETTING_STARTED.md",
    ROOT / "docs/GETTING_STARTED_CN.md",
    ROOT / "docs/QUICKSTART_CN.md",
    ROOT / "docs/TROUBLESHOOTING.md",
    ROOT / "docs/TROUBLESHOOTING_CN.md",
    ROOT / "docs/PRACTICE_LAB.md",
    ROOT / "docs/PRACTICE_LAB_CN.md",
    ROOT / "docs/TASK_HANDOFF_TEMPLATE.md",
    ROOT / "docs/TASK_HANDOFF_TEMPLATE_CN.md",
    ROOT / "docs/RELEASE_NOTES_0.5.0.md",
    ROOT / "docs/RELEASE_NOTES_0.5.0_CN.md",
]

STALE_CLAIMS = [
    "General per-workspace locking and transactional crash recovery are not yet implemented.",
    "通用的每工作区锁和事务式崩溃恢复尚未实现。",
    "Current MCP does not submit local tasks or read unpublished worktree changes.",
    "当前 MCP 不提交本地任务，也不读取未发布 worktree 修改。",
    "not an implemented TaskSpec/EvidencePack API",
    "不是已实现的 TaskSpec/EvidencePack API",
    "A future evidence interface should remove this manual reconstruction, but does not exist yet.",
    "package version `0.3.0`",
    "包版本仍为 `0.3.0`",
]


class ReleaseDocumentationTests(unittest.TestCase):
    def test_v050_package_and_release_notes_are_aligned(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        init = (ROOT / "src/gitlab_agent/__init__.py").read_text(encoding="utf-8")
        self.assertRegex(pyproject, r'(?m)^version = "0\.5\.0"$')
        self.assertIn('__version__ = "0.5.0"', init)

        en = (ROOT / "docs/RELEASE_NOTES_0.5.0.md").read_text(encoding="utf-8")
        cn = (ROOT / "docs/RELEASE_NOTES_0.5.0_CN.md").read_text(encoding="utf-8")
        self.assertIn("# ReasonFirst 0.5.0 release notes", en)
        self.assertIn("# ReasonFirst 0.5.0 发布说明", cn)
        self.assertNotIn("0.5.0 release candidate", en.lower())
        self.assertNotIn("Release Candidate", cn)

    def test_active_docs_do_not_reintroduce_known_stale_claims(self):
        for path in ACTIVE_DOCS:
            text = path.read_text(encoding="utf-8")
            for stale in STALE_CLAIMS:
                with self.subTest(path=str(path.relative_to(ROOT)), stale=stale):
                    self.assertNotIn(stale, text)

    def test_architecture_matches_current_core_surfaces(self):
        text = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        for marker in (
            "Read-only GitLab MCP",
            "Bridge Preview orchestration MCP",
            "codex-cli",
            "copilot-cli",
            "codex-desktop",
            "Cross-process locking",
            "src/gitlab_agent/locking.py",
            "Durable TaskSpec / attempts",
            "Bounded EvidencePack",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_site_navigation_and_workflow_enforce_release_docs(self):
        mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/docs-pages.yml").read_text(encoding="utf-8")
        self.assertIn("docs/RELEASE_NOTES_0.5.0.md", mkdocs)
        self.assertIn("docs/RELEASE_NOTES_0.5.0_CN.md", mkdocs)
        self.assertIn("scripts/check_docs_site_links.py", workflow)
        self.assertIn("Check rendered internal links", workflow)

    def test_homepage_surfaces_validation_without_claiming_a_fixed_release_sha(self):
        text = (ROOT / "website/index.md").read_text(encoding="utf-8")
        self.assertIn("v0.5.0 validated baseline", text)
        self.assertIn("445 tests", text)
        self.assertIn("package-release-candidate", (ROOT / "docs/RELEASE_NOTES_0.5.0.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
