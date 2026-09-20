from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gitlab_agent.gitlab_api import GitLabAPI


class GitLabAPITests(unittest.TestCase):
    def settings(self, token: str = "token") -> SimpleNamespace:
        return SimpleNamespace(
            gitlab_base_url="http://gitlab.example.internal",
            api_token=token,
            api_verify_ssl=True,
            api_trust_env=False,
        )

    def test_merge_request_uses_encoded_project_and_direct_network_mode(self) -> None:
        response = MagicMock()
        response.is_error = False
        response.json.return_value = {
            "iid": 42,
            "title": "Fix thing",
            "source_branch": "chatgpt/fix-thing-abc",
            "target_branch": "main",
        }

        client = MagicMock()
        client.get.return_value = response
        client_cm = MagicMock()
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = False

        with patch("gitlab_agent.gitlab_api.httpx.Client", return_value=client_cm) as cls:
            api = GitLabAPI(self.settings())
            result = api.merge_request("team/project", 42)

        self.assertEqual(result["iid"], 42)
        cls.assert_called_once()
        kwargs = cls.call_args.kwargs
        self.assertFalse(kwargs["trust_env"])
        self.assertTrue(kwargs["verify"])
        client.get.assert_called_once_with(
            "http://gitlab.example.internal/api/v4/projects/team%2Fproject/merge_requests/42"
        )

    def test_pipeline_helpers_use_expected_paths_and_params(self) -> None:
        response = MagicMock()
        response.is_error = False
        response.json.return_value = [
            {"id": 12, "status": "failed", "sha": "abc", "ref": "feature"}
        ]

        client = MagicMock()
        client.get.return_value = response
        client_cm = MagicMock()
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = False

        with patch("gitlab_agent.gitlab_api.httpx.Client", return_value=client_cm):
            api = GitLabAPI(self.settings())
            result = api.pipelines("team/project", ref="feature", per_page=7)

        self.assertEqual(result[0]["id"], 12)
        client.get.assert_called_once_with(
            "http://gitlab.example.internal/api/v4/projects/team%2Fproject/pipelines",
            params={
                "ref": "feature",
                "per_page": 7,
                "page": 1,
                "order_by": "id",
                "sort": "desc",
            },
        )

    def test_pipeline_jobs_paginates_until_short_page(self) -> None:
        api = GitLabAPI(self.settings())
        first_page = [
            {"id": index, "status": "success"}
            for index in range(100)
        ]
        second_page = [{"id": 100, "status": "failed"}]

        with patch.object(
            api,
            "get_json",
            side_effect=[first_page, second_page],
        ) as get_json:
            result = api.pipeline_jobs(
                "team/project",
                77,
                per_page=100,
                max_pages=3,
            )

        self.assertEqual(len(result), 101)
        self.assertEqual(result[-1]["id"], 100)
        self.assertEqual(get_json.call_count, 2)
        self.assertEqual(get_json.call_args_list[0].kwargs["params"]["page"], 1)
        self.assertEqual(get_json.call_args_list[1].kwargs["params"]["page"], 2)

    def test_pipeline_jobs_refuses_silent_pagination_truncation(self) -> None:
        api = GitLabAPI(self.settings())
        full_page = [{"id": index} for index in range(2)]

        with patch.object(
            api,
            "get_json",
            side_effect=[full_page, full_page],
        ):
            with self.assertRaisesRegex(RuntimeError, "potentially incomplete"):
                api.pipeline_jobs(
                    "team/project",
                    77,
                    per_page=2,
                    max_pages=2,
                )

    def test_job_trace_reads_text(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.iter_raw.return_value = [b"build failed\n"]

        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = response
        stream_cm.__exit__.return_value = False
        client = MagicMock()
        client.stream.return_value = stream_cm
        client_cm = MagicMock()
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = False

        with patch("gitlab_agent.gitlab_api.httpx.Client", return_value=client_cm):
            api = GitLabAPI(self.settings())
            result = api.job_trace("team/project", 99)

        self.assertEqual(result, "build failed\n")
        client.stream.assert_called_once_with(
            "GET",
            "http://gitlab.example.internal/api/v4/projects/team%2Fproject/jobs/99/trace",
            headers={"Accept-Encoding": "identity"}, follow_redirects=False, timeout=10.0,
        )

    def test_job_trace_tail_streams_and_caps_memory(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.iter_raw.return_value = [
            b"0123456789",
            b"abcdefghij",
            b"KLMNOPQRST",
        ]

        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = response
        stream_cm.__exit__.return_value = False

        client = MagicMock()
        client.stream.return_value = stream_cm
        client_cm = MagicMock()
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = False

        with patch("gitlab_agent.gitlab_api.httpx.Client", return_value=client_cm):
            api = GitLabAPI(self.settings())
            result = api.job_trace_tail(
                "team/project",
                99,
                tail_bytes=1000,
            )

        self.assertEqual(result["original_text_bytes"], 30)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            result["content"],
            "0123456789abcdefghijKLMNOPQRST",
        )
        client.stream.assert_called_once_with(
            "GET",
            "http://gitlab.example.internal/api/v4/projects/team%2Fproject/jobs/99/trace",
            headers={"Accept-Encoding": "identity"}, follow_redirects=False, timeout=10.0,
        )

    def test_job_trace_tail_truncates_to_requested_bound(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.iter_raw.return_value = [
            b"a" * 800,
            b"b" * 800,
        ]

        stream_cm = MagicMock()
        stream_cm.__enter__.return_value = response
        stream_cm.__exit__.return_value = False

        client = MagicMock()
        client.stream.return_value = stream_cm
        client_cm = MagicMock()
        client_cm.__enter__.return_value = client
        client_cm.__exit__.return_value = False

        with patch("gitlab_agent.gitlab_api.httpx.Client", return_value=client_cm):
            api = GitLabAPI(self.settings())
            result = api.job_trace_tail(
                "team/project",
                99,
                tail_bytes=1000,
            )

        self.assertEqual(result["original_text_bytes"], 1600)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(str(result["content"]).encode("utf-8")), 1000)
        self.assertTrue(str(result["content"]).endswith("b" * 800))

    def test_api_operations_require_gitlab_token(self) -> None:
        api = GitLabAPI(self.settings(token=""))
        with self.assertRaises(RuntimeError):
            api.merge_request("team/project", 1)


if __name__ == "__main__":
    unittest.main()
