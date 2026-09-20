from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from gitlab_agent.log_evidence import (
    MAX_LINE_BYTES, MAX_TRACE_BYTES, SafeLogTail, TraceReadError,
    aread_trace_tail, read_trace_tail,
)
from gitlab_agent.gitlab_api import GitLabAPI
from gitlab_agent.ci_feedback import collect_ci_feedback


def token() -> str:
    return "gl" + "pat-" + "notARealCredential0123456789"


def key_marker(kind: str) -> bytes:
    return ("-----" + kind + " PRIVATE " + "KEY-----\n").encode()


class TraceStream(httpx.SyncByteStream, httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def __aiter__(self):
        for chunk in self:
            yield chunk

    def close(self):
        self.closed = True

    async def aclose(self):
        self.close()


class SafeLogTailTests(unittest.TestCase):
    def process(self, raw, **kwargs):
        tail = SafeLogTail(1000, **kwargs)
        tail.feed(raw)
        return tail.finish()

    def test_plain_text_preserved_and_metadata_explicit(self):
        result = self.process(b"build failed\n")
        self.assertEqual(result["content"], "build failed\n")
        self.assertEqual(result["original_text_bytes"], 13)
        self.assertTrue(result["read_complete"])
        self.assertTrue(result["sanitized"])
        self.assertEqual(result["trust"], "untrusted_diagnostic_data")

    def test_empty_trace_is_a_successful_empty_read(self):
        result = self.process(b"")
        self.assertEqual(result["content"], "")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["original_text_bytes"], 0)

    def test_credential_split_at_every_chunk_boundary(self):
        raw = ("TOKEN=" + token() + "\nfailed\n").encode()
        for split in range(len(raw) + 1):
            with self.subTest(split=split):
                tail = SafeLogTail(1000)
                tail.feed(raw[:split])
                tail.feed(raw[split:])
                result = tail.finish()
                self.assertNotIn(token(), json.dumps(result))
                self.assertIn("failed", result["content"])

    def test_utf8_split_into_single_bytes(self):
        text = "构建失败 café\n"
        tail = SafeLogTail(1000)
        for byte in text.encode():
            tail.feed(bytes([byte]))
        self.assertEqual(tail.finish()["content"], text)

    def test_tail_is_bounded_after_sanitization(self):
        result = self.process(b"a" * 800 + b"b" * 800)
        self.assertEqual(result["returned_text_bytes"], 1000)
        self.assertTrue(result["content"].endswith("b" * 800))
        self.assertTrue(result["truncated"])

    def test_utf8_tail_is_valid_and_within_cap(self):
        result = self.process(("界" * 700).encode())
        self.assertLessEqual(result["returned_text_bytes"], 1000)
        self.assertNotIn("\ufffd", result["content"])

    def test_credential_crossing_tail_boundary_is_not_partially_exposed(self):
        raw = ("TOKEN=" + token() + "\n" + "z" * 980).encode()
        result = self.process(raw)
        self.assertNotIn(token()[-15:], result["content"])

    def test_private_key_opening_outside_tail_does_not_expose_body(self):
        tail = SafeLogTail(1000)
        tail.feed(key_marker("BEGIN"))
        for _ in range(100):
            tail.feed(b"opaqueKeyBodyNotAToken\n")
        tail.feed(key_marker("END") + b"failure location\n")
        result = tail.finish()
        self.assertNotIn("opaqueKeyBody", result["content"])
        self.assertIn("failure location", result["content"])
        self.assertIn("Private key block", result["redactions"])

    def test_unterminated_private_key_body_stays_suppressed(self):
        result = self.process(key_marker("BEGIN") + b"opaqueKeyBody\n")
        self.assertNotIn("opaqueKeyBody", result["content"])

    def test_private_key_markers_split_into_chunks(self):
        raw = key_marker("BEGIN") + b"opaqueKeyBody\n" + key_marker("END")
        tail = SafeLogTail(1000)
        for byte in raw:
            tail.feed(bytes([byte]))
        self.assertNotIn("opaqueKeyBody", tail.finish()["content"])

    def test_ansi_inside_token_is_cleaned_before_redaction(self):
        value = token()
        raw = (value[:9] + "\x1b[31m" + value[9:] + "\x1b[0m\n").encode()
        result = self.process(raw)
        self.assertNotIn(value, result["content"])
        self.assertNotIn("\x1b", result["content"])

    def test_quoted_assignments_are_redacted(self):
        result = self.process(b'PASSWORD="opaque value"\n')
        self.assertNotIn("opaque value", result["content"])
        self.assertEqual(result["redactions"], ["secret assignment"])

    def test_secret_assignment_names_do_not_expand_metadata(self):
        tail = SafeLogTail(1000)
        for i in range(1000):
            tail.feed(f"TOKEN_{i}=value\n".encode())
        self.assertEqual(tail.finish()["redactions"], ["secret assignment"])

    def test_byte_limit_fails_instead_of_claiming_real_tail(self):
        with self.assertRaisesRegex(TraceReadError, "byte limit"):
            self.process(b"x" * 101, max_trace_bytes=100)

    def test_line_limit_fails_without_exporting_fragment(self):
        with self.assertRaisesRegex(TraceReadError, "line limit"):
            self.process(b"x" * 101, max_line_bytes=100)

    def test_invalid_utf8_fails_instead_of_dropping_hidden_bytes(self):
        with self.assertRaisesRegex(TraceReadError, "UTF-8"):
            self.process(b"prefix\xffsecret\n")

    def test_unknown_terminal_controls_are_not_returned(self):
        with self.assertRaisesRegex(TraceReadError, "terminal escape"):
            self.process(b"\x1b]52;c;secret\x07\n")

    def test_elapsed_budget_is_checked_at_feed_and_eof(self):
        clock = [0.0]
        tail = SafeLogTail(1000, clock=lambda: clock[0])
        tail.feed(b"safe\n")
        clock[0] = 31
        with self.assertRaisesRegex(TraceReadError, "elapsed"):
            tail.finish()

    def test_input_tail_bounds_are_clamped(self):
        self.assertEqual(SafeLogTail(-1).cap, 1000)
        self.assertEqual(SafeLogTail(999999).cap, 80000)
        with self.assertRaises(ValueError):
            SafeLogTail(True)

    def test_finish_cannot_be_reused(self):
        tail = SafeLogTail(1000)
        tail.finish()
        with self.assertRaises(TraceReadError):
            tail.feed(b"late")


