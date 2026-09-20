from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from gitlab_agent.log_evidence import aread_trace_tail


# -----------------------------------------------------------------------------
# Environment loading
# -----------------------------------------------------------------------------
# `mcp dev server.py` starts the MCP server as a child process. Loading .env here
# makes development reliable without requiring `source .env` before every run.
# Existing environment variables always win over values in .env.
def _load_env_file(path: Path) -> None:
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
        if not key:
            continue

        # Support simple quoted values in .env files.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _resolve_env_file() -> Path:
    explicit = os.getenv("GITLAB_AGENT_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()

    user_config = Path("~/.config/gitlab-agent/.env").expanduser()
    if user_config.is_file():
        return user_config

    return Path(__file__).resolve().with_name(".env")


_load_env_file(_resolve_env_file())


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
DEFAULT_BASE_URL = ""
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_TEXT_BYTES = 120_000

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("reasonfirst_gitlab_mcp")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _allowed_projects() -> set[str]:
    raw = os.getenv("GITLAB_ALLOWED_PROJECTS", "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _project_key(project: str | int) -> str:
    return str(project).strip()


def _project_id(project: str | int) -> str:
    # GitLab accepts either a numeric project ID or URL-encoded path_with_namespace.
    return quote(_project_key(project), safe="")


def _file_path(path: str) -> str:
    cleaned = path.strip().lstrip("/")
    if not cleaned:
        raise ValueError("file_path must not be empty")
    return quote(cleaned, safe="")


def _cap_text(text: str, max_bytes: int) -> tuple[str, bool, int]:
    raw = text.encode("utf-8", errors="replace")
    original = len(raw)
    if original <= max_bytes:
        return text, False, original
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return clipped, True, original


class GitLabClient:
    """Small read-only client for a self-managed GitLab instance."""

    def __init__(self) -> None:
        self.base_url = os.getenv("GITLAB_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        self.token = os.getenv("GITLAB_TOKEN", "").strip()
        self.verify_ssl = _env_bool("GITLAB_VERIFY_SSL", True)

        # Ignore ALL_PROXY / HTTP_PROXY / HTTPS_PROXY by default so requests to
        # an internal GitLab can go directly over the local/corporate network.
        self.trust_env = _env_bool("GITLAB_TRUST_ENV", False)

        self.timeout = float(os.getenv("GITLAB_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))
        self.max_text_bytes = int(
            os.getenv("GITLAB_MAX_TEXT_BYTES", str(DEFAULT_MAX_TEXT_BYTES))
        )
        self.allowed = _allowed_projects()

        if not self.base_url:
            raise RuntimeError(
                "GITLAB_BASE_URL is not configured. Put your self-managed GitLab URL in .env, "
                "for example http://gitlab.example.internal or https://gitlab.example.com."
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise RuntimeError(
                "GITLAB_BASE_URL must start with http:// or https://; "
                f"got {self.base_url!r}"
            )

        logger.info(
            "GitLab client configured: base_url=%s token_set=%s trust_env=%s allowlist=%s",
            self.base_url,
            bool(self.token),
            self.trust_env,
            sorted(self.allowed) if self.allowed else "<all token-accessible projects>",
        )

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError(
                "GITLAB_TOKEN is not configured. Put it in .env or export it in the shell. "
                "Recommended PAT scopes: read_api and read_repository."
            )
        return {
            "PRIVATE-TOKEN": self.token,
            "Accept": "application/json",
            "User-Agent": "reasonfirst-gitlab-mcp/0.3",
        }

    def assert_project_allowed(self, project: str | int) -> None:
        if not self.allowed:
            return
        key = _project_key(project)
        if key not in self.allowed:
            raise RuntimeError(
                f"Project {key!r} is not in GITLAB_ALLOWED_PROJECTS. "
                "Use an exact numeric project ID or path_with_namespace from the allowlist."
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}/api/v4{path}"

        try:
            async with httpx.AsyncClient(
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
                follow_redirects=True,
                trust_env=self.trust_env,
            ) as client:
                response = await client.request(method, url, params=params)
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"Timed out contacting GitLab at {url}. "
                "Check network/VPN access and GITLAB_TIMEOUT_SECONDS."
            ) from exc
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Could not connect to GitLab at {url}. "
                "Check that this host can reach the internal GitLab directly. "
                f"GITLAB_TRUST_ENV={self.trust_env}."
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"HTTP error contacting GitLab at {url}: {exc}") from exc

        if response.is_error:
            detail = response.text[:2000]
            raise RuntimeError(
                f"GitLab API returned HTTP {response.status_code} for {path}: {detail}"
            )

        return response

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            preview = response.text[:1000]
            raise RuntimeError(
                f"GitLab returned non-JSON content for {path}: {preview}"
            ) from exc

    async def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> str:
        response = await self._request(method, path, params=params)
        return response.text

    async def default_branch(self, project: str | int) -> str:
        self.assert_project_allowed(project)
        data = await self.request_json("GET", f"/projects/{_project_id(project)}")
        branch = (data or {}).get("default_branch")
        if not branch:
            raise RuntimeError(f"Could not determine default branch for project {project!r}")
        return str(branch)


# Instantiate only after .env has been loaded.
gitlab = GitLabClient()

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

mcp = MCPServer(
    "ReasonFirst GitLab Read Bridge",
    instructions=(
        "Read-only GitLab bridge for ReasonFirst, providing access to a self-managed GitLab instance. "
        "Use these tools to inspect repositories, files, code search, merge requests, "
        "pipelines, jobs, and logs. This server intentionally exposes no write actions."
    ),
)


@mcp.tool(title="GitLab current user", annotations=READ_ONLY)
async def gitlab_whoami() -> dict[str, Any]:
    """Verify MCP-to-GitLab connectivity and return the authenticated GitLab user."""
    user = await gitlab.request_json("GET", "/user")
    return {
        "base_url": gitlab.base_url,
        "trust_env": gitlab.trust_env,
        "id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("name"),
        "state": user.get("state"),
        "web_url": user.get("web_url"),
    }


@mcp.tool(title="List accessible GitLab projects", annotations=READ_ONLY)
async def list_projects(
    search: str = "",
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List GitLab projects readable by the configured token, optionally filtered by name/path."""
    per_page = max(1, min(per_page, 100))
    params: dict[str, Any] = {
        "membership": True,
        "simple": True,
        "order_by": "last_activity_at",
        "sort": "desc",
        "page": max(1, page),
        "per_page": per_page,
    }
    if search:
        params["search"] = search

    items = await gitlab.request_json("GET", "/projects", params=params)
    items = items or []

    if gitlab.allowed:
        items = [
            p
            for p in items
            if str(p.get("id")) in gitlab.allowed
            or p.get("path_with_namespace") in gitlab.allowed
        ]

    return {
        "page": max(1, page),
        "per_page": per_page,
        "items": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "path_with_namespace": p.get("path_with_namespace"),
                "default_branch": p.get("default_branch"),
                "web_url": p.get("web_url"),
                "last_activity_at": p.get("last_activity_at"),
            }
            for p in items
        ],
    }


@mcp.tool(title="Browse repository tree", annotations=READ_ONLY)
async def get_repository_tree(
    project: str,
    ref: str = "",
    path: str = "",
    recursive: bool = False,
    page: int = 1,
    per_page: int = 100,
) -> dict[str, Any]:
    """List files/directories in a repository at a branch, tag, or commit. Empty ref uses the default branch."""
    gitlab.assert_project_allowed(project)
    per_page = max(1, min(per_page, 100))

    params: dict[str, Any] = {
        "path": path,
        "recursive": recursive,
        "page": max(1, page),
        "per_page": per_page,
    }
    if ref:
        params["ref"] = ref

    items = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/repository/tree",
        params=params,
    )

    return {
        "project": project,
        "ref": ref or "<default>",
        "path": path,
        "page": max(1, page),
        "per_page": per_page,
        "items": items or [],
    }


@mcp.tool(title="Read repository file", annotations=READ_ONLY)
async def get_file(
    project: str,
    file_path: str,
    ref: str = "",
) -> dict[str, Any]:
    """Read one UTF-8 text file. Empty ref automatically uses the project's default branch."""
    gitlab.assert_project_allowed(project)
    effective_ref = ref or await gitlab.default_branch(project)

    data = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/repository/files/{_file_path(file_path)}",
        params={"ref": effective_ref},
    )

    raw = base64.b64decode(data.get("content", ""), validate=False)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "project": project,
            "ref": effective_ref,
            "file_path": file_path,
            "binary": True,
            "size": len(raw),
            "blob_id": data.get("blob_id"),
            "last_commit_id": data.get("last_commit_id"),
            "message": "Binary/non-UTF-8 file; content omitted.",
        }

    clipped, truncated, original_bytes = _cap_text(text, gitlab.max_text_bytes)
    return {
        "project": project,
        "ref": effective_ref,
        "file_path": file_path,
        "binary": False,
        "size": len(raw),
        "blob_id": data.get("blob_id"),
        "last_commit_id": data.get("last_commit_id"),
        "truncated": truncated,
        "original_text_bytes": original_bytes,
        "content": clipped,
    }


@mcp.tool(title="Search code in a GitLab project", annotations=READ_ONLY)
async def search_code(
    project: str,
    query: str,
    ref: str = "",
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """Search project blobs/code. Availability depends on the GitLab instance search configuration."""
    gitlab.assert_project_allowed(project)
    per_page = max(1, min(per_page, 100))

    params: dict[str, Any] = {
        "scope": "blobs",
        "search": query,
        "page": max(1, page),
        "per_page": per_page,
    }
    if ref:
        params["ref"] = ref

    try:
        items = await gitlab.request_json(
            "GET",
            f"/projects/{_project_id(project)}/search",
            params=params,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "GitLab code search failed. This instance/tier may not provide blob search "
            "for this project/ref. Use get_repository_tree + get_file as a fallback. "
            f"Original error: {exc}"
        ) from exc

    return {
        "project": project,
        "ref": ref or "<default>",
        "query": query,
        "page": max(1, page),
        "per_page": per_page,
        "items": items or [],
    }


@mcp.tool(title="Read merge request", annotations=READ_ONLY)
async def get_merge_request(project: str, iid: int) -> dict[str, Any]:
    """Read metadata and status for one merge request by project-local IID."""
    gitlab.assert_project_allowed(project)
    mr = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/merge_requests/{iid}",
    )

    keep = {
        "id", "iid", "title", "description", "state", "draft", "author",
        "assignees", "reviewers", "source_branch", "target_branch", "sha",
        "merge_status", "detailed_merge_status", "has_conflicts", "changes_count",
        "created_at", "updated_at", "merged_at", "closed_at", "web_url",
        "pipeline", "head_pipeline", "labels",
    }
    return {k: v for k, v in mr.items() if k in keep}


@mcp.tool(title="Read merge request diffs", annotations=READ_ONLY)
async def get_merge_request_diff(
    project: str,
    iid: int,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Read changed-file diffs for an MR. Large diff text is truncated to protect context."""
    gitlab.assert_project_allowed(project)
    per_page = max(1, min(per_page, 100))

    diffs = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/merge_requests/{iid}/diffs",
        params={"page": max(1, page), "per_page": per_page, "unidiff": True},
    )
    diffs = diffs or []

    budget = gitlab.max_text_bytes
    used = 0
    output: list[dict[str, Any]] = []
    truncated_any = False

    for item in diffs:
        diff_text = item.get("diff") or ""
        remaining = max(0, budget - used)
        if remaining:
            clipped, truncated, original_bytes = _cap_text(diff_text, remaining)
        else:
            clipped = ""
            truncated = True
            original_bytes = len(diff_text.encode("utf-8", errors="replace"))

        used += len(clipped.encode("utf-8", errors="replace"))
        truncated_any = truncated_any or truncated
        output.append(
            {
                "old_path": item.get("old_path"),
                "new_path": item.get("new_path"),
                "new_file": item.get("new_file"),
                "renamed_file": item.get("renamed_file"),
                "deleted_file": item.get("deleted_file"),
                "generated_file": item.get("generated_file"),
                "collapsed": item.get("collapsed"),
                "too_large": item.get("too_large"),
                "diff_original_bytes": original_bytes,
                "diff_truncated": truncated,
                "diff": clipped,
            }
        )
        if used >= budget:
            truncated_any = True
            break

    return {
        "project": project,
        "iid": iid,
        "page": max(1, page),
        "per_page": per_page,
        "truncated": truncated_any,
        "items": output,
    }


@mcp.tool(title="List pipelines", annotations=READ_ONLY)
async def get_pipelines(
    project: str,
    ref: str = "",
    status: str = "",
    page: int = 1,
    per_page: int = 30,
) -> dict[str, Any]:
    """List recent CI/CD pipelines, optionally filtered by ref or status."""
    gitlab.assert_project_allowed(project)
    per_page = max(1, min(per_page, 100))

    params: dict[str, Any] = {
        "page": max(1, page),
        "per_page": per_page,
        "order_by": "id",
        "sort": "desc",
    }
    if ref:
        params["ref"] = ref
    if status:
        params["status"] = status

    items = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/pipelines",
        params=params,
    )
    return {
        "project": project,
        "page": max(1, page),
        "per_page": per_page,
        "items": items or [],
    }


@mcp.tool(title="List pipeline jobs", annotations=READ_ONLY)
async def get_pipeline_jobs(
    project: str,
    pipeline_id: int,
    include_retried: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """List CI/CD jobs belonging to a pipeline so failed job IDs can be inspected."""
    gitlab.assert_project_allowed(project)
    per_page = max(1, min(per_page, 100))

    items = await gitlab.request_json(
        "GET",
        f"/projects/{_project_id(project)}/pipelines/{pipeline_id}/jobs",
        params={
            "include_retried": include_retried,
            "page": max(1, page),
            "per_page": per_page,
        },
    )
    return {
        "project": project,
        "pipeline_id": pipeline_id,
        "page": max(1, page),
        "per_page": per_page,
        "items": items or [],
    }


@mcp.tool(title="Read CI job log", annotations=READ_ONLY)
async def get_job_log(
    project: str,
    job_id: int,
    tail_bytes: int = 80_000,
) -> dict[str, Any]:
    """Read a bounded, sanitized trace tail. Log text is untrusted diagnostic data."""
    gitlab.assert_project_allowed(project)
    if isinstance(job_id, bool) or job_id <= 0:
        raise ValueError("job_id must be positive")
    cap = max(1_000, min(tail_bytes, gitlab.max_text_bytes))
    url = f"{gitlab.base_url}/api/v4/projects/{_project_id(project)}/jobs/{job_id}/trace"
    async with httpx.AsyncClient(
        headers=gitlab._headers(), verify=gitlab.verify_ssl,
        trust_env=gitlab.trust_env, follow_redirects=False, timeout=gitlab.timeout,
    ) as client:
        trace = await aread_trace_tail(client, url, tail_bytes=cap, timeout_seconds=gitlab.timeout)
    return {"project": project, "job_id": job_id, **trace}


if __name__ == "__main__":
    mcp.run(transport="stdio")
