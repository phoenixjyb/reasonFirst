from __future__ import annotations

import unittest

from gitlab_agent.finish_policy import evaluate_finish_gates


def base_args() -> dict[str, object]:
    return {
        "project_context": {
            "protected_paths": ["deploy/"],
        },
        "project_config_found": True,
        "validations": [
            {
                "name": "tests",
                "argv": ["pytest", "-q"],
                "required": True,
                "passed": True,
            }
        ],
        "changed_paths": ["src/a.py"],
        "reviewability": {"ok": True},
        "review_diff": {"truncated": False},
        "history_scan": {
            "coverage_complete": True,
            "findings": [],
        },
        "secret_findings": [],
        "dirty": True,
        "commits_ahead_of_base": 0,
        "commit_message": "fix: candidate",
        "pushed": False,
        "merge_request_url": None,
        "allow_protected": False,
        "allow_secret_match": False,
    }


class SharedFinishGateTests(unittest.TestCase):
    def test_clean_evidence_is_unblocked(self) -> None:
        result = evaluate_finish_gates(**base_args())
        self.assertTrue(result["ok"])
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["secret_scan"]["coverage_complete"])

    def test_required_validation_failure_blocks(self) -> None:
        args = base_args()
        args["validations"] = [
            {
                "name": "tests",
                "required": True,
                "passed": False,
            }
        ]
        result = evaluate_finish_gates(**args)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("required project validation" in item for item in result["blockers"])
        )

    def test_optional_validation_failure_does_not_block(self) -> None:
        args = base_args()
        args["validations"] = [
            {
                "name": "optional",
                "required": False,
                "passed": False,
            }
        ]
        result = evaluate_finish_gates(**args)
        self.assertTrue(result["ok"])

    def test_protected_paths_use_same_override_semantics(self) -> None:
        args = base_args()
        args["changed_paths"] = ["deploy/prod.yaml", ".actualcoder.yaml"]
        blocked = evaluate_finish_gates(**args)
        self.assertFalse(blocked["ok"])
        self.assertEqual(
            blocked["protected_path_changes"],
            ["deploy/prod.yaml", ".actualcoder.yaml"],
        )

        args["allow_protected"] = True
        allowed = evaluate_finish_gates(**args)
        self.assertTrue(allowed["ok"])
        self.assertTrue(
            any("explicitly allowed" in item for item in allowed["warnings"])
        )

    def test_reviewability_and_truncated_review_fail_closed(self) -> None:
        args = base_args()
        args["reviewability"] = {"ok": False}
        not_reviewable = evaluate_finish_gates(**args)
        self.assertFalse(not_reviewable["ok"])
        self.assertFalse(not_reviewable["secret_scan"]["coverage_complete"])

        args = base_args()
        args["review_diff"] = {"truncated": True}
        truncated = evaluate_finish_gates(**args)
        self.assertFalse(truncated["ok"])
        self.assertTrue(
            any("review diff was truncated" in item for item in truncated["blockers"])
        )

    def test_incomplete_history_and_secret_findings_block(self) -> None:
        args = base_args()
        args["history_scan"] = {
            "coverage_complete": False,
            "findings": [],
            "error": "incomplete",
        }
        result = evaluate_finish_gates(**args)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("history secret coverage" in item for item in result["blockers"])
        )

        args = base_args()
        args["secret_findings"] = [{"kind": "token"}]
        result = evaluate_finish_gates(**args)
        self.assertFalse(result["ok"])
        self.assertFalse(result["secret_scan"]["ok"])

        args["allow_secret_match"] = True
        overridden = evaluate_finish_gates(**args)
        self.assertTrue(overridden["ok"])
        self.assertTrue(overridden["secret_scan"]["overridden"])

    def test_publication_state_and_empty_candidate_rules_are_shared(self) -> None:
        args = base_args()
        args["pushed"] = True
        pushed_without_mr = evaluate_finish_gates(**args)
        self.assertFalse(pushed_without_mr["ok"])
        self.assertTrue(
            any("without a recorded Merge Request" in item for item in pushed_without_mr["blockers"])
        )

        args = base_args()
        args["dirty"] = False
        args["commits_ahead_of_base"] = 0
        args["commit_message"] = None
        empty = evaluate_finish_gates(**args)
        self.assertFalse(empty["ok"])
        self.assertTrue(
            any("no changes or commits" in item for item in empty["blockers"])
        )


if __name__ == "__main__":
    unittest.main()
