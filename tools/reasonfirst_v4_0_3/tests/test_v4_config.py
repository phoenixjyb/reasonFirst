from __future__ import annotations
import pathlib, subprocess, tempfile, yaml

ROOT=pathlib.Path(__file__).resolve().parents[1]

def main():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td)
        bridge=root/'bridge.yaml'; codex=root/'config.toml'
        bridge.write_text('version: 3\ndefaults:\n  target: local\n  worker_backend: copilot-cli\ntargets:\n  gpu:\n    type: ssh\n    host: gpu\n    repo: /work/app\n')
        codex.write_text('model = "gpt-5.6-sol"\nmodel_reasoning_effort = "high"\n\n[mcp_servers.context7]\nurl = "https://example.invalid/mcp"\n\n[mcp_servers.reasonfirst]\ncommand = "/old/reasonfirst"\n')
        subprocess.run([
            'python',str(ROOT/'configure_v4.py'),
            '--bridge-dir',str(ROOT),
            '--bridge-config',str(bridge),
            '--codex-config',str(codex),
            '--backup-dir',str(root/'backups'),
        ],check=True,capture_output=True,text=True)
        b=yaml.safe_load(bridge.read_text())
        assert b['version']==4
        assert b['targets']['gpu']['repo']=='/work/app'
        assert b['defaults']['codex_backend']=='global-config-local'
        assert b['defaults']['worker_backend']=='copilot-cli'
        assert b['targets']['local']['worker_backend']=='copilot-cli'
        text=codex.read_text()
        assert 'model = "gpt-5.6-sol"' in text
        assert '[mcp_servers.context7]' in text
        assert text.count('[mcp_servers.reasonfirst]')==1
        assert 'url = "http://127.0.0.1:8765/mcp"' in text
        assert '# BEGIN REASONFIRST V4 MANAGED' in text
    print('v4 config preserves global Codex config and replaces only ReasonFirst MCP: OK')

if __name__=='__main__': main()
