import ast
import json
import unittest

import test_worker_recipe as fixture
from gitlab_agent.python_runtime import handoff_runtime_guidance


class WorkerRecipeOrderTests(unittest.TestCase):
    def test_duplicate_names_keep_each_original_timeout(self):
        case = fixture.WorkerRecipeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        context = {"validation_commands": [
            {"name": "repeat", "argv": ["python3", "-V"], "timeout_seconds": 3},
            {"name": "unbound", "argv": ["git", "status"], "timeout_seconds": 8},
            {"name": "repeat", "argv": ["python3", "-V"], "timeout_seconds": 9},
        ]}
        text, evidence = handoff_runtime_guidance(case.fixture.manager, case.fixture.wid, context)
        source_line = next(line for line in text.splitlines() if line.startswith("RECIPE_JSON = "))
        payload = json.loads(ast.literal_eval(source_line.split(" = ", 1)[1]))
        self.assertEqual([item["timeout_seconds"] for item in payload["recipes"]], [3, 9])
        self.assertEqual(len(evidence), 2)
        self.assertEqual(text.count("def execute_recipe(payload):"), 1)


if __name__ == "__main__":
    unittest.main()
