#!/usr/bin/env python3
"""CI-only native API fixture, never a maintainer's ReasonFirst job.

Loads exactly one random-label /bin/sleep job in a clean CI user's context, then
unloads that same job. This tests the adapter/comparator using the prepared Python;
it is not an end-to-end ReasonFirst activation or the public command's full path.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

from gitlab_agent.upgrade import loaded_service as loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ci-fixture', action='store_true', required=True)
    parser.parse_args()
    if sys.platform != 'darwin' or os.environ.get('GITHUB_ACTIONS') != 'true' or os.getuid() == 0:
        raise SystemExit('Native service fixture requires a non-root disposable macOS Actions runner.')
    label = 'com.reasonfirst.fixture.loaded-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='rf-native-job-') as td:
        root = Path(td).resolve()
        env = {'HOME': str(root), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL':'C'}
        def command(*args):
            return subprocess.run(['/bin/launchctl', *args], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=root, env=env, timeout=12, check=False)
        # A collision would be unexpected: never remove or reuse a preexisting job.
        if command('list',label).returncode == 0:
            raise RuntimeError('Random fixture label already exists; nothing changed.')
        cwd = root / "initial 空间"
        cwd.mkdir()
        changed_cwd = root / 'changed'
        changed_cwd.mkdir()
        plist = root / (label+'.plist')
        original = {'Label': label, 'ProgramArguments': ['/bin/sleep','90'],
                    'WorkingDirectory': str(cwd), 'RunAtLoad': True,
                    'EnvironmentVariables': {'RF_SYNTHETIC_FIXTURE': 'private-fixture'}}
        original_raw = plistlib.dumps(original)
        plist.write_bytes(original_raw); plist.chmod(0o600)
        attempted = False
        try:
            attempted = True
            if command('load',str(plist)).returncode:
                raise RuntimeError('Native fixture could not load; no success claim.')
            deadline = time.monotonic()+10
            job = None
            while time.monotonic()<deadline:
                try:
                    job = loaded._query_job(label,root)
                except loaded.LoadedServiceError:
                    raise  # API unsupported is an actual failed native test, not a skip.
                if job.get('PID'):
                    break
                time.sleep(0.1)
            if not job or not job.get('PID'):
                raise RuntimeError('Fixture PID not observed.')
            initial = loaded._compare(original,job)
            if not initial['selected_launch_fields_match']:
                # Field classifications only, never arbitrary job environment/args.
                raise RuntimeError('Native fields incomplete: '+json.dumps(initial))
            changed = {**original, 'WorkingDirectory': str(changed_cwd),
                       'EnvironmentVariables': {'RF_SYNTHETIC_FIXTURE':'changed'}}
            changed_raw=plistlib.dumps(changed)
            plist.write_bytes(changed_raw)
            second=loaded._query_job(label,root)
            if second.get('PID')!=job['PID']:
                raise RuntimeError('Fixture PID changed unexpectedly.')
            difference=loaded._compare(changed,second)
            if difference['selected_fields']['WorkingDirectory']!='differs' or difference['selected_fields']['EnvironmentVariables']!='differs':
                raise RuntimeError('Native query did not distinguish saved-file drift from loaded job.')
            if plist.read_bytes()!=changed_raw:
                raise RuntimeError('Native query modified saved registration.')
            print(json.dumps({'operation':'loaded-job-native-fixture','ok':True,
                              'deprecated_structured_api':True,'actual_loaded_job_queried':True,
                              'saved_file_drift_detected':True,'helper_python':sys.version.split()[0],
                              'production_label_used':False,'reasonfirst_server_started':False,
                              'public_cli_full_path_tested':False,'activation_tested':False},sort_keys=True))
        finally:
            if attempted:
                # Label belongs only to this disposable test. Never issue a broad
                # unload or remove arbitrary user state, and do not ignore failure.
                plist.write_bytes(original_raw)
                cleanup=command('unload',str(plist))
                if cleanup.returncode or command('list',label).returncode==0:
                    raise RuntimeError('Fixture cleanup not confirmed.')
    return 0


if __name__=='__main__':raise SystemExit(main())
