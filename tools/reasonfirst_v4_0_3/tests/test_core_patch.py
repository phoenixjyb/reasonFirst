from __future__ import annotations
import pathlib, tempfile, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from patch_reasonfirst_v3 import patch_cli

FIX='''from __future__ import annotations\n\ndef _build_parser(prog: str = "gitlab-agent") -> argparse.ArgumentParser:\n    p.add_argument(\n        "--offline",\n        action="store_true",\n        help="Skip live GitLab API connectivity/authentication check",\n    )\n    p.add_argument(\n        "--offline-doctor",\n        action="store_true",\n        help="Skip the live GitLab API check in the preflight doctor",\n    )\n\ndef main():\n    try:\n        if args.command == "doctor":\n            result = run_doctor(offline=args.offline)\n            _print(result)\n            return 0 if bool(result.get("ok")) else 1\n        elif args.command == "start":\n            preflight = run_doctor(offline=args.offline_doctor)\n            preflight_summary = {\n                "ok": preflight.get("ok"),\n            }\n'''

def main():
    with tempfile.TemporaryDirectory() as td:
        p=pathlib.Path(td)/'cli.py'; p.write_text(FIX)
        assert patch_cli(p) is True
        text=p.read_text()
        assert 'def _git_only_preflight(' in text
        assert text.count('"--git-only"') == 2
        assert 'preflight = _git_only_preflight(preflight)' in text
        assert patch_cli(p) is False
    print('ReasonFirst core git-only patch: OK')

if __name__=='__main__': main()
