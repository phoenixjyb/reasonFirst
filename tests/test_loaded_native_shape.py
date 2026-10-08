"""Bounded native-response diagnostics; not replacement observation evidence."""
from __future__ import annotations

import copy
import json
import unittest

import test_loaded_fixture_reporting as support

fixture, harness = support.fixture, support.harness
SECRET = 'fixture-' + 'sensitive-value'
SAVED = {
    'Label': 'com.reasonfirst.fixture.loaded-synthetic',
    'ProgramArguments': ['/bin/sleep', '90'],
    'WorkingDirectory': '/synthetic/private-cwd',
    'EnvironmentVariables': {'RF_SYNTHETIC_FIXTURE': SECRET},
}


class NativeShapeTests(unittest.TestCase):
    def shape(self, job):
        before = copy.deepcopy(job)
        result = fixture.native_shape(job, SAVED)
        harness.validate_native_shape(result)
        self.assertEqual(job, before)
        encoded = json.dumps(result)
        for value in (SECRET, SAVED['WorkingDirectory'], SAVED['Label'], '/bin/sleep'):
            self.assertNotIn(value, encoded)
        return result

    def test_complete_dictionary_reports_only_fixed_types_and_counts(self):
        result = self.shape(SAVED)
        self.assertTrue(result['traversal_complete'])
        self.assertEqual(result['selected_top_level_types'], {
            'Program': 'absent', 'ProgramArguments': 'array',
            'WorkingDirectory': 'string', 'EnvironmentVariables': 'dictionary'})
        self.assertEqual(result['occurrences'], {
            'working_directory_key': 1, 'environment_variables_key': 1,
            'fixture_cwd_value': 1, 'fixture_environment_value': 1})

    def test_ci_observed_subset_does_not_invent_the_missing_values(self):
        job = {'Label': SAVED['Label'], 'PID': 12345,
               'ProgramArguments': SAVED['ProgramArguments']}
        result = self.shape(job)
        self.assertTrue(result['traversal_complete'])
        self.assertEqual(result['occurrences'], {key: 0 for key in result['occurrences']})
        comparison = fixture.loaded._compare(SAVED, job)
        self.assertFalse(comparison['selected_launch_fields_match'])
        self.assertEqual(comparison['selected_fields']['WorkingDirectory'], 'not_reported')
        self.assertEqual(comparison['selected_fields']['EnvironmentVariables'], 'not_reported')

    def test_nested_known_keys_detected_but_not_promoted_to_loaded_fields(self):
        job = {'Config': copy.deepcopy(SAVED)}
        result = self.shape(job)
        self.assertEqual(result['occurrences']['working_directory_key'], 1)
        self.assertEqual(result['occurrences']['environment_variables_key'], 1)
        self.assertEqual(result['occurrences']['fixture_cwd_value'], 1)
        self.assertEqual(result['occurrences']['fixture_environment_value'], 1)
        self.assertEqual(set(result['selected_top_level_types'].values()), {'absent'})
        self.assertFalse(fixture.loaded._compare(SAVED, job)['selected_launch_fields_match'])

    def test_alternate_names_detected_by_synthetic_value_without_echoing_names(self):
        job = {SECRET: [SAVED['WorkingDirectory'], {'unreviewed-alias': SECRET}]}
        result = self.shape(job)
        self.assertEqual(result['occurrences']['working_directory_key'], 0)
        self.assertEqual(result['occurrences']['environment_variables_key'], 0)
        self.assertGreater(result['occurrences']['fixture_cwd_value'], 0)
        self.assertGreater(result['occurrences']['fixture_environment_value'], 0)
        self.assertNotIn('unreviewed-alias', json.dumps(result))

    def test_large_container_is_explicitly_incomplete_not_absence(self):
        result = self.shape({'items': [0] * 5000 + [SECRET]})
        self.assertFalse(result['traversal_complete'])
        self.assertLessEqual(result['visited_nodes'], 4096)

    def test_excessive_nesting_is_explicitly_incomplete(self):
        job = {'leaf': SECRET}
        for _ in range(20):
            job = {'nested': job}
        result = self.shape(job)
        self.assertFalse(result['traversal_complete'])
        self.assertLessEqual(result['max_depth_seen'], 16)

    def test_cycles_cannot_make_diagnostic_walk_unbounded(self):
        job = {}; job['cycle'] = job
        result = fixture.native_shape(job, SAVED)
        self.assertFalse(result['traversal_complete'])
        harness.validate_native_shape(result)

    def test_native_scalar_types_do_not_become_arbitrary_type_names(self):
        for value, tag in ((True, 'boolean'), (2, 'integer'), (1.5, 'real'),
                           (b'sensitive', 'data'), (None, 'other')):
            with self.subTest(tag=tag):
                result = self.shape({'WorkingDirectory': value})
                self.assertEqual(result['selected_top_level_types']['WorkingDirectory'], tag)

    def test_new_diagnostic_does_not_make_original_native_failure_succeed(self):
        for name in ('WorkingDirectory', 'EnvironmentVariables'):
            with self.subTest(name=name):
                report, _ = support.FixtureReportingTests().invoke(missing=name)
                self.assertFalse(report['ok'])
                self.assertEqual(report['error_code'], 'native_fields_incomplete_or_different')
                self.assertEqual(report['native_shape']['selected_top_level_types'][name], 'absent')
                self.assertTrue(report['cleanup_confirmed'])

    def test_no_native_data_means_no_shape_claim(self):
        report, _ = support.FixtureReportingTests().invoke(
            native_error=fixture.loaded.LoadedServiceError('native_query_failed'))
        self.assertNotIn('native_shape', report)
        self.assertFalse(report['ok'])

    def test_harness_preserves_valid_shape_without_using_it_for_acceptance(self):
        report = support.failure_report()
        report['native_shape'] = fixture.native_shape(SAVED, SAVED)
        self.assertEqual(harness.loaded_fixture_report(json.dumps(report).encode()), report)
        self.assertFalse(report['ok'])

    def test_harness_rejects_raw_values_unknown_fields_and_unbounded_counts(self):
        shape = fixture.native_shape(SAVED, SAVED)
        for change in ({'extra': SECRET}, {'scope': SECRET}, {'traversal_complete': 1},
                       {'top_level_entries': -1}, {'visited_nodes': 5000},
                       {'visited_nodes': True}, {'max_depth_seen': 17},
                       {'selected_top_level_types': {'WorkingDirectory': SECRET}},
                       {'occurrences': {'fixture_cwd_value': SECRET}}):
            with self.subTest(change=change):
                report = {**support.failure_report(), 'native_shape': {**shape, **change}}
                with self.assertRaises(RuntimeError) as caught:
                    harness.loaded_fixture_report(json.dumps(report).encode())
                self.assertNotIn(SECRET, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
