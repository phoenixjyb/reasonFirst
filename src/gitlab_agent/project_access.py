"""Read-only project readiness, with no automatic provisioning or access grants.

An allowlist denial happens before network access. A GitLab 404 cannot
establish nonexistence: GitLab may conceal resources from this credential.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from functools import wraps
import json
import re
import ssl
from typing import Any, TYPE_CHECKING
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from .config import AgentSettings


MAX_REQUIRED_FILES = 16
MAX_METADATA_BYTES = 128 * 1024
PROBE_TIMEOUT_SECONDS = 30
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.IGNORECASE)
_PROJECT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\Z")

_GRANT = (
    "Ask the user or MCP operator to confirm the exact GitLab instance and project path/ID, "
    "then explicitly append only the approved project to GITLAB_ALLOWED_PROJECTS in the "
    "effective local MCP configuration. Preserve existing entries and restart the existing "
    "MCP/tunnel process; an exported environment value can override the configuration file. "
    "This is not an OpenAI tunnel-profile project permission. Do not clear the allowlist, "
    "create a project, read credential files, or grant access automatically."
)
_CONFIRM = (
    "Ask the user to verify the exact project path/ID on this GitLab instance and the token "
    "owner's project membership and read scopes. A 404 does not distinguish missing from "
    "hidden/inaccessible. Create and seed a new project only after the user confirms it is "
    "absent and separately authorizes creation; do not switch to another project."
)

# Static descriptions: no server body, exception text, token, URL or headers.
_ERRORS = {
    "invalid_project": ("Use an exact positive numeric project ID or namespace/project, not a URL or leading-slash path.", ["Confirm the identifier with the user; do not guess another repository."]),
    "project_not_allowlisted": ("Project access has not been granted in GITLAB_ALLOWED_PROJECTS; remote existence was not checked.", [_GRANT]),
    "write_allowlist_required": ("GITLAB_ALLOWED_PROJECTS is empty; local/write workflows require explicit project approval.", [_GRANT]),
    "credential_missing": ("GITLAB_TOKEN is not configured in this process.", ["Ask the operator to load a valid GitLab read credential privately and restart MCP. Do not request or print the token."]),
    "gitlab_unauthorized": ("GitLab rejected authentication (HTTP 401).", ["Ask the operator to check the configured token's validity and expiry privately. This is not the OpenAI tunnel key."]),
    "gitlab_forbidden": ("GitLab refused this request (HTTP 403).", ["Ask the user/administrator to review project membership, token scopes and instance policy. Local allowlisting does not grant GitLab permissions."]),
    "project_missing_or_inaccessible": ("GitLab could not expose this project to the configured credential (HTTP 404). Existence is unknown.", [_CONFIRM]),
    "ref_missing_or_inaccessible": ("The project was readable, but the requested ref could not be resolved (HTTP 404).", ["Ask the user to confirm the branch/tag/commit, initial seed and repository-read permissions. Do not silently fall back to main or another ref."]),
    "required_file_missing_or_inaccessible": ("A required file could not be read at the pinned commit (HTTP 404).", ["Ask the user to confirm that the intended repository was seeded and the exact file path is readable. Stop task planning; do not invent its contents."]),
    "resource_missing_or_inaccessible": ("GitLab returned HTTP 404 for the requested resource; missing versus inaccessible is not established.", ["Run check_project_access for the exact project/ref before retrying file reads. Check the resource ID/path without guessing a different repository."]),
    "repository_empty": ("GitLab reports that this project's repository is empty.", ["Ask the user to seed the intended branch and required files in this project, then repeat the check. No project or branch was created."]),
    "default_branch_unavailable": ("The project was readable but did not supply a usable default branch.", ["Ask the user to confirm or initialize the intended branch. Supply that exact ref; do not infer a branch or assume the project is absent."]),
    "invalid_probe_input": ("Invalid ref or required file list; the probe did not contact GitLab.", ["Use a nonempty safe ref (or omit it), and at most 16 unique repository-relative file paths. Do not use URLs, traversal, control characters or credential-file paths."]),
    "gitlab_rate_limited": ("GitLab rate limited the request (HTTP 429).", ["Stop repeated attempts and retry later under the instance's rate-limit policy; do not rotate credentials to bypass it."]),
    "gitlab_unavailable": ("GitLab or its upstream service returned a server error.", ["Ask the operator to check GitLab availability. Do not change repository access or credentials merely because the service failed."]),
    "gitlab_http_error": ("GitLab returned an unexpected HTTP status.", ["Ask the operator to check the endpoint and service; raw response bodies are intentionally omitted."]),
    "transport_error": ("The GitLab API request failed at the transport/destination-policy layer.", ["Check network/VPN, final endpoint, CA trust and redirects on the MCP host. Keep certificate and destination checks enabled."]),
    "tls_verification_failed": ("GitLab TLS certificate verification failed.", ["Ask the operator to correct the certificate chain/hostname or configure the approved CA bundle. Never disable verification."]),
    "redirect_refused": ("GitLab API redirect refused; configure the final endpoint directly.", ["Ask the operator to verify the final GitLab API endpoint. Do not follow a redirect or forward the token to its target."]),
    "timeout": ("The project readiness check timed out; evidence is incomplete.", ["Check the service/network and retry the same project after recovery. Do not call missing evidence success."]),
    "invalid_response": ("GitLab did not return the expected bounded metadata or revision evidence.", ["Ask the operator to inspect the service response privately. No response body or credentials are included here."]),
    "configuration_error": ("The effective local GitLab configuration could not be loaded or validated.", ["Ask the operator to check the selected configuration, endpoint and CA settings privately. Do not print configuration contents or disable verification."]),
}


class ProjectAccessError(RuntimeError):
    """Safe, structured diagnostics for both local helpers and MCP tool results."""

    def __init__(self, code: str, *, stage: str = "resource", http_status: int | None = None):
        message, actions = _ERRORS[code]
        self.code = code
        self.stage = stage
        self.http_status = http_status
        super().__init__(f"{code}: {message} Next: " + " ".join(actions))

    def result(self) -> dict[str, Any]:
        message, actions = _ERRORS[self.code]
        return {
            "ok": False,
            "error": {
                "code": self.code, "stage": self.stage,
                "http_status": self.http_status, "message": message,
                "next_steps": list(actions), "requires_user_action": True,
            },
        }


def assert_project_allowed(project: str | int, allowed: set[str]) -> str:
    """Validate, then compare exact keys. Never resolve aliases outside policy."""
    key = str(project).strip()
    parts = key.split("/")
    if (
        isinstance(project, bool) or len(key) > 512
        or not (re.fullmatch(r"[1-9][0-9]*", key) or _PROJECT.fullmatch(key))
        or any(p in {".", ".."} for p in parts)
    ):
        raise ProjectAccessError("invalid_project", stage="local_policy")
    if allowed and key not in allowed:
        raise ProjectAccessError("project_not_allowlisted", stage="local_policy")
    return key


def http_failure(status: int, stage: str = "resource") -> ProjectAccessError:
    if status == 401:
        code = "gitlab_unauthorized"
    elif status == 403:
        code = "gitlab_forbidden"
    elif status == 404:
        code = {
            "project": "project_missing_or_inaccessible",
            "ref": "ref_missing_or_inaccessible",
            "required_files": "required_file_missing_or_inaccessible",
        }.get(stage, "resource_missing_or_inaccessible")
    elif status == 429:
        code = "gitlab_rate_limited"
    elif 300 <= status < 400:
        code = "redirect_refused"
    elif 500 <= status < 600:
        code = "gitlab_unavailable"
    else:
        code = "gitlab_http_error"
    return ProjectAccessError(code, stage=stage, http_status=status)


def transport_failure(exc: httpx.HTTPError, stage: str = "resource") -> ProjectAccessError:
    if isinstance(exc, httpx.TimeoutException):
        return ProjectAccessError("timeout", stage=stage)
    cause: BaseException | None = exc
    for _ in range(8):
        if isinstance(cause, ssl.SSLCertVerificationError):
            return ProjectAccessError("tls_verification_failed", stage=stage)
        cause = cause.__cause__ if cause is not None else None
    # This exact static string is emitted by our TLS policy, not copied back.
    if str(exc) == "GitLab API redirect refused; configure the final endpoint directly":
        return ProjectAccessError("redirect_refused", stage=stage)
    return ProjectAccessError("transport_error", stage=stage)


def visible_access_errors(fn):
    """MCP registration wrapper: expected access failures survive error masking.

    Success payloads are unchanged. ok=false is diagnostic data, not file
    content or readiness, even if the MCP transport itself succeeded.
    """
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ProjectAccessError as exc:
            return exc.result()
    return wrapped


def _validate_probe(ref: str, files: list[str] | None) -> list[str]:
    if (
        not isinstance(ref, str) or len(ref) > 512
        or any(ord(c) < 33 or ord(c) == 127 for c in ref)
        or any(c in ref for c in ("\\", ":", "?", "#", "%"))
        or any(part in {".", ".."} for part in ref.split("/"))
        or ref.startswith(("-", "/"))
        or files is not None and not isinstance(files, list)
    ):
        raise ProjectAccessError("invalid_probe_input", stage="input")
    out = files if files is not None else []
    if len(out) > MAX_REQUIRED_FILES:
        raise ProjectAccessError("invalid_probe_input", stage="input")
    for path in out:
        if (
            not isinstance(path, str) or not path or len(path) > 512
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(c in path for c in ("\\", ":", "?", "#", "%"))
            or any(p in {"", ".", ".."} for p in path.split("/"))
            or any(p == ".env" or p.startswith(".env.") for p in path.split("/"))
        ):
            raise ProjectAccessError("invalid_probe_input", stage="input")
    if len(set(out)) != len(out):
        raise ProjectAccessError("invalid_probe_input", stage="input")
    return out


async def check_project_access(
    project: str,
    *,
    allowed: set[str],
    token_present: bool,
    client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]],
    base_url: str,
    ref: str = "",
    required_files: list[str] | None = None,
) -> dict[str, Any]:
    """Check policy -> project -> pinned ref -> file metadata; never write.

    The client factory is invoked only after the local-policy/token checks.
    File probes use HEAD at the resolved commit, never download file contents.
    """
    report: dict[str, Any] = {
        "ok": False, "scope": "read_only_project_preflight",
        "policy_source": "GITLAB_ALLOWED_PROJECTS",
        "local_allowed": None, "remote_checked": False,
        "project_exists": None, "resolved_commit_sha": None,
        "files": [], "required_files_complete": False,
        "writes_performed": False, "write_access_checked": False,
    }
    stage = "local_policy"
    try:
        key = assert_project_allowed(project, set())
        report["project"] = key
        assert_project_allowed(key, allowed)
        report["local_allowed"] = True
        files = _validate_probe(ref, required_files)
        report["requested_ref"] = ref or None
        if not token_present:
            raise ProjectAccessError("credential_missing", stage="credential")

        async def probe() -> None:
            nonlocal stage
            async with client_factory() as client:
                async def request(method: str, path: str, params=None) -> dict[str, Any]:
                    report["remote_checked"] = True
                    async with client.stream(method, f"{base_url}/api/v4{path}", params=params) as response:
                        if response.status_code != 200:
                            raise http_failure(response.status_code, stage)
                        if method == "HEAD":
                            return {}  # No file content or response headers are returned.
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > MAX_METADATA_BYTES:
                                raise ProjectAccessError("invalid_response", stage=stage)
                            body.extend(chunk)
                        try:
                            data = json.loads(body)
                        except (ValueError, UnicodeError):
                            raise ProjectAccessError("invalid_response", stage=stage) from None
                        if not isinstance(data, dict):
                            raise ProjectAccessError("invalid_response", stage=stage)
                        return data

                prefix = f"/projects/{quote(key, safe='')}"
                stage = "project"
                data = await request("GET", prefix)
                pid = data.get("id")
                path = data.get("path_with_namespace")
                if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(path, str):
                    raise ProjectAccessError("invalid_response", stage=stage)
                # Sanity-check returned identity; never silently use a redirected project.
                try:
                    returned_key = assert_project_allowed(path, set())
                except ProjectAccessError:
                    raise ProjectAccessError("invalid_response", stage=stage) from None
                if key != str(pid) and key != returned_key:
                    raise ProjectAccessError("invalid_response", stage=stage)
                report.update(project_exists=True, project_id=pid, path_with_namespace=path)
                if data.get("empty_repo") is True:
                    raise ProjectAccessError("repository_empty", stage="ref")
                branch = data.get("default_branch")
                effective_ref = ref or branch
                if not isinstance(effective_ref, str) or not effective_ref:
                    raise ProjectAccessError("default_branch_unavailable", stage="ref")
                try:
                    _validate_probe(effective_ref, [])
                except ProjectAccessError:
                    raise ProjectAccessError("invalid_response", stage="ref") from None
                report["ref"] = effective_ref
                stage = "ref"
                commit = await request("GET", prefix + "/repository/commits/" + quote(effective_ref, safe=""), {"stats": False})
                sha = commit.get("id")
                if not isinstance(sha, str) or not _SHA.fullmatch(sha):
                    raise ProjectAccessError("invalid_response", stage=stage)
                report["resolved_commit_sha"] = sha
                stage = "required_files"
                for path in files:
                    try:
                        await request("HEAD", prefix + "/repository/files/" + quote(path, safe=""), {"ref": sha})
                    except ProjectAccessError as exc:
                        report["files"].append({"path": path, "readable": False, "error_code": exc.code})
                        raise
                    report["files"].append({"path": path, "readable": True})
                report.update(ok=True, status="ready", required_files_checked=len(files),
                              required_files_complete=True)

        await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
    except ProjectAccessError as exc:
        if exc.code == "project_not_allowlisted":
            report["local_allowed"] = False
        report.update(exc.result())
    except (asyncio.TimeoutError, TimeoutError):
        report.update(ProjectAccessError("timeout", stage=stage).result())
    except httpx.HTTPError as exc:
        report.update(transport_failure(exc, stage).result())
    except (OSError, ValueError, RuntimeError):
        # Configuration and client construction failures may include secrets/paths.
        report.update(ProjectAccessError("configuration_error", stage=stage).result())
    return report


def _build_parser(*, parser_class=argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = parser_class(description="Read-only project access preflight; never grants access or creates a project.")
    parser.add_argument("project")
    parser.add_argument("--ref", default="")
    parser.add_argument("--require-file", action="append", default=[], dest="required_files")
    return parser


def main(
    argv: list[str] | None = None, *, resolved_settings: AgentSettings | None = None,
    child_context=None,
) -> int:
    """Standalone preflight: no workspace/cache, worker, Git, or policy writes."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        from .config import AgentSettings
        from .tls import api_client_options
        settings = AgentSettings.load() if resolved_settings is None else resolved_settings

        def factory():
            return httpx.AsyncClient(
                headers={"PRIVATE-TOKEN": settings.api_token, "Accept": "application/json"},
                timeout=10, trust_env=settings.api_trust_env,
                **api_client_options(settings.gitlab_base_url, verify_ssl=settings.api_verify_ssl,
                                     ca_bundle=settings.api_ca_bundle, asynchronous=True),
            )

        result = asyncio.run(check_project_access(
            args.project, allowed=settings.allowed_projects, token_present=bool(settings.api_token),
            client_factory=factory, base_url=settings.gitlab_base_url,
            ref=args.ref, required_files=args.required_files,
        ))
        # The read-only bridge can allow an empty list, while workspace writes
        # intentionally require explicit authorization by default. Report both.
        result["workspace_policy_allowed"] = (
            args.project in settings.allowed_projects
            if settings.allowed_projects else not settings.require_write_allowlist
        )
    except (OSError, ValueError, RuntimeError) as exc:
        if child_context is not None:
            from .upgrade.service_children import ServiceChildBindingError
            if isinstance(exc, ServiceChildBindingError):
                raise
        result = ProjectAccessError("configuration_error", stage="configuration").result()
    result["mcp_connection_checked"] = False
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
