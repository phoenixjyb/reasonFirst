from __future__ import annotations

# Compatibility wrapper: historical tests/tools import symbols from root server.py.
# The packaged implementation lives in gitlab_agent.read_mcp so source checkout
# and installed-wheel users execute the same MCP server.
from gitlab_agent.read_mcp import *  # noqa: F401,F403
from gitlab_agent.read_mcp import main


if __name__ == "__main__":
    main()
