"""Disposable native saved-source assessment; never a live LaunchAgent test."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from gitlab_agent.upgrade import compatibility as c, runtime as r


def acceptance(*, home: Path, runtime_id: str, python: Path, source_root: Path, snapshot):
    # Called only after the install harness creates its synthetic registration.
    # Existing production commands have already proved no onboarding fabrication.
    tools = home / '.local/share/reasonfirst/v4-service/tools/codex_web_bridge'
    tools.mkdir(parents=True)
    for name in c.LAUNCH_FILES:
        shutil.copyfile(source_root/'tools/reasonfirst_v4_0_3'/name, tools/name)
    (tools/'reasonfirst_codex_bridge').mkdir()
    for name in c.IMPLEMENTATIONS:
        shutil.copyfile(source_root/'src/gitlab_agent/bridge_preview'/name, tools/'reasonfirst_codex_bridge'/name)
    cfg = home / '.config/reasonfirst/bridge.yaml'
    cfg.write_text('version: 4\ndefaults: {target: local}\ntargets: {local: {type: local}}\n', encoding='utf-8')
    cfg.chmod(0o600)
    def invoke(action, digest=None, error=None):
        argv = [str(python), '-I', '-B', '-m', 'gitlab_agent.upgrade.runtime', action,
                '--runtime-id', runtime_id, '--json']
        if digest is not None:
            argv += ['--expect-digest', digest]
        before = snapshot(home)
        proc = subprocess.run(argv, cwd=home, env=r.child_env(home), stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=120, check=False)
        result = json.loads(proc.stdout)
        assert proc.returncode == (1 if error else 0), result
        assert result['ok'] is (error is None), result
        assert result['compatibility_verified'] is False and result['ready_for_activation'] is False
        assert result['commands_executed'] is False and result['credential_store_files_read'] is False
        assert snapshot(home) == before, 'Assessment changed the synthetic fixture'
        if error:
            assert result['error_code'] == error, result
            assert 'assessment_digest' not in result
        return result
    result = invoke('deployment-assess')
    assert result['assessment_completed'] is True
    assert result['assessment_status'] == 'blocked_by_declared_policy'
    assert result['blockers'] == ['legacy_control_route_not_supported_by_target']
    assert result['evidence']['bridge_config']['schema_status'] == 'known_top_level_shape'
    assert invoke('deployment-assess-check', result['assessment_digest'])['review_digest_matches'] is True
    raw = cfg.read_bytes()
    cfg.write_bytes(raw+b'\n')
    invoke('deployment-assess-check', result['assessment_digest'], 'assessment_changed')
    cfg.write_bytes(raw)
    script = tools/'run_reasonfirst.sh'
    raw = script.read_bytes()
    script.write_bytes(raw+b'\n')
    invoke('deployment-assess', error='unrecognized_launch_source')
    script.write_bytes(raw)
    print(json.dumps({'operation':'legacy-source-assessment-native-acceptance', 'ok':True,
                      'prepared_runtime_cli':True, 'control_mismatch_blocked':True,
                      'config_drift_refused':True, 'source_drift_refused':True,
                      'compatibility_verified':False, 'live_service_tested':False,
                      'activation_tested':False}, sort_keys=True))
