from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.check_release_tag import verify
from scripts.render_homebrew_formula import render


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_release_tag_matches_current_package_metadata(self) -> None:
        # Current stacked development still reports 0.5.0 until the v0.5.1
        # release-preparation change intentionally bumps package metadata.
        self.assertEqual(
            verify("v0.5.0", ROOT),
            {"tag": "v0.5.0", "version": "0.5.0"},
        )

    def test_release_tag_mismatch_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "tag/package version mismatch"):
            verify("v0.5.1", ROOT)

    def test_homebrew_formula_renderer_pins_source_and_hash(self) -> None:
        template = (ROOT / "packaging/homebrew/reasonfirst.rb.in").read_text(
            encoding="utf-8"
        )
        digest = "a" * 64
        source = (
            "https://github.com/phoenixjyb/reasonFirst/"
            "archive/refs/tags/v0.5.1.tar.gz"
        )
        output = render(
            template=template,
            version="0.5.1",
            source_url=source,
            sha256=digest,
        )
        self.assertIn('version "0.5.1"', output)
        self.assertIn(f'sha256 "{digest}"', output)
        self.assertIn(source, output)
        self.assertIn("reasonfirst-bridge-mcp", output)
        self.assertNotIn("__VERSION__", output)
        self.assertNotIn("__SHA256__", output)

    def test_homebrew_formula_renderer_rejects_untrusted_source(self) -> None:
        template = (ROOT / "packaging/homebrew/reasonfirst.rb.in").read_text(
            encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "https://github.com"):
            render(
                template=template,
                version="0.5.1",
                source_url="https://example.invalid/reasonfirst.tar.gz",
                sha256="a" * 64,
            )


if __name__ == "__main__":
    unittest.main()
