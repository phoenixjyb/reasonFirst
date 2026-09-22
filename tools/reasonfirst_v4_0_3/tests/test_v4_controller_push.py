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
        root=pathlib.Path(td); bare=root/'origin.git'; repo=root/'repo'; home=root/'remote-home'; binp=root/'bin'; state=root/'state'
        home.mkdir(); binp.mkdir()
        run('git','init','--bare',str(bare)); run('git','clone',str(bare),str(repo))
        run('git','config','user.email','test@example.com',cwd=repo); run('git','config','user.name','Test',cwd=repo)
        (repo/'a.txt').write_text('one\n'); run('git','add','.',cwd=repo); run('git','commit','-m','init',cwd=repo); run('git','branch','-M','main',cwd=repo); run('git','push','-u','origin','main',cwd=repo)
        (binp/'ssh').symlink_to(ROOT/'tests'/'fake_ssh.py')
        old=dict(os.environ)
        os.environ['PATH']=str(binp)+os.pathsep+os.environ.get('PATH','')
        os.environ['RF_FAKE_REMOTE_HOME']=str(home); os.environ['RF_FAKE_CODEX']=str(ROOT/'tests'/'fake_codex.py')
        os.environ['CODEX_BRIDGE_CODEX_BIN']=str(ROOT/'tests'/'fake_codex.py'); os.environ['RF_CODEX_BRIDGE_STATE_DIR']=str(state)
        os.environ['GITLAB_BASE_URL']='https://gitlab.example.test'
        os.environ['GITLAB_TOKEN']='test-token'
        os.environ['GITLAB_GIT_TOKEN']='test-token'
        os.environ['GITLAB_ALLOWED_PROJECTS']='g/p'
        os.environ['GITLAB_WORKSPACE_ROOT']=str(root/'local-workspaces')
        os.environ['RF_ENABLE_EXPERIMENTAL_REMOTE_PUSH']='true'
        try:
            target=ExecutionTarget(type='ssh',name='fake',host='fake-host',repo=str(repo),codex_backend='desktop-proxy')
            mgr=RemoteWorkspaceManager(target)
            ws=mgr.create_workspace(project='g/p',base_ref='main',task='v4')
            rec={**ws,'task':'v4','goal':'g','target':target.to_dict(),'kind':'ssh','updated_at':int(time.time())}
            ctrl=BridgeController(); ctrl._remote_manager=lambda target: mgr
            ctrl._state['workspaces'][ws['workspace_id']]=rec; ctrl._save_state()
            started=ctrl.start_codex(workspace_id=ws['workspace_id'],goal='edit only')
            tid=started['thread_id']; session=ctrl._session(tid)
            mgr.write_file(rec,'a.txt','two\n')
            auth=ctrl.authorize_push(thread_id=tid,commit_message='test: controller approved push')
            assert auth['digest'] and auth['branch'].startswith('chatgpt/')
            assert auth['candidate_tree'] and auth['origin_url']
            msg={'method':'item/tool/call','params':{'threadId':tid,'namespace':'reasonfirst_remote','tool':'commit_push','arguments':{}}}
            response=ctrl._handle_dynamic_tool_request(str(session['app_key']),msg)
            assert response['success'] is True
            assert 'push_approval' not in ctrl._session(tid)
            assert ctrl._session(tid).get('last_push',{}).get('commit_sha')
            ctrl.close()
        finally:
            os.environ.clear(); os.environ.update(old)
    print('v4 controller review authorization -> Codex commit_push: OK')

if __name__=='__main__': main()
