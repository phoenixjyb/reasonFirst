from __future__ import annotations
import os, pathlib, sys, tempfile, types

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import reasonfirst_mcp_server as mod

class FakeAnnotations:
    def __init__(self, **kwargs): self.kwargs=kwargs

class FakeMCPServer:
    def __init__(self, name, instructions=None):
        self.name=name; self.instructions=instructions; self.tools={}; self.tool_meta={}; self.routes=[]
    def tool(self, name=None, **kwargs):
        def deco(fn):
            key=name or fn.__name__
            self.tools[key]=fn
            self.tool_meta[key]=kwargs
            return fn
        return deco
    def custom_route(self, path, *args, **kwargs):
        def deco(fn):
            self.routes.append((path, tuple(kwargs.get("methods") or ())))
            return fn
        return deco
    def run(self, **kwargs): pass

class FakeController:
    state_dir=pathlib.Path(tempfile.mkdtemp())
    def close(self): pass

mcp_pkg=types.ModuleType('mcp'); server_pkg=types.ModuleType('mcp.server'); types_pkg=types.ModuleType('mcp.types')
server_pkg.MCPServer=FakeMCPServer; types_pkg.ToolAnnotations=FakeAnnotations
sys.modules['mcp']=mcp_pkg; sys.modules['mcp.server']=server_pkg; sys.modules['mcp.types']=types_pkg
mod.BridgeController=FakeController
os.environ.pop('RF_MCP_READ_ONLY',None)
os.environ.pop('RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH',None)
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
assert ('/control', ('POST',)) in server.routes
assert ('/healthz', ('GET',)) in server.routes

for tool_name, meta in server.tool_meta.items():
    annotations = meta.get("annotations")
    assert annotations is not None, tool_name
    kwargs = annotations.kwargs
    assert isinstance(kwargs.get("read_only_hint"), bool), (tool_name, kwargs)
    assert isinstance(kwargs.get("destructive_hint"), bool), (tool_name, kwargs)
    assert isinstance(kwargs.get("open_world_hint"), bool), (tool_name, kwargs)

for tool_name in {
    'reasonfirst_doctor','reasonfirst_target_probe','reasonfirst_workspace_status',
    'reasonfirst_files','reasonfirst_read','reasonfirst_diff','reasonfirst_codex_status',
    'reasonfirst_codex_events','reasonfirst_pending_approvals','reasonfirst_finish_preview',
    'reasonfirst_ci','reasonfirst_evidence','reasonfirst_review_bundle','reasonfirst_artifacts',
}:
    assert server.tool_meta[tool_name]["annotations"].kwargs["read_only_hint"] is True
    assert server.tool_meta[tool_name]["annotations"].kwargs["destructive_hint"] is False
    assert server.tool_meta[tool_name]["annotations"].kwargs["open_world_hint"] is False

for tool_name in {
    'reasonfirst_dispatch','reasonfirst_codex_start','reasonfirst_codex_continue',
    'reasonfirst_codex_steer','reasonfirst_codex_interrupt','reasonfirst_approve',
    'reasonfirst_decline','reasonfirst_finish',
}:
    assert server.tool_meta[tool_name]["annotations"].kwargs["read_only_hint"] is False
    assert server.tool_meta[tool_name]["annotations"].kwargs["destructive_hint"] is False
    assert server.tool_meta[tool_name]["annotations"].kwargs["open_world_hint"] is False

read_only_expected={
 'reasonfirst_doctor','reasonfirst_target_probe','reasonfirst_workspace_status',
 'reasonfirst_files','reasonfirst_read','reasonfirst_diff','reasonfirst_codex_status',
 'reasonfirst_codex_events','reasonfirst_pending_approvals','reasonfirst_ci',
 'reasonfirst_review_bundle','reasonfirst_artifacts'
}
os.environ['RF_MCP_READ_ONLY']='true'
try:
    read_only=mod.build_server()
    assert set(read_only.tools.keys()) == read_only_expected, sorted(read_only.tools)
    assert 'strict read-only compatibility mode' in read_only.instructions
    assert ('/control', ('POST',)) not in read_only.routes
    assert ('/healthz', ('GET',)) in read_only.routes
    for tool_name, meta in read_only.tool_meta.items():
        annotations=meta['annotations'].kwargs
        assert annotations['read_only_hint'] is True, (tool_name, annotations)
        assert annotations['destructive_hint'] is False, (tool_name, annotations)
        assert annotations['open_world_hint'] is False, (tool_name, annotations)
finally:
    os.environ.pop('RF_MCP_READ_ONLY',None)

os.environ['RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH']='true'
try:
    enabled=mod.build_server()
    assert 'reasonfirst_authorize_push' in enabled.tools
    annotations=enabled.tool_meta['reasonfirst_authorize_push']['annotations'].kwargs
    assert annotations['read_only_hint'] is False
    assert annotations['destructive_hint'] is False
    assert annotations['open_world_hint'] is False
finally:
    os.environ.pop('RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH',None)

print('v4 MCP tool registration: OK')
