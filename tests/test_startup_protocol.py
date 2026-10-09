from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import traceback
import unittest
from unittest.mock import patch

from gitlab_agent.upgrade import startup_protocol as s


def claims():
    return s.StartupClaims('1'*64, '2'*64, '3'*64, '4'*64,
                           '127.0.0.1', 8765, '/mcp', 'read-only', 'disabled')


class StartupProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 123456789
        self.expected = claims()
        self.pending = self.new()

    def new(self, **kwargs):
        return s.PendingStartupChallenge(expected=kwargs.get('expected', self.expected),
                                        expected_pid=kwargs.get('expected_pid', os.getpid()),
                                        _clock_ns=lambda: self.now)

    def reply(self, pending=None, observed=None):
        p = pending or self.pending
        return s.make_reply(p.start(), observed=observed or self.expected)

    def assert_code(self, code, fn):
        with self.assertRaises(s.StartupProtocolError) as err:
            fn()
        self.assertEqual(str(err.exception), code)

    def test_fresh_exchange_matches_but_never_authenticates_or_activates(self):
        report = self.pending.finish(self.reply())
        self.assertTrue(report['ok'])
        self.assertTrue(report['fresh_reply_claims_match'])
        for key in ('peer_identity_verified', 'running_code_verified', 'effective_configuration_verified',
                    'managed_startup_confirmation_verified', 'compatibility_verified',
                    'activation_authorized', 'ready_for_activation', 'service_changed'):
            self.assertIs(report[key], False)
        self.assertIn(s.STARTUP_BLOCKER, report['blockers'])

    def test_all_nine_identity_fields_bound_individually(self):
        changes = dict(runtime_id='a'*64, manifest_digest='b'*64, interpreter_digest='c'*64,
                       configuration_digest='d'*64, host='::1', port=8766, path='/other', mode='full-chat')
        for field, value in changes.items():
            with self.subTest(field=field):
                p = self.new()
                bad = self.reply(p, replace(self.expected, **{field: value}))
                self.assert_code('reply_mismatch', lambda: p.finish(bad))
        self.assert_code('invalid_claims', lambda: replace(self.expected, control_policy='legacy'))

    def test_expected_pid_not_taken_from_reply(self):
        p = self.new(expected_pid=os.getpid()+1)
        self.assert_code('reply_mismatch', lambda: p.finish(self.reply(p)))

    def test_nonce_and_launch_id_bound(self):
        for field in ('nonce', 'launch_id'):
            p = self.new(); value = json.loads(self.reply(p)); value[field] = 'f'*64
            self.assert_code('reply_mismatch', lambda: p.finish(s._canonical(value)))

    def test_cross_challenge_replay_rejected(self):
        other = self.new(); raw = self.reply(other); self.pending.start()
        self.assert_code('reply_mismatch', lambda: self.pending.finish(raw))

    def test_replay_after_success_rejected(self):
        raw = self.reply(); self.pending.finish(raw)
        self.assert_code('challenge_used', lambda: self.pending.finish(raw))

    def test_failed_attempt_consumes_nonce(self):
        valid = self.reply()
        self.assert_code('invalid_message', lambda: self.pending.finish(b'not json'))
        self.assert_code('challenge_used', lambda: self.pending.finish(valid))

    def test_start_only_once(self):
        raw = self.pending.start()
        self.assert_code('challenge_used', self.pending.start)
        self.assertTrue(self.pending.finish(s.make_reply(raw, observed=self.expected))['ok'])

    def test_finish_before_start_consumes_challenge(self):
        self.assert_code('challenge_not_started', lambda: self.pending.finish(b'{}'))
        self.assert_code('challenge_used', self.pending.start)

    def test_cancel_before_and_after_start(self):
        self.pending.cancel()
        self.assert_code('challenge_used', self.pending.start)
        p = self.new(); reply = self.reply(p); p.cancel()
        self.assert_code('challenge_used', lambda: p.finish(reply))

    def test_expiry_is_exact_at_eight_seconds(self):
        raw = self.reply(); self.now += s.LIFETIME_NS
        self.assert_code('challenge_expired', lambda: self.pending.finish(raw))
        self.assert_code('challenge_used', lambda: self.pending.finish(raw))

    def test_before_expiry_accepted(self):
        raw = self.reply(); self.now += s.LIFETIME_NS - 1
        self.assertTrue(self.pending.finish(raw)['ok'])

    def test_delayed_start_does_not_refresh_lifetime(self):
        self.now += s.LIFETIME_NS - 1
        raw = self.reply(); self.now += 1
        self.assert_code('challenge_expired', lambda: self.pending.finish(raw))

    def test_expired_before_start_consumed(self):
        self.now += s.LIFETIME_NS
        self.assert_code('challenge_expired', self.pending.start)
        self.now = 123456789
        self.assert_code('challenge_used', self.pending.start)

    def test_clock_failure_or_backwards_movement_fails_closed(self):
        for now in (True, -1, None, 12.5, 0):
            p = self.new(); raw = self.reply(p); original = self.now; self.now = now
            self.assert_code('invalid_clock', lambda: p.finish(raw))
            self.now = original
            self.assert_code('challenge_used', lambda: p.finish(raw))

    def test_time_is_rechecked_after_parsing(self):
        raw = self.reply(); original = s._decode
        def slow(data):
            result = original(data); self.now += s.LIFETIME_NS; return result
        with patch.object(s, '_decode', side_effect=slow):
            self.assert_code('challenge_expired', lambda: self.pending.finish(raw))

    def test_clock_exception_is_sanitized(self):
        p = self.new(); raw = self.reply(p)
        p._clock = lambda: (_ for _ in ()).throw(RuntimeError('private-fixture'))
        self.assert_code('invalid_clock', lambda: p.finish(raw))

    def test_challenge_contains_no_expected_claims_or_pid(self):
        value = json.loads(self.pending.start())
        self.assertEqual(set(value), {'protocol','kind','nonce','launch_id'})
        for known in self.expected.to_mapping().values():
            if type(known) is str and len(known) > 20:
                self.assertNotIn(known, json.dumps(value))

    def test_reply_uses_actual_process_pid(self):
        value = json.loads(self.reply())
        self.assertEqual(value['pid'], os.getpid())
        self.assertEqual(value['claims'], self.expected.to_mapping())

    def test_response_never_gets_expected_claims_from_request(self):
        request = json.loads(self.pending.start()); request['claims'] = self.expected.to_mapping()
        self.assert_code('invalid_challenge', lambda: s.make_reply(s._canonical(request), observed=self.expected))

    def test_message_bound_before_decode(self):
        for raw in (b'', b'x'*(s.MAX_MESSAGE_BYTES+1), '{}', bytearray(b'{}'), None):
            p = self.new(); p.start()
            self.assert_code('invalid_message', lambda: p.finish(raw))

    def test_json_schema_types_duplicates_constants_encoding(self):
        raw = self.reply(); value = json.loads(raw)
        invalid = [b'[]', b'null', b'false', b'{', b'\xff', b'\xef\xbb\xbf{}', '{}'.encode('utf-16'),
                   b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                   b'{"x":0,"x":1}', b'['*1200 + b']'*1200,
                   raw + b'{}', b'{"claims":{"x":1,"x":2}}']
        for data in invalid:
            with self.subTest(data=data[:20]):
                p = self.new(); p.start()
                self.assert_code('invalid_message', lambda: p.finish(data))

    def test_missing_extra_unknown_reply_keys_rejected(self):
        for name in ('protocol','kind','launch_id','nonce','pid','claims'):
            p=self.new(); v=json.loads(self.reply(p)); v.pop(name)
            self.assert_code('invalid_reply', lambda: p.finish(s._canonical(v)))
        p=self.new(); v=json.loads(self.reply(p));v['activation_authorized']=True
        self.assert_code('invalid_reply', lambda: p.finish(s._canonical(v)))

    def test_wrong_protocol_kind_nonce_and_pid_types(self):
        for name, value in [('protocol','other'), ('kind','challenge'), ('nonce','F'*64),
                            ('launch_id','bad'), ('pid',True), ('pid',0), ('pid',2**31), ('pid','123')]:
            p=self.new();v=json.loads(self.reply(p));v[name]=value
            self.assert_code('invalid_reply',lambda:p.finish(s._canonical(v)))

    def test_claims_require_exact_projection_no_credentials(self):
        for field in self.expected.to_mapping():
            value=self.expected.to_mapping();value.pop(field)
            self.assert_code('invalid_claims',lambda:s.StartupClaims.from_mapping(value))
        for name in ('token', 'env', 'argv', 'secret', 'ready', 'peer_verified'):
            value={**self.expected.to_mapping(),name:'synthetic-private'}
            self.assert_code('invalid_claims',lambda:s.StartupClaims.from_mapping(value))
        for value in (None, [], 'raw'):
            self.assert_code('invalid_claims',lambda:s.StartupClaims.from_mapping(value))

    def test_bad_digest_shapes(self):
        for name in ('runtime_id', 'manifest_digest','interpreter_digest','configuration_digest'):
            for value in ('', 'F'*64, 'a'*63, 'a'*65, 'a'*64+'\n', True, 1, None):
                with self.subTest(name=name,value=value):
                    self.assert_code('invalid_claims',lambda:replace(self.expected,**{name:value}))

    def test_endpoint_validation_reuses_packaged_http_policy(self):
        for name, value in [('host','0.0.0.0'),('host','localhost'),('port',0),('port',True),
                            ('port','8765'),('port',65536),('path','/control'),('path','/healthz'),
                            ('path','/mcp/'),('path','/x%2Fy'),('mode','unknown'),
                            ('control_policy','legacy'),('path',[]),('host',None)]:
            self.assert_code('invalid_claims',lambda:replace(self.expected,**{name:value}))

    def test_no_dynamic_dispatch_or_secret_values_in_public_report(self):
        report=self.pending.finish(self.reply())
        text=json.dumps(report)
        for value in (self._expected_wire_values()):self.assertNotIn(value,text)
        self.assertNotIn('nonce',text)
        self.assertNotIn('launch_id',text)

    def _expected_wire_values(self):
        return [v for v in self.expected.to_mapping().values() if type(v)is str and len(v)>10]

    def test_error_traceback_does_not_render_bad_wire(self):
        self.pending.start(); marker='do-not-reflect-fixture'
        try:self.pending.finish(b'\xff'+marker.encode())
        except s.StartupProtocolError:
            text=traceback.format_exc()
        else:self.fail('expected protocol error')
        self.assertNotIn(marker,text)
        self.assertNotIn('UnicodeDecodeError',text)

    def test_fresh_nonces_and_launch_ids_are_not_reused(self):
        values=[json.loads(self.new().start()) for _ in range(32)]
        self.assertEqual(len({v['nonce'] for v in values}),32)
        self.assertEqual(len({v['launch_id'] for v in values}),32)

    def test_defensive_snapshot_of_expected_claims(self):
        expected=claims(); p=self.new(expected=expected);original=claims()
        object.__setattr__(expected,'port',8000)
        raw=s.make_reply(p.start(),observed=original)
        self.assertTrue(p.finish(raw)['ok'])

    def test_no_os_pid_or_attestation_override_argument(self):
        with self.assertRaises(TypeError):
            s.make_reply(self.pending.start(),observed=self.expected,pid=1)
        with self.assertRaises(TypeError):
            self.new().__init__(expected=self.expected,expected_pid=os.getpid(),peer_verified=True)

    def test_invalid_expected_pid_refused(self):
        for value in (0,-1,True,None,'123',2**31):
            self.assert_code('invalid_expected_pid',lambda:self.new(expected_pid=value))

    def test_protocol_io_free_does_not_touch_service_or_configuration(self):
        with ExitStack() as stack:
            for target in ('builtins.open','os.open','subprocess.Popen','socket.socket'):
                stack.enter_context(patch(target,side_effect=AssertionError('forbidden IO')))
            p=self.new();result=p.finish(self.reply(p))
        self.assertTrue(result['ok']);self.assertFalse(result['service_changed'])

    def test_concurrent_verification_allows_at_most_one_success(self):
        raw=self.reply();barrier=threading.Barrier(8);out=[];lock=threading.Lock()
        def verify():
            barrier.wait()
            try:value=self.pending.finish(raw)['ok']
            except s.StartupProtocolError as exc:value=str(exc)
            with lock:out.append(value)
        threads=[threading.Thread(target=verify) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=5);self.assertFalse(t.is_alive())
        self.assertEqual(out.count(True),1)
        self.assertEqual(out.count('challenge_used'),7)

    def test_matching_self_report_is_still_not_identity_evidence(self):
        # A forged payload can know the public expected fields and nonce; this
        # layer must not elevate that fact to peer/process attestation.
        p=self.new(expected_pid=45678);request=json.loads(p.start())
        forged={**request,'kind':'reply','pid':45678,'claims':self.expected.to_mapping()}
        report=p.finish(s._canonical(forged))
        self.assertTrue(report['fresh_reply_claims_match'])
        self.assertFalse(report['peer_identity_verified'])
        self.assertFalse(report['managed_startup_confirmation_verified'])
        self.assertIn(s.STARTUP_BLOCKER,report['blockers'])

    def test_generated_challenge_has_no_default_claims_fallback(self):
        request = self.pending.start()
        self.assert_code('invalid_claims',lambda:s.make_reply(request,observed=None))