class TraceTransportTests(unittest.TestCase):
    def run_reader(self, async_mode, chunks, status=200, headers=None):
        stream = TraceStream(chunks)
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(status, headers=headers or {}, stream=stream)
        transport = httpx.MockTransport(handler)
        async def arun():
            async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
                return await aread_trace_tail(client, "http://gitlab.example.invalid/trace", tail_bytes=1000)
        def srun():
            with httpx.Client(transport=transport, follow_redirects=True) as client:
                return read_trace_tail(client, "http://gitlab.example.invalid/trace", tail_bytes=1000)
        self.addCleanup(lambda: self.assertTrue(stream.closed))
        return (arun, srun, stream, requests)

    def invoke(self, async_mode, **kwargs):
        arun, srun, stream, requests = self.run_reader(async_mode, **kwargs)
        return asyncio.run(arun()) if async_mode else srun(), stream, requests

    def test_sync_and_async_evidence_are_identical(self):
        chunks = [b"\x1b[31m", (token() + "\x1b[0m\n").encode(), b"a" * 800, b"b" * 800]
        a, _, _ = self.invoke(True, chunks=chunks)
        b, _, _ = self.invoke(False, chunks=chunks)
        self.assertEqual(a, b)
        self.assertNotIn(token(), json.dumps(a))

    def test_identity_encoding_and_bounded_read_timeout_requested(self):
        for mode in (False, True):
            result, _, requests = self.invoke(mode, chunks=[b"ok"])
            self.assertEqual(requests[0].headers["accept-encoding"], "identity")
            self.assertEqual(requests[0].extensions["timeout"]["read"], 10)
            self.assertTrue(result["read_complete"])

    def test_http_errors_do_not_read_or_echo_body(self):
        for mode in (False, True):
            arun, srun, stream, _ = self.run_reader(mode, [token().encode()], status=500)
            with self.assertRaisesRegex(TraceReadError, "HTTP 500") as cm:
                asyncio.run(arun()) if mode else srun()
            self.assertEqual(stream.reads, 0)
            self.assertNotIn(token(), str(cm.exception))

    def test_redirects_not_followed_even_when_client_default_is_true(self):
        for mode in (False, True):
            arun, srun, stream, requests = self.run_reader(mode, [], status=302,
                headers={"location": "https://elsewhere.invalid/?credential=" + token()})
            with self.assertRaisesRegex(TraceReadError, "HTTP 302"):
                asyncio.run(arun()) if mode else srun()
            self.assertEqual(len(requests), 1)
            self.assertEqual(stream.reads, 0)

    def test_transport_errors_hide_raw_exception_text(self):
        for mode in (False, True):
            with self.assertRaises(TraceReadError) as cm:
                self.invoke(mode, chunks=[b"safe\n", httpx.ReadError("private-value=" + token())])
            self.assertNotIn(token(), str(cm.exception))
            self.assertTrue(cm.exception.__suppress_context__)

    def test_timeout_is_not_a_successful_partial_read(self):
        for mode in (False, True):
            with self.assertRaisesRegex(TraceReadError, "transport failed"):
                self.invoke(mode, chunks=[b"safe\n", httpx.ReadTimeout("internal credential")])

    def test_oversized_content_length_fails_before_body_read(self):
        for mode in (False, True):
            arun, srun, stream, _ = self.run_reader(mode, [b"not consumed"],
                headers={"content-length": str(MAX_TRACE_BYTES + 1)})
            with self.assertRaisesRegex(TraceReadError, "byte limit"):
                asyncio.run(arun()) if mode else srun()
            self.assertEqual(stream.reads, 0)

    def test_compression_is_refused_before_decompression(self):
        for mode in (False, True):
            with self.assertRaisesRegex(TraceReadError, "Compressed"):
                self.invoke(mode, chunks=[b"not gzip"], headers={"content-encoding": "gzip"})

    def test_partial_http_response_is_not_full_trace_evidence(self):
        for mode in (False, True):
            with self.assertRaisesRegex(TraceReadError, "HTTP 206"):
                self.invoke(mode, chunks=[b"fragment"], status=206)

    def test_truncated_body_does_not_claim_read_complete(self):
        for mode in (False, True):
            with self.assertRaisesRegex(TraceReadError, "Incomplete"):
                self.invoke(mode, chunks=[b"short"], headers={"content-length": "100"})

    def test_actual_stream_byte_count_is_bounded_without_length_header(self):
        for mode in (False, True):
            with self.assertRaisesRegex(TraceReadError, "byte limit"):
                self.invoke(mode, chunks=[b"x" * (MAX_TRACE_BYTES + 1)])

    def test_long_stream_retains_only_small_tail_and_line_buffer(self):
        tail = SafeLogTail(1000)
        for _ in range(2048):
            tail.feed(b"x" * 1023 + b"\n")
            self.assertLessEqual(len(tail.tail), 1000)
            self.assertLessEqual(len(tail.pending), MAX_LINE_BYTES)
        self.assertTrue(tail.finish()["truncated"])

    def test_cli_adapter_and_legacy_trace_use_shared_sanitizer(self):
        settings = SimpleNamespace(gitlab_base_url="http://gitlab.example.invalid")
        api = GitLabAPI(settings)
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=TraceStream([(token() + "\nfailed\n").encode()]))
        for legacy in (False, True):
            with patch.object(api, "_client", return_value=httpx.Client(transport=httpx.MockTransport(handler))):
                value = api.job_trace("team/project", 99) if legacy else api.job_trace_tail("team/project", 99, tail_bytes=1000)
            self.assertNotIn(token(), str(value))
        self.assertEqual(calls[0].url.raw_path, b"/api/v4/projects/team%2Fproject/jobs/99/trace")


