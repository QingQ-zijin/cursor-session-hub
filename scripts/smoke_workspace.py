"""Frozen, real background workspace batch with an isolated synthetic Cursor home."""
import argparse,json,os,sqlite3,subprocess,sys,tempfile,time,secrets
from pathlib import Path
import urllib.request
import psutil
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);args=parser.parse_args()
    binary=ROOT/'desktop/binaries'/('hub-core-'+args.target+('.exe' if sys.platform=='win32' else ''))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        root=Path(temporary);home=root/'user';home.mkdir();data=root/'library';token=secrets.token_urlsafe(32)
        app=home/'AppData/Roaming' if sys.platform=='win32' else home/'Library/Application Support' if sys.platform=='darwin' else home/'.config'
        database=app/'Cursor/User/globalStorage/state.vscdb';database.parent.mkdir(parents=True)
        project=str(home/'Research')
        with sqlite3.connect(database) as conn:
            conn.execute('CREATE TABLE ItemTable(key TEXT PRIMARY KEY,value BLOB)');conn.execute('CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)')
            conn.execute('CREATE TABLE composerHeaders(composerId TEXT PRIMARY KEY,isArchived INTEGER,isSubagent INTEGER,value BLOB)')
            conn.execute('INSERT INTO ItemTable VALUES(?,?)',('glass.localAgentProjects.v1',json.dumps([{'id':'p','name':'Research','workspace':{'uri':{'fsPath':project}}}])))
            conn.execute('INSERT INTO ItemTable VALUES(?,?)',('glass.localAgentProjectMembership.v1',json.dumps({'named':'p','unnamed':'p'})))
            for ident,title in [('named','Named Cursor session'),('unnamed','')]:conn.execute('INSERT INTO composerHeaders VALUES(?,?,?,?)',(ident,0,0,json.dumps({'name':title})))
        transcripts=home/'.cursor/projects/research/agent-transcripts';transcripts.mkdir(parents=True)
        for name in ['named','unnamed']:(transcripts/(name+'.jsonl')).write_text(json.dumps({'role':'user','message':{'content':[{'type':'text','text':'hello'}]}})+'\n'+json.dumps({'role':'assistant','message':{'content':[{'type':'text','text':'complete answer'}]}})+'\n',encoding='utf-8')
        env=dict(os.environ,HOME=str(home),USERPROFILE=str(home),APPDATA=str(app),XDG_CONFIG_HOME=str(app),CSH_HOME=str(data),CSH_LOCAL_TOKEN=token,CSH_WORKER_EXTERNAL='0',CSH_MIN_FREE_BYTES='1048576',CSH_MIN_FREE_RATIO='0',CSH_MIGRATE_LEGACY='0')
        env.pop('CSH_DATABASE_URL',None)
        with (root/'server.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([str(binary),'serve','--mode','local','--host','127.0.0.1','--port','0'],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                port=None
                for _ in range(900):
                    for line in (root/'server.log').read_text(encoding='utf-8',errors='replace').splitlines():
                        if line.startswith('CSH_READY '):port=json.loads(line[10:])['port']
                    if port:break
                    if process.poll() is not None:raise RuntimeError('Frozen core stopped')
                    time.sleep(.1)
                assert port
                base=f'http://127.0.0.1:{port}/api/v1'
                def call(path,body=None):
                    req=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
                    with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
                for scope,total in [(None,1),(project,2)]:
                    ident=call('/source-batches',{'project':scope,'destination':'local'})['id']
                    for _ in range(180):
                        batch=next(b for b in call('/source-batches')['items'] if b['id']==ident)
                        if batch['state'] in ('succeeded','failed','completed_with_errors'):break
                        time.sleep(.5)
                    assert batch['state']=='succeeded' and batch['total']==total,batch
                assert len(call('/sessions')['items'])==2
                assert call('/sources/sidebar-workspaces')['items'][0]['label']=='Research'
            finally:
                try:owned=psutil.Process(process.pid).children(recursive=True)+[psutil.Process(process.pid)]
                except psutil.Error:owned=[]
                for child in reversed(owned):
                    try:child.terminate()
                    except psutil.Error:pass
                _,alive=psutil.wait_procs(owned,timeout=10)
                for child in alive:
                    try:child.kill()
                    except psutil.Error:pass
                process.wait(timeout=15)
    path=ROOT/'.runtime'/('smoke-workspace-'+args.target+'.json');path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'ok':True,'all_named':1,'workspace_all_sessions':2,'background_queue':True}),encoding='utf-8')
    print('Frozen workspace batch smoke PASS:',args.target)
if __name__=='__main__':main()
