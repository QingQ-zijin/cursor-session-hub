"""Run the actual frozen backend and parser on a clean per-OS data directory."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
import psutil

ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--target',required=True);a=p.parse_args()
 ext='.exe' if sys.platform=='win32' else ''
 binary=ROOT/'desktop/binaries'/f'hub-core-{a.target}{ext}'
 out=ROOT/'.runtime';out.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
  home=Path(td);token=secrets.token_urlsafe(32)
  sample=home/'示例.jsonl';sample.write_text(json.dumps({'role':'user','message':{'content':[{'type':'text','text':'你好，请检查这个记录。'}]}},ensure_ascii=False)+'\n'+json.dumps({'role':'assistant','message':{'content':[{'type':'text','text':'已完成。\n\n```python\nprint(1)\n```'}]}},ensure_ascii=False)+'\n',encoding='utf-8')
  env=dict(os.environ,CSH_HOME=str(home),CSH_LOCAL_TOKEN=token,CSH_MIN_FREE_BYTES='1048576',CSH_MIN_FREE_RATIO='0',CSH_MIGRATE_LEGACY='0')
  log=(home/'stderr.log').open('w',encoding='utf-8')
  process=subprocess.Popen([str(binary),'serve','--mode','local','--host','127.0.0.1','--port','0'],env=env,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8')
  try:
   deadline=time.monotonic()+90;port=None
   while time.monotonic()<deadline:
    line=process.stdout.readline()
    if line.startswith('CSH_READY '):port=json.loads(line[10:])['port'];break
    if process.poll() is not None:raise RuntimeError('Frozen core exited: '+(home/'stderr.log').read_text(encoding='utf-8'))
   if not port:raise RuntimeError('Frozen core readiness timeout')
   base=f'http://127.0.0.1:{port}/api/v1'
   def call(path,body=None):
    request=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
   for _ in range(50):
    try:call('/capabilities');break
    except OSError:time.sleep(.1)
   imported=call('/imports/path',{'path':str(sample)})
   for _ in range(120):
    job=call('/jobs/'+imported['job']['id'])
    if job['state']=='succeeded':break
    if job['state'] in ('failed','cancelled'):raise RuntimeError(job.get('error'))
    time.sleep(.5)
   else:raise RuntimeError('Frozen parser timeout')
   session=call('/sessions/'+imported['session_id'])
   assert session['event_count']==2,session
   (out/f'smoke-core-{a.target}.json').write_text(json.dumps({'ok':True,'events':2,'target':a.target}),encoding='utf-8')
   print('Frozen core smoke PASS:',a.target)
  finally:
   try: owned = psutil.Process(process.pid).children(recursive=True) + [psutil.Process(process.pid)]
   except psutil.Error: owned = []
   if sys.platform=='win32':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   else:process.terminate()
   try:process.wait(timeout=15)
   except subprocess.TimeoutExpired:process.kill();process.wait()
   _, remaining = psutil.wait_procs(owned, timeout=10)
   for child in remaining:
    try: child.kill()
    except psutil.Error: pass
   psutil.wait_procs(remaining,timeout=5)
   log.close()
if __name__=='__main__':main()