class CIFeedbackLogTests(unittest.TestCase):
    def feedback(self, logs, *, limit=3):
        manager = SimpleNamespace(
            status=lambda _: {"project": "team/project", "branch": "feature", "head": "sha"},
            get_state=lambda _: SimpleNamespace(pushed=True, merge_request_url=None),
        )
        def trace(project, job_id, **kwargs):
            result = logs[job_id - 1]
            if isinstance(result, Exception):
                raise result
            return result
        api = SimpleNamespace(
            pipelines=lambda *a, **k: [{"id": 1, "sha": "sha", "status": "failed"}],
            pipeline_jobs=lambda *a, **k: [
                {"id": i + 1, "name": "unit", "status": "failed"} for i in range(len(logs))],
            job_trace_tail=trace,
        )
        return collect_ci_feedback(manager=manager, api=api, workspace_id="test", max_failed_jobs=limit)

    def good_log(self):
        tail = SafeLogTail(1000)
        tail.feed((token() + "\nfailed\n").encode())
        return tail.finish()

    def test_metadata_and_redactions_reach_reasoning_context(self):
        result = self.feedback([self.good_log()])
        self.assertTrue(result["failed_job_logs_complete"])
        self.assertIn("GitLab PAT", result["failed_job_logs"][0]["redactions"])
        self.assertIn("Trace response fully read: True", result["repair_context"])
        self.assertNotIn(token(), json.dumps(result))

    def test_unknown_exception_is_not_reflected_into_prompt(self):
        result = self.feedback([RuntimeError("opaqueServerSecret " + token())])
        self.assertFalse(result["failed_job_logs_complete"])
        self.assertNotIn("opaqueServerSecret", json.dumps(result))
        self.assertNotIn(token(), json.dumps(result))
        self.assertEqual(result["failed_job_logs"][0]["content"], "")
        self.assertTrue(any("incomplete" in x for x in result["warnings"]))

    def test_safe_shared_error_retains_useful_limit_reason(self):
        result = self.feedback([TraceReadError("Trace byte limit exceeded; no log evidence returned")])
        self.assertIn("byte limit", result["repair_context"])
        self.assertFalse(result["failed_job_logs_complete"])

    def test_omitted_failed_jobs_are_not_claimed_as_complete(self):
        result = self.feedback([self.good_log(), self.good_log()], limit=1)
        self.assertFalse(result["failed_job_logs_complete"])
        self.assertEqual(len(result["failed_job_logs"]), 1)

    def test_old_adapter_remains_sanitized_but_completeness_unknown(self):
        result = self.feedback([{"content": token(), "truncated": False}])
        self.assertFalse(result["failed_job_logs_complete"])
        self.assertNotIn(token(), json.dumps(result))
        self.assertIsNone(result["failed_job_logs"][0]["read_complete"])


if __name__ == "__main__":
    unittest.main()
