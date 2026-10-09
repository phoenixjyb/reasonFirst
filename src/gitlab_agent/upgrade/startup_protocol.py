"""One-shot startup *claim* exchange, not process attestation or activation.

Internal protocol primitive only: no CLI, socket, process launch, service manager,
filesystem inspection or integration with the running Bridge is added here.
The caller must derive expected identity independently and acquire a reply on a
separately reviewed channel. Matching self-reported values never verifies a peer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Callable

PROTOCOL = "reasonfirst-startup-claims-v1"
MAX_MESSAGE_BYTES = 16 * 1024
LIFETIME_NS = 8_000_000_000
STARTUP_BLOCKER = "managed_startup_confirmation_not_verified"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_CLAIM_FIELDS = frozenset({"runtime_id", "manifest_digest", "interpreter_digest",
                         "configuration_digest", "host", "port", "path", "mode",
                         "control_policy"})
_ERRORS = frozenset({"invalid_claims", "invalid_message", "invalid_challenge",
                     "invalid_reply", "invalid_expected_pid", "invalid_clock",
                     "challenge_expired", "challenge_used", "challenge_not_started",
                     "reply_mismatch", "challenge_cancelled"})


class StartupProtocolError(ValueError):
    """Only fixed codes; never a raw reply, nonce, path, or configuration value."""
    def __init__(self, code: str):
        super().__init__(code if code in _ERRORS else "invalid_message")


def _fail(code: str):
    raise StartupProtocolError(code) from None


def _hex(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _pid(value: object) -> bool:
    return type(value) is int and 0 < value <= 2**31 - 1


def _canonical(value: object) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail("invalid_message")
    if len(raw) > MAX_MESSAGE_BYTES:
        _fail("invalid_message")
    return raw


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            _fail("invalid_message")
        result[name] = value
    return result


def _constant(_value):
    _fail("invalid_message")


def _decode(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MESSAGE_BYTES:
        _fail("invalid_message")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        _fail("invalid_message")
    if type(value) is not dict:
        _fail("invalid_message")
    return value


@dataclass(frozen=True, repr=False)
class StartupClaims:
    """Selected claims only; no file is read to establish their truth.

    configuration_digest must cover an approved *non-secret* resolved projection,
    never raw .env bytes, credentials, or an arbitrary environment dictionary.
    These digests are identifiers, not signatures. Their collection is deferred.
    """
    runtime_id: str
    manifest_digest: str
    interpreter_digest: str
    configuration_digest: str
    host: str
    port: int
    path: str
    mode: str
    control_policy: str

    def __post_init__(self):
        for value in (self.runtime_id, self.manifest_digest,
                      self.interpreter_digest, self.configuration_digest):
            if not _hex(value):
                _fail("invalid_claims")
        # Reuse the existing transport validator, without importing the SDK or
        # controller and without allowing ambient process policy into a message.
        if any(type(v) is not str for v in (self.host, self.path, self.mode, self.control_policy)):
            _fail("invalid_claims")
        from ..bridge_http import HTTPLaunch, HTTPLaunchError
        try:
            HTTPLaunch(self.host, self.port, self.path, self.mode, self.control_policy).validate({})
        except HTTPLaunchError:
            _fail("invalid_claims")

    @classmethod
    def from_mapping(cls, value: object) -> StartupClaims:
        if type(value) is not dict or set(value) != _CLAIM_FIELDS:
            _fail("invalid_claims")
        return cls(**value)

    def to_mapping(self) -> dict:
        return asdict(self)


def _challenge(raw: bytes) -> dict:
    value = _decode(raw)
    if (set(value) != {"protocol", "kind", "launch_id", "nonce"}
            or value["protocol"] != PROTOCOL or value["kind"] != "challenge"
            or not _hex(value["launch_id"]) or not _hex(value["nonce"])):
        _fail("invalid_challenge")
    return value


def make_reply(challenge: bytes, *, observed: StartupClaims) -> bytes:
    """Encode caller-collected claims with this process's PID, not a supplied PID.

    The request deliberately contains NO expected claims to echo. Observed values
    must come from resolved startup state in a future integration, not the plan.
    This helper alone does not perform that collection or authenticate its caller.
    """
    request = _challenge(challenge)
    if type(observed) is not StartupClaims:
        _fail("invalid_claims")
    pid = os.getpid()
    if not _pid(pid):
        _fail("invalid_reply")
    return _canonical({"protocol": PROTOCOL, "kind": "reply",
                       "launch_id": request["launch_id"], "nonce": request["nonce"],
                       "pid": pid, "claims": observed.to_mapping()})


def _report() -> dict:
    # Deliberately contains neither nonce nor reported identity values. A caller
    # cannot grant attestation/activation by passing a boolean option to this API.
    return {"operation": "startup-claims-check", "protocol": PROTOCOL, "ok": True,
            "fresh_reply_claims_match": True, "pid_claim_matches": True,
            "peer_identity_verified": False, "running_code_verified": False,
            "effective_configuration_verified": False,
            "managed_startup_confirmation_verified": False,
            "compatibility_verified": False, "activation_authorized": False,
            "ready_for_activation": False, "service_changed": False,
            "blockers": [STARTUP_BLOCKER, "startup_peer_identity_not_verified",
                         "startup_claims_collection_not_verified"]}


class PendingStartupChallenge:
    """Single process, single-use, bounded-lifetime verifier.

    expected_pid is an expectation from a future supervisor's own process handle,
    NOT a proof obtained by accepting the responder's PID field. Never serialize
    pending state or replay it after a supervisor restart. The clock injection is
    solely a deterministic unit-test seam, not a wire option or user CLI flag.
    """
    def __init__(self, *, expected: StartupClaims, expected_pid: int,
                 _clock_ns: Callable[[], int] = time.monotonic_ns):
        if type(expected) is not StartupClaims:
            _fail("invalid_claims")
        if not _pid(expected_pid):
            _fail("invalid_expected_pid")
        self._expected = _canonical(expected.to_mapping())
        self._expected_pid = expected_pid
        self._clock = _clock_ns
        self._created = self._now()
        self._last = self._created
        self._nonce = secrets.token_hex(32)
        self._launch_id = secrets.token_hex(32)
        self._wire = _canonical({"protocol": PROTOCOL, "kind": "challenge",
                                 "nonce": self._nonce, "launch_id": self._launch_id})
        _challenge(self._wire)
        self._state = "pending"
        self._lock = threading.Lock()

    def _now(self) -> int:
        try:
            now = self._clock()
        except Exception:
            _fail("invalid_clock")
        if type(now) is not int or now < 0:
            _fail("invalid_clock")
        return now

    def _time_check(self):
        now = self._now()
        if now < self._last:
            _fail("invalid_clock")
        self._last = now
        if now - self._created >= LIFETIME_NS:
            _fail("challenge_expired")

    def start(self) -> bytes:
        with self._lock:
            if self._state != "pending":
                _fail("challenge_used")
            # Any failure also consumes the challenge; never silently retry a
            # failed attempt with the same nonce or renewed deadline.
            self._state = "consumed"
            self._time_check()
            self._state = "awaiting_reply"
            return self._wire

    def cancel(self) -> None:
        with self._lock:
            self._state = "consumed"

    def finish(self, reply: bytes) -> dict:
        with self._lock:
            if self._state == "pending":
                self._state = "consumed"
                _fail("challenge_not_started")
            if self._state != "awaiting_reply":
                _fail("challenge_used")
            self._state = "consumed"
            self._time_check()
            value = _decode(reply)
            if (set(value) != {"protocol", "kind", "launch_id", "nonce", "pid", "claims"}
                    or value["protocol"] != PROTOCOL or value["kind"] != "reply"
                    or not _hex(value["launch_id"]) or not _hex(value["nonce"])
                    or not _pid(value["pid"])):
                _fail("invalid_reply")
            claims = StartupClaims.from_mapping(value["claims"])
            if (not hmac.compare_digest(value["nonce"], self._nonce)
                    or not hmac.compare_digest(value["launch_id"], self._launch_id)
                    or value["pid"] != self._expected_pid
                    or not hmac.compare_digest(_canonical(claims.to_mapping()), self._expected)):
                _fail("reply_mismatch")
            # Parsing/checking time counts toward the same fixed lifetime.
            self._time_check()
            return _report()
