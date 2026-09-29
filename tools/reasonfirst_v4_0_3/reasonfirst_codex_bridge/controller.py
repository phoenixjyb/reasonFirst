"""Compatibility module alias to the packaged ReasonFirst Bridge implementation."""

import sys

from gitlab_agent.bridge_preview import controller as _impl

# Preserve historical module-level monkey-patching semantics used by the v4
# regression suite and external compatibility users.
sys.modules[__name__] = _impl
