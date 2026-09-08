"""Bounded live-service acceptance using only newly created synthetic records.

Admin credentials: CSH_ACCEPT_ADMIN_USERNAME / CSH_ACCEPT_ADMIN_PASSWORD, or
--admin-credentials PATH containing {username,password} or {admin:{...}}.
No administrator initialization/reset, SSH, Compose, or direct server DB access.
HTTP is permitted only through a loopback route (for an independently created
SSH tunnel); HTTPS certificate validation remains enabled for public endpoints.

Cleanup revokes the test session and disables its two test members. The product
retains revoked rows/files; this script never purges storage or other records.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import ssl
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import httpx
from PIL import Image
from sqlalchemy import select
from hub import db
from hub.bundles import build_bundle
from hub.config import Config
from hub.ingest import ingest_path

def endpoint(value):
    value=value.strip().rstrip('/')
    parts=urlsplit(value)
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Use a server URL without credentials, query, or fragment')
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('Use HTTPS, or the loopback URL of an independently established SSH tunnel')
    return value

def checked(response,status=200):
    if response.status_code!=status:
        try:detail=response.json().get('detail','')
        except (ValueError,httpx.ResponseNotRead):detail='Response body was not JSON'
        raise AssertionError(f'{response.request.method} {response.request.url.path}: HTTP {response.status_code}, expected {status}: {detail}')
    return response.json()

def bounded_download(client,path,limit):
    parts=[];total=0
    with client.stream('GET',path) as response:
        if response.status_code!=200:raise AssertionError(f'Download {path}: HTTP {response.status_code}')
        for chunk in response.iter_bytes(65536):
            total+=len(chunk)
            if total>limit:raise AssertionError('Synthetic result exceeded its bounded acceptance download size')
            parts.append(chunk)
    return b''.join(parts)

def wait_job(client,ident,timeout):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=checked(client.get('/api/v1/jobs/'+ident))
        if job['state']=='succeeded':return job
        if job['state'] in ('failed','paused','cancelled'):raise AssertionError(f"Synthetic job {job['state']}: {job.get('error')}")
        time.sleep(.5)
    raise AssertionError('Synthetic job did not finish within the bounded acceptance timeout')

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

def synthetic_package(folder,run_id):
    title='CSH acceptance '+run_id
    output=io.BytesIO();Image.new('RGB',(48,24),(31,92,205)).save(output,format='PNG')
    png=output.getvalue()
    encoded='data:image/png;base64,'+base64.b64encode(png).decode()
    path=folder/'synthetic.jsonl'
    records=[]
    for number in range(1,4):
        user=[{'type':'text','text':f'{title}：第 {number} 轮合成问题。'}]
        if number==1:user.append({'type':'image','data_uri':encoded})
        assistant=[{'type':'text','text':f'{title}：第 {number} 轮协作结论，已确认完整文字可供同事阅读。'}]
        if number==2:
            assistant.append({'type':'tool_use','id':'synthetic-read','name':'Read','input':{'path':'SYNTHETIC.md'},'result':{'text':'Synthetic tool output retained.','images':[]}})
        records.extend([{'kind':'user','blocks':user},{'kind':'assistant','blocks':assistant}])
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    # Explicit SQLite URL prevents inherited production environment variables
    # from directing this fixture parser to any actual server database.
    config=Config(mode='local',home=folder/'local',database_url='',worker_external=True,min_free_bytes=0,min_free_ratio=0)
    engine=db.get_engine(config);db.init_db(engine)
    sid,jid=db.new_id(),db.new_id()
    with engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=sid,owner_id='local',title=title,original_title=title,source_kind='cursor_jsonl',native_id=sid))
        conn.execute(db.jobs.insert().values(id=jid,owner_id='local',kind='ingest',state='queued',session_id=sid,payload_json={'path':str(path),'source_kind':'cursor_jsonl'}))
    ingest_path(config,jid)
    with engine.connect() as conn:
        session=conn.execute(select(db.sessions).where(db.sessions.c.id==sid)).mappings().one()
        assert session['event_count']==6 and session['round_count']==3
        revision=session['current_revision']
    bundle=folder/'synthetic.csh';build_bundle(config,sid,revision,bundle)
    engine.dispose()
    assert bundle.stat().st_size<256*1024
    return bundle,png,title

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--origin',help='Configured public browser origin when testing through a loopback SSH tunnel')
    parser.add_argument('--admin-credentials',type=Path)
    parser.add_argument('--ca-file',type=Path,help='Optional trusted private CA; never disables certificate validation')
    parser.add_argument('--job-timeout',type=int,default=90)
    args=parser.parse_args()
    try:
        url=endpoint(args.url)
        if args.admin_credentials:
            supplied=json.loads(args.admin_credentials.read_text(encoding='utf-8'))
            supplied=supplied.get('admin',supplied)
            admin_credentials={k:supplied[k] for k in ('username','password')}
        else:
            admin_credentials={'username':os.environ.get('CSH_ACCEPT_ADMIN_USERNAME','admin'),'password':os.environ.get('CSH_ACCEPT_ADMIN_PASSWORD','')}
        if not admin_credentials['password']:raise ValueError('Provide existing administrator credentials through environment variables or an explicit JSON file')
        if not 5<=args.job_timeout<=300:raise ValueError('Job timeout must be between 5 and 300 seconds')
    except (ValueError,KeyError,OSError) as error:
        parser.error(str(error))
    run_id=time.strftime('%Y%m%d')+'_'+secrets.token_hex(5)
    folder=ROOT/'.runtime/production-acceptance'/run_id;folder.mkdir(parents=True,mode=0o700)
    ledger={'version':1,'run_id':run_id,'server_url':url,'accounts':{},'invites':[],'jobs':[]}
    metrics={'ok':False,'run_id':run_id,'server_url':url,'started_at':time.time(),'checks':[],'cleanup_errors':[]}
    secrets_to_hide=[admin_credentials['password']]
    clients={};admin=None;admin_profile=None;session_revoked=False
    verify=ssl.create_default_context(cafile=str(args.ca_file)) if args.ca_file else True
    base_headers={'Origin':args.origin} if args.origin else {}
    def save_ledger():
        destination=folder/'private-ledger.json'
        destination.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
        os.chmod(destination,0o600)
    def scrub(value):
        value=str(value)
        for secret in secrets_to_hide:
            if secret:value=value.replace(secret,'[redacted]')
        return value[:2000]
    def passed(name):
        metrics['checks'].append(name)
        print(json.dumps({'check':name,'passed':True}),flush=True)
    def login(credentials):
        client=httpx.Client(base_url=url,headers=base_headers,timeout=30,verify=verify)
        try:
            result=checked(client.post('/api/v1/auth/device-login',json={k:credentials[k] for k in ('username','password')}))
            secrets_to_hide.append(result['token'])
            client.headers['Authorization']='Bearer '+result['token']
            return client,result['user']
        except BaseException:
            client.close();raise
    try:
        with httpx.Client(base_url=url,headers=base_headers,timeout=10,verify=verify) as anonymous:
            health=checked(anonymous.get('/health'))
            assert health['mode']=='cloud'
            assert anonymous.get('/api/v1/sessions').status_code==401
            assert anonymous.post('/api/v1/imports/path',json={'path':'unused'}).status_code==404
        admin,admin_profile=login(admin_credentials)
        assert admin_profile['role']=='admin' and admin_profile['id']!='local'
        ledger['admin_id']=admin_profile['id'];save_ledger()
        passed('cloud_health_login_and_no_anonymous_sessions')
        for key in ('a','b'):
            invitation=checked(admin.post('/api/v1/invites',json={}))
            ledger['invites'].append(invitation['id']);secrets_to_hide.append(invitation['token']);save_ledger()
            account={'username':'csh_accept_'+key+'_'+run_id,'display_name':'CSH验收 '+key.upper()+' '+run_id,'password':secrets.token_urlsafe(24)}
            secrets_to_hide.append(account['password'])
            with httpx.Client(base_url=url,headers=base_headers,timeout=30,verify=verify) as registration:
                registered=checked(registration.post('/api/v1/auth/register',json={'token':invitation['token'],**account}))['user']
            assert registered['role']=='member' and registered['id']!=admin_profile['id']
            account['id']=registered['id'];ledger['accounts'][key]=account;save_ledger()
            clients[key],profile=login(account)
            assert profile['id']==account['id']
        passed('two_new_disposable_members_registered_by_invitation')
        bundle,png,title=synthetic_package(folder,run_id)
        passed('synthetic_six_event_three_round_package_only')
        a,b=clients['a'],clients['b']
        with bundle.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        upload=checked(a.post('/api/v1/uploads',json={'filename':run_id+'.csh','total_bytes':bundle.stat().st_size,'sha256':sha,'session_key':'acceptance:'+run_id,'device_id':'acceptance:'+run_id}))
        ledger['upload_id']=upload['id'];save_ledger()
        with bundle.open('rb') as source:
            number=0
            while chunk:=source.read(min(upload['chunk_size'],4*1024**2)):
                checked(a.put('/api/v1/uploads/'+upload['id']+'/chunks/'+str(number),content=chunk,headers={'Content-Type':'application/octet-stream','X-Chunk-SHA256':hashlib.sha256(chunk).hexdigest()}))
                number+=1
        submitted=checked(a.post('/api/v1/uploads/'+upload['id']+'/complete',json={}))
        sid=submitted['session_id'];ledger['session_id']=sid
        ledger['jobs'].append({'id':submitted['job']['id'],'owner':'a'});save_ledger()
        wait_job(a,submitted['job']['id'],args.job_timeout)
        passed('actual_upload_and_cloud_worker_publication')
        session=checked(b.get('/api/v1/sessions/'+sid))
        assert session['owner_id']==ledger['accounts']['a']['id'] and session['title']==title and session['event_count']==6
        catalog=checked(b.get('/api/v1/sessions',params={'owner_id':ledger['accounts']['a']['id']}))
        assert any(row['id']==sid for row in catalog['items'])
        events=checked(b.get('/api/v1/sessions/'+sid+'/events'))['items']
        assert len(events)==6 and '第 3 轮协作结论' in json.dumps(events,ensure_ascii=False)
        assert 'Synthetic tool output retained.' in json.dumps(events)
        aid=find_image(events);assert aid
        assert hashlib.sha256(bounded_download(b,'/api/v1/assets/'+aid,65536)).digest()==hashlib.sha256(png).digest()
        assert len(checked(b.get('/api/v1/sessions/'+sid+'/rounds',params={'recent':3}))['items'])==3
        passed('member_b_reads_member_a_text_tool_image_and_rounds')
        comment=checked(b.post('/api/v1/sessions/'+sid+'/comments',json={'revision_id':session['current_revision'],'event_id':events[0]['id'],'text':title+'：B 已核对，A 可以看到此评论。'}))
        ledger['comment_id']=comment['id'];save_ledger()
        assert any(row['id']==comment['id'] for row in checked(a.get('/api/v1/sessions/'+sid+'/comments'))['items'])
        favorite=checked(b.post('/api/v1/favorites',json={'session_id':sid,'revision_id':session['current_revision'],'event_id':events[0]['id']}))
        ledger['favorite_id']=favorite['id'];save_ledger()
        assert any(row['id']==favorite['id'] for row in checked(b.get('/api/v1/favorites',params={'session_id':sid}))['items'])
        assert checked(a.get('/api/v1/favorites',params={'session_id':sid}))['items']==[]
        passed('cross_member_comment_and_private_favorite')
        assert b.patch('/api/v1/sessions/'+sid,json={'title':'unauthorized'}).status_code==403
        assert b.delete('/api/v1/sessions/'+sid).status_code==403
        assert b.post('/api/v1/invites',json={}).status_code==403
        passed('member_permissions_cannot_modify_another_owners_session')
        exported=checked(b.post('/api/v1/sessions/'+sid+'/exports',json={'format':'markdown'}))['job']
        ledger['jobs'].append({'id':exported['id'],'owner':'b'});save_ledger()
        wait_job(b,exported['id'],args.job_timeout)
        export=bounded_download(b,'/api/v1/exports/'+exported['id']+'/download',1024**2)
        assert '第 3 轮协作结论' in export.decode('utf-8')
        passed('complete_synthetic_session_export')
        # Remove only this run's comment/favorite before revoking the anchor.
        checked(b.delete('/api/v1/comments/'+ledger['comment_id']));ledger['comment_removed']=True
        checked(b.delete('/api/v1/favorites/'+ledger['favorite_id']));ledger['favorite_removed']=True
        checked(a.delete('/api/v1/sessions/'+sid));session_revoked=True;ledger['session_revoked']=True;save_ledger()
        assert b.get('/api/v1/sessions/'+sid).status_code==404
        assert b.get('/api/v1/assets/'+aid).status_code==404
        assert b.get('/api/v1/exports/'+exported['id']+'/download').status_code==404
        passed('revoked_test_share_denies_new_session_image_and_export_requests')
        metrics['functional_checks_passed']=True
    except BaseException as error:
        metrics['error']=scrub(error)
    finally:
        # Every cleanup target comes from this run's creation responses. No
        # account enumeration/deletion pattern is used to choose targets.
        if clients.get('b'):
            for key,path in (('comment','/api/v1/comments/'),('favorite','/api/v1/favorites/')):
                ident=ledger.get(key+'_id')
                if ident and not ledger.get(key+'_removed'):
                    try:
                        response=clients['b'].delete(path+ident)
                        if response.status_code not in (200,404):raise AssertionError(f'{key} cleanup HTTP {response.status_code}')
                        ledger[key+'_removed']=response.status_code==200
                    except Exception as error:metrics['cleanup_errors'].append(scrub(error))
        for job in ledger['jobs']:
            owner=clients.get(job['owner'])
            if owner:
                try:
                    result=owner.get('/api/v1/jobs/'+job['id'])
                    if result.status_code==200 and result.json()['state'] in ('queued','running','paused'):
                        checked(owner.post('/api/v1/jobs/'+job['id']+'/cancel',json={}))
                except Exception as error:metrics['cleanup_errors'].append(scrub(error))
        if clients.get('a') and ledger.get('session_id') and not session_revoked:
            try:
                response=clients['a'].get('/api/v1/sessions/'+ledger['session_id'])
                if response.status_code==200:
                    assert response.json()['owner_id']==ledger['accounts']['a']['id']
                    checked(clients['a'].delete('/api/v1/sessions/'+ledger['session_id']))
                elif response.status_code!=404:raise AssertionError('Could not verify test session owner for cleanup')
                ledger['session_revoked']=True
            except Exception as error:metrics['cleanup_errors'].append(scrub(error))
        elif clients.get('a') and ledger.get('upload_id') and not ledger.get('session_id'):
            try:
                response=clients['a'].delete('/api/v1/uploads/'+ledger['upload_id'])
                if response.status_code not in (200,404):raise AssertionError('Test upload cleanup failed')
            except Exception as error:metrics['cleanup_errors'].append(scrub(error))
        if admin and admin_profile:
            for key,account in ledger['accounts'].items():
                try:
                    assert account['id']!=admin_profile['id'] and account['username']=='csh_accept_'+key+'_'+run_id
                    disabled=checked(admin.patch('/api/v1/members/'+account['id'],json={'active':False}))
                    assert disabled['username']==account['username'] and disabled['role']=='member' and not disabled['active']
                    ledger['accounts'][key]['disabled']=True
                    if clients.get(key):assert clients[key].get('/api/v1/auth/me').status_code==401
                except Exception as error:metrics['cleanup_errors'].append(scrub(error))
            for ident in ledger['invites']:
                try:checked(admin.delete('/api/v1/invites/'+ident))
                except Exception as error:metrics['cleanup_errors'].append(scrub(error))
            try:
                assert checked(admin.get('/api/v1/auth/me'))==admin_profile
                metrics['live_admin_profile_unchanged']=True
                checked(admin.post('/api/v1/auth/logout',json={}))
            except Exception as error:metrics['cleanup_errors'].append(scrub(error))
        for client in [*clients.values(),admin]:
            if client:client.close()
        metrics['test_accounts_disabled']=sum(bool(a.get('disabled')) for a in ledger['accounts'].values())
        metrics['test_session_revoked']=bool(ledger.get('session_revoked'))
        metrics['cleanup_semantics']='Test accounts disabled and share revoked; product-retained rows/files are not physically purged.'
        metrics['ok']=bool(metrics.get('functional_checks_passed')) and not metrics['cleanup_errors'] and metrics['test_accounts_disabled']==2
        metrics['elapsed_seconds']=round(time.time()-metrics['started_at'],3)
        save_ledger()
        result_path=folder/'metrics.json';result_path.write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':metrics['ok'],'checks':len(metrics['checks']),'accounts_disabled':metrics['test_accounts_disabled'],'session_revoked':metrics['test_session_revoked'],'metrics':str(result_path),'error':metrics.get('error'),'cleanup_errors':metrics['cleanup_errors']},ensure_ascii=False),flush=True)
    return 0 if metrics['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
