#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


def patch_cli(path: Path) -> bool:
    s = path.read_text(encoding="utf-8")
    if "def _git_only_preflight(" in s and s.count('"--git-only"') >= 2:
        return False

    marker = "\ndef _build_parser(prog: str = \"gitlab-agent\") -> argparse.ArgumentParser:\n"
    helper = r'''

def _git_only_preflight(result: dict[str, object]) -> dict[str, object]:
    """Treat missing GitLab API token as non-blocking for explicit Git-only flows.

    Git-only still requires every other doctor check to pass, including the Git
    HTTPS credential, workspace permissions, disk state, and coding backend.
    """
    checks = result.get("checks")
    if not isinstance(checks, list):
        return result
    blocking = [
        item for item in checks
        if isinstance(item, dict)
        and item.get("status") == "fail"
        and item.get("name") != "gitlab_api_token"
    ]
    api_missing = any(
        isinstance(item, dict)
        and item.get("name") == "gitlab_api_token"
        and item.get("status") == "fail"
        for item in checks
    )
    if blocking or not api_missing:
        return result

    out = dict(result)
    rewritten: list[dict[str, object]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        copy = dict(item)
        if copy.get("name") == "gitlab_api_token" and copy.get("status") == "fail":
            copy["status"] = "skip"
            copy["message"] = (
                "GITLAB_TOKEN is intentionally absent in Git-only mode; "
                "GitLab REST API/MCP/CI features are unavailable"
            )
        rewritten.append(copy)
    summary = {"pass": 0, "skip": 0, "warn": 0, "fail": 0}
    for item in rewritten:
        status = str(item.get("status") or "warn")
        summary[status] = summary.get(status, 0) + 1
    out["checks"] = rewritten
    out["summary"] = summary
    out["ok"] = summary.get("fail", 0) == 0
    out["overall"] = "warn" if summary.get("warn", 0) else "pass"
    out["git_only"] = True
    return out
'''
    if marker not in s:
        raise RuntimeError("Could not find _build_parser insertion point")
    s = s.replace(marker, helper + marker, 1)

    old = '''    p.add_argument(\n        "--offline",\n        action="store_true",\n        help="Skip live GitLab API connectivity/authentication check",\n    )\n'''
    new = old + '''    p.add_argument(\n        "--git-only",\n        action="store_true",\n        help="Allow Git HTTPS credentials without GITLAB_TOKEN; API/MCP/CI features stay disabled",\n    )\n'''
    if old not in s:
        raise RuntimeError("Could not patch doctor parser")
    s = s.replace(old, new, 1)

    old = '''    p.add_argument(\n        "--offline-doctor",\n        action="store_true",\n        help="Skip the live GitLab API check in the preflight doctor",\n    )\n'''
    new = old + '''    p.add_argument(\n        "--git-only",\n        action="store_true",\n        help="Accept Git-only authentication during preflight; requires a configured Git credential",\n    )\n'''
    if old not in s:
        raise RuntimeError("Could not patch start parser")
    s = s.replace(old, new, 1)

    old = '''        if args.command == "doctor":\n            result = run_doctor(offline=args.offline)\n            _print(result)\n            return 0 if bool(result.get("ok")) else 1\n'''
    new = '''        if args.command == "doctor":\n            result = run_doctor(offline=args.offline)\n            if args.git_only:\n                result = _git_only_preflight(result)\n            _print(result)\n            return 0 if bool(result.get("ok")) else 1\n'''
    if old not in s:
        raise RuntimeError("Could not patch doctor command")
    s = s.replace(old, new, 1)

    old = '''        elif args.command == "start":\n            preflight = run_doctor(offline=args.offline_doctor)\n            preflight_summary = {\n'''
    new = '''        elif args.command == "start":\n            preflight = run_doctor(offline=args.offline_doctor)\n            if args.git_only:\n                preflight = _git_only_preflight(preflight)\n            preflight_summary = {\n'''
    if old not in s:
        raise RuntimeError("Could not patch start preflight")
    s = s.replace(old, new, 1)
    path.write_text(s, encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_reasonfirst_v3.py /path/to/reasonFirst", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).expanduser().resolve()
    cli = root / "src/gitlab_agent/cli.py"
    if not cli.is_file():
        print(f"missing {cli}", file=sys.stderr)
        return 2
    changed = patch_cli(cli)
    print("patched" if changed else "already-patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