class StartupProtocolProcessTests(unittest.TestCase):
    def test_real_disposable_child_reply_without_bridge_or_network(self):
        # A real process exchanges bytes on its private stdin/stdout. The child
        # exits before verification: therefore this cannot be a liveness claim.
        root=Path(__file__).resolve().parents[1]
        bootstrap='''
import json,sys
sys.path.insert(0,sys.argv[1])
from gitlab_agent.upgrade.startup_protocol import StartupClaims,make_reply,MAX_MESSAGE_BYTES
local=StartupClaims('1'*64,'2'*64,'3'*64,'4'*64,'127.0.0.1',8765,'/mcp','read-only','disabled')
request=sys.stdin.buffer.read(MAX_MESSAGE_BYTES+1)
sys.stdout.buffer.write(make_reply(request,observed=local))
sys.stdout.buffer.flush()
'''
        env={k:v for k,v in os.environ.items() if k in ('SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP','LANG')}
        proc=subprocess.Popen([sys.executable,'-I','-S','-B','-c',bootstrap,str(root/'src')],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
        try:
            p=s.PendingStartupChallenge(expected=claims(),expected_pid=proc.pid)
            stdout,stderr=proc.communicate(p.start(),timeout=7)
            self.assertEqual(proc.returncode,0,stderr[-1000:])
            report=p.finish(stdout)
            self.assertTrue(report['ok']);self.assertFalse(report['peer_identity_verified'])
            self.assertFalse(report['managed_startup_confirmation_verified'])
            self.assertFalse(report['ready_for_activation'])
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=3)
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                if stream is not None:stream.close()


class StartupProtocolDocumentationTests(unittest.TestCase):
    def test_bilingual_boundaries_and_links(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('STARTUP_CONFIRMATION.md','STARTUP_CONFIRMATION_CN.md'):
            text=(root/'docs'/name).read_text(encoding='utf-8')
            for marker in (s.PROTOCOL, 'v0.5.1', 'managed_startup_confirmation_verified',
                           'peer_identity_verified', 'activation_authorized', 'nonce'):
                self.assertIn(marker,text)
        import re
        for name in ('STARTUP_CONFIRMATION.md','STARTUP_CONFIRMATION_CN.md'):
            text=(root/'docs'/name).read_text(encoding='utf-8')
            for target in re.findall(r'\]\(([^)]+)\)',text):
                if '://' not in target:self.assertTrue((root/'docs'/target.split('#')[0]).is_file(),target)


if __name__ == "__main__":
    unittest.main()
