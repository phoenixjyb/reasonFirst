"""Private, retained CA inputs for selected managed GitLab API clients.

The selected PEM bytes, rather than a later pathname read, construct each TLS
context. Selected files are checked for drift before covered use. This does not
verify a TLS peer, native Git/SSH trust, provider configuration, or activation.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import stat
import threading

import certifi

from ..config import AgentSettings
from ..tls import MAX_CA_BUNDLE_BYTES, _PRIVATE_KEY, validate_base_url
from .service_runtime import _FileObservation, _identity, _observe_file


SCOPE = "selected-gitlab-api-trust-v1"
MAX_TRUST_PAYLOAD_BYTES = 12 * 1024 * 1024
_MAX_BASE64_CHARS = ((MAX_CA_BUNDLE_BYTES + 2) // 3) * 4
_READ_CHUNK = 1024 * 1024
_AUTHORITY = object()
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CODES = frozenset({
    "invalid_api_trust", "api_trust_wrong_process", "api_trust_unavailable",
    "api_trust_limit", "api_trust_changed", "api_trust_invalid_material",
    "api_trust_options_mismatch", "api_trust_invalid_payload",
})
_BUNDLE_KEYS = frozenset({"path", "resolved_path", "identity", "sha256", "pem_base64"})


class ServiceAPITrustError(RuntimeError):
    """Finite diagnostics that never expose paths, material or original errors."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _CODES else "invalid_api_trust"
        super().__init__(self.code)


def _fail(code):
    raise ServiceAPITrustError(code) from None


def _absolute_path(value, code):
    if isinstance(value, Path):
        value = str(value)
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail(code)
    path = Path(value)
    if not path.is_absolute():
        _fail(code)
    # Do not resolve the selected invocation: its symlink changes matter too.
    return path


def _selected_options(settings):
    if type(settings) is not AgentSettings:
        _fail("invalid_api_trust")
    try:
        value = settings.gitlab_base_url
        if type(value) is not str or len(value) > 4096 or settings.api_verify_ssl is not True:
            _fail("api_trust_options_mismatch")
        endpoint = validate_base_url(value)
        custom = (None if settings.api_ca_bundle is None else
                  _absolute_path(settings.api_ca_bundle, "api_trust_options_mismatch"))
        return endpoint, custom
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_options_mismatch")


def _pem_text(raw):
    if type(raw) is not bytes or not raw:
        _fail("api_trust_invalid_material")
    if len(raw) > MAX_CA_BUNDLE_BYTES:
        _fail("api_trust_limit")
    try:
        text = raw.decode("ascii")
        if _PRIVATE_KEY.search(text):
            _fail("api_trust_invalid_material")
        return text
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_invalid_material")


def _validate_pem(raw):
    text = _pem_text(raw)
    try:
        # No ambient/system trust store participates in validation.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=text)
        if not context.get_ca_certs(binary_form=True):
            _fail("api_trust_invalid_material")
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_invalid_material")


@dataclass(frozen=True, repr=False, slots=True)
class _Bundle:
    invoked: Path
    observed: _FileObservation
    pem: bytes


def _read_bundle(path):
    """Produce material and its observation from the same bounded descriptor."""
    fd = named_fd = None
    try:
        resolved = path.resolve(strict=True)
        flags = os.O_RDONLY
        for name in ("O_NONBLOCK", "O_NOFOLLOW", "O_BINARY", "O_CLOEXEC"):
            flags |= getattr(os, name, 0)
        fd = os.open(resolved, flags)
        os.set_inheritable(fd, False)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("api_trust_unavailable")
        if before.st_size > MAX_CA_BUNDLE_BYTES:
            _fail("api_trust_limit")
        material = bytearray()
        while True:
            chunk = os.read(fd, min(_READ_CHUNK, MAX_CA_BUNDLE_BYTES + 1 - len(material)))
            if not chunk:
                break
            material.extend(chunk)
            if len(material) > MAX_CA_BUNDLE_BYTES:
                _fail("api_trust_limit")
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or len(material) != before.st_size:
            _fail("api_trust_changed")
        # Keep both handles and compare fstat consistently, including on Windows.
        named_fd = os.open(resolved, flags)
        os.set_inheritable(named_fd, False)
        named = os.fstat(named_fd)
        if _identity(after) != _identity(named) or path.resolve(strict=True) != resolved:
            _fail("api_trust_changed")
        raw = bytes(material)
        return _Bundle(path, _FileObservation(resolved, _identity(after), hashlib.sha256(raw).hexdigest()), raw)
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_unavailable")
    finally:
        try:
            if named_fd is not None:
                os.close(named_fd)
        finally:
            if fd is not None:
                os.close(fd)


