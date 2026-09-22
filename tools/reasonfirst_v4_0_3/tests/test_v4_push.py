from __future__ import annotations
import os, pathlib, subprocess, tempfile, sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.remote_workspace import RemoteWorkspaceManager, RemoteWorkspaceError


def run(*args, cwd=None, capture=False):
    return subprocess.run(list(args),cwd=cwd,check=True,text=True,capture_output=capture)


def main():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td); bare=root/'origin.git'; repo=root/'repo'; home=root/'remote-home'; binp=root/'bin'
        home.mkdir(); binp.mkdir()
        run('git','init','--bare',str(bare))
        run('git','clone',str(bare),str(repo))
        run('git','config','user.email','test@example.com',cwd=repo)
        run('git','config','user.name','Test',cwd=repo)
        (repo/'a.txt').write_text('one\n')
        run('git','add','.',cwd=repo); run('git','commit','-m','init',cwd=repo)
        run('git','branch','-M','main',cwd=repo); run('git','push','-u','origin','main',cwd=repo)
        ssh=binp/'ssh'; ssh.symlink_to(ROOT/'tests'/'fake_ssh.py')
        old=dict(os.environ)
        os.environ['PATH']=str(binp)+os.pathsep+os.environ.get('PATH','')
        os.environ['RF_FAKE_REMOTE_HOME']=str(home)
        os.environ['RF_FAKE_CODEX']=str(ROOT/'tests'/'fake_codex.py')
        try:
            target=ExecutionTarget(type='ssh',name='fake',host='fake-host',repo=str(repo),codex_backend='desktop-proxy')
            mgr=RemoteWorkspaceManager(target)
            ws=mgr.create_workspace(project='g/p',base_ref='main',task='push-test')
            mgr.write_file(ws,'a.txt','two\n')
            snap=mgr.snapshot(ws)
            assert snap['dirty'] and snap['branch'].startswith('chatgpt/')
            # Any post-review change invalidates approval.
            mgr.write_file(ws,'b.txt','later\n')
            try:
                mgr.commit_push(ws,expected_snapshot=snap,message='test: should reject stale approval')
                raise AssertionError('stale digest should fail')
            except RemoteWorkspaceError as exc:
                assert 'changed after ChatGPT push approval' in str(exc)
            # Review the new exact snapshot. Destination changes must also
            # invalidate approval even when source content is unchanged.
            snap2=mgr.snapshot(ws)
            original_origin=run('git','-C',str(ws['worktree_path']),'remote','get-url','origin',capture=True).stdout.strip()
            run('git','-C',str(ws['worktree_path']),'remote','set-url','origin',str(root/'other.git'))
            try:
                mgr.commit_push(ws,expected_snapshot=snap2,message='test: should reject destination change')
                raise AssertionError('destination change should fail')
            except RemoteWorkspaceError as exc:
                assert 'publication destination changed' in str(exc)
            run('git','-C',str(ws['worktree_path']),'remote','set-url','origin',original_origin)

            snap3=mgr.snapshot(ws)
            result=mgr.commit_push(ws,expected_snapshot=snap3,message='test: v4 reviewed push')
            assert result['ok'] and result['pushed']
            branch=result['branch']
            sha=run('git','--git-dir',str(bare),'rev-parse',f'refs/heads/{branch}',capture=True).stdout.strip()
            assert sha==result['commit_sha']
            assert mgr.status(ws)['dirty'] is False
        finally:
            os.environ.clear(); os.environ.update(old)
    print('v4 reviewed snapshot commit/push gate: OK')

if __name__=='__main__': main()
