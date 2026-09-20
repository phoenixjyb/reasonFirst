"""Shared bounded, sanitized GitLab trace reads for sync CLI and async MCP.

Logs are untrusted diagnostic data. Sanitization is heuristic, not a DLP guarantee.
No partial result is returned when retrieval or processing fails.
"""
from __future__ import annotations

import re
import time
from typing import Callable

import httpx

from .secret_scan import redact_sensitive_text

MAX_TAIL_BYTES = 80_000
MAX_TRACE_BYTES = 16 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_ELAPSED_SECONDS = 30.0
READ_TIMEOUT_SECONDS = 10.0

_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_KEY_MARKER = re.compile(r"-----(BEGIN|END) (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")


class TraceReadError(RuntimeError):
    """Safe error containing neither response body nor transport exception text."""


def _tail_cap(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("tail_bytes must be an integer")
    return max(1_000, min(value, MAX_TAIL_BYTES))


class SafeLogTail:
    """Sanitize complete bounded lines *before* retaining a bounded UTF-8 tail.

    Line buffering preserves credentials split between HTTP chunks. A private-key
    block remains suppressed even when its opening line has left the output tail.
    Oversized lines fail closed rather than leaking an unrecognized fragment.
    """

    def __init__(
        self, tail_bytes: int, *, max_trace_bytes: int = MAX_TRACE_BYTES,
        max_line_bytes: int = MAX_LINE_BYTES,
        clock: Callable[[], float] | None = None,
        max_elapsed_seconds: float = MAX_ELAPSED_SECONDS,
    ) -> None:
        self.cap = _tail_cap(tail_bytes)
        if not (1 <= max_trace_bytes <= MAX_TRACE_BYTES and 1 <= max_line_bytes <= MAX_LINE_BYTES):
            raise ValueError("Invalid trace processing limits")
        if not 0 < max_elapsed_seconds <= MAX_ELAPSED_SECONDS:
            raise ValueError("Invalid trace time budget")
        self.max_elapsed_seconds = max_elapsed_seconds
        self.max_trace_bytes = max_trace_bytes
        self.max_line_bytes = max_line_bytes
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.total = 0
        self.sanitized_total = 0
        self.pending = bytearray()
        self.tail = bytearray()
        self.redactions: set[str] = set()
        self.in_key = False
        self.closed = False

    def _check(self) -> None:
        if self.closed:
            raise TraceReadError("Trace processor is closed; no log evidence returned")
        if self.clock() - self.started > self.max_elapsed_seconds:
            raise TraceReadError("Trace elapsed budget exceeded; no log evidence returned")

    def feed(self, chunk: bytes) -> None:
        self._check()
        self.total += len(chunk)
        if self.total > self.max_trace_bytes:
            raise TraceReadError("Trace byte limit exceeded; no log evidence returned")
        offset = 0
        while offset < len(chunk):
            self._check()
            end = chunk.find(b"\n", offset)
            stop = len(chunk) if end < 0 else end + 1
            if len(self.pending) + stop - offset > self.max_line_bytes:
                raise TraceReadError("Trace line limit exceeded; no log evidence returned")
            self.pending.extend(chunk[offset:stop])
            if end >= 0:
                self._line(bytes(self.pending))
                self.pending.clear()
            offset = stop

    def _line(self, raw: bytes) -> None:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise TraceReadError("Trace is not valid UTF-8; no log evidence returned") from None
        text = _CSI.sub("", text)
        # OSC/DCS/unknown or incomplete escape strings could conceal data or
        # manipulate terminals. Do not guess their intent or return their payload.
        if "\x1b" in text:
            raise TraceReadError("Unsupported terminal escape in trace; no log evidence returned")
        text = _CONTROLS.sub("", text)
        markers = list(_KEY_MARKER.finditer(text))
        if self.in_key or markers:
            if not self.in_key:
                self._append("[REDACTED:Private key block]\n")
            self.redactions.add("Private key block")
            if markers:
                self.in_key = markers[-1].group(1) == "BEGIN"
            return
        text, kinds = redact_sensitive_text(text)
        # Assignment names are repository-controlled and unbounded in variety;
        # expose a fixed category instead of reflecting names into diagnostics.
        self.redactions.update("secret assignment" if k.startswith("assignment:") else k for k in kinds)
        self._append(text)

    def _append(self, text: str) -> None:
        raw = text.encode("utf-8")
        self.sanitized_total += len(raw)
        self.tail.extend(raw[-self.cap:])
        if len(self.tail) > self.cap:
            del self.tail[:-self.cap]

    def finish(self) -> dict[str, object]:
        self._check()
        if self.pending:
            self._line(bytes(self.pending))
            self.pending.clear()
        self.closed = True
        content = bytes(self.tail).decode("utf-8", errors="ignore")
        return {
            "content": content,
            "truncated": self.total > self.cap or self.sanitized_total > self.cap,
            "original_text_bytes": self.total,
            "sanitized_text_bytes": self.sanitized_total,
            "returned_text_bytes": len(content.encode("utf-8")),
            "tail_bytes": self.cap,
            "read_complete": True,
            "sanitized": True,
            "redactions": sorted(self.redactions),
            "scope": "sanitized UTF-8 tail of the trace response, not all job output",
            "trust": "untrusted_diagnostic_data",
        }


def _validate_response(response: httpx.Response) -> int | None:
    if response.status_code != 200:
        # No response.read(), response.text, Location, or request URL in errors.
        raise TraceReadError(f"GitLab trace returned HTTP {response.status_code}; no log evidence returned")
    if response.headers.get("content-encoding", "identity").lower().strip() not in {"", "identity"}:
        raise TraceReadError("Compressed trace response refused; no log evidence returned")
    if "content-range" in response.headers:
        raise TraceReadError("Partial trace response refused; no log evidence returned")
    length = response.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise TraceReadError("Invalid trace length; no log evidence returned") from None
        if size < 0 or size > MAX_TRACE_BYTES:
            raise TraceReadError("Trace byte limit exceeded; no log evidence returned")
        return size
    return None


def read_trace_tail(
    client: httpx.Client, url: str, *, tail_bytes: int,
    timeout_seconds: float = MAX_ELAPSED_SECONDS,
) -> dict[str, object]:
    budget = min(timeout_seconds, MAX_ELAPSED_SECONDS)
    tail = SafeLogTail(tail_bytes, max_elapsed_seconds=budget)
    try:
        # Explicitly disallow redirects for token-bearing trace requests. Request
        # identity encoding and read raw bytes to avoid decompression allocation.
        with client.stream(
            "GET", url, headers={"Accept-Encoding": "identity"},
            follow_redirects=False, timeout=min(budget, READ_TIMEOUT_SECONDS),
        ) as response:
            expected_size = _validate_response(response)
            for chunk in response.iter_raw():
                tail.feed(chunk)
        if expected_size is not None and tail.total != expected_size:
            raise TraceReadError("Incomplete trace response; no log evidence returned")
        return tail.finish()
    except httpx.HTTPError:
        raise TraceReadError("GitLab trace transport failed; no log evidence returned") from None


async def aread_trace_tail(
    client: httpx.AsyncClient, url: str, *, tail_bytes: int,
    timeout_seconds: float = MAX_ELAPSED_SECONDS,
) -> dict[str, object]:
    budget = min(timeout_seconds, MAX_ELAPSED_SECONDS)
    tail = SafeLogTail(tail_bytes, max_elapsed_seconds=budget)
    try:
        async with client.stream(
            "GET", url, headers={"Accept-Encoding": "identity"},
            follow_redirects=False, timeout=min(budget, READ_TIMEOUT_SECONDS),
        ) as response:
            expected_size = _validate_response(response)
            async for chunk in response.aiter_raw():
                tail.feed(chunk)
        if expected_size is not None and tail.total != expected_size:
            raise TraceReadError("Incomplete trace response; no log evidence returned")
        return tail.finish()
    except httpx.HTTPError:
        raise TraceReadError("GitLab trace transport failed; no log evidence returned") from None
