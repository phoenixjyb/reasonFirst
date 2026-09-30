#!/usr/bin/env python3
"""Run the pinned upstream command parser and real child exec, without a tunnel.

This is a parser/OS process-boundary test, not live provider authorization.
The Go function is extracted byte-for-byte from a verified upstream Git blob;
it is not a Python imitation of the receiving parser. Go is CI-only tooling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from gitlab_agent.setup_tunnel import _command_string

UPSTREAM_COMMIT = "a390c168ff1b2d14e73a95991c186c6aba3ff5a0"  # tunnel-client v0.0.15
CONFIG_BLOB = "a724464b28ed9684da6c8f6573474afd82c0e559"


def run(argv, *, cwd=None, data=None):
    proc = subprocess.run(argv, cwd=cwd, input=data, capture_output=True, timeout=180)
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace")[-4000:])
    return proc.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    head = run(["git", "rev-parse", "HEAD"], cwd=args.upstream).decode().strip()
    if head != UPSTREAM_COMMIT:
        raise RuntimeError("Upstream parser checkout is not the pinned commit")
    blob = run(["git", "show", "HEAD:pkg/runtimeconfig/config.go"], cwd=args.upstream)
    if hashlib.sha1(b"blob " + str(len(blob)).encode() + b"\0" + blob).hexdigest() != CONFIG_BLOB:
        raise RuntimeError("Upstream config blob differs from the reviewed source")
    start = blob.index(b"func parseCommandArgv(")
    end = blob.index(b"\n}", start) + 2
    function = blob[start:end].decode("utf-8")

    with tempfile.TemporaryDirectory(prefix="reasonfirst-native-boundary-") as td:
        root = Path(td)
        suffix = ".exe" if os.name == "nt" else ""
        probe_source = root / "probe.go"
        probe_source.write_text('package main\nimport "fmt"\nfunc main() { fmt.Print("probe-ok") }\n', encoding="utf-8")
        probe = root / ("probe" + suffix)
        run(["go", "build", "-o", str(probe), str(probe_source)], cwd=root)

        cases = []
        for executable in (
            r"C:\Users\example\.local\bin\reasonfirst-gitlab-mcp.exe",
            r"C:\Program Files\ReasonFirst\reasonfirst-bridge-mcp.exe",
            "C:\\Users\\O'Brien 测试 & $name\\reasonfirst-gitlab-mcp.exe",
            r"\\server\share\tools\reasonfirst-bridge-mcp.exe",
            '/tmp/space/quote\'and"double/worker',
        ):
            cases.append({"Command": _command_string(executable), "Want": executable})
        # Exercise actual exec.Command under each native OS, including both MCP
        # names and legal quoting-sensitive/Unicode directory characters.
        for dirname in ("plain", "Space O'Brien 测试 & $name"):
            directory = root / dirname
            directory.mkdir()
            for name in ("reasonfirst-gitlab-mcp", "reasonfirst-bridge-mcp"):
                target = directory / (name + suffix)
                target.write_bytes(probe.read_bytes())
                target.chmod(0o700)
                cases.append({"Command": _command_string(str(target)), "Want": str(target), "Launch": True})

        # Demonstrate the pre-fix serializer fails the real receiving parser.
        old_path = r"C:\Users\example\.local\bin\reasonfirst-gitlab-mcp.exe"
        cases.append({"Command": subprocess.list2cmdline([old_path]), "Want": old_path, "MustDiffer": True})
        harness = root / "harness.go"
        harness.write_text('''package main
import ("encoding/json"; "errors"; "fmt"; "os"; "os/exec"; "strings")
''' + function + '''
type Case struct { Command, Want string; Launch, MustDiffer bool }
func main() {
    var cases []Case
    if err := json.NewDecoder(os.Stdin).Decode(&cases); err != nil { panic(err) }
    for i, c := range cases {
        args, err := parseCommandArgv(c.Command)
        equal := err == nil && len(args) == 1 && args[0] == c.Want
        if c.MustDiffer { if equal { panic("old serializer unexpectedly preserved path") }; continue }
        if !equal { panic(fmt.Sprintf("case %d: argv=%q error=%v want=%q", i, args, err, c.Want)) }
        if c.Launch {
            output, err := exec.Command(args[0], args[1:]...).CombinedOutput()
            if err != nil || string(output) != "probe-ok" { panic(fmt.Sprintf("case %d: launch %v %q", i, err, output)) }
        }
    }
    fmt.Printf("Actual upstream parser + native exec: OK (%d cases)\\n", len(cases))
}
''', encoding="utf-8")
        harness_bin = root / ("harness" + suffix)
        run(["go", "build", "-o", str(harness_bin), str(harness)], cwd=root)
        print(run([str(harness_bin)], data=json.dumps(cases, ensure_ascii=False).encode("utf-8")).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
