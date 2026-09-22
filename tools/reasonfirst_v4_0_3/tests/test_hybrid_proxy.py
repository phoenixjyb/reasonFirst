from __future__ import annotations
import os, pathlib, subprocess, tempfile, sys, time

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from reasonfirst_codex_bridge.bridge_config import ExecutionTarget
from reasonfirst_codex_bridge.remote_workspace import RemoteWorkspaceManager
from reasonfirst_codex_bridge.controller import BridgeController


def run(*args, cwd=None):
    subprocess.run(list(args),cwd=cwd,check=True,stdout=subprocess.DEVNULL)


def main():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td)
        bare=root/'origin.git'; repo=root/'repo'; home=root/'remote-home'; binp=root/'bin'; state_dir=root/'state'
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
        old_env=dict(os.environ)
        os.environ['PATH']=str(binp)+os.pathsep+oldpath
        os.environ['RF_FAKE_REMOTE_HOME']=str(home)
        os.environ['RF_FAKE_CODEX']=str(ROOT/'tests'/'fake_codex.py')
        os.environ['CODEX_BRIDGE_CODEX_BIN']=str(ROOT/'tests'/'fake_codex.py')
        os.environ['RF_CODEX_BRIDGE_STATE_DIR']=str(state_dir)
        os.environ['GITLAB_BASE_URL']='https://gitlab.example.test'
        os.environ['GITLAB_TOKEN']='test-token'
        os.environ['GITLAB_GIT_TOKEN']='test-token'
        os.environ['GITLAB_ALLOWED_PROJECTS']='group/project'
        os.environ['GITLAB_WORKSPACE_ROOT']=str(root/'local-workspaces')

        try:
            proxy_target=ExecutionTarget(type='ssh',name='fake',host='fake-host',repo=str(repo),codex_backend='desktop-proxy')
            mgr=RemoteWorkspaceManager(proxy_target)
            ws=mgr.create_workspace(project='group/project',base_ref='main',task='hybrid')
            # Simulate a workspace created by v3.0.2, which stored remote-ssh even
            # though the remote host has no Codex binary. v3.0.3 must migrate the
            # execution backend without recreating the remote worktree/branch.
            legacy_target=ExecutionTarget(type='ssh',name='fake',host='fake-host',repo=str(repo),codex_backend='remote-ssh')
            rec={**ws,'task':'hybrid','goal':'test hybrid','target':legacy_target.to_dict(),'kind':'ssh','updated_at':int(time.time())}
            ctrl=BridgeController()
            class ManagerProxy:
                def __getattr__(self, name):
                    return getattr(mgr, name)
                def probe(self):
                    data=mgr.probe()
                    data['stdout']='host=fake\nuser=test\nrepo='+str(repo)+'\ncodex='
                    return data
            ctrl._remote_manager=lambda target: ManagerProxy()
            ctrl._state['workspaces'][ws['workspace_id']]=rec
            ctrl._save_state()
            started=ctrl.start_codex(workspace_id=ws['workspace_id'],goal='implement reviewed plan')
            assert started['execution_migrated'] is True, started
            assert started['execution']['codex_backend']=='desktop-proxy', started
            assert started['worktree_path']==ws['worktree_path'], started
            assert started['remote_tools'] is True, started
            assert started['codex_backend'] in {'standalone-local','desktop-bundled','desktop-managed','global-config-local'}, started
            assert pathlib.Path(started['codex_cwd']).is_dir()
            deadline=time.monotonic()+10
            while True:
                ev=ctrl.events(thread_id=started['thread_id'],limit=30)
                if any(item.get('method')=='turn/completed' for item in ev['events']) or time.monotonic()>=deadline:
                    break
                time.sleep(0.1)
            assert any(item.get('method')=='turn/completed' for item in ev['events']), ev
            assert 'dynamic:' in ev['last_agent_message'], ev
            status=mgr.status(rec); assert status['dirty'] is False
            ctrl.close()
        finally:
            os.environ.clear(); os.environ.update(old_env)
    print('hybrid local Codex + remote SSH dynamic tools: OK')

if __name__=='__main__': main()
