"""Real HTTP concurrency acceptance against the local Compose deployment.

Five members read five distinct long indexed sessions while two members upload
new packages. Only synthetic sessions and ignored .runtime credentials are used.
Preparation is excluded from HTTP latency measurements.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import httpx
from sqlalchemy import select
from hub import db
from hub.config import Config
from hub.ingest import ingest_path
from hub.bundles import build_bundle

def checked(response):
    if response.status_code!=200:
        try:detail=response.json().get('detail','')
        except ValueError:detail=response.text[:300]
        raise AssertionError(f'{response.request.method} {response.request.url.path}: HTTP {response.status_code}: {detail}')
    return response.json()

def wait_job(client,ident,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        row=checked(client.get('/api/v1/jobs/'+ident))
        if row['state']=='succeeded':return row
        if row['state'] in ('failed','cancelled','paused'):raise AssertionError(f"Cloud job {row['state']}: {row.get('error')}")
        time.sleep(.25)
    raise AssertionError('Cloud ingestion exceeded acceptance timeout')

def package(config,engine,folder,number,large=False):
    source=folder/f'{number}.jsonl'
    ident,job=db.new_id(),db.new_id()
    with source.open('w',encoding='utf-8',newline='\n') as output:
        for seq in range(1000):
            text=f'并发验收 {number} 记录 {seq}：所有文字按轮次建立持久化索引，阅读请求只读取分页结果。'
            if large and seq%2:
                # One long word yields bounded token postings, while ZIP cannot
                # collapse the entire upload to a few hundred bytes.
                text+='\n'+secrets.token_hex(12288)
            event={'kind':'user' if seq%2==0 else 'assistant','blocks':[{'type':'text','text':text}]}
            output.write(json.dumps(event,ensure_ascii=False)+'\n')
    with engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=ident,owner_id='local',title=f'并发验收 {number} · 500 轮对话',source_kind='cursor_jsonl',native_id=ident))
        conn.execute(db.jobs.insert().values(id=job,owner_id='local',kind='ingest',state='queued',session_id=ident,payload_json={'path':str(source),'source_kind':'cursor_jsonl'}))
    ingest_path(config,job)
    with engine.connect() as conn:
        session=conn.execute(select(db.sessions).where(db.sessions.c.id==ident)).mappings().one()
        assert session['event_count']==1000 and session['round_count']==500
    path=folder/f'{number}.csh'
    build_bundle(config,ident,session['current_revision'],path)
    return {'local_id':ident,'path':path,'events':1000,'rounds':500}

def upload(client,pkg,device,throttled=False,start=None,finish=None):
    path=pkg['path']
    with path.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    state=checked(client.post('/api/v1/uploads',json={'filename':path.name,'total_bytes':path.stat().st_size,'sha256':digest,'session_key':pkg['local_id'],'device_id':device}))
    if start:start()
    size=state['chunk_size'];received=set(state['received_chunks'])
    with path.open('rb') as source:
        number=0
        while chunk:=source.read(size):
            if number not in received:
                def segments(block=chunk):
                    for offset in range(0,len(block),256*1024):
                        yield block[offset:offset+256*1024]
                        if throttled:time.sleep(.25)
                response=client.put('/api/v1/uploads/'+state['id']+'/chunks/'+str(number),content=segments() if throttled else chunk,headers={'Content-Type':'application/octet-stream','X-Chunk-SHA256':hashlib.sha256(chunk).hexdigest()})
                checked(response)
            number+=1
    result=checked(client.post('/api/v1/uploads/'+state['id']+'/complete',json={}))
    if finish:finish()
    wait_job(client,result['job']['id'])
    return result['session_id']

MONITOR_CODE=r'''
import json,sys,time,select
import psutil
from sqlalchemy import select as sqlselect,func
from hub import db
from hub.config import Config
engine=db.get_engine(Config())
deadline=time.monotonic()+300
while time.monotonic()<deadline:
    if select.select([sys.stdin],[],[],0)[0]:
        sys.stdin.readline()
        break
    with engine.connect() as conn:
        running=conn.execute(sqlselect(func.count()).select_from(db.jobs).where(db.jobs.c.state=='running')).scalar_one()
    parser=0;rss=0
    for process in psutil.process_iter(['cmdline','memory_info']):
        cmd=process.info.get('cmdline') or []
        if '--worker-job' in cmd:parser+=1
        if any(x.endswith('python') or x.endswith('python3') for x in cmd[:1]):rss+=process.info['memory_info'].rss
    print(json.dumps({'at':time.time(),'running':running,'parser_children':parser,'worker_namespace_python_rss':rss}),flush=True)
    time.sleep(.2)
engine.dispose()
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8088')
    parser.add_argument('--seconds',type=int,default=20)
    args=parser.parse_args()
    folder=ROOT/'.runtime/concurrency';folder.mkdir(parents=True,exist_ok=True)
    credential_file=folder/'credentials.json'
    metrics={'ok':False,'started_at':time.time(),'server_url':args.url,'readers':5,'simultaneous_uploaders':2,'events_per_baseline':1000,'rounds_per_baseline':500}
    monitor=None;stop=threading.Event();all_clients=[]
    try:
        initial=json.loads((ROOT/'.runtime/cloud-e2e/credentials.json').read_text(encoding='utf-8'))
        health=httpx.get(args.url+'/health',timeout=3);assert health.status_code==200
        admin=httpx.Client(base_url=args.url,headers={'Origin':args.url},timeout=30);all_clients.append(admin)
        checked(admin.post('/api/v1/auth/login',json=initial['admin']))
        if credential_file.exists():accounts=json.loads(credential_file.read_text(encoding='utf-8'))
        else:accounts=[initial['alice'],initial['bob']]
        while len(accounts)<5:
            invite=checked(admin.post('/api/v1/invites',json={}))
            account={'username':'load_reader_'+secrets.token_hex(4),'password':secrets.token_urlsafe(24),'display_name':f'并发验收成员 {len(accounts)+1}'}
            with httpx.Client(base_url=args.url,headers={'Origin':args.url},timeout=30) as registration:
                member=checked(registration.post('/api/v1/auth/register',json={'token':invite['token'],**account}))['user']
            account['id']=member['id'];accounts.append(account)
            credential_file.write_text(json.dumps(accounts,ensure_ascii=False,indent=2),encoding='utf-8')
        tokens=[]
        for account in accounts[:5]:
            with httpx.Client(base_url=args.url,timeout=30) as login:
                result=checked(login.post('/api/v1/auth/device-login',json={k:account[k] for k in ('username','password')}))
            tokens.append(result['token'])
        config=Config(mode='local',home=folder/'local',worker_external=True,min_free_bytes=0,min_free_ratio=0)
        engine=db.get_engine(config);db.init_db(engine)
        baseline=[]
        for number in range(5):
            pkg=package(config,engine,folder,'baseline-'+str(number))
            with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+tokens[number]},timeout=60) as client:
                cloud_id=upload(client,pkg,'concurrency-'+str(number))
            baseline.append(cloud_id)
            print(json.dumps({'prepared_baseline':number+1,'events':1000,'rounds':500}),flush=True)
        incoming=[package(config,engine,folder,'incoming-'+str(n),large=True) for n in range(2)]
        metrics['incoming_bytes']=[x['path'].stat().st_size for x in incoming]
        metrics['baseline_session_ids']=baseline
        print(json.dumps({'preparation_complete':True,'incoming_bytes':metrics['incoming_bytes']}),flush=True)
        # Worker observes PostgreSQL with one count query, avoiding state races
        # from checking each owner's job through separate HTTP requests.
        samples=[];monitor_errors=[]
        monitor=subprocess.Popen(['docker','compose','exec','-T','worker','python','-u','-c',MONITOR_CODE],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def monitor_reader():
            for line in monitor.stdout:
                try:samples.append(json.loads(line))
                except ValueError:monitor_errors.append('Monitor emitted an invalid metric')
        watch=threading.Thread(target=monitor_reader,daemon=True);watch.start()
        latencies=[];response_sizes=[];errors=[];reader_counts=[0]*5;overlap_samples=0
        upload_lock=threading.Lock();active_uploads=0;max_uploads=0
        def upload_started():
            nonlocal active_uploads,max_uploads
            with upload_lock:active_uploads+=1;max_uploads=max(max_uploads,active_uploads)
        def upload_finished():
            nonlocal active_uploads
            with upload_lock:active_uploads-=1
        barrier=threading.Barrier(7)
        measured_start=time.perf_counter()
        def reader(index):
            nonlocal overlap_samples
            rng=random.Random(index)
            with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+tokens[index]},timeout=15) as client:
                barrier.wait()
                while not stop.is_set():
                    n=reader_counts[index]
                    if n%10==0:
                        path='/api/v1/sessions';params={'owner_id':accounts[index]['id'],'limit':50};label='catalog'
                    elif n%5==0:
                        path='/api/v1/sessions/'+baseline[index]+'/rounds';params={'limit':100,'cursor':rng.randrange(0,399)};label='rounds'
                    else:
                        path='/api/v1/sessions/'+baseline[index]+'/events';params={'limit':40,'cursor':rng.randrange(0,959)};label='events'
                    tick=time.perf_counter()
                    try:
                        response=client.get(path,params=params)
                        elapsed=time.perf_counter()-tick
                        result=checked(response)
                        assert len(response.content)<=1024**2
                        if label=='events':assert len(result['items'])<=40 and result['items']
                        if label=='rounds':assert len(result['items'])<=100 and result['items']
                        with upload_lock:
                            overlap=active_uploads==2
                        latencies.append((label,elapsed));response_sizes.append(len(response.content));reader_counts[index]+=1
                        if overlap:overlap_samples+=1
                    except Exception as error:
                        errors.append(str(error));stop.set();break
                    time.sleep(.08)
        def sender(index):
            with httpx.Client(base_url=args.url,headers={'Authorization':'Bearer '+tokens[index]},timeout=60) as client:
                barrier.wait()
                return upload(client,incoming[index],'concurrency-incoming-'+str(index),True,upload_started,upload_finished)
        with ThreadPoolExecutor(max_workers=7) as pool:
            readers=[pool.submit(reader,n) for n in range(5)]
            writers=[pool.submit(sender,n) for n in range(2)]
            try:
                for future in writers:future.result(timeout=180)
                until=measured_start+max(args.seconds,15)
                while time.perf_counter()<until and not stop.is_set():time.sleep(.2)
            finally:
                stop.set()
            for future in readers:future.result(timeout=20)
        measured_seconds=time.perf_counter()-measured_start
        if monitor.stdin:
            monitor.stdin.write('\n');monitor.stdin.flush();monitor.stdin.close()
        try:monitor.wait(timeout=5)
        except subprocess.TimeoutExpired:monitor.terminate();monitor.wait(timeout=5)
        watch.join(timeout=2)
        if monitor.stderr:
            stderr=monitor.stderr.read()
            if stderr:monitor_errors.append('Monitor stderr: '+stderr[:500])
        assert not errors,errors[:3]
        assert samples and not monitor_errors,monitor_errors
        assert all(reader_counts),reader_counts
        ordered=sorted(value for _,value in latencies)
        p95=ordered[max(0,math.ceil(len(ordered)*.95)-1)]
        by_endpoint={}
        for label in ('events','rounds','catalog'):
            values=sorted(value for name,value in latencies if name==label)
            by_endpoint[label]={'requests':len(values),'p95_ms':round(values[max(0,math.ceil(len(values)*.95)-1)]*1000,3),'max_ms':round(max(values)*1000,3)}
        metrics.update(measured_seconds=round(measured_seconds,3),http_requests=len(latencies),per_reader_requests=reader_counts,p95_ms=round(p95*1000,3),max_response_bytes=max(response_sizes),by_endpoint=by_endpoint,peak_active_uploads=max_uploads,requests_during_two_uploads=overlap_samples,monitor_samples=len(samples),peak_running_jobs=max(s['running'] for s in samples),peak_parser_children=max(s['parser_children'] for s in samples),peak_worker_namespace_python_rss_bytes=max(s['worker_namespace_python_rss'] for s in samples))
        assert max_uploads==2 and overlap_samples>=5,'Two uploads did not overlap representative reader requests'
        assert metrics['peak_running_jobs']==1 and metrics['peak_parser_children']==1,'Expected exactly one active parsing worker'
        assert p95<1,f'HTTP P95 {p95:.3f}s exceeds 1s target'
        metrics['ok']=True
        engine.dispose()
    except Exception as error:
        metrics['error']=str(error)
        raise
    finally:
        stop.set()
        if monitor and monitor.poll() is None:
            try:monitor.stdin.write('\n');monitor.stdin.flush()
            except Exception:pass
            try:monitor.wait(timeout=5)
            except subprocess.TimeoutExpired:monitor.terminate()
        for client in all_clients:client.close()
        metrics['finished_at']=time.time();metrics['total_seconds']=round(metrics['finished_at']-metrics['started_at'],3)
        (folder/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(metrics,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
