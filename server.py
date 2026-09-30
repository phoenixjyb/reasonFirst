from __future__ import annotations

"""Source-checkout compatibility entrypoint for the packaged read MCP.

Historically tests/tools imported this root file as a fresh module and patched
its globals. Execute the packaged implementation in this module's namespace so
that behavior remains intact, while installed users launch
`reasonfirst-gitlab-mcp` from `gitlab_agent.read_mcp`.
"""

from importlib.util import find_spec
from pathlib import Path


_impl_spec = find_spec("gitlab_agent.read_mcp")
if _impl_spec is None or not _impl_spec.origin:
    raise RuntimeError("Could not locate packaged ReasonFirst read MCP implementation")

_impl_path = Path(_impl_spec.origin)
_impl_source = _impl_path.read_text(encoding="utf-8")
exec(compile(_impl_source, str(_impl_path), "exec"), globals(), globals())
