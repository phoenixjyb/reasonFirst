from __future__ import annotations
import os, pathlib, sys, tempfile, types

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import reasonfirst_mcp_server as mod

class FakeAnnotations:
    def __init__(self, **kwargs): self.kwargs=kwargs

class FakeMCPServer:
    def __init__(self, name, instructions=None):
        self.name=name; self.instructions=instructions; self.tools={}
    def tool(self, name=None, **kwargs):
        def deco(fn): self.tools[name or fn.__name__]=fn; return fn
        return deco
    def custom_route(self, *args, **kwargs):
        return lambda fn: fn
    def run(self, **kwargs): pass

class FakeController:
    state_dir=pathlib.Path(tempfile.mkdtemp())
    def close(self): pass

mcp_pkg=types.ModuleType('mcp'); server_pkg=types.ModuleType('mcp.server'); types_pkg=types.ModuleType('mcp.types')
server_pkg.MCPServer=FakeMCPServer; types_pkg.ToolAnnotations=FakeAnnotations
sys.modules['mcp']=mcp_pkg; sys.modules['mcp.server']=server_pkg; sys.modules['mcp.types']=types_pkg
mod.BridgeController=FakeController
server=mod.build_server()
expected={
 'reasonfirst_doctor','reasonfirst_target_probe','reasonfirst_dispatch','reasonfirst_workspace_status',
 'reasonfirst_files','reasonfirst_read','reasonfirst_diff','reasonfirst_codex_start','reasonfirst_codex_continue',
 'reasonfirst_codex_steer','reasonfirst_codex_interrupt','reasonfirst_codex_status','reasonfirst_codex_events',
 'reasonfirst_pending_approvals','reasonfirst_approve','reasonfirst_decline',
 'reasonfirst_finish_preview','reasonfirst_finish','reasonfirst_ci','reasonfirst_evidence',
 'reasonfirst_review_bundle','reasonfirst_artifacts'
}
assert expected.issubset(server.tools.keys()), sorted(server.tools)
assert 'reasonfirst_authorize_push' not in server.tools
assert 'ChatGPT is the planner/reviewer' in server.instructions

os.environ['RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH']='true'
try:
    enabled=mod.build_server()
    assert 'reasonfirst_authorize_push' in enabled.tools
finally:
    os.environ.pop('RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH',None)

print('v4 MCP tool registration: OK')
