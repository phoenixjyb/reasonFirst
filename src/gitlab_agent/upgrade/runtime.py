"""Offline, create-only service-runtime preparation; never service activation.

Trusted wheels/interpreters only. This is a package lifecycle helper, not a
sandbox, dependency publisher, Windows ACL adapter or crash-recovery engine.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

from . import deployment as storage
from ..python_runtime import executable_fingerprint, probe_python

PARTS = (".local", "share", "reasonfirst", "runtimes")
SCOPE = "offline-runtime-preparation-v1"
PACKAGE = "chatgpt-selfhosted-gitlab-mcp"
MAX_WHEEL = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")

# No controller is instantiated by this probe. The selected artifact and all its
# dependencies are executable code: approval precedes even import-only probes.
PROBE = r'''
import importlib.metadata as md, inspect, json, pathlib, sys
import gitlab_agent
from gitlab_agent import bridge_http, bridge_mcp
from gitlab_agent.bridge_preview import controller
from mcp.server import MCPServer
root = pathlib.Path(sys.prefix).resolve()
origins = [pathlib.Path(m.__file__).resolve() for m in
           (gitlab_agent, bridge_http, bridge_mcp, controller)]
assert all(p.is_relative_to(root) for p in origins)
assert sys.prefix != sys.base_prefix
inspect.signature(MCPServer.run).bind(None, transport="streamable-http",
    host="127.0.0.1", port=8765, streamable_http_path="/mcp")
def norm(s):
    import re
    return re.sub(r"[-_.]+", "-", s).lower()
pairs = [(norm(d.metadata['Name']), d.version) for d in md.distributions()]
assert len(pairs) == len(dict(pairs))
pkgs = dict(sorted(pairs))
assert pkgs['chatgpt-selfhosted-gitlab-mcp'] == gitlab_agent.__version__
eps = {e.name: e.value for e in md.distribution('chatgpt-selfhosted-gitlab-mcp').entry_points}
assert eps['reasonfirst-bridge-http'] == 'gitlab_agent.bridge_http:main'
assert eps['reasonfirst-bridge-mcp'] == 'gitlab_agent.bridge_mcp:main'
assert eps['reasonfirst'] == 'gitlab_agent.reasonfirst_cli:main'
print(json.dumps({'packages': pkgs, 'version': gitlab_agent.__version__,
 'python_version': list(sys.version_info[:3]), 'prefix': str(root),
 'origins': [str(p.relative_to(root)) for p in origins],
 'mcp_version': md.version('mcp'), 'http_signature_accepted': True,
 'controller_instantiated': False, 'server_started': False}, sort_keys=True))
'''


class RuntimeErrorCode(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def fail(code: str):
    raise RuntimeErrorCode(code)


def supported():
    if os.name != "posix" or platform.system() not in {"Darwin", "Linux"}:
        fail("unsupported_platform")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate_metadata")
        result[key] = value
    return result


def path_arg(value):
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(p)):
        fail("invalid_path")
    return p


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_metadata(name, raw):
    if not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", name):
        fail("invalid_wheel_name")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            if len(infos) > 8192 or sum(i.file_size for i in infos) > MAX_TOTAL:
                fail("wheel_limit")
            names = [i.filename for i in infos]
            if len(set(n.casefold() for n in names)) != len(names):
                fail("duplicate_wheel_path")
            for i in infos:
                p = PurePosixPath(i.filename)
                if (p.is_absolute() or ".." in p.parts or "\\" in i.filename
                    or ":" in i.filename or any(ord(c) < 32 for c in i.filename)
                    or stat.S_ISLNK(i.external_attr >> 16)):
                    fail("unsafe_wheel_path")
            candidates = [n for n in names if n.count("/") == 1 and n.endswith(".dist-info/METADATA")]
            if len(candidates) != 1 or z.getinfo(candidates[0]).file_size > 1024 * 1024:
                fail("invalid_wheel_metadata")
            meta = BytesParser().parsebytes(z.read(candidates[0]))
            if len(meta.get_all("Name", [])) != 1 or len(meta.get_all("Version", [])) != 1:
                fail("invalid_wheel_metadata")
            dist, version = meta['Name'], meta['Version']
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", dist) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version):
                fail("invalid_wheel_metadata")
            if any("@" in v for v in meta.get_all("Requires-Dist", [])):
                fail("direct_dependency_unsupported")
            if norm(dist) == PACKAGE:
                required = {"gitlab_agent/bridge_http.py", "gitlab_agent/bridge_mcp.py",
                            "gitlab_agent/bridge_preview/controller.py"}
                if not required.issubset(names):
                    fail("http_package_required")
            return {"name": norm(dist), "version": version,
                    "filename": name, "sha256": hashlib.sha256(raw).hexdigest()}
    except (zipfile.BadZipFile, KeyError, UnicodeError, ValueError):
        fail("invalid_wheel")


def wheelhouse_snapshot(folder, package_sha256):
    if not isinstance(package_sha256, str) or not HEX.fullmatch(package_sha256):
        fail("package_hash_required")
    wheels, total = [], 0
    with storage._directory(path_arg(folder), ()) as fd:
        names = sorted(os.listdir(fd))
        if not 1 <= len(names) <= 96:
            fail("wheelhouse_limit")
        for name in names:
            if not name.endswith(".whl"):
                fail("wheelhouse_must_contain_only_wheels")
            raw = storage._read_at(fd, name, limit=MAX_WHEEL)
            if raw is None:
                fail("changed_input")
            total += len(raw)
            if total > MAX_TOTAL:
                fail("wheelhouse_limit")
            wheels.append(wheel_metadata(name, raw))
    if len({w['name'] for w in wheels}) != len(wheels):
        fail("duplicate_distribution")
    package = [w for w in wheels if w['name'] == PACKAGE]
    if len(package) != 1 or package[0]['sha256'] != package_sha256:
        fail("package_hash_mismatch")
    return wheels


def uv_fingerprint(value):
    p = path_arg(value)
    if p.name != "uv" or not os.access(p, os.X_OK):
        fail("invalid_uv")
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_WHEEL:
        fail("invalid_uv")
    return {"path": str(p), "resolved_file": str(p.resolve(strict=True)),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def plan(*, wheelhouse, python, uv, package_sha256, home=None):
    supported()
    home = storage._home(home)
    selected = str(path_arg(python))
    fingerprint = executable_fingerprint(selected)
    if fingerprint['venv_config_sha256'] is not None:
        fail('base_python_required')
    data = {"schema_version": 1, "scope": SCOPE, "home": str(home),
            "platform": platform.system(), "machine": platform.machine(),
            "wheelhouse": str(path_arg(wheelhouse)),
            "wheels": wheelhouse_snapshot(wheelhouse, package_sha256),
            "python": {"path": selected, "fingerprint": fingerprint},
            "uv": uv_fingerprint(uv), "package_sha256": package_sha256}
    runtime_id = digest(data)
    with storage._directory(home, PARTS) as fd:
        exists = fd is not None and runtime_id in os.listdir(fd)
    return {"ok": True, "operation": "runtime-plan", "mutating": False,
            "input": data, "runtime_id": runtime_id, "plan_digest": runtime_id,
            "runtime_path": str(home.joinpath(*PARTS, runtime_id)),
            "destination_exists": exists, "prepared": False,
            "ready_for_activation": False, "commands_executed": False,
            "dependency_compatibility_checked": False}


def _write_new(fd, name, raw):
    child = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(child, "wb", closefd=False) as f:
            f.write(raw)
            f.flush()
            os.fsync(child)
    finally:
        os.close(child)
    os.fsync(fd)


def child_env(home):
    # Explicit environment allowlist rather than an ever-growing secret denylist.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "LD_LIBRARY_PATH", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
               XDG_DATA_HOME=str(home), TMPDIR=str(home), TEMP=str(home), TMP=str(home),
               PYTHONDONTWRITEBYTECODE="1", UV_PYTHON_DOWNLOADS="never")
    return env


def _run(argv, *, cwd, env, timeout=120):
    # Bounded time and returned bytes. Temp-file buffering is not a hard disk
    # quota or hostile-code sandbox. Output is never echoed in an error.
    with tempfile.TemporaryFile(dir=cwd) as out, tempfile.TemporaryFile(dir=cwd) as err:
        try:
            proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=err, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            fail("command_timeout")
        except OSError:
            fail("command_launch_failed")
        if proc.returncode:
            fail("command_failed")
        out.seek(0)
        raw = out.read(MAX_MANIFEST + 1)
        if len(raw) > MAX_MANIFEST:
            fail("command_output_limit")
        return raw


def _tree(root, interpreter):
    """Hash the installed tree without executing it or following directory links."""
    storage._check(root.lstat(), directory=True)
    rows, total = [], 0
    def onerror(_error):
        fail("runtime_unreadable")
    for base, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        for name in sorted(dirs + files):
            p = Path(base) / name
            rel = p.relative_to(root).as_posix()
            info = p.lstat()
            if stat.S_ISLNK(info.st_mode):
                destination = p.resolve(strict=True)
                permitted = ((rel == "lib64" and destination == root / "lib") or
                             (re.fullmatch(r"bin/python(?:3(?:\.\d+)?)?", rel) and
                              str(destination) == interpreter['fingerprint']['resolved_file']))
                if not permitted:
                    fail("unsafe_runtime_link")
                rows.append([rel, "link", os.readlink(p)])
            else:
                storage._check(info, directory=stat.S_ISDIR(info.st_mode))
                if stat.S_ISDIR(info.st_mode):
                    rows.append([rel, "dir", stat.S_IMODE(info.st_mode)])
                else:
                    total += info.st_size
                    if total > MAX_TOTAL or info.st_size > MAX_WHEEL:
                        fail("runtime_limit")
                    rows.append([rel, "file", stat.S_IMODE(info.st_mode), hashlib.sha256(p.read_bytes()).hexdigest()])
            if len(rows) > 20000:
                fail("runtime_limit")
    return sorted(rows)


def prepare(*, expect_digest, approved=False, **kwargs):
    supported()
    if not approved or not isinstance(expect_digest, str) or not HEX.fullmatch(expect_digest):
        fail("approval_required")
    reviewed = plan(**kwargs)
    if reviewed['plan_digest'] != expect_digest:
        fail("changed_input")
    if reviewed['destination_exists']:
        fail("destination_exists_inspect_status")
    data = reviewed['input']
    home = Path(data['home'])
    runtime_id = reviewed['runtime_id']
    root = Path(reviewed['runtime_path'])
    created = False
    phase = "create_destination"
    try:
        with storage._directory(home, PARTS, create=True) as parent:
            storage._check(os.fstat(parent), directory=True, private=True)
            try:
                os.mkdir(runtime_id, 0o700, dir_fd=parent)
            except FileExistsError:
                fail("destination_exists_inspect_status")
            created = True
            os.fsync(parent)
            with storage._directory(home, PARTS + (runtime_id,)) as fd:
                phase = "record_intent"
                _write_new(fd, "intent.json", canonical(data))
                for name in ("inputs", "build-home"):
                    os.mkdir(name, 0o700, dir_fd=fd)
                # Copy only the reviewed bytes into this new private directory;
                # uv never installs from mutable user Downloads paths.
                phase = "copy_inputs"
                with storage._directory(path_arg(data['wheelhouse']), ()) as source, storage._directory(root, ("inputs",)) as dest:
                    for wheel in data['wheels']:
                        raw = storage._read_at(source, wheel['filename'], limit=MAX_WHEEL)
                        if raw is None or hashlib.sha256(raw).hexdigest() != wheel['sha256']:
                            fail("changed_input")
                        _write_new(dest, wheel['filename'], raw)
                    requirements = "\n".join(w['name'] + "==" + w['version'] + " --hash=sha256:" + w['sha256'] for w in data['wheels']) + "\n"
                    _write_new(dest, "requirements.txt", requirements.encode('ascii'))
                phase = "probe_python"
                python = probe_python(data['python']['path'])
                if python['implementation'] != 'cpython' or python['version'][:2] < [3, 10] or python['fingerprint'] != data['python']['fingerprint']:
                    fail("python_identity_mismatch")
                if uv_fingerprint(data['uv']['path']) != data['uv']:
                    fail("changed_input")
                build_home = root / "build-home"
                env = child_env(build_home)
                cmd = [data['uv']['path'], '--no-config', '--offline', '--no-cache']
                phase = "create_venv"
                _run(cmd + ['venv', '--no-project', '--no-python-downloads', '--python', python['executable'], str(root / 'venv')], cwd=build_home, env=env)
                target = root / 'venv/bin/python'
                # Hash/verify the selected interpreter before running installed code.
                if target.resolve(strict=True) != Path(python['fingerprint']['resolved_file']):
                    fail("python_identity_mismatch")
                phase = "install_packages"
                _run(cmd + ['pip', 'install', '--python', str(target), '--no-python-downloads',
                    '--no-index', '--find-links', str(root / 'inputs'), '--only-binary', ':all:',
                    '--require-hashes', '--link-mode', 'copy', '-r', str(root / 'inputs/requirements.txt')], cwd=build_home, env=env)
                phase = "check_dependencies"
                _run(cmd + ['pip', 'check', '--python', str(target)], cwd=build_home, env=env)
                phase = "probe_package"
                observation = json.loads(_run([str(target), '-I', '-B', '-c', PROBE], cwd=build_home, env=env), object_pairs_hook=unique)
                expected = {w['name']: w['version'] for w in data['wheels']}
                if (observation['packages'] != expected or observation['version'] != expected[PACKAGE]
                    or observation['python_version'] != python['version']
                    or observation['prefix'] != str(root.resolve() / 'venv')
                    or observation['controller_instantiated'] is not False or observation['server_started'] is not False):
                    fail("runtime_identity_mismatch")
                if executable_fingerprint(python['executable']) != python['fingerprint']:
                    fail("changed_input")
                phase = "record_manifest"
                record = {"schema_version": 1, "scope": SCOPE, "runtime_id": runtime_id,
                    "input": data, "observation": observation,
                    "python": python, "tree": _tree(root / 'venv', data['python']),
                    "prepared_at": datetime.now(timezone.utc).isoformat(),
                    "activation_authorized": False, "service_started": False}
                record['manifest_digest'] = digest(record)
                raw = canonical(record)
                if len(raw) > MAX_MANIFEST:
                    fail("runtime_limit")
                _write_new(fd, 'runtime.json', raw)
        phase = "check_runtime"
        result = status(runtime_id=runtime_id, home=home)
        result.update(operation="runtime-prepare", mutating=True, created=True)
        if result.get('runtime_status') != 'prepared_matches_record':
            result['ok'] = False
        return result
    except (Exception, KeyboardInterrupt) as exc:
        if not created:
            raise
        code = exc.code if isinstance(exc, RuntimeErrorCode) else ("storage_" + exc.code if isinstance(exc, storage.DeploymentError) else "preparation_failed")
        return {"ok": False, "operation": "runtime-prepare", "error_code": code, "failure_stage": phase,
                "created": True, "runtime_id": runtime_id, "runtime_path": str(root),
                "runtime_status": "inspect_before_retry", "prepared": False,
                "ready_for_activation": False, "existing_installation_changed": False,
                "message": "New directory retained for inspection; no retry, cleanup or activation attempted."}


def status(*, runtime_id, home=None):
    supported()
    if not isinstance(runtime_id, str) or not HEX.fullmatch(runtime_id):
        fail("invalid_runtime_id")
    home = storage._home(home)
    root = home.joinpath(*PARTS, runtime_id)
    result = {"ok": True, "operation": "runtime-status", "mutating": False,
              "runtime_id": runtime_id, "runtime_path": str(root), "prepared": False,
              "ready_for_activation": False, "commands_executed": False,
              "live_service_verified": False}
    with storage._directory(home, PARTS + (runtime_id,)) as fd:
        if fd is None:
            return dict(result, runtime_status="not_found")
        storage._check(os.fstat(fd), directory=True, private=True)
        raw = storage._read_at(fd, 'runtime.json', limit=MAX_MANIFEST, private=True)
        if raw is None:
            return dict(result, runtime_status="preparation_incomplete")
        record = json.loads(raw, object_pairs_hook=unique)
        keys = {'schema_version', 'scope', 'runtime_id', 'input', 'observation', 'python', 'tree',
                'prepared_at', 'activation_authorized', 'service_started', 'manifest_digest'}
        if not isinstance(record, dict) or set(record) != keys:
            fail('invalid_runtime_record')
        signature = record.pop('manifest_digest')
        if (signature != digest(record) or record['schema_version'] != 1
            or type(record['schema_version']) is not int
            or record['scope'] != SCOPE or record['runtime_id'] != runtime_id
            or digest(record['input']) != runtime_id or record['input']['home'] != str(home)
            or record['activation_authorized'] is not False or record['service_started'] is not False):
            fail('invalid_runtime_record')
        input_keys = {'schema_version', 'scope', 'home', 'platform', 'machine', 'wheelhouse',
                      'wheels', 'python', 'uv', 'package_sha256'}
        observation_keys = {'packages', 'version', 'python_version', 'prefix', 'origins',
                            'mcp_version', 'http_signature_accepted', 'controller_instantiated', 'server_started'}
        observation = record['observation']
        if (set(record['input']) != input_keys or set(observation) != observation_keys
            or observation['prefix'] != str(root.resolve() / 'venv')
            or observation['packages'] != {w['name']: w['version'] for w in record['input']['wheels']}
            or observation['controller_instantiated'] is not False or observation['server_started'] is not False
            or observation['http_signature_accepted'] is not True
            or record['python']['fingerprint'] != record['input']['python']['fingerprint']
            or record['python']['executable'] != record['input']['python']['path']):
            fail('invalid_runtime_record')
        try:
            matched = (_tree(root / 'venv', record['input']['python']) == record['tree'] and
                       executable_fingerprint(record['python']['executable']) == record['python']['fingerprint'])
        except (OSError, RuntimeError, ValueError):
            matched = False
        # Successful metadata inspection is not a live-health or restart claim.
        return dict(result, runtime_status='prepared_matches_record' if matched else 'drifted',
                    prepared=matched, observed=record['observation'], manifest_digest=signature)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'reasonfirst-runtime: invalid_arguments; run --help\n')


def main(argv=None):
    parser = Parser(description='Development-only offline runtime staging; no activation.', allow_abbrev=False)
    sub = parser.add_subparsers(dest='action', required=True, parser_class=Parser)
    for action in ('plan', 'prepare'):
        p = sub.add_parser(action, allow_abbrev=False)
        for arg in ('wheelhouse', 'python', 'uv', 'package-sha256'):
            p.add_argument('--' + arg, required=True)
        if action == 'prepare':
            p.add_argument('--expect-digest', required=True)
            p.add_argument('--yes', action='store_true')
        p.add_argument('--json', action='store_true')
    p = sub.add_parser('status', allow_abbrev=False)
    p.add_argument('--runtime-id', required=True)
    p.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'status':
            result = status(runtime_id=args.runtime_id)
        else:
            kwargs = dict(wheelhouse=args.wheelhouse, python=args.python, uv=args.uv, package_sha256=args.package_sha256)
            result = plan(**kwargs) if args.action == 'plan' else prepare(expect_digest=args.expect_digest, approved=args.yes, **kwargs)
    except RuntimeErrorCode as exc:
        result = {'ok': False, 'error_code': exc.code, 'ready_for_activation': False}
    except (Exception, KeyboardInterrupt):
        result = {'ok': False, 'error_code': 'inspection_failed', 'ready_for_activation': False}
    print(json.dumps(result, sort_keys=True, ensure_ascii=True, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
