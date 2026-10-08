"""Private native output must not become implicit service activation authority."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import ast
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import loaded_service as m
from gitlab_agent.upgrade import launch_review, pairing, runtime, deployment

LABEL = deployment.LABEL
SECRET = 'private-' + 'fixture-value'
SAVED = {'Label': LABEL, 'ProgramArguments': ['/fixture/run.sh'], 'WorkingDirectory': '/fixture',
         'EnvironmentVariables': {'RF_MCP_READ_ONLY': 'false', 'EXAMPLE_TOKEN': SECRET}}
REVIEW = {'blockers': ['legacy_control_required_but_target_has_no_control'], 'plan_digest': 'c'*64}


class LoadedFieldTests(unittest.TestCase):
    def test_present_selected_fields_match_without_returning_values(self):
        loaded = {**SAVED, 'Program': SAVED['ProgramArguments'][0], 'PID': 123, 'LastExitStatus': -15}
        result = m._compare(SAVED, loaded)
        self.assertTrue(result['selected_launch_fields_match'])
        self.assertEqual(result['pid'], 123)
        self.assertEqual(result['last_exit_status'], -15)
        self.assertTrue(result['running_pid_reported'])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn('/fixture/run.sh', json.dumps(result))

    def test_each_mismatch_is_visible_without_echo(self):
        for name, value in (('Program', '/private-path'), ('ProgramArguments', ['/other', SECRET]),
                            ('WorkingDirectory', '/private-cwd'), ('EnvironmentVariables', {'OTHER': SECRET})):
            with self.subTest(name=name):
                result = m._compare(SAVED, {**SAVED, name: value, 'PID': 123})
                self.assertFalse(result['selected_launch_fields_match'])
                self.assertEqual(result['selected_fields'][name], 'differs')
                self.assertNotIn(SECRET, json.dumps(result))
                self.assertNotIn('private-path', json.dumps(result))

    def test_missing_loaded_fields_are_not_success_or_saved_defaults(self):
        result = m._compare(SAVED, {'Label': LABEL})
        self.assertFalse(result['selected_launch_fields_match'])
        self.assertTrue(all(x == 'not_reported' for x in result['selected_fields'].values()))
        self.assertFalse(result['running_pid_reported'])
        self.assertIsNone(result['pid'])

    def test_present_empty_environment_differs_from_unreported(self):
        saved = {k: v for k, v in SAVED.items() if k != 'EnvironmentVariables'}
        self.assertEqual(m._compare(saved, saved)['selected_fields']['EnvironmentVariables'], 'not_reported')
        self.assertEqual(m._compare(saved, {**saved, 'EnvironmentVariables': {}})['selected_fields']['EnvironmentVariables'], 'matches')

    def test_saved_and_loaded_argv_are_compared_exactly(self):
        for value in (['/fixture/run.sh', ''], ['/fixture/run.sh '], ['/fixture/../fixture/run.sh']):
            self.assertEqual(m._compare(SAVED, {**SAVED, 'ProgramArguments': value})['selected_fields']['ProgramArguments'], 'differs')

    def test_invalid_types_and_bounds_fail_closed(self):
        for key, value in (('Program', []), ('Program', ''), ('WorkingDirectory', 7),
                           ('ProgramArguments', SECRET), ('ProgramArguments', []), ('ProgramArguments', [7]),
                           ('EnvironmentVariables', []), ('EnvironmentVariables', {'token': 3}),
                           ('PID', True), ('PID', -2), ('PID', '1'), ('PID', 2**40),
                           ('LastExitStatus', [])):
            with self.subTest(key=key, value=value), self.assertRaises(m.LoadedServiceError):
                m._parse(plistlib.dumps({**SAVED, key:value}), LABEL)

    def test_invalid_duplicate_mislabeled_and_oversized_native_output(self):
        raw = plistlib.dumps(SAVED)
        duplicate = raw.replace(b'<key>Label</key>', b'<key>Label</key><string>'+LABEL.encode()+b'</string><key>Label</key>')
        for value in (b'not xml '+SECRET.encode(), plistlib.dumps([]), duplicate,
                      plistlib.dumps({**SAVED,'Label':'other'}), b'x'*(m.MAX_OUTPUT+1)):
            with self.subTest(size=len(value)), self.assertRaises(m.LoadedServiceError) as failure:
                m._parse(value, LABEL)
            self.assertNotIn(SECRET, str(failure.exception))

    def test_json_record_does_not_substitute_for_native_plist(self):
        with self.assertRaises(m.LoadedServiceError):
            m._parse(json.dumps(SAVED).encode(), LABEL)


class ObservationTests(unittest.TestCase):
    def run_inspection(self, *, queries=None, reviews=None, raws=None):
        raw = plistlib.dumps(SAVED)
        loaded = {**SAVED, 'PID':123}
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, '_supported'))
            stack.enter_context(patch.object(deployment, '_home', return_value=Path('/fixture')))
            stack.enter_context(patch.object(os, 'getuid', return_value=501, create=True))
            check = stack.enter_context(patch.object(launch_review, 'check', side_effect=reviews or [REVIEW]*3))
            read = stack.enter_context(patch.object(deployment, '_registration_bytes', side_effect=raws or [raw,raw]))
            query = stack.enter_context(patch.object(m, '_query_job', side_effect=queries or [loaded,loaded]))
            stack.enter_context(patch.object(Path, 'open', side_effect=AssertionError('no application read')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('only mocked native read')))
            result=m.inspect(runtime_id='a'*64, expect_pairing_digest='b'*64, expect_digest='c'*64)
        self.query_count, self.check_count = query.call_count, check.call_count
        for key in ('mutating','service_changed','service_restarted','state_files_written','application_configuration_read',
                    'workspace_state_read','effective_environment_verified','running_code_verified','compatibility_verified',
                    'activation_authorized','ready_for_activation'):
            self.assertIs(result[key], False)
        self.assertEqual(result['proposed_actions'], [])
        self.assertNotIn(SECRET,json.dumps(result))
        return result

    def test_two_real_read_slots_then_revalidate_and_keep_compatibility_blockers(self):
        result=self.run_inspection()
        self.assertTrue(result['ok']);self.assertTrue(result['loaded_job_observed'])
        self.assertEqual(self.query_count,2);self.assertEqual(self.check_count,3)
        self.assertTrue(result['comparison']['selected_launch_fields_match'])
        self.assertIn('legacy_control_required_but_target_has_no_control',result['blockers'])
        self.assertTrue(result['deprecated_api'])

    def test_loaded_mismatch_is_successful_observation_not_activation(self):
        loaded={**SAVED,'PID':123,'WorkingDirectory':'/another'}
        result=self.run_inspection(queries=[loaded,loaded])
        self.assertTrue(result['ok'])
        self.assertFalse(result['comparison']['selected_launch_fields_match'])
        self.assertIn('loaded_launch_fields_differ_or_not_reported',result['blockers'])

    def test_job_without_pid_is_not_reported_running(self):
        result=self.run_inspection(queries=[SAVED,SAVED])
        self.assertTrue(result['ok']);self.assertFalse(result['comparison']['running_pid_reported'])
        self.assertIn('no_running_pid_reported',result['blockers'])

    def test_changed_pid_or_field_does_not_return_partial_observation(self):
        for value in ({**SAVED,'PID':124},{**SAVED,'PID':123,'EnvironmentVariables':{}}):
            result=self.run_inspection(queries=[{**SAVED,'PID':123},value])
            self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'loaded_job_changed')
            self.assertNotIn('comparison',result);self.assertNotIn('launch_review_digest',result)

    def test_rejected_initial_review_never_queries_native_api(self):
        result=self.run_inspection(reviews=[launch_review.LaunchReviewError('invalid_review_digest')])
        self.assertFalse(result['ok']);self.assertEqual(self.query_count,0)
        self.assertFalse(result['native_query_requested'])

    def test_changed_review_before_query_is_refused(self):
        result=self.run_inspection(reviews=[REVIEW,{**REVIEW,'plan_digest':'d'*64}])
        self.assertEqual(result['error_code'],'review_changed');self.assertEqual(self.query_count,0)

    def test_changed_sources_after_query_discard_observation(self):
        result=self.run_inspection(reviews=[REVIEW,REVIEW,launch_review.LaunchReviewError('known_launcher_modified')])
        self.assertFalse(result['ok']);self.assertTrue(result['native_query_requested'])
        self.assertEqual(result['error_code'],'launch_review_not_verified')
        self.assertNotIn('comparison',result)

    def test_raw_plist_change_after_query_is_refused(self):
        result=self.run_inspection(raws=[plistlib.dumps(SAVED),plistlib.dumps({**SAVED,'KeepAlive':False})])
        self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'review_changed')

    def test_native_error_does_not_mean_absent_service(self):
        result=self.run_inspection(queries=[m.LoadedServiceError('job_unavailable_or_query_failed')])
        self.assertFalse(result['ok']);self.assertEqual(result['error_code'],'job_unavailable_or_query_failed')
        self.assertNotIn('service_absent', result);self.assertNotIn('comparison',result)

    def test_unknown_exception_and_interrupt_do_not_echo_private_diagnostics(self):
        for error in (RuntimeError(SECRET),KeyboardInterrupt()):
            result=self.run_inspection(queries=[error])
            self.assertEqual(result['error_code'],'loaded_inspection_failed')


class QueryBoundaryTests(unittest.TestCase):
    def test_foreign_platform_refuses_before_home_files_and_processes(self):
        for platform in ('Linux','Windows'):
            with patch.object(m.platform,'system',return_value=platform), \
                 patch.object(deployment,'_home',side_effect=AssertionError('no home')), \
                 patch('subprocess.Popen',side_effect=AssertionError('no child')):
                result=m.inspect(runtime_id='a'*64,expect_pairing_digest='b'*64,expect_digest='c'*64)
            self.assertFalse(result['ok']);self.assertFalse(result['native_query_requested'])
            self.assertEqual(result['error_code'],'unsupported_loaded_service_platform')

    @unittest.skipUnless(os.name=='posix','POSIX uid boundary')
    def test_root_and_setuid_do_not_select_a_different_domain(self):
        for uid,euid in ((0,0),(501,0),(501,502)):
            with patch.object(m.platform,'system',return_value='Darwin'), \
                 patch.object(os,'getuid',return_value=uid), patch.object(os,'geteuid',return_value=euid):
                with self.assertRaisesRegex(m.LoadedServiceError,'user_domain_required'):
                    m._supported()

    def test_helper_is_isolated_and_receives_no_credential_or_loader_environment(self):
        raw=plistlib.dumps(SAVED)
        with patch.object(m,'_supported'), patch.object(m,'_capture',return_value=(0,raw)) as capture, \
             patch.dict(os.environ,{'TOKEN':SECRET,'DYLD_INSERT_LIBRARIES':SECRET,'PYTHONPATH':SECRET}):
            self.assertEqual(m._query_job(LABEL,Path('/fixture'))['Label'],LABEL)
        argv,env=capture.call_args.args
        self.assertEqual(argv,[sys.executable,'-I','-S','-B','-c',m.QUERY_SCRIPT,LABEL])
        self.assertEqual(env,{'HOME':'/fixture','PATH':'/usr/bin:/bin:/usr/sbin:/sbin','LC_ALL':'C'})
        self.assertNotIn(SECRET,repr(capture.call_args))

    def test_failure_output_never_used_as_native_dictionary(self):
        for code in (60,61,62,63,-11,1):
            with patch.object(m,'_supported'), patch.object(m,'_capture',return_value=(code,SECRET.encode())):
                with self.assertRaises(m.LoadedServiceError) as error:m._query_job(LABEL,Path('/fixture'))
            self.assertNotIn(SECRET,str(error.exception))

    def test_helper_contains_only_query_api_and_compiles(self):
        ast.parse(m.QUERY_SCRIPT)
        self.assertIn('SMJobCopyDictionary',m.QUERY_SCRIPT)
        for word in ('SMJobSubmit','SMJobRemove','SMJobBless','SMCopyAllJobDictionaries','launchctl'):
            self.assertNotIn(word,m.QUERY_SCRIPT)

    def test_cli_requires_review_and_cannot_change_label_or_activate(self):
        for extra in ([],['--label','other'],['--activate'],['--yes'],['--force']):
            args=['loaded-inspect'] if not extra else ['loaded-inspect','--runtime-id','a'*64,
                   '--expect-pairing-digest','b'*64,'--expect-digest','c'*64,*extra]
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as failure:runtime.main(args)
            self.assertEqual(failure.exception.code,2)

    def test_bilingual_guides_explain_deprecated_api_and_nonauthorization(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('LOADED_SERVICE.md','LOADED_SERVICE_CN.md'):
            text=(root/'docs'/name).read_text(encoding='utf-8')
            for marker in ('loaded-inspect','SMJobCopyDictionary','v0.5.1',
                           'ready_for_activation','-I -S -B','deprecated','/control'):
                self.assertIn(marker,text)

    def test_native_fixture_is_only_wired_to_explicit_macos_ci(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'scripts/runtime_e2e.py').read_text(encoding='utf-8')
        self.assertIn("sys.platform == 'darwin' and os.environ.get('GITHUB_ACTIONS') == 'true'",text)
        self.assertIn("source/'scripts/loaded_service_e2e.py'",text)
        fixture=(root/'scripts/loaded_service_e2e.py').read_text(encoding='utf-8')
        self.assertIn("'com.reasonfirst.fixture.loaded-'",fixture)
        self.assertNotIn("command('load',str(plist),'-w')",fixture)

    def test_cli_dispatches_exact_digests_as_one_json_result(self):
        out=io.StringIO()
        with patch.object(m,'inspect',return_value={'ok':False,'error_code':'fixture'}) as call,redirect_stdout(out):
            code=runtime.main(['loaded-inspect','--runtime-id','a'*64,'--expect-pairing-digest','b'*64,
                               '--expect-digest','c'*64,'--json'])
        self.assertEqual(code,1);self.assertEqual(json.loads(out.getvalue())['error_code'],'fixture')
        call.assert_called_once_with(runtime_id='a'*64,expect_pairing_digest='b'*64,expect_digest='c'*64)


@unittest.skipUnless(os.name=='posix','Pipe selector subprocess tests are POSIX-only')
class BoundedHelperTests(unittest.TestCase):
    def test_both_streams_drained_without_returning_stderr(self):
        code,raw=m._capture([sys.executable,'-I','-S','-c','import sys;sys.stderr.write("private");print("data")'],{})
        self.assertEqual((code,raw),(0,b'data\n'))

    def test_output_limits_and_timeout_terminate_only_the_helper(self):
        for script,code in [('import sys;print("x"*300000)','native_output_limit'),
                            ('import sys;sys.stderr.write("x"*20000)','native_output_limit'),
                            ('import time;time.sleep(4)','native_query_timeout')]:
            with self.subTest(script=script),patch.object(m,'TIMEOUT',0.3):
                with self.assertRaisesRegex(m.LoadedServiceError,code):
                    m._capture([sys.executable,'-I','-S','-c',script],{})

    def test_spawn_failure_is_classified(self):
        with self.assertRaisesRegex(m.LoadedServiceError,'native_query_failed'):
            m._capture(['/nonexistent/fixture-command'],{})


if __name__=='__main__':unittest.main()