@dataclass(frozen=True, repr=False, eq=False, init=False)
class ServiceAPITrust:
    """Factory-owned CA bytes for one exact selected settings object and PID."""

    _settings: AgentSettings
    _endpoint: str
    _custom_path: Path | None
    _public: _Bundle
    _custom: _Bundle | None
    _authority: object
    _pid: int
    _lock: object
    _failure: str | None
    _summary: dict

    def __init__(self, *args, **kwargs):
        _fail("invalid_api_trust")

    def __repr__(self):
        return "ServiceAPITrust(retained_api_trust=private)"

    def _check_pid(self):
        if getattr(self, "_authority", None) is not _AUTHORITY:
            _fail("invalid_api_trust")
        if os.getpid() != self._pid:
            # Never acquire a potentially inherited mutex in another process.
            object.__setattr__(self, "_failure", "api_trust_wrong_process")
            _fail("api_trust_wrong_process")

    def _check_valid_locked(self):
        if self._failure is not None:
            _fail(self._failure)

    def _invalidate_locked(self, code):
        if self._failure is None:
            object.__setattr__(self, "_failure", code)
        _fail(self._failure)

    @property
    def settings(self):
        self._check_pid()
        return self._settings

    def summary(self):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            return dict(self._summary)

    def _revalidate_locked(self):
        self._check_valid_locked()
        try:
            for bundle in (self._public, self._custom):
                if bundle is not None and _observe_file(bundle.invoked, MAX_CA_BUNDLE_BYTES) != bundle.observed:
                    self._invalidate_locked("api_trust_changed")
        except ServiceAPITrustError:
            raise
        except Exception:
            self._invalidate_locked("api_trust_changed")

    def revalidate(self):
        self._check_pid()
        with self._lock:
            self._revalidate_locked()

    def assert_options(self, base_url, verify_ssl, ca_bundle):
        self._check_pid()
        with self._lock:
            self._check_valid_locked()
            try:
                if (type(base_url) is not str or len(base_url) > 4096 or verify_ssl is not True
                        or validate_base_url(base_url) != self._endpoint):
                    _fail("api_trust_options_mismatch")
                path = None if ca_bundle is None else _absolute_path(ca_bundle, "api_trust_options_mismatch")
                if path != self._custom_path:
                    _fail("api_trust_options_mismatch")
            except ServiceAPITrustError:
                raise
            except Exception:
                _fail("api_trust_options_mismatch")

    def ssl_context(self):
        self._check_pid()
        with self._lock:
            self._revalidate_locked()
            try:
                context = ssl.create_default_context(cadata=_pem_text(self._public.pem))
                if self._custom is not None:
                    context.load_verify_locations(cadata=_pem_text(self._custom.pem))
                if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
                    self._invalidate_locked("api_trust_invalid_material")
                return context
            except ServiceAPITrustError:
                raise
            except Exception:
                self._invalidate_locked("api_trust_invalid_material")


def _create(settings, options, public, custom):
    trust = object.__new__(ServiceAPITrust)
    fields = {
        "_settings": settings, "_endpoint": options[0], "_custom_path": options[1],
        "_public": public, "_custom": custom, "_authority": _AUTHORITY,
        "_pid": os.getpid(), "_lock": threading.RLock(), "_failure": None,
        "_summary": {
            "schema_version": 1, "scope": SCOPE, "material_retained": True,
            "selected_bundle_count": 1 if custom is None else 2,
            "current_process_only": True, "tls_peer_verified": False,
            "provider_configuration_verified": False, "native_git_trust_verified": False,
            "native_ssh_trust_verified": False, "activation_authorized": False,
        },
    }
    for name, value in fields.items():
        object.__setattr__(trust, name, value)
    return trust


