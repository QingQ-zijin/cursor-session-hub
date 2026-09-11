"""Exercise a real HTTP model stream with a disposable cloud backend; no paid API."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hub import auth,db
from hub.config import Config


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target');args=parser.parse_args()
    prefix=[str(ROOT/'desktop/binaries'/('hub-core-'+args.target+('.exe' if sys.platform=='win32' else '')))] if args.target else [sys.executable,'-m','hub']
    captured=[];secret=secrets.token_urlsafe(24)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append((self.path,self.headers.get('Authorization')=='Bearer '+secret,value))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for part in ['已阅读历史😀，','回答完整结束。']:
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':part}}]},ensure_ascii=False)+'\n\n').encode());self.wfile.flush();time.sleep(.12)
            self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
    provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            home=Path(directory);cfg=Config(mode='cloud',home=home,worker_external=True)
            engine=db.get_engine(cfg);db.init_db(engine);password=secrets.token_urlsafe(24)
            uid=auth.bootstrap_admin(engine,'smoke-admin','Smoke',password)
            sid,rid=db.new_id(),db.new_id()
            with engine.begin() as conn:
                conn.execute(db.sessions.insert().values(id=sid,owner_id=uid,title='测试引用',current_revision=rid,status='ready',event_count=1))
                conn.execute(db.revisions.insert().values(id=rid,session_id=sid,state='ready',event_count=1))
                conn.execute(db.events.insert().values(id=db.new_id(),revision_id=rid,seq=1,round_number=1,kind='assistant',event_json={'kind':'assistant','blocks':[{'type':'text','text':'测试历史上下文 ORIGINAL-42'}]}))
            engine.dispose()
            env=dict(os.environ,CSH_HOME=str(home),CSH_DATABASE_URL=cfg.database_url,CSH_WORKER_EXTERNAL='1',CSH_COOKIE_SECURE='0',CSH_MIGRATE_LEGACY='0')
            with (home/'backend.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen(prefix+['serve','--mode','cloud','--host','127.0.0.1','--port','0'],cwd=ROOT,env=env,stdout=log,stderr=log)
                try:
                    port=None
                    for _ in range(900):
                        lines=(home/'backend.log').read_text(encoding='utf-8',errors='replace').splitlines()
                        for line in lines:
                            if line.startswith('CSH_READY '):port=json.loads(line[10:])['port']
                        if port:break
                        if process.poll() is not None:raise RuntimeError('Backend failed to start in smoke test')
                        time.sleep(.1)
                    assert port,'Backend readiness timeout'
                    with httpx.Client(base_url=f'http://127.0.0.1:{port}/api/v1',timeout=25) as client:
                        for _ in range(100):
                            try:
                                login=client.post('/auth/device-login',json={'username':'smoke-admin','password':password});login.raise_for_status();break
                            except httpx.ConnectError:time.sleep(.1)
                        client.headers['Authorization']='Bearer '+login.json()['token']
                        configured=client.patch('/ai/settings',json={'enabled':True,'base_url':f'http://127.0.0.1:{provider.server_port}/v1','model':'test-model','api_key':secret});configured.raise_for_status()
                        assert secret not in configured.text
                        preview=client.post('/ai/context',json={'references':[{'session_id':sid}]}).json()
                        request_id=db.new_id()
                        body={'text':'基于引用继续回答','references':[{'session_id':sid,'revision_id':rid}],'request_id':request_id,'settings_version':preview['settings_version']}
                        sent=client.post('/ai/messages',json=body);sent.raise_for_status();reply=sent.json()['assistant']['id']
                        offset=0;text=''
                        for _ in range(30):
                            response=client.get('/ai/replies/'+reply,params={'offset':offset});response.raise_for_status();item=response.json()
                            text+=item['text'];offset=item['next_offset']
                            if item['state'] not in ('running','queued') and not item['has_more']:break
                        assert item['state']=='complete' and text=='已阅读历史😀，回答完整结束。',item
                        assert client.post('/ai/messages',json=body).json()['assistant']['id']==reply
                        assert len(captured)==1 and captured[0][0]=='/v1/chat/completions' and captured[0][1]
                        assert 'ORIGINAL-42' in json.dumps(captured[0][2],ensure_ascii=False)
                        assert 'tools' not in captured[0][2]
                        history=client.get('/ai/threads/'+sent.json()['thread_id']).json()
                        assert len(history['items'])==2 and secret not in json.dumps(history)
                finally:
                    process.terminate()
                    try:process.wait(timeout=20)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
            assert len((home/'ai-master.key').read_bytes())==44
    finally:provider.shutdown();provider.server_close()
    output=ROOT/'.runtime'/('smoke-ai-'+(args.target or 'source')+'.json');output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'ok':True,'actual_http_stream':True,'context':True,'idempotent':True,'encrypted_key':True}),encoding='utf-8')
    print('API chat HTTP smoke PASS:',args.target or 'source')

if __name__=='__main__':main()
