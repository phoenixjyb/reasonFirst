from __future__ import annotations
import os, pathlib, subprocess, tempfile, sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.remote_workspace import RemoteWorkspaceManager
from reasonfirst_codex_bridge.app_server import AppServerClient
from gitlab_agent.worker_policy import default_worker_policy


def run(*args, cwd=None):
    subprocess.run(list(args),cwd=cwd,check=True,stdout=subprocess.DEVNULL)


def main():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td)
        bare=root/'origin.git'; repo=root/'repo'; home=root/'remote-home'; binp=root/'bin'
        home.mkdir(); binp.mkdir()
        run('git','init','--bare',str(bare))
        run('git','clone',str(bare),str(repo))
        run('git','config','user.email','test@example.com',cwd=repo)
        run('git','config','user.name','Test',cwd=repo)
        (repo/'src').mkdir(); (repo/'src'/'a.py').write_text('print("one")\n')
        run('git','add','.',cwd=repo); run('git','commit','-m','init',cwd=repo)
        run('git','branch','-M','main',cwd=repo); run('git','push','-u','origin','main',cwd=repo)

        ssh=binp/'ssh'; ssh.symlink_to(ROOT/'tests'/'fake_ssh.py')
        oldpath=os.environ.get('PATH','')
        os.environ['PATH']=str(binp)+os.pathsep+oldpath
        os.environ['RF_FAKE_REMOTE_HOME']=str(home)
        os.environ['RF_FAKE_CODEX']=str(ROOT/'tests'/'fake_codex.py')
        target=ExecutionTarget(type='ssh',name='fake',host='fake-host',repo=str(repo),codex_backend='desktop-proxy')
        mgr=RemoteWorkspaceManager(target)
        probe=mgr.probe(); assert probe['ok'],probe
        state=mgr.create_workspace(project='group/project',base_ref='main',task='remote-test')
        assert pathlib.Path(state['worktree_path']).is_dir()
        listing=mgr.list_files(state,'src'); assert any(x['path']=='src/a.py' for x in listing['items'])
        read=mgr.read_file(state,'src/a.py'); assert 'one' in read['content']
        write=mgr.write_file(state,'src/a.py','print("two")\n'); assert write['bytes']>0
        patch='''diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-print("two")\n+print("three")\n'''
        applied=mgr.apply_patch(state,patch); assert applied['ok'],applied
        ran=mgr.run_command(state,"python3 src/a.py",timeout_seconds=30); assert ran['returncode']==0 and 'three' in ran['stdout']
        reports=pathlib.Path(state['worktree_path'],'reports'); reports.mkdir()
        (reports/'metrics.json').write_text('{"latency_ms":12.5}')
        diff=mgr.diff(state); assert '+print("three")' in diff['diff']
        candidates=mgr.artifact_candidates(state,'reports',since_epoch=0,max_entries=10)
        assert any(x['path']=='reports/metrics.json' for x in candidates)
        blob=mgr.read_bytes_b64(state,'reports/metrics.json')
        assert blob['size']>0 and blob['base64']

        app=AppServerClient.remote_ssh('fake-host',event_handler=lambda e: None)
        policy=default_worker_policy("codex")
        tid=app.start_thread(cwd=state['worktree_path'],policy=policy)
        turn=app.start_turn(thread_id=tid,cwd=state['worktree_path'],prompt='remote test',policy=policy,network_access=False)
        assert tid.startswith('thr_') and turn.startswith('turn_')
        app.close()
        os.environ['PATH']=oldpath
    print('remote workspace + remote codex app-server flow: OK')

if __name__=='__main__': main()