def capture_api_trust(settings):
    """Select public/custom material only when the managed caller needs an API."""
    options = _selected_options(settings)
    pid = os.getpid()
    try:
        public = _read_bundle(_absolute_path(certifi.where(), "api_trust_unavailable"))
        _validate_pem(public.pem)
        custom = _read_bundle(options[1]) if options[1] is not None else None
        if custom is not None:
            _validate_pem(custom.pem)
        if os.getpid() != pid:
            _fail("api_trust_wrong_process")
        return _create(settings, options, public, custom)
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_unavailable")


def _bundle_payload(bundle):
    return {
        "path": str(bundle.invoked), "resolved_path": str(bundle.observed.path),
        "identity": list(bundle.observed.identity), "sha256": bundle.observed.sha256,
        "pem_base64": base64.b64encode(bundle.pem).decode("ascii"),
    }


def encode_api_trust(trust):
    """Return a detached private pipe payload; this is never public evidence."""
    if type(trust) is not ServiceAPITrust:
        _fail("invalid_api_trust")
    trust._check_pid()
    with trust._lock:
        trust._revalidate_locked()
        result = {
            "schema_version": 1, "scope": SCOPE, "public": _bundle_payload(trust._public),
            "custom": _bundle_payload(trust._custom) if trust._custom is not None else None,
        }
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_TRUST_PAYLOAD_BYTES:
            _fail("api_trust_limit")
        return result


def _decode_bundle(value):
    if type(value) is not dict or set(value) != _BUNDLE_KEYS:
        _fail("api_trust_invalid_payload")
    if type(value["path"]) is not str or type(value["resolved_path"]) is not str:
        _fail("api_trust_invalid_payload")
    invoked = _absolute_path(value["path"], "api_trust_invalid_payload")
    resolved = _absolute_path(value["resolved_path"], "api_trust_invalid_payload")
    identity = value["identity"]
    if (type(identity) is not list or len(identity) != 6
            or any(type(item) is not int or abs(item) >= 2**128 for item in identity)
            or any(item < 0 for item in identity[:4])
            or identity[2] > 2**32 - 1 or not stat.S_ISREG(identity[2])
            or not 1 <= identity[3] <= MAX_CA_BUNDLE_BYTES):
        _fail("api_trust_invalid_payload")
    digest, encoded = value["sha256"], value["pem_base64"]
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        _fail("api_trust_invalid_payload")
    if type(encoded) is not str or not encoded or len(encoded) > _MAX_BASE64_CHARS:
        _fail("api_trust_invalid_payload")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        _fail("api_trust_invalid_payload")
    if (len(raw) != identity[3] or not 1 <= len(raw) <= MAX_CA_BUNDLE_BYTES
            or base64.b64encode(raw).decode("ascii") != encoded
            or hashlib.sha256(raw).hexdigest() != digest):
        _fail("api_trust_invalid_payload")
    _validate_pem(raw)
    return _Bundle(invoked, _FileObservation(resolved, tuple(identity), digest), raw)


def decode_api_trust(settings, payload):
    """Bind transferred bytes to child settings without any new CA selection."""
    options = _selected_options(settings)
    try:
        if (type(payload) is not dict or set(payload) != {"schema_version", "scope", "public", "custom"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or type(payload["scope"]) is not str or payload["scope"] != SCOPE):
            _fail("api_trust_invalid_payload")
        public = _decode_bundle(payload["public"])
        custom = None if payload["custom"] is None else _decode_bundle(payload["custom"])
        if ((custom is None) != (options[1] is None)
                or (custom is not None and custom.invoked != options[1])):
            _fail("api_trust_invalid_payload")
        if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_TRUST_PAYLOAD_BYTES:
            _fail("api_trust_limit")
        trust = _create(settings, options, public, custom)
        trust.revalidate()
        return trust
    except ServiceAPITrustError:
        raise
    except Exception:
        _fail("api_trust_invalid_payload")
