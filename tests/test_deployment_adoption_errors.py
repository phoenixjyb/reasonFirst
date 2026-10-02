"""Post-commit drift is a failure, but must not erase the record-write evidence."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from gitlab_agent import reasonfirst_cli as cli
from gitlab_agent.upgrade import deployment as d


@unittest.skipUnless(os.name == 'posix', 'Real directory-fd recording requires POSIX; not Windows ACL coverage')
class PostCommitDriftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.plist = self.home / 'Library/LaunchAgents' / (d.LABEL + '.plist')
        self.plist.parent.mkdir(parents=True)
        root = self.home / '.local/share/reasonfirst/v4-service'
        self.plist.write_bytes(plistlib.dumps({
            'Label': d.LABEL,
            'ProgramArguments': [str(root / 'tools/codex_web_bridge/run_reasonfirst.sh')],
            'WorkingDirectory': str(root),
        }))
        self.plist.chmod(0o600)
        self.digest = d.plan_adoption(system_name='Darwin', home=self.home)['plan_digest']
        self.record = self.home / '.config/reasonfirst/deployments' / (d.LABEL + '.json')
        self.original_snapshot = d._snapshot
        self.calls = 0

    def changing_snapshot(self, home):
        self.calls += 1
        if self.calls == 3:
            # Two snapshots precede the write; this third one is the post-write
            # observation. The registration disappears but the record remains.
            self.plist.unlink()
        return self.original_snapshot(home)

    def invoke(self, as_json):
        argv = ['deployment', 'adopt', '--expect-digest', self.digest, '--yes']
        if as_json:
            argv.append('--json')
        out, err = io.StringIO(), io.StringIO()
        with patch.object(d.platform, 'system', return_value='Darwin'), \
             patch.object(d, '_home', return_value=self.home), \
             patch.object(d, '_snapshot', side_effect=self.changing_snapshot), \
             patch('subprocess.run', side_effect=AssertionError('No processes')), \
             patch('socket.socket', side_effect=AssertionError('No network')), \
             redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, 3)
        self.assertTrue(self.record.is_file())
        self.assertEqual(json.loads(self.record.read_bytes())['plan_digest'], self.digest)
        return out.getvalue(), err.getvalue()

    def test_json_keeps_written_record_evidence_and_classifies_error(self):
        out, err = self.invoke(True)
        result = json.loads(out)
        self.assertFalse(result['ok'])
        self.assertTrue(result['record_written'])
        self.assertFalse(result['registration_unchanged_at_check'])
        self.assertFalse(result['service_changed'])
        self.assertEqual(result['error_code'], 'changed_registration')
        self.assertTrue(result['inspect_status_before_retry'])
        self.assertIn('Record was written', result['error'])
        self.assertEqual(err, '')

    def test_human_error_does_not_collapse_into_keyerror(self):
        out, err = self.invoke(False)
        self.assertIn('Record was written', err)
        self.assertNotIn("'error'", err)
        self.assertNotIn('ReasonFirst command failed', err)
        self.assertNotIn('record_written: False', out)
        self.assertFalse(self.plist.exists())


if __name__ == '__main__':
    unittest.main()
