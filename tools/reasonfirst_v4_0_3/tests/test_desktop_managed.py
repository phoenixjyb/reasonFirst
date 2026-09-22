from __future__ import annotations
import json, os, pathlib, tempfile, threading, sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from websockets.sync.server import unix_serve
from reasonfirst_codex_bridge.app_server import AppServerClient


def handler(ws):
    for raw in ws:
        msg=json.loads(raw)
        if 'id' not in msg:
            continue
        rid=msg['id']; method=msg.get('method')
        if method=='initialize': result={'userAgent':'fake-managed','codexHome':'/tmp/fake'}
        elif method=='configRequirements/read': result={'requirements':None}
        elif method=='thread/start': result={'thread':{'id':'thr_managed'}}
        elif method=='turn/start': result={'turn':{'id':'turn_managed','status':'inProgress'}}
        elif method in {'thread/name/set','thread/goal/set','thread/metadata/update'}: result={}
        elif method=='thread/read': result={'thread':{'id':'thr_managed'}}
        else: result={}
        ws.send(json.dumps({'id':rid,'result':result}))


def main():
    with tempfile.TemporaryDirectory(dir="/private/tmp") as td:
        home=pathlib.Path(td)/'.codex'; sock=home/'app-server-control'/'app-server-control.sock'
        sock.parent.mkdir(parents=True)
        old=os.environ.get('CODEX_HOME'); os.environ['CODEX_HOME']=str(home)
        server=unix_serve(handler,path=str(sock)); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
        try:
            app=AppServerClient.desktop_preferred(required=True)
            assert app.backend_name=='desktop-managed'
            tid=app.start_thread(cwd='/tmp')
            turn=app.start_turn(thread_id=tid,cwd='/tmp',prompt='test')
            assert tid=='thr_managed' and turn=='turn_managed'
            app.close()
        finally:
            server.shutdown()
            if old is None: os.environ.pop('CODEX_HOME',None)
            else: os.environ['CODEX_HOME']=old
    print('desktop-managed Unix socket flow: OK')

if __name__=='__main__': main()
