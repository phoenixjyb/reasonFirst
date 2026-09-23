#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, sys
from pathlib import Path

args=sys.argv[1:]
i=0
while i < len(args):
    if args[i] == '--':
        i += 1; break
    if args[i] in {'-T'}:
        i += 1; continue
    if args[i] == '-o':
        i += 2; continue
    i += 1
if i >= len(args):
    raise SystemExit(2)
host=args[i]; i+=1
cmd=args[i:]
env=os.environ.copy()
env['HOME']=os.environ['RF_FAKE_REMOTE_HOME']
remote = ' '.join(cmd)
if 'app-server' in remote and 'codex' in remote:
    os.execve(sys.executable, [sys.executable, os.environ['RF_FAKE_CODEX']], env)
if remote == 'sh -s':
    script=sys.stdin.read()
    proc=subprocess.run(['sh','-s'],input=script,text=True,env=env)
    raise SystemExit(proc.returncode)
if len(cmd)>=3 and cmd[0]=='sh' and cmd[1]=='-lc':
    proc=subprocess.run(['sh','-lc',cmd[2]],env=env)
    raise SystemExit(proc.returncode)
print('unsupported fake ssh command',cmd,file=sys.stderr)
raise SystemExit(3)
