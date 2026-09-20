from __future__ import annotations

from typing import Any, TYPE_CHECKING
from urllib.parse import quote

import httpx

if TYPE_CHECKING:
    from .config import AgentSettings
from .log_evidence import MAX_TAIL_BYTES, read_trace_tail


class GitLabAPI:
    """Small synchronous GitLab API helper for local CLI workflows."""

    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_token:
            raise RuntimeError(
                "GITLAB_TOKEN is required for GitLab API operations such as checkout-mr"
            )
        return {
            "PRIVATE-TOKEN": self.settings.api_token,
            "Accept": "application/json",
            "User-Agent": "reasonfirst-gitlab-control/0.3",
        }

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers=self._headers(),
            verify=self.settings.api_verify_ssl,
            trust_env=self.settings.api_trust_env,
            follow_redirects=True,
            timeout=30.0,
        )

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.settings.gitlab_base_url}/api/v4{path}"
        try:
            with self._client() as client:
                response = (
                    client.get(url)
                    if params is None
                    else client.get(url, params=params)
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"GitLab API request failed for {url}: {exc}") from exc

        if response.is_error:
            detail = response.text[:2000]
            raise RuntimeError(
                f"GitLab API returned HTTP {response.status_code} for {path}: {detail}"
            )
        return response.json()

    def get_text(self, path: str) -> str:
        url = f"{self.settings.gitlab_base_url}/api/v4{path}"
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"GitLab API request failed for {url}: {exc}") from exc

        if response.is_error:
            detail = response.text[:2000]
            raise RuntimeError(
                f"GitLab API returned HTTP {response.status_code} for {path}: {detail}"
            )
        return response.text

    def get_text_tail(
        self,
        path: str,
        *,
        tail_bytes: int,
    ) -> dict[str, object]:
        """Return shared bounded, sanitized trace evidence (not raw log text)."""
        url = f"{self.settings.gitlab_base_url}/api/v4{path}"
        with self._client() as client:
            return read_trace_tail(client, url, tail_bytes=tail_bytes)

    def merge_request(self, project: str, iid: int) -> dict[str, Any]:
        encoded = quote(project.strip(), safe="")
        data = self.get_json(f"/projects/{encoded}/merge_requests/{iid}")
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected GitLab MR response")
        return data

    def pipelines(
        self,
        project: str,
        *,
        ref: str,
        per_page: int = 20,
    ) -> list[dict[str, Any]]:
        encoded = quote(project.strip(), safe="")
        data = self.get_json(
            f"/projects/{encoded}/pipelines",
            params={
                "ref": ref,
                "per_page": max(1, min(per_page, 100)),
                "page": 1,
                "order_by": "id",
                "sort": "desc",
            },
        )
        if not isinstance(data, list):
            raise RuntimeError("Unexpected GitLab pipelines response")
        return [item for item in data if isinstance(item, dict)]

    def pipeline_jobs(
        self,
        project: str,
        pipeline_id: int,
        *,
        include_retried: bool = False,
        per_page: int = 100,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        encoded = quote(project.strip(), safe="")
        page_size = max(1, min(per_page, 100))
        page_cap = max(1, min(max_pages, 20))
        items: list[dict[str, Any]] = []

        for page in range(1, page_cap + 1):
            data = self.get_json(
                f"/projects/{encoded}/pipelines/{pipeline_id}/jobs",
                params={
                    "include_retried": str(include_retried).lower(),
                    "per_page": page_size,
                    "page": page,
                },
            )
            if not isinstance(data, list):
                raise RuntimeError("Unexpected GitLab pipeline jobs response")

            items.extend(item for item in data if isinstance(item, dict))
            if len(data) < page_size:
                return items

        raise RuntimeError(
            f"Pipeline {pipeline_id} has more than {page_cap * page_size} jobs; "
            "refusing to return potentially incomplete CI evidence"
        )

    def job_trace(self, project: str, job_id: int) -> str:
        """Compatibility text-only view; use job_trace_tail for scope metadata."""
        return str(self.job_trace_tail(project, job_id, tail_bytes=MAX_TAIL_BYTES)["content"])

    def job_trace_tail(
        self,
        project: str,
        job_id: int,
        *,
        tail_bytes: int,
    ) -> dict[str, object]:
        encoded = quote(project.strip(), safe="")
        return self.get_text_tail(
            f"/projects/{encoded}/jobs/{job_id}/trace",
            tail_bytes=tail_bytes,
        )
