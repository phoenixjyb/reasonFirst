"""Compatibility import for the App Server client now owned by ReasonFirst core."""

from gitlab_agent.codex_app_server import (  # noqa: F401
    AppServerClient,
    AppServerError,
    managed_app_server_socket,
    resolve_codex_binary,
    resolve_desktop_or_codex_binary,
)

__all__ = [
    "AppServerClient",
    "AppServerError",
    "managed_app_server_socket",
    "resolve_codex_binary",
    "resolve_desktop_or_codex_binary",
]
