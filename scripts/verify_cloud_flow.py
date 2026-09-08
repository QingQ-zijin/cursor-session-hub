"""Exercise the real Compose PostgreSQL/API/worker with synthetic team records.

Run from the project root with .venv Python after `docker compose up -d`.
Private demo credentials and results are written only under ignored .runtime/.
The OS vault is replaced in this one test process; actual cloud HTTP is not mocked.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import httpx
from fastapi.testclient import TestClient
from PIL import Image,ImageDraw
from sqlalchemy import select,update
from hub.api import create_app
from hub.config import Config
from hub import db,remote
from hub.ingest import ingest_path
from hub.worker import execute_job

def checked(response,status=200):
    if response.status_code!=status:
        try: detail=response.json().get('detail','')
        except ValueError: detail=response.text[:400]
        raise AssertionError(f'{response.request.method} {response.request.url.path}: HTTP {response.status_code}, expected {status}: {detail}')
    if status==204:return None
    return response.json()

def wait_job(client,job_id,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=checked(client.get('/api/v1/jobs/'+job_id))
        if job['state']=='succeeded':return job
        if job['state'] in ('failed','cancelled','paused'):
            raise AssertionError(f"Cloud job {job_id} {job['state']}: {job.get('error')}")
        time.sleep(.3)
    raise AssertionError('Cloud job did not finish within the acceptance timeout')

def find_image(value):
    if isinstance(value,dict):
        if value.get('type')=='image' and value.get('asset_id'):return value['asset_id']
        for child in value.values():
            found=find_image(child)
            if found:return found
    elif isinstance(value,list):
        for child in value:
            found=find_image(child)
            if found:return found
    return None

def synthesize(path):
    bitmap=Image.new('RGB',(420,160),'#eef3ff')
    draw=ImageDraw.Draw(bitmap)
    draw.rounded_rectangle((22,28,398,132),radius=12,fill='#1559d6')
    draw.text((42,58),'CURSOR SESSION HUB  /  TEAM TEST',fill='white')
    output=io.BytesIO();bitmap.save(output,format='PNG')
    png=output.getvalue();image='data:image/png;base64,'+base64.b64encode(png).decode()
    records=[]
    for number in range(1,7):
        prompt=f'第 {number} 轮：请整理模型实验的关键决策和下一步工作。'
        answer=f'第 {number} 轮结论：已经验证分页索引可保留完整文字；下一步由同事核对图片和测试结果。'
        blocks=[{'type':'text','text':prompt}]
        if number==1:blocks.append({'type':'image','data_uri':image})
        records.append({'role':'user','message':{'content':blocks}})
        records.append({'role':'assistant','message':{'content':[{'type':'text','text':answer}]}})
    # A large text block verifies indexed content references survive synchronization.
    records.append({'role':'assistant','message':{'content':[{'type':'text','text':'完整实验记录：'+'结论完整保留，按需分页读取。'*5000}]}})
    with path.open('w',encoding='utf-8',newline='\n') as handle:
        for record in records:handle.write(json.dumps(record,ensure_ascii=False)+'\n')
    return png,len(records)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8088')
    parser.add_argument('--origin',default=None,help='Browser origin when the physical localhost route differs')
    parser.add_argument('--output',default=str(ROOT/'.runtime/cloud-e2e'))
    parser.add_argument('--health-wait',type=int,default=10)
    args=parser.parse_args()
    origin=args.origin or args.url
    out=Path(args.output).resolve()
    if not out.is_relative_to(ROOT/'.runtime'):
        raise SystemExit('Acceptance output must stay within the ignored .runtime directory')
    out.mkdir(parents=True,exist_ok=True)
    metrics={'ok':False,'server_url':args.url,'started_at':time.time(),'steps':[]}
    private=out/'credentials.json'
    if private.exists():credentials=json.loads(private.read_text(encoding='utf-8'))
    else:
        credentials={'url':args.url,'admin':{'username':'hub_demo_admin','password':secrets.token_urlsafe(24)}}
        private.write_text(json.dumps(credentials,indent=2),encoding='utf-8')
    @contextmanager
    def step(name):
        started=time.perf_counter();metrics['current_step']=name
        yield
        metrics['steps'].append({'name':name,'passed':True,'seconds':round(time.perf_counter()-started,3)})
        print(json.dumps({'step':name,'passed':True}),flush=True)
    try:
        with step('docker_postgresql_health'):
            deadline=time.monotonic()+args.health_wait
            while True:
                try:
                    response=httpx.get(args.url+'/health',timeout=2)
                    assert response.status_code==200 and response.json()['mode']=='cloud'
                    break
                except (httpx.HTTPError,AssertionError):
                    if time.monotonic()>=deadline:raise AssertionError('Compose HTTP health is not ready')
                    time.sleep(1)
            details=subprocess.run(['docker','compose','ps','--format','json'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',check=True)
            metrics['compose_running']='api' in details.stdout and 'worker' in details.stdout and 'db' in details.stdout
            assert metrics['compose_running']
        with httpx.Client(base_url=args.url,timeout=30,headers={'Origin':origin}) as admin:
            with step('initialize_admin_without_logging_secret'):
                response=admin.post('/api/v1/auth/login',json=credentials['admin'])
                if response.status_code!=200:
                    env=dict(os.environ,CSH_BOOTSTRAP_SECRET=credentials['admin']['password'])
                    command=['docker','compose','exec','-T','-e','CSH_BOOTSTRAP_SECRET','api','python','-m','hub','bootstrap-admin','--username',credentials['admin']['username'],'--display-name','演示管理员','--password-env','CSH_BOOTSTRAP_SECRET']
                    created=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
                    if created.returncode:
                        raise AssertionError('Admin initialization failed; an existing administrator may already have been initialized')
                    response=admin.post('/api/v1/auth/login',json=credentials['admin'])
                checked(response)
                assert 'httponly' in response.headers.get('set-cookie','').lower()
            with step('invite_register_two_members'):
                stamp=secrets.token_hex(3)
                for key,display in [('alice','林曦 · 实验负责人'),('bob','陈宇 · 协作成员')]:
                    if credentials.get(key,{}).get('id'):
                        known=checked(admin.get('/api/v1/members'))['items']
                        if any(m['id']==credentials[key]['id'] for m in known):
                            checked(admin.patch('/api/v1/members/'+credentials[key]['id'],json={'active':True}))
                            metrics['reused_previously_invited_members']=True
                            continue
                    invite=checked(admin.post('/api/v1/invites',json={}))
                    values={'username':'demo_'+key+'_'+stamp,'password':secrets.token_urlsafe(24),'display_name':display}
                    # Keep member cookies separate from the administrator.
                    response=httpx.post(args.url+'/api/v1/auth/register',json={'token':invite['token'],**values},headers={'Origin':origin},timeout=30)
                    account=checked(response)['user'];values['id']=account['id']
                    credentials[key]=values
                    used=httpx.post(args.url+'/api/v1/auth/register',json={'token':invite['token'],**values},headers={'Origin':origin})
                    assert used.status_code==410
                credentials['url']=args.url
                credentials['browser_url']=origin
                private.write_text(json.dumps(credentials,ensure_ascii=False,indent=2),encoding='utf-8')
            cfg=Config(mode='local',home=out/'local',local_token=secrets.token_urlsafe(32),worker_external=True,min_free_bytes=0,min_free_ratio=0)
            app=create_app(cfg)
            local_headers={'Authorization':'Bearer '+cfg.local_token}
            vault={}
            with patch.object(remote.keyring,'get_password',side_effect=lambda service,key:vault.get((service,key))),patch.object(remote.keyring,'set_password',side_effect=lambda service,key,value:vault.__setitem__((service,key),value)),patch.object(remote.keyring,'delete_password',side_effect=lambda service,key:vault.pop((service,key),None)),TestClient(app) as local,httpx.Client(base_url=args.url,timeout=30,headers={'Origin':origin}) as bob:
                with step('local_offline_import_preserves_text_and_image'):
                    source=out/'synthetic-team-session.jsonl'
                    png,event_count=synthesize(source)
                    imported=checked(local.post('/api/v1/imports/path',headers=local_headers,json={'path':str(source)}))
                    local_id=imported['session_id']
                    if imported['job']['state']!='succeeded':ingest_path(cfg,imported['job']['id'])
                    session=checked(local.get('/api/v1/sessions/'+local_id,headers=local_headers))
                    assert session['event_count']==event_count
                    checked(local.patch('/api/v1/sessions/'+local_id,headers=local_headers,json={'title':'团队协作演示：模型实验与研究交接'}))
                    assert session['sync_status']!='synced'
                    metrics['events']=event_count;metrics['rounds']=session['round_count']
                with step('desktop_login_uses_only_os_vault'):
                    checked(local.put('/api/v1/remote/config',headers=local_headers,json={'url':args.url}))
                    checked(local.post('/api/v1/remote/login',headers=local_headers,json={k:credentials['alice'][k] for k in ('username','password')}))
                    assert vault
                    stored=(cfg.home/'remote.json').read_text()
                    assert credentials['alice']['password'] not in stored
                    assert all(token not in stored for token in vault.values())

                def sync_local():
                    created=checked(local.post('/api/v1/remote/sync',headers=local_headers,json={'session_ids':[local_id]}))
                    job_id=created['jobs'][0]['id']
                    with app.state.engine.begin() as conn:conn.execute(update(db.jobs).where(db.jobs.c.id==job_id).values(state='running'))
                    execute_job(cfg,job_id)
                    with app.state.engine.connect() as conn:job=dict(conn.execute(select(db.jobs).where(db.jobs.c.id==job_id)).mappings().one())
                    assert job['state']=='succeeded',job.get('error')
                    return job['result_json']['remote_session_id'],job['result_json']['remote_job_id']

                with step('actual_chunk_upload_cloud_worker_atomic_publication'):
                    shared_id,cloud_job=sync_local()
                    metrics['cloud_job_id']=cloud_job
                with step('member_b_browser_cookie_reads_a_filtered_history'):
                    response=bob.post('/api/v1/auth/login',json={k:credentials['bob'][k] for k in ('username','password')})
                    checked(response);assert 'httponly' in response.headers.get('set-cookie','').lower()
                    assert checked(bob.get('/api/v1/auth/me'))['id']==credentials['bob']['id']
                    catalog=checked(bob.get('/api/v1/sessions',params={'owner_id':credentials['alice']['id']}))
                    assert any(s['id']==shared_id for s in catalog['items'])
                    assert all(s['owner_id']==credentials['alice']['id'] for s in catalog['items'])
                    shared=checked(bob.get('/api/v1/sessions/'+shared_id))
                    assert shared['event_count']==event_count and shared['current_revision']
                    events=checked(bob.get('/api/v1/sessions/'+shared_id+'/events'))['items']
                    assert len(events)==event_count
                    joined=json.dumps(events,ensure_ascii=False)
                    assert '关键决策' in joined and '结论完整保留' in joined
                    asset_id=find_image(events);assert asset_id
                    image=bob.get('/api/v1/assets/'+asset_id)
                    assert image.status_code==200 and hashlib.sha256(image.content).digest()==hashlib.sha256(png).digest()
                    expanded=next(e['event']['blocks'][0]['content_id'] for e in events if e['event'].get('blocks') and e['event']['blocks'][0].get('content_id'))
                    complete=checked(bob.get('/api/v1/contents/'+expanded))
                    assert '结论完整保留' in complete['text']
                    recent=checked(bob.get('/api/v1/sessions/'+shared_id+'/rounds',params={'recent':3}))
                    assert len(recent['items'])==3
                with step('cross_member_comment_anchor_and_private_favorite'):
                    comment=checked(bob.post('/api/v1/sessions/'+shared_id+'/comments',json={'revision_id':shared['current_revision'],'event_id':events[0]['id'],'text':'已核对图片和完整结论，下一步由我继续实验。'}))
                    comments=checked(local.get('/api/v1/remote/api/sessions/'+shared_id+'/comments',headers=local_headers))
                    assert any(c['id']==comment['id'] and c['owner_id']==credentials['bob']['id'] for c in comments['items'])
                    checked(bob.post('/api/v1/favorites',json={'session_id':shared_id,'revision_id':shared['current_revision'],'event_id':events[0]['id']}))
                    own=checked(bob.get('/api/v1/favorites'))['items'];assert any(f['session_id']==shared_id for f in own)
                    assert checked(local.get('/api/v1/remote/api/favorites',headers=local_headers))['items']==[]
                with step('unauthenticated_and_cross_owner_mutation_denied'):
                    assert httpx.get(args.url+'/api/v1/sessions/'+shared_id).status_code==401
                    assert bob.patch('/api/v1/sessions/'+shared_id,json={'title':'not allowed'}).status_code==403
                    assert bob.delete('/api/v1/sessions/'+shared_id).status_code==403
                    assert bob.post('/api/v1/invites',json={}).status_code==403
                with step('disable_member_revokes_web_and_device_tokens'):
                    bearer=checked(bob.post('/api/v1/auth/device-login',json={k:credentials['bob'][k] for k in ('username','password')}))['token']
                    checked(admin.patch('/api/v1/members/'+credentials['bob']['id'],json={'active':False}))
                    assert bob.get('/api/v1/auth/me').status_code==401
                    assert httpx.get(args.url+'/api/v1/auth/me',headers={'Authorization':'Bearer '+bearer}).status_code==401
                    checked(admin.patch('/api/v1/members/'+credentials['bob']['id'],json={'active':True}))
                    checked(bob.post('/api/v1/auth/login',json={k:credentials['bob'][k] for k in ('username','password')}))
                with step('queued_export_contains_complete_conversation'):
                    exported=checked(bob.post('/api/v1/sessions/'+shared_id+'/exports',json={'format':'markdown'}))['job']
                    wait_job(bob,exported['id'])
                    result=bob.get('/api/v1/exports/'+exported['id']+'/download')
                    assert result.status_code==200
                    assert '结论完整保留' in result.text and '第 6 轮结论' in result.text
                    metrics['export_bytes']=len(result.content)
                with step('revoke_share_blocks_old_content_image_export'):
                    checked(local.delete('/api/v1/remote/api/sessions/'+shared_id,headers=local_headers))
                    assert bob.get('/api/v1/sessions/'+shared_id).status_code==404
                    assert bob.get('/api/v1/assets/'+asset_id).status_code==404
                    assert bob.get('/api/v1/contents/'+expanded).status_code==404
                    assert bob.get('/api/v1/exports/'+exported['id']+'/download').status_code==404
                with step('reshare_after_revoke_and_leave_reviewable_demo'):
                    final_id,_=sync_local()
                    assert final_id!=shared_id
                    final=checked(bob.get('/api/v1/sessions/'+final_id))
                    first=checked(bob.get('/api/v1/sessions/'+final_id+'/events'))['items'][0]
                    checked(bob.post('/api/v1/sessions/'+final_id+'/comments',json={'revision_id':final['current_revision'],'event_id':first['id'],'text':'演示验收完成：跨成员阅读、图片、评论与权限均已验证。'}))
                    checked(bob.post('/api/v1/favorites',json={'session_id':final_id,'revision_id':final['current_revision'],'event_id':first['id']}))
                    credentials['demo_session_id']=final_id
                    private.write_text(json.dumps(credentials,ensure_ascii=False,indent=2),encoding='utf-8')
                    metrics['demo_session_id']=final_id
            app.state.engine.dispose()
        metrics['ok']=True
        metrics.pop('current_step',None)
    except Exception as error:
        safe=str(error)
        for account in ('admin','alice','bob'):
            secret=credentials.get(account,{}).get('password')
            if secret:safe=safe.replace(secret,'[redacted]')
        metrics['error']=safe
        raise
    finally:
        metrics['finished_at']=time.time();metrics['elapsed_seconds']=round(metrics['finished_at']-metrics['started_at'],3)
        (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':metrics['ok'],'checks':len(metrics['steps']),'metrics':str(out/'metrics.json')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
