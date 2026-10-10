"""Explicit TLS trust and credential-destination policy for GitLab API clients.

This module configures Python HTTP clients only. Native Git has a separate
trust store; GITLAB_CA_BUNDLE does not configure Git in this increment.
"""
from __future__ import annotations

import re
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import certifi
import httpx


MAX_CA_BUNDLE_BYTES = 4 * 1024 * 1024
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


def validate_base_url(value: str) -> str:
    """Validate a credential destination without echoing malformed input."""
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and "?" not in value and "#" not in value
            and "\\" not in value and "%" not in parsed.netloc
            and not any(ord(c) < 33 or ord(c) == 127 for c in value)
            and (port is None or 1 <= port <= 65535)
            and re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", parsed.path) is not None
            and all(part not in {".", ".."} for part in parsed.path.split("/"))
        )
        # Let the actual HTTP client reject invalid host/port syntax as well.
        httpx.URL(value)
    except (ValueError, httpx.InvalidURL):
        valid = False
    if not valid:
        raise ValueError(
            "GITLAB_BASE_URL must be an HTTP(S) endpoint with a hostname and an "
            "optional plain path prefix; credentials, query, fragment and unsafe paths are forbidden"
        )
    return value


def ca_bundle_path(value: str | Path | None) -> Path | None:
    """Require an unambiguous user-level path; never read a repo-relative CA."""
    if value is None or str(value).strip() == "":
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("GITLAB_CA_BUNDLE must be an absolute path or start with ~/")
    return path


def verified_context(
    base_url: str, *, verify_ssl: bool = True, ca_bundle: str | Path | None = None,
    retained_trust=None,
) -> ssl.SSLContext:
    """Keep public roots and optionally add a user-provided PEM trust bundle.

    CA selection is explicit and independent of HTTPX proxy/environment policy.
    We do not honor SSL_CERT_FILE/SSL_CERT_DIR implicitly, even with trust_env=True.
    """
    if retained_trust is not None:
        from .upgrade.service_trust import ServiceAPITrust, ServiceAPITrustError
        if type(retained_trust) is not ServiceAPITrust:
            raise ServiceAPITrustError("invalid_api_trust") from None
        retained_trust.assert_options(base_url, verify_ssl, ca_bundle)
        return retained_trust.ssl_context()
    validate_base_url(base_url)
    if verify_ssl is not True:
        raise ValueError(
            "TLS verification cannot be disabled for GitLab API clients; "
            "set GITLAB_VERIFY_SSL=true and configure GITLAB_CA_BUNDLE for a private CA"
        )
    context = ssl.create_default_context(cafile=certifi.where())
    path = ca_bundle_path(ca_bundle)
    if path is not None:
        try:
            if not path.is_file():
                raise ValueError("not a regular file")
            with path.open("rb") as stream:
                raw = stream.read(MAX_CA_BUNDLE_BYTES + 1)
            if not raw or len(raw) > MAX_CA_BUNDLE_BYTES:
                raise ValueError("invalid bundle size")
            text = raw.decode("ascii")
            if _PRIVATE_KEY.search(text):
                raise ValueError("private key in trust bundle")
            # Load the bounded bytes we inspected, not a second read of the path.
            context.load_verify_locations(cadata=text)
        except (OSError, ValueError, UnicodeError, ssl.SSLError) as exc:
            raise ValueError(
                "GITLAB_CA_BUNDLE must contain readable PEM CA certificates only "
                "(nonempty, at most 4 MiB, no private keys)"
            ) from exc
    return context


def api_client_options(
    base_url: str, *, verify_ssl: bool = True,
    ca_bundle: str | Path | None = None, asynchronous: bool = False,
    retained_trust=None,
) -> dict[str, Any]:
    """Options for both sync/async HTTPX, with pre-send destination checks.

    All redirects (including same-origin login/canonicalization redirects) are
    refused. Configure the final API endpoint; never infer credential authority
    from Location. Request hooks also reject explicit off-origin client reuse.
    """
    context = verified_context(base_url, verify_ssl=verify_ssl, ca_bundle=ca_bundle,
                               **({"retained_trust": retained_trust} if retained_trust is not None else {}))
    endpoint = httpx.URL(validate_base_url(base_url))
    prefix = endpoint.path.rstrip("/") + "/api/v4"
    origin = (endpoint.scheme, endpoint.host, endpoint.port)

    def guard_request(request: httpx.Request) -> None:
        if retained_trust is not None:
            retained_trust.revalidate()
        url = request.url
        if (
            (url.scheme, url.host, url.port) != origin
            or url.username or url.password or url.fragment
            or request.headers.get("host", "").lower() != url.netloc.decode("ascii").lower()
            or "\\" in url.path
            or any(part in {".", ".."} for part in url.path.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in url.path)
            or not (url.path == prefix or url.path.startswith(prefix + "/"))
        ):
            raise httpx.RequestError(
                "GitLab credential destination rejected; use the configured API endpoint",
                request=request,
            )

    def guard_response(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            raise httpx.RequestError(
                "GitLab API redirect refused; configure the final endpoint directly",
                request=response.request,
            )

    async def aguard_request(request: httpx.Request) -> None:
        guard_request(request)

    async def aguard_response(response: httpx.Response) -> None:
        guard_response(response)

    return {
        "verify": context,
        "follow_redirects": False,
        "event_hooks": {
            "request": [aguard_request if asynchronous else guard_request],
            "response": [aguard_response if asynchronous else guard_response],
        },
    }
