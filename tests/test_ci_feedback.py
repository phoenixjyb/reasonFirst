from __future__ import annotations

import unittest
from types import SimpleNamespace

from gitlab_agent.ci_feedback import collect_ci_feedback


class FakeManager:
    def __init__(self, *, head: str = "head123") -> None:
        self.head = head
        self.state = SimpleNamespace(
            pushed=True,
            merge_request_url="https://gitlab.example.test/team/project/-/merge_requests/3",
        )

    def status(self, workspace_id: str) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "project": "team/project",
            "branch": "chatgpt/fix-abc",
            "head": self.head,
        }

    def get_state(self, workspace_id: str) -> SimpleNamespace:
        return self.state


class FakeAPI:
    def __init__(
        self,
        *,
        pipeline_sha: str = "head123",
        pipeline_status: str = "failed",
        pipelines: list[dict[str, object]] | None = None,
        trace_content: str | None = None,
    ) -> None:
        self.pipeline_sha = pipeline_sha
        self.pipeline_status = pipeline_status
        self._pipelines = pipelines
        self.trace_content = trace_content
        self.trace_calls: list[int] = []

    def pipelines(
        self,
        project: str,
        *,
        ref: str,
        per_page: int = 20,
    ) -> list[dict[str, object]]:
        if self._pipelines is not None:
            return self._pipelines
        return [
            {
                "id": 41,
                "iid": 41,
                "status": self.pipeline_status,
                "ref": ref,
                "sha": self.pipeline_sha,
                "source": "push",
                "web_url": "https://gitlab.example.test/pipelines/41",
                "created_at": "2026-09-19T00:00:00Z",
                "updated_at": "2026-09-19T00:01:00Z",
            }
        ]

    def pipeline_jobs(
        self,
        project: str,
        pipeline_id: int,
        *,
        include_retried: bool = False,
        per_page: int = 100,
    ) -> list[dict[str, object]]:
        return [
            {
                "id": 1001,
                "name": "unit-tests",
                "stage": "test",
                "status": "failed",
                "allow_failure": False,
                "failure_reason": "script_failure",
                "web_url": "https://gitlab.example.test/jobs/1001",
            },
            {
                "id": 1002,
                "name": "lint",
                "stage": "test",
                "status": "success",
                "allow_failure": False,
                "web_url": "https://gitlab.example.test/jobs/1002",
            },
        ]

    def job_trace_tail(
        self,
        project: str,
        job_id: int,
        *,
        tail_bytes: int,
    ) -> dict[str, object]:
        self.trace_calls.append(job_id)
        token = "gl" + "pat-" + "abcdefghijklmnop"
        content = self.trace_content or (
            "\x1b[31mFAILED test_timeout\x1b[0m\n"
            f"GITLAB_TOKEN={token}\n"
            "AssertionError: expected 3 got 4\n"
        )
        return {
            "content": content,
            "truncated": False,
            "original_text_bytes": len(content.encode("utf-8")),
            "tail_bytes": tail_bytes,
        }


class CIFeedbackTests(unittest.TestCase):
    def test_failed_pipeline_collects_redacted_failed_job_context(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(),  # type: ignore[arg-type]
            api=FakeAPI(),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        self.assertTrue(result["found"])
        self.assertTrue(result["head_matches_pipeline"])
        self.assertFalse(result["pipeline_success"])
        self.assertEqual(len(result["failed_jobs"]), 1)
        self.assertEqual(len(result["blocking_failed_jobs"]), 1)
        self.assertEqual(len(result["failed_job_logs"]), 1)

        log = result["failed_job_logs"][0]
        self.assertNotIn("glpat-", str(log["content"]))
        self.assertNotIn("\x1b[31m", str(log["content"]))
        self.assertIn("[REDACTED", str(log["content"]))
        self.assertIn("untrusted", str(result["repair_context"]).lower())

    def test_custom_executor_failure_is_classified_as_runner_configuration(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(),  # type: ignore[arg-type]
            api=FakeAPI(
                trace_content=(
                    'Preparing the "custom" executor\n'
                    "WARNING: custom executor is missing RunExec\n"
                    "ERROR: Job failed: custom executor is missing RunExec\n"
                )
            ),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        diagnosis = result["diagnosis"]
        self.assertEqual(diagnosis["category"], "runner_configuration")
        self.assertEqual(diagnosis["code"], "custom_executor_missing_runexec")
        self.assertFalse(diagnosis["worker_repair_recommended"])
        self.assertIn("do not ask the coding worker", str(diagnosis["next_action"]).lower())
        self.assertIn("runner_configuration", str(result["repair_context"]))

    def test_stale_pipeline_is_reported(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(head="new-head"),  # type: ignore[arg-type]
            api=FakeAPI(pipeline_sha="old-head"),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        self.assertTrue(result["found"])
        self.assertTrue(result["stale_for_workspace"])
        self.assertFalse(result["head_matches_pipeline"])
        self.assertTrue(any("stale" in item.lower() for item in result["warnings"]))

    def test_no_pipeline_is_nonfatal_inspection_result(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(),  # type: ignore[arg-type]
            api=FakeAPI(pipelines=[]),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        self.assertFalse(result["found"])
        self.assertIsNone(result["pipeline"])
        self.assertIn("no pipeline", str(result["repair_context"]).lower())

    def test_success_context_does_not_invent_failure(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(),  # type: ignore[arg-type]
            api=FakeAPI(pipeline_status="success"),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        context = str(result["repair_context"])
        self.assertIn("pipeline succeeded", context.lower())
        self.assertIn("no ci failure to repair", context.lower())
        self.assertNotIn("diagnose the code/build failure", context)

    def test_running_pipeline_warns_results_may_change(self) -> None:
        result = collect_ci_feedback(
            manager=FakeManager(),  # type: ignore[arg-type]
            api=FakeAPI(pipeline_status="running"),  # type: ignore[arg-type]
            workspace_id="abc123def456",
        )

        self.assertFalse(result["pipeline_complete"])
        self.assertTrue(
            any("may still change" in item.lower() for item in result["warnings"])
        )
        self.assertIn(
            "not complete yet",
            str(result["repair_context"]).lower(),
        )


if __name__ == "__main__":
    unittest.main()
