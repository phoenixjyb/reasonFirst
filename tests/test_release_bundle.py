from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.release_bundle import stage_bundle, verify_bundle, check_upload, _read_json, MANIFEST, SUMS


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rf-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.root / "empty.gitconfig"))
        (self.root / "empty.gitconfig").write_text("")
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Example")
        self.git("config", "user.email", "example@example.invalid")
        (self.root / "pyproject.toml").write_text('[project]\nversion = "0.5.1"\n', encoding="utf-8")
        package = self.root / "src/gitlab_agent"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text('__version__ = "0.5.1"\n', encoding="utf-8")
        self.git("add", "pyproject.toml", "src")
        self.git("commit", "-m", "synthetic release")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.5.1")
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.wheel = "chatgpt_selfhosted_gitlab_mcp-0.5.1-py3-none-any.whl"
        self.names = {self.wheel, "chatgpt_selfhosted_gitlab_mcp-0.5.1.tar.gz", "reasonfirst.rb"}
        for name in self.names:
            (self.dist / name).write_bytes(b"synthetic artifact; archive inspection is a separate gate")
        self.out = self.root / "release-download"
        self.kwargs = dict(root=self.root, dist=self.dist, output=self.out, tag="v0.5.1",
                           repository="example/project", source_sha=self.sha, run_id="123", run_attempt="1")
        self.release = {"id": 12, "tag_name": "v0.5.1", "draft": False, "prerelease": False,
                        "immutable": False, "url": "https://api.github.com/repos/example/project/releases/12"}

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env,
                                       stderr=subprocess.DEVNULL, encoding="utf-8", timeout=20).strip()

    def stage(self, **overrides):
        with patch.dict(os.environ, self.env, clear=True):
            return stage_bundle(**{**self.kwargs, **overrides})

    def check(self, **overrides):
        return check_upload(**{**dict(directory=self.out, release=self.release, asset_pages=[[]],
                                      repository="example/project", release_id=12, remote_sha=self.sha), **overrides})

    def test_stage_exact_source_and_all_checksums_without_source_change(self):
        before = self.git("status", "--porcelain", "--untracked-files=no")
        manifest = self.stage()
        self.assertEqual(manifest["source_commit"], self.sha)
        self.assertEqual(manifest["source_tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(manifest["release_tag"], "v0.5.1")
        self.assertEqual(verify_bundle(self.out), manifest)
        self.assertEqual(set(self.check()), self.names | {MANIFEST, SUMS})
        for line in (self.out / SUMS).read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=no"), before)

    def test_mismatched_source_and_moved_local_tag_refused(self):
        with self.assertRaises(ValueError):
            self.stage(source_sha="f" * 40)
        self.git("commit", "--allow-empty", "-m", "another synthetic commit")
        head = self.git("rev-parse", "HEAD")
        with self.assertRaises(ValueError):
            self.stage(source_sha=head)
        self.assertFalse(self.out.exists())

    def test_annotated_tag_dereferences_to_commit(self):
        self.git("tag", "-f", "-a", "v0.5.1", "-m", "synthetic annotated tag")
        self.assertEqual(self.stage()["source_commit"], self.sha)

    def test_invalid_tag_metadata_and_workflow_identity_refused(self):
        for kwargs in ({"tag": "v0.5.1rc1"}, {"tag": " v0.5.1"}, {"repository": "../bad/name"},
                       {"run_id": "0"}, {"run_attempt": "2;echo"}, {"source_sha": "main"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.stage(**kwargs)
        self.assertFalse(self.out.exists())

    def test_changed_tracked_source_refused(self):
        (self.root / "pyproject.toml").write_text('[project]\nversion="0.5.1"\n# changed\n')
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_missing_extra_and_wrong_version_artifact_refused(self):
        extra = self.dist / "private.log"
        extra.write_text("synthetic extra")
        with self.assertRaises(ValueError):
            self.stage()
        extra.unlink()
        (self.dist / self.wheel).rename(self.dist / self.wheel.replace("0.5.1", "0.5.0"))
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_directory_input_is_not_regular_artifact(self):
        path = self.dist / self.wheel
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            self.stage()
        self.assertFalse(self.out.exists())

    def test_existing_output_is_never_overwritten(self):
        self.stage()
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_tampered_asset_and_checksum_list_fail(self):
        self.stage()
        path = self.out / self.wheel
        before = path.read_bytes()
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.check()
        path.write_bytes(before)
        (self.out / SUMS).write_text("")
        with self.assertRaises(ValueError):
            self.check()

    def test_unexpected_staged_file_refused_before_upload(self):
        self.stage()
        (self.out / "extra.txt").write_text("not part of the approved release")
        with self.assertRaises(ValueError):
            self.check()

    def test_collision_on_later_page_refuses_even_identical_asset(self):
        self.stage()
        manifest = verify_bundle(self.out)
        with self.assertRaisesRegex(ValueError, "no overwrite"):
            self.check(asset_pages=[[{"name": "unrelated.txt"}],
                                    [{"name": self.wheel, "digest": "sha256:" + manifest["files_sha256"][self.wheel]}]])
        self.assertEqual(len(self.check(asset_pages=[[{"name": "unrelated.txt"}]])), 5)

    def test_changed_remote_release_or_tag_is_refused(self):
        self.stage()
        for key, value in (("draft", True), ("draft", None), ("prerelease", True), ("immutable", True),
                           ("id", 13), ("tag_name", "v0.5.2"), ("url", "https://example.invalid/12")):
            release = {**self.release, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check(release=release)
        with self.assertRaises(ValueError):
            self.check(remote_sha="f" * 40)
        with self.assertRaises(ValueError):
            self.check(repository="example/other")

    def test_malformed_inventory_and_duplicate_json_refused(self):
        self.stage()
        for pages in ([], {}, [{"name": self.wheel}], [[{}]], [["bad"]]):
            with self.subTest(pages=pages), self.assertRaises(ValueError):
                self.check(asset_pages=pages)
        path = self.root / "duplicate.json"
        path.write_text('{"name": "one", "name": "two"}')
        with self.assertRaises(ValueError):
            _read_json(path)

    def test_manifest_traversal_is_not_used_as_a_path(self):
        self.stage()
        manifest = json.loads((self.out / MANIFEST).read_text())
        manifest["files_sha256"]["../outside"] = "a" * 64
        (self.out / MANIFEST).write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            verify_bundle(self.out)

    def test_direct_cli_checks_metadata_but_never_uploads(self):
        self.stage()
        release_path = self.root / "release.json"
        assets_path = self.root / "assets.json"
        release_path.write_text(json.dumps(self.release))
        assets_path.write_text("[[]]")
        script = Path(__file__).resolve().parents[1] / "scripts/release_bundle.py"
        argv = [sys.executable, str(script), "check-upload", "--directory", str(self.out),
                "--release-json", str(release_path), "--assets-json", str(assets_path),
                "--repository", "example/project", "--release-id", "12", "--remote-sha", self.sha]
        result = subprocess.run(argv, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["publication_attempted"])
        assets_path.write_text(json.dumps([[{"name": self.wheel}]]))
        result = subprocess.run(argv, capture_output=True, encoding="utf-8", timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(list(self.out.iterdir())), 5)

    def test_workflow_keeps_human_release_boundary_and_no_replacement(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/release-assets.yml").read_text()
        for required in ("types: [published]", "ref: ${{ github.sha }}", "persist-credentials: false",
                         "cancel-in-progress: false", "release_bundle.py stage", "release_bundle.py check-upload",
                         "--paginate --slurp", "gh release upload", "SHA256SUMS.txt"):
            self.assertIn(required, text)
        for forbidden in ("--clobber", "gh release create", "gh release delete", "git push", "git tag "):
            self.assertNotIn(forbidden, text)
        self.assertLess(text.index("release_bundle.py check-upload"), text.index("gh release upload"))


if __name__ == "__main__":
    unittest.main()
