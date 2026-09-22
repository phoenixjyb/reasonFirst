from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gitlab_agent.config import AgentSettings
from gitlab_agent.project_config import parse_project_config


class ProjectConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AgentSettings(
            config_file=root / ".env",
            gitlab_base_url="https://gitlab.example.test",
            api_token="token",
            api_verify_ssl=True,
            api_trust_env=False,
            git_token="token",
            git_username="oauth2",
            git_trust_env=False,
            allowed_projects={"team/project"},
            require_write_allowlist=True,
            workspace_root=root / "workspace",
            branch_prefix="chatgpt/",
            default_base_ref="main",
            allowed_executables={"python", "uv", "pytest", "cmake"},
            command_timeout_seconds=300,
            max_output_bytes=120000,
            max_file_bytes=1000000,
            git_author_name=None,
            git_author_email=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_contract_produces_effective_config(self) -> None:
        text = """
version: 1
project:
  base_branch: develop
agents:
  preferred:
    - copilot
    - codex
validation:
  commands:
    - name: unit-tests
      argv: [uv, run, pytest, -q]
      required: true
      timeout_seconds: 180
protected_paths:
  - deploy/
  - .gitlab-ci.yml
instructions:
  - Keep ROS 2 package boundaries intact.
executables:
  required:
    - uv
    - cmake
mr:
  target_branch: develop
  title_prefix: "[ActualCoder] "
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertTrue(result.found)
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.effective["base_branch"], "develop")
        self.assertEqual(
            result.effective["preferred_agents"],
            ["copilot", "codex"],
        )
        commands = result.effective["validation_commands"]
        self.assertEqual(commands[0]["argv"], ["uv", "run", "pytest", "-q"])
        self.assertEqual(result.effective["mr"]["target_branch"], "develop")

    def test_explicit_desktop_backend_is_supported(self) -> None:
        text = """
version: 1
agents:
  preferred:
    - codex-desktop
    - codex-cli
    - copilot-cli
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertTrue(result.valid)
        self.assertEqual(
            result.effective["preferred_agents"],
            ["codex-desktop", "codex-cli", "copilot-cli"],
        )
        self.assertFalse(any("unsupported backend" in item for item in result.warnings))

    def test_repository_cannot_self_authorize_executable(self) -> None:
        text = """
version: 1
validation:
  commands:
    - name: unsafe
      argv: [bash, -c, echo hello]
executables:
  required:
    - curl
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        joined = "\n".join(result.errors)
        self.assertIn("bash", joined)
        self.assertIn("curl", joined)
        self.assertIn("GITLAB_ALLOWED_EXECUTABLES", joined)

    def test_timeout_is_capped_by_user_policy(self) -> None:
        text = """
version: 1
validation:
  commands:
    - name: tests
      argv: [pytest, -q]
      timeout_seconds: 999
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertTrue(result.valid)
        commands = result.effective["validation_commands"]
        self.assertEqual(commands[0]["timeout_seconds"], 300)
        self.assertTrue(any("will be capped" in item for item in result.warnings))

    def test_unknown_keys_and_unsafe_paths_fail(self) -> None:
        text = """
version: 1
mystery: true
protected_paths:
  - ../outside
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("Unknown key" in item for item in result.errors))
        self.assertTrue(any("safe repository-relative path" in item for item in result.errors))

    def test_boolean_version_is_not_accepted_as_integer_one(self) -> None:
        result = parse_project_config(
            "version: true\n",
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("version must be integer 1" in item for item in result.errors))

    def test_non_string_unknown_key_reports_validation_error(self) -> None:
        result = parse_project_config(
            "version: 1\n42: value\n",
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("Unknown key root.42" in item for item in result.errors))

    def test_duplicate_yaml_keys_are_rejected(self) -> None:
        text = """
version: 1
project:
  base_branch: main
  base_branch: develop
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        self.assertTrue(
            any("Duplicate YAML mapping key" in item for item in result.errors)
        )

    def test_contract_size_is_bounded_before_yaml_parse(self) -> None:
        text = "version: 1\ninstructions:\n  - " + ("x" * 70000)
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        self.assertTrue(any("too large" in item for item in result.errors))

    def test_instruction_and_command_cardinality_are_bounded(self) -> None:
        instructions = "\n".join(f"  - item-{i}" for i in range(40))
        commands = "\n".join(
            "    - name: cmd-{0}\n      argv: [pytest, -q]".format(i)
            for i in range(40)
        )
        text = (
            "version: 1\n"
            "instructions:\n"
            + instructions
            + "\nvalidation:\n  commands:\n"
            + commands
            + "\n"
        )
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        joined = "\n".join(result.errors)
        self.assertIn("instructions must contain at most", joined)
        self.assertIn("validation.commands must contain at most", joined)

    def test_unsafe_ref_and_windows_style_path_are_rejected(self) -> None:
        text = """
version: 1
project:
  base_branch: ../main
protected_paths:
  - ..\\outside
mr:
  target_branch: --help
"""
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.valid)
        joined = "\n".join(result.errors)
        self.assertIn("project.base_branch", joined)
        self.assertIn("safe repository-relative path", joined)
        self.assertIn("mr.target_branch", joined)

    def test_validation_argv_size_and_nul_are_rejected(self) -> None:
        long_arg = "x" * 5000
        text = (
            "version: 1\nvalidation:\n  commands:\n"
            "    - name: bad\n"
            "      argv:\n"
            "        - pytest\n"
            f"        - {long_arg}\n"
        )
        result = parse_project_config(
            text,
            settings=self.settings,
            source_ref="main",
        )
        self.assertFalse(result.valid)
        self.assertTrue(any("UTF-8 bytes" in item for item in result.errors))

    def test_missing_contract_is_valid_and_uses_user_defaults(self) -> None:
        result = parse_project_config(
            None,
            settings=self.settings,
            source_ref="main",
        )

        self.assertFalse(result.found)
        self.assertTrue(result.valid)
        self.assertEqual(result.effective["base_branch"], "main")
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
