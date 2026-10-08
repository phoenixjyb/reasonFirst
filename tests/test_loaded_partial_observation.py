"""Approved partial-observation contract; never a substitute activation gate.

The original four-field comparison still fails for missing native settings.
These regressions distinguish successful observation from verified equivalence.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import test_loaded_fixture_reporting as fixture_support
import test_loaded_service as service_support
from gitlab_agent.upgrade import loaded_service as loaded

SAVED = service_support.SAVED
LABEL = service_support.LABEL
CONTRACT = 'partial-selected-fields-v1'
STARTUP_BLOCKER = 'managed_startup_confirmation_not_verified'
MISSING_BLOCKER = 'loaded_launch_fields_differ_or_not_reported'
OPTIONAL = ('WorkingDirectory', 'EnvironmentVariables')


def partial_job():
    return {'Label': LABEL, 'Program': SAVED['ProgramArguments'][0],
            'ProgramArguments': list(SAVED['ProgramArguments']), 'PID': 123}


def partial_report():
    report = fixture_support.success_report()
    for name in OPTIONAL:
        report['selected_fields'][name] = 'not_reported'
        report['drift_fields'][name] = 'not_reported'
    report.update(field_coverage='partial', selected_launch_fields_match=False)
    report['activation_blockers'].append(MISSING_BLOCKER)
    return report


class PartialComparisonTests(unittest.TestCase):
    def test_native_subset_has_partial_coverage_not_full_match(self):
        job = partial_job()
        before = copy.deepcopy(job)
        result = loaded._compare(SAVED, job)
        self.assertEqual(result['field_coverage'], 'partial')
        self.assertEqual(result['unreported_fields'], list(OPTIONAL))
        self.assertTrue(result['reported_fields_match'])
        self.assertFalse(result['selected_launch_fields_match'])
        self.assertEqual(job, before)
        for name in OPTIONAL:
            self.assertEqual(result['selected_fields'][name], 'not_reported')
        for value in (service_support.SECRET, '/fixture/run.sh', '/fixture'):
            self.assertNotIn(value, json.dumps(result))

    def test_no_reported_fields_is_not_vacuous_success(self):
        result = loaded._compare(SAVED, {'Label': LABEL})
        self.assertEqual(result['field_coverage'], 'none')
        self.assertFalse(result['reported_fields_match'])
        self.assertFalse(result['selected_launch_fields_match'])
        self.assertEqual(len(result['unreported_fields']), 4)
        self.assertIn('no_running_pid_reported', loaded._observation_blockers(result))

    def test_complete_selected_fields_still_need_startup_confirmation(self):
        result = loaded._compare(SAVED, {**SAVED, 'PID': 123})
        self.assertEqual(result['field_coverage'], 'complete')
        self.assertEqual(result['unreported_fields'], [])
        self.assertTrue(result['reported_fields_match'])
        self.assertTrue(result['selected_launch_fields_match'])
        self.assertEqual(loaded._observation_blockers(result), [STARTUP_BLOCKER])

    def test_partial_mismatch_never_becomes_reported_match(self):
        result = loaded._compare(SAVED, {**partial_job(), 'ProgramArguments': ['/other']})
        self.assertEqual(result['field_coverage'], 'partial')
        self.assertFalse(result['reported_fields_match'])
        self.assertFalse(result['selected_launch_fields_match'])
        self.assertIn(MISSING_BLOCKER, loaded._observation_blockers(result))

    def test_one_available_config_field_does_not_fill_the_other(self):
        for name in OPTIONAL:
            with self.subTest(name=name):
                result = loaded._compare(SAVED, {**partial_job(), name: SAVED[name]})
                self.assertEqual(result['field_coverage'], 'partial')
                self.assertEqual(result['unreported_fields'], [k for k in OPTIONAL if k != name])
                self.assertFalse(result['selected_launch_fields_match'])


class PartialPublicInspectionTests(unittest.TestCase):
    def inspect(self, **kwargs):
        result = service_support.ObservationTests().run_inspection(**kwargs)
        self.assertEqual(result['observation_contract'], CONTRACT)
        self.assertFalse(result['managed_startup_confirmation_verified'])
        return result

    def test_public_partial_observation_retains_every_activation_blocker(self):
        result = self.inspect(queries=[partial_job(), partial_job()])
        self.assertTrue(result['ok'])
        self.assertEqual(result['comparison']['field_coverage'], 'partial')
        self.assertFalse(result['comparison']['selected_launch_fields_match'])
        self.assertTrue(result['comparison']['reported_fields_match'])
        for value in (STARTUP_BLOCKER, MISSING_BLOCKER, 'legacy_control_required_but_target_has_no_control'):
            self.assertIn(value, result['blockers'])
        self.assertFalse(result['ready_for_activation'])
        self.assertFalse(result['activation_authorized'])

    def test_even_complete_observation_without_prior_blockers_cannot_activate(self):
        review = {**service_support.REVIEW, 'blockers': []}
        result = self.inspect(reviews=[review]*3)
        self.assertTrue(result['ok'])
        self.assertTrue(result['comparison']['selected_launch_fields_match'])
        self.assertEqual(result['blockers'], [STARTUP_BLOCKER])
        self.assertFalse(result['ready_for_activation'])

    def test_intersample_field_disappearance_is_still_failure(self):
        first = {**partial_job(), 'WorkingDirectory': SAVED['WorkingDirectory']}
        result = self.inspect(queries=[first, partial_job()])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'loaded_job_changed')
        self.assertNotIn('comparison', result)

    def test_failed_observation_still_exposes_no_confirmation_or_comparison(self):
        result = self.inspect(queries=[loaded.LoadedServiceError('native_query_failed')])
        self.assertFalse(result['ok'])
        self.assertNotIn('comparison', result)
        self.assertFalse(result['managed_startup_confirmation_verified'])


class PartialNativeFixtureTests(unittest.TestCase):
    def fixture(self, **kwargs):
        return fixture_support.FixtureReportingTests().invoke(**kwargs)[0]

    def test_actual_ci_shaped_subset_completes_only_narrow_contract(self):
        report = self.fixture(missing=OPTIONAL)
        self.assertTrue(report['ok'])
        self.assertEqual(report['observation_contract'], CONTRACT)
        self.assertEqual(report['field_coverage'], 'partial')
        self.assertFalse(report['selected_launch_fields_match'])
        self.assertEqual(report['drift_fields']['ProgramArguments'], 'differs')
        self.assertTrue(report['saved_file_drift_detected'])
        for name in OPTIONAL:
            self.assertEqual(report['selected_fields'][name], 'not_reported')
            self.assertEqual(report['drift_fields'][name], 'not_reported')
        self.assertIn(MISSING_BLOCKER, report['activation_blockers'])
        self.assertIn(STARTUP_BLOCKER, report['activation_blockers'])
        self.assertTrue(report['cleanup_confirmed'])
        self.assertFalse(report['managed_startup_confirmation_verified'])
        self.assertFalse(report['activation_authorized'])
        self.assertFalse(report['ready_for_activation'])

    def test_missing_program_arguments_still_fails_native_contract(self):
        report = self.fixture(missing='ProgramArguments')
        self.assertFalse(report['ok'])
        self.assertEqual(report['error_code'], 'native_fields_incomplete_or_different')
        self.assertTrue(report['cleanup_confirmed'])

    def test_present_but_wrong_optional_fields_cannot_be_ignored(self):
        for name in OPTIONAL:
            with self.subTest(name=name):
                report = self.fixture(mismatch=name)
                self.assertFalse(report['ok'])
                self.assertEqual(report['selected_fields'][name], 'differs')
                self.assertEqual(report['error_code'], 'native_fields_incomplete_or_different')

    def test_api_reading_saved_file_instead_of_loaded_argv_still_fails(self):
        report = self.fixture(missing=OPTIONAL, drift_failure=True)
        self.assertFalse(report['ok'])
        self.assertEqual(report['error_code'], 'saved_loaded_drift_not_detected')
        self.assertFalse(report['saved_file_drift_detected'])

    def test_unchanged_pid_is_still_required_for_drift_check(self):
        report = self.fixture(missing=OPTIONAL, changed_native='PID')
        self.assertFalse(report['ok'])
        self.assertEqual(report['error_code'], 'fixture_pid_changed')

    def test_native_program_changes_still_fail_comparison(self):
        report = self.fixture(missing=OPTIONAL, changed_native='Program')
        self.assertFalse(report['ok'])
        self.assertEqual(report['error_code'], 'saved_loaded_drift_not_detected')

    def test_filling_unreported_optional_field_from_saved_plist_is_refused(self):
        for name in OPTIONAL:
            with self.subTest(name=name):
                report = self.fixture(missing=OPTIONAL, retained_optional_drift=name)
                self.assertFalse(report['ok'])
                self.assertEqual(report['error_code'], 'saved_loaded_drift_not_detected')

    def test_partial_observation_does_not_hide_cleanup_failure(self):
        report = self.fixture(missing=OPTIONAL, cleanup_failure=True)
        self.assertFalse(report['ok'])
        self.assertFalse(report['cleanup_confirmed'])
        self.assertEqual(report['error_code'], 'fixture_cleanup_not_confirmed')


class PartialReportValidationTests(unittest.TestCase):
    def reject(self, value):
        with self.assertRaises(RuntimeError):
            fixture_support.harness.loaded_fixture_report(json.dumps(value).encode())

    def test_valid_partial_report_keeps_unknown_fields_and_blockers(self):
        value = partial_report()
        self.assertEqual(fixture_support.harness.loaded_fixture_report(json.dumps(value).encode()), value)

    def test_partial_report_cannot_claim_complete_coverage_or_full_match(self):
        for changes in ({'field_coverage': 'complete'}, {'selected_launch_fields_match': True}):
            with self.subTest(changes=changes):
                self.reject({**partial_report(), **changes})

    def test_removing_startup_blocker_is_rejected_for_complete_and_partial_reports(self):
        for value in (partial_report(), fixture_support.success_report()):
            value['activation_blockers'].remove(STARTUP_BLOCKER)
            self.reject(value)

    def test_unreported_fields_require_incomplete_comparison_blocker(self):
        value = partial_report()
        value['activation_blockers'] = [STARTUP_BLOCKER]
        self.reject(value)

    def test_no_report_can_authorize_or_attest_startup(self):
        for value in (partial_report(), fixture_support.failure_report()):
            for flag in ('activation_authorized', 'ready_for_activation', 'managed_startup_confirmation_verified'):
                with self.subTest(flag=flag):
                    self.reject({**value, flag: True})

    def test_unchanged_argv_cannot_claim_saved_loaded_drift(self):
        value = partial_report()
        value['drift_fields']['ProgramArguments'] = 'matches'
        self.reject(value)

    def test_unknown_config_cannot_be_relabelled_as_detected_drift(self):
        for name in OPTIONAL:
            value = partial_report()
            value['drift_fields'][name] = 'differs'
            self.reject(value)

    def test_old_fixture_schema_is_not_silently_accepted(self):
        value = fixture_support.success_report()
        value.pop('observation_contract')
        self.reject(value)
        value = fixture_support.success_report()
        value['observation_contract'] = 'four-field-equivalence'
        self.reject(value)

    def test_empty_observation_and_duplicate_or_unknown_blockers_fail(self):
        value = partial_report()
        value.update(selected_fields={}, field_coverage='not_observed')
        self.reject(value)
        for blockers in ([STARTUP_BLOCKER, STARTUP_BLOCKER], [STARTUP_BLOCKER, 'secret-value'], [True]):
            self.reject({**partial_report(), 'activation_blockers': blockers})

    def test_reported_optional_mismatch_cannot_hide_in_success(self):
        value = partial_report()
        value['selected_fields']['WorkingDirectory'] = 'differs'
        self.reject(value)

    def test_missing_essential_fields_or_pid_is_not_successful_fixture_acceptance(self):
        value = partial_report()
        value['selected_fields']['ProgramArguments'] = 'not_reported'
        self.reject(value)
        value = partial_report()
        value['activation_blockers'].append('no_running_pid_reported')
        self.reject(value)


class PartialDocumentationTests(unittest.TestCase):
    def test_both_guides_explain_scope_change_and_original_failure(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('LOADED_SERVICE.md', 'LOADED_SERVICE_CN.md'):
            value = (root / 'docs' / name).read_text(encoding='utf-8')
            for marker in (CONTRACT, STARTUP_BLOCKER, '8e1e4862', '37781648284',
                           'selected_launch_fields_match', 'not_reported', 'ProgramArguments'):
                self.assertIn(marker, value)


if __name__ == '__main__':
    unittest.main()
