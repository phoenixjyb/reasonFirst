"""Small green baseline, NOT proof of the future EXERCISE.md requirements."""
import unittest

from clip_summary import summarize_clips


class BaselineTests(unittest.TestCase):
    def test_ordinary_list(self):
        self.assertEqual(summarize_clips([1, 2, 3]), {
            "count": 3, "total_seconds": 6, "mean_seconds": 2.0,
        })

    def test_empty(self):
        self.assertEqual(summarize_clips([]), {
            "count": 0, "total_seconds": 0, "mean_seconds": 0.0,
        })

    def test_generator(self):
        self.assertEqual(summarize_clips(x for x in [1, 2]), {
            "count": 2, "total_seconds": 3, "mean_seconds": 1.5,
        })

    def test_fractional_seconds(self):
        self.assertEqual(summarize_clips([0.5, 1.5])["total_seconds"], 2.0)

    def test_input_is_not_mutated(self):
        durations = [3, 1, 2]
        summarize_clips(durations)
        self.assertEqual(durations, [3, 1, 2])


if __name__ == "__main__":
    unittest.main()
