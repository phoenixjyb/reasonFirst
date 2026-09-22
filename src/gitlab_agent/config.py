from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .tls import ca_bundle_path, validate_base_url
from .project_access import ProjectAccessError, assert_project_allowed


DEFAULT_ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "uv",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "make",
    "cmake",
    "ninja",
    "cargo",
    "go",
    "mvn",
    "gradle",
}


def load_env_file(path: Path) -> None:
    """Load a simple .env file without overwriting already-exported variables."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def csv_set(name: str, default: set[str] | None = None) -> set[str]:
    raw = os.getenv(name)
    if raw is None:
        return set(default or set())
    return {item.strip() for item in raw.split(",") if item.strip()}


def csv_tuple(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return tuple(default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def env_choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip() or default
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(f"{name} must be one of: {choices}")
    return value


def resolve_env_file() -> Path:
    """Resolve config for global CLI use while keeping local .env compatibility."""
    explicit = os.getenv("GITLAB_AGENT_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()

    user_config = Path("~/.config/gitlab-agent/.env").expanduser()
    if user_config.is_file():
        return user_config

    return Path(".env")


@dataclass(frozen=True)
class AgentSettings:
    config_file: Path
    gitlab_base_url: str
    api_token: str
    api_verify_ssl: bool
    api_trust_env: bool
    git_token: str
    git_username: str
    git_trust_env: bool
    allowed_projects: set[str]
    require_write_allowlist: bool
    workspace_root: Path
    branch_prefix: str
    default_base_ref: str
    allowed_executables: set[str]
    command_timeout_seconds: int
    max_output_bytes: int
    max_file_bytes: int
    git_author_name: str | None
    git_author_email: str | None
    api_ca_bundle: Path | None = None
    codex_model: str = "gpt-5.6-sol"
    codex_reasoning_effort: str = "high"
    codex_execution_mode: str = "interactive"
    codex_sandbox_mode: str = "workspace-write"
    codex_approval_policy: str = "on-request"
    codex_network_access: bool = False
    copilot_model: str | None = None
    copilot_reasoning_effort: str | None = None
    copilot_execution_mode: str = "interactive"
    copilot_disable_builtin_mcps: bool = True
    copilot_allow_tools: tuple[str, ...] = ()
    copilot_deny_tools: tuple[str, ...] = ("shell(git push)",)
    git_credential_source: str = "api-token"

    @classmethod
    def load(cls) -> "AgentSettings":
        env_file = resolve_env_file()
        load_env_file(env_file)

        base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("GITLAB_BASE_URL is required")
        if not base_url.startswith(("http://", "https://")):
            raise RuntimeError("GITLAB_BASE_URL must start with http:// or https://")

        base_url = validate_base_url(base_url)

        api_token = os.getenv("GITLAB_TOKEN", "").strip()
        git_scoped_token = os.getenv("GITLAB_GIT_TOKEN", "").strip()
        git_password = os.getenv("GITLAB_GIT_PASSWORD", "").strip()
        if git_scoped_token and git_password:
            raise RuntimeError(
                "Set only one of GITLAB_GIT_TOKEN or GITLAB_GIT_PASSWORD; "
                "ReasonFirst refuses ambiguous Git credential sources"
            )
        if git_scoped_token:
            git_token = git_scoped_token
            git_credential_source = "git-token"
        elif git_password:
            git_token = git_password
            git_credential_source = "git-password"
        else:
            git_token = api_token
            git_credential_source = "api-token"

        root = Path(
            os.getenv(
                "GITLAB_WORKSPACE_ROOT",
                "~/.local/share/chatgpt-gitlab-mcp",
            )
        ).expanduser()

        branch_prefix = os.getenv("GITLAB_BRANCH_PREFIX", "chatgpt/").strip()
        if not branch_prefix or " " in branch_prefix:
            raise RuntimeError("GITLAB_BRANCH_PREFIX must be a non-empty branch prefix")

        return cls(
            config_file=env_file.resolve() if env_file.exists() else env_file.expanduser(),
            gitlab_base_url=base_url,
            api_ca_bundle=ca_bundle_path(os.getenv("GITLAB_CA_BUNDLE")),
            api_token=api_token,
            api_verify_ssl=env_bool("GITLAB_VERIFY_SSL", True),
            api_trust_env=env_bool("GITLAB_TRUST_ENV", False),
            git_token=git_token,
            git_username=os.getenv("GITLAB_GIT_USERNAME", "oauth2").strip() or "oauth2",
            git_trust_env=env_bool("GITLAB_GIT_TRUST_ENV", False),
            git_credential_source=git_credential_source,
            allowed_projects=csv_set("GITLAB_ALLOWED_PROJECTS"),
            require_write_allowlist=env_bool("GITLAB_REQUIRE_WRITE_ALLOWLIST", True),
            workspace_root=root,
            branch_prefix=branch_prefix,
            default_base_ref=os.getenv("GITLAB_DEFAULT_BASE_REF", "main").strip() or "main",
            allowed_executables=csv_set(
                "GITLAB_ALLOWED_EXECUTABLES",
                DEFAULT_ALLOWED_EXECUTABLES,
            ),
            command_timeout_seconds=int(
                os.getenv("GITLAB_COMMAND_TIMEOUT_SECONDS", "300")
            ),
            max_output_bytes=int(os.getenv("GITLAB_COMMAND_MAX_OUTPUT_BYTES", "120000")),
            max_file_bytes=int(os.getenv("GITLAB_MAX_WRITE_FILE_BYTES", "1000000")),
            git_author_name=os.getenv("GITLAB_GIT_AUTHOR_NAME") or None,
            git_author_email=os.getenv("GITLAB_GIT_AUTHOR_EMAIL") or None,
            codex_model=os.getenv("REASONFIRST_CODEX_MODEL", "gpt-5.6-sol").strip()
            or "gpt-5.6-sol",
            codex_reasoning_effort=env_choice(
                "REASONFIRST_CODEX_REASONING_EFFORT",
                "high",
                {"minimal", "low", "medium", "high", "xhigh", "max"},
            ),
            codex_execution_mode=env_choice(
                "REASONFIRST_CODEX_EXECUTION_MODE",
                "interactive",
                {"interactive", "exec"},
            ),
            codex_sandbox_mode=env_choice(
                "REASONFIRST_CODEX_SANDBOX",
                "workspace-write",
                {"read-only", "workspace-write"},
            ),
            codex_approval_policy=env_choice(
                "REASONFIRST_CODEX_APPROVAL_POLICY",
                "on-request",
                {"on-request", "never"},
            ),
            codex_network_access=env_bool("REASONFIRST_CODEX_NETWORK_ACCESS", False),
            copilot_model=os.getenv("REASONFIRST_COPILOT_MODEL", "").strip() or None,
            copilot_reasoning_effort=(
                env_choice(
                    "REASONFIRST_COPILOT_REASONING_EFFORT",
                    "high",
                    {"low", "medium", "high", "xhigh"},
                )
                if os.getenv("REASONFIRST_COPILOT_REASONING_EFFORT", "").strip()
                else None
            ),
            copilot_execution_mode=env_choice(
                "REASONFIRST_COPILOT_EXECUTION_MODE",
                "interactive",
                {"interactive", "programmatic"},
            ),
            copilot_disable_builtin_mcps=env_bool(
                "REASONFIRST_COPILOT_DISABLE_BUILTIN_MCPS",
                True,
            ),
            copilot_allow_tools=csv_tuple("REASONFIRST_COPILOT_ALLOW_TOOLS"),
            copilot_deny_tools=csv_tuple(
                "REASONFIRST_COPILOT_DENY_TOOLS",
                ("shell(git push)",),
            ),
        )

    def assert_project_allowed_for_workspace(self, project: str) -> None:
        assert_project_allowed(project, set())
        if self.allowed_projects and project not in self.allowed_projects:
            raise ProjectAccessError("project_not_allowlisted", stage="local_policy")
        if not self.allowed_projects and self.require_write_allowlist:
            raise ProjectAccessError("write_allowlist_required", stage="local_policy")
