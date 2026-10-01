"""Frozen-source release recovery: actual uv output and non-publication gates.

No GitHub requests, credentials or remote writes. The small offline build backend
is deliberately synthetic; the recovery workflow separately builds the real tag.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_release_bundle as fixture

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b"
WHEEL_HASH = "12d5663c44a68a7b417e510028f4780dd363feaf590a609d46de8b19fd21a7e6"
GUARD_BLOB = "54ea47a7dafad1990189f182bb5474c9b0f24760"


class ReleaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/release-recovery.yml").read_text(encoding="utf-8")
        self.normal = (ROOT / ".github/workflows/release-assets.yml").read_text(encoding="utf-8")
        self.stage, self.attach = self.workflow.split("\n  attach:\n", 1)

    def test_both_real_builds_disable_marker_instead_of_weakening_guard(self):
        self.assertIn("run: uv build --no-create-gitignore\n", self.normal)
        self.assertIn("run: uv build --no-create-gitignore\n", self.stage)
        # The actual tagged allowlist/collision guard is unchanged byte for byte.
        data = (ROOT / "scripts/release_bundle.py").read_text(encoding="utf-8").encode("utf-8")
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        self.assertEqual(blob, GUARD_BLOB)
        self.assertNotIn("rm ", self.workflow)

    def test_attachment_requires_manual_main_confirmation(self):
        condition = next(line.strip() for line in self.attach.splitlines() if line.strip().startswith("if:"))
        for required in ("github.event_name == 'workflow_dispatch'", "github.ref == 'refs/heads/main'",
                         "github.repository == 'phoenixjyb/reasonFirst'", "inputs.confirmation == 'ATTACH v0.5.1'"):
            self.assertIn(required, condition)
        self.assertEqual(condition.count("&&"), 3)
        self.assertNotIn("||", condition)
        self.assertIn("default: ''", self.stage)
        self.assertNotIn("pull_request_target:", self.workflow)
        self.assertIn("needs: stage", self.attach)

    def test_stage_is_read_only_and_validates_real_frozen_source(self):
        self.assertNotIn("contents: write", self.stage)
        self.assertNotIn("gh release upload", self.stage)
        for required in ("working-directory: source", "path: tooling", "path: source",
                         "python -m pip install uv==0.12.21", "check_release_tag.py",
                         "test_release_bundle.py", "test_release_recovery.py",
                         "check_release_artifacts.py", "install_e2e.py", "--history",
                         "ruby -c", "scripts/release_bundle.py stage"):
            self.assertIn(required, self.stage)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.stage)

    def test_source_and_guard_checkouts_are_frozen_not_moving_main(self):
        refs = re.findall(r"^\s+ref: (.+)$", self.workflow, re.MULTILINE)
        self.assertEqual(refs, ["${{ github.sha }}", SOURCE, SOURCE])
        self.assertIn("EXPECTED_SOURCE_SHA: " + SOURCE, self.workflow)
        self.assertIn("RELEASE_ID: '400714020'", self.workflow)
        self.assertIn("EXPECTED_WHEEL_SHA256: " + WHEEL_HASH, self.workflow)
        self.assertIn('refs/tags/$RELEASE_TAG^{commit}', self.stage)
        self.assertIn('git rev-parse HEAD', self.stage)
        self.assertEqual(self.workflow.count("persist-credentials: false"), 3)
        self.assertNotIn("persist-credentials: true", self.workflow)

    def test_upload_uses_current_artifact_then_unchanged_collision_checks(self):
        self.assertIn('gh run download "$GITHUB_RUN_ID"', self.attach)
        self.assertIn('v051-recovery-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}', self.attach)
        for required in ("verify_bundle", "workflow_run_id", "workflow_run_attempt", "release_tooling_commit",
                         "--paginate --slurp", "--release-id", "--remote-sha", "release_bundle.py check-upload"):
            self.assertIn(required, self.attach)
        self.assertLess(self.attach.index("verify_bundle"), self.attach.index("release_bundle.py check-upload"))
        self.assertLess(self.attach.index("release_bundle.py check-upload"), self.attach.index("gh release upload"))
        self.assertEqual(self.workflow.count("gh release upload"), 1)

    def test_no_retagging_republication_latest_or_asset_overwrite(self):
        for forbidden in ("--clobber", "gh release create", "gh release delete", "gh release edit",
                          "git push", "git tag ", "update-ref", "delete-asset", "make_latest", "--latest"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.workflow)
        self.assertIn("group: release-assets-400714020", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("overwrite: false", self.workflow)
        self.assertIn("include-hidden-files: false", self.workflow)

    def test_provenance_distinguishes_tooling_from_unchanged_source(self):
        self.assertIn("RECOVERY_TOOLING_SHA: ${{ github.sha }}", self.workflow)
        self.assertIn("manifest['release_tooling_commit'] = tooling_sha", self.stage)
        self.assertIn("manifest['recovery_of_workflow_run'] = '36827292184'", self.stage)
        self.assertNotIn("manifest['source_commit'] =", self.workflow)
        self.assertIn("sums['RELEASE.json'] = hashlib.sha256(path.read_bytes()).hexdigest()", self.stage)
        self.assertIn("verify_bundle(out)", self.stage)

    def test_actions_use_exact_reviewed_shas_and_no_new_credential(self):
        refs = re.findall(r"uses: (\S+)", self.workflow)
        for action in refs:
            self.assertRegex(action, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[a-f0-9]{40}$")
        self.assertNotRegex(self.workflow, r"\$\{\{\s*secrets\.")
        self.assertNotIn("id-token:", self.workflow)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.attach)
        self.assertIn("contents: write", self.attach)
        self.assertIn("actions: read", self.attach)

    def test_real_uv_marker_reproduces_failure_and_flag_preserves_strict_staging(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "Release regression requires the existing uv build frontend")
        case = fixture.ReleaseBundleTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        project_tmp = tempfile.TemporaryDirectory(prefix="rf-offline-build-")
        self.addCleanup(project_tmp.cleanup)
        project = Path(project_tmp.name)
        (project / "pyproject.toml").write_text(
            '[project]\nname="chatgpt-selfhosted-gitlab-mcp"\nversion="0.5.1"\n'
            '[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n',
            encoding="utf-8",
        )
        (project / "backend.py").write_text(
            'from pathlib import Path\nimport tarfile\n'
            'def build_sdist(sdist_directory, config_settings=None):\n'
            '    stem="chatgpt_selfhosted_gitlab_mcp-0.5.1"\n'
            '    name=stem+".tar.gz"\n'
            '    with tarfile.open(Path(sdist_directory)/name,"w:gz") as archive:\n'
            '        for item in ("pyproject.toml","backend.py"):\n'
            '            archive.add(item,arcname=stem+"/"+item)\n'
            '    return name\n', encoding="utf-8",
        )
        env = {k: v for k, v in case.env.items() if not k.upper().startswith("UV_")}
        env["UV_CACHE_DIR"] = str(case.root / "uv-cache")
        for suppress in (False, True):
            with self.subTest(suppress_marker=suppress):
                directory = case.root / ("fixed-output" if suppress else "default-output")
                argv = [uv, "--no-config", "--offline", "build", "--sdist", "--no-build-isolation",
                        "--no-python-downloads", "--python", sys.executable, "--out-dir", str(directory)]
                if suppress:
                    argv.append("--no-create-gitignore")
                process = subprocess.run(argv, cwd=project, env=env, capture_output=True,
                                         encoding="utf-8", errors="replace", timeout=30)
                self.assertEqual(process.returncode, 0, process.stderr)
                # The marker comes from real uv, not from the synthetic backend.
                if not suppress:
                    self.assertEqual((directory / ".gitignore").read_bytes(), b"*")
                for name in (case.wheel, "reasonfirst.rb"):
                    shutil.copyfile(case.dist / name, directory / name)
                if suppress:
                    self.assertEqual({p.name for p in directory.iterdir()}, case.names)
                    manifest = case.stage(dist=directory)
                    self.assertEqual(manifest["source_commit"], case.sha)
                    self.assertEqual(len(case.check()), 5)
                    self.assertFalse((case.out / ".gitignore").exists())
                else:
                    with self.assertRaisesRegex(ValueError, "Expected exactly"):
                        case.stage(dist=directory)
                    self.assertFalse(case.out.exists())
        # No broad hidden-file exemption: any extra file still blocks.
        (case.root / "fixed-output" / ".unexpected").write_text("synthetic")
        with self.assertRaisesRegex(ValueError, "Expected exactly"):
            case.stage(dist=case.root / "fixed-output", output=case.root / "must-not-exist")
        self.assertFalse((case.root / "must-not-exist").exists())


if __name__ == "__main__":
    unittest.main()
