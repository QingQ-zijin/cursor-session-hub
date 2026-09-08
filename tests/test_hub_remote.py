"""Remote sync boundaries, OS credential persistence, and commit acknowledgement."""
import hashlib
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from hub import auth,db,remote
from hub.config import Config
from hub.api import create_app

@pytest.fixture
def installation(tmp_path,monkeypatch):
    cfg=Config(mode='local',home=tmp_path,local_token='launch-token',worker_external=True,min_free_bytes=0,min_free_ratio=0)
    vault={}
    monkeypatch.setattr(remote.keyring,'get_password',lambda service,key:vault.get((service,key)))
    monkeypatch.setattr(remote.keyring,'set_password',lambda service,key,value:vault.__setitem__((service,key),value))
    monkeypatch.setattr(remote.keyring,'delete_password',lambda service,key:vault.pop((service,key),None))
    app=create_app(cfg)
    with TestClient(app) as client:
        yield cfg,app,client,{'Authorization':'Bearer launch-token'},vault

def intercept_sync(monkeypatch,handler):
    real=httpx.Client
    monkeypatch.setattr(remote.httpx,'Client',lambda *args,**kwargs:real(*args,**kwargs,transport=httpx.MockTransport(handler)))

@pytest.mark.parametrize('url',['http://example.com','https://a:b@example.com','https://example.com/?secret=x','https://example.com/#token','file:///tmp/foo','not-a-url'])
def test_reject_unsafe_server_url(url):
    with pytest.raises(ValueError):remote.normalize_url(url)

def test_https_and_localhost_urls():
    assert remote.normalize_url(' https://team.example.com/ ')=='https://team.example.com'
    assert remote.normalize_url('http://127.0.0.1:8000/')=='http://127.0.0.1:8000'
    assert remote.normalize_url('http://[::1]:8000')=='http://[::1]:8000'


@pytest.mark.parametrize('consent',[False,None,'true','false',1])
def test_public_http_requires_explicit_boolean_consent(installation,consent):
    cfg,app,client,headers,vault=installation
    response=client.put('/api/v1/remote/config',headers=headers,json={'url':'http://team.example.com:8000','allow_insecure_http':consent})
    assert response.status_code==400
    assert not (cfg.home/'remote.json').exists()


def test_public_http_login_allowed_only_for_consented_address(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    url='http://team.example.com:8000'
    def server(request):
        assert str(request.url).startswith(url+'/api/v1/')
        if request.url.path.endswith('device-login'):
            return httpx.Response(200,json={'user':{'id':'test-member'},'token':'private-token'})
        assert request.headers['authorization']=='Bearer private-token'
        return httpx.Response(200,json={'id':'test-member'})
    intercept_sync(monkeypatch,server)
    assert client.put('/api/v1/remote/config',headers=headers,json={'url':url,'allow_insecure_http':True}).status_code==200
    assert client.post('/api/v1/remote/login',headers=headers,json={'username':'member','password':'request-secret-only'}).status_code==200
    assert client.get('/api/v1/remote/me',headers=headers).json()['user']['id']=='test-member'
    saved=json.loads((cfg.home/'remote.json').read_text())
    assert saved['allow_insecure_http'] is True
    assert 'private-token' not in json.dumps(saved) and 'request-secret-only' not in json.dumps(saved)
    with pytest.raises(ValueError):remote.configured_url(saved,'http://different.example.com:8000')
    assert client.put('/api/v1/remote/config',headers=headers,json={'url':'http://different.example.com:8000'}).status_code==400
    assert client.put('/api/v1/remote/config',headers=headers,json={'url':'https://team.example.com','allow_insecure_http':True}).status_code==200
    assert client.get('/api/v1/remote/config',headers=headers).json()['allow_insecure_http'] is False


@pytest.mark.parametrize('url',['file:///tmp/example','ftp://example.com','https://user:pass@example.com','http://example.com/?token=secret'])
def test_http_consent_never_allows_other_unsafe_urls(url):
    with pytest.raises(ValueError):remote.normalize_url(url,allow_insecure_http=True)

def test_only_os_vault_persists_credentials_and_logout(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    observed=[]
    def server(request):
        observed.append(request)
        if request.url.path.endswith('device-login'):
            assert json.loads(request.content)['password']=='secret-in-request-only'
            return httpx.Response(200,json={'user':{'id':'remote-member','username':'alice','display_name':'Alice','role':'member','active':True},'token':'device-secret'})
        if request.url.path.endswith('/me'):
            assert request.headers['authorization']=='Bearer device-secret'
            return httpx.Response(200,json={'id':'remote-member','username':'alice','display_name':'Alice','role':'member','active':True})
        return httpx.Response(200,json={'ok':True})
    intercept_sync(monkeypatch,server)
    assert client.put('/api/v1/remote/config',headers=headers,json={'url':'https://team.example.com'}).status_code==200
    assert client.post('/api/v1/remote/login',headers=headers,json={'username':'alice','password':'secret-in-request-only'}).status_code==200
    persisted=(cfg.home/'remote.json').read_text()
    assert 'device-secret' not in persisted and 'secret-in-request-only' not in persisted
    assert list(vault.values())==['device-secret']
    assert client.get('/api/v1/remote/me',headers=headers).json()['user']['id']=='remote-member'
    assert client.post('/api/v1/remote/logout',headers=headers).status_code==200
    assert vault=={}
    assert any(r.url.path.endswith('/auth/logout') for r in observed)

def test_no_plaintext_fallback_when_vault_unavailable(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    def unavailable(*args):raise RuntimeError('vault locked')
    monkeypatch.setattr(remote.keyring,'set_password',unavailable)
    with pytest.raises(RuntimeError,match='系统凭证存储'):
        remote.set_token(cfg,'https://team.example.com','never-written')
    assert not (cfg.home/'remote.json').exists()

def test_remote_routes_are_local_and_launch_token_required(installation,tmp_path):
    cfg,app,client,headers,vault=installation
    assert client.get('/api/v1/remote/config').status_code==401
    cloud=create_app(Config(mode='cloud',home=tmp_path/'cloud',worker_external=True))
    with TestClient(cloud) as team:
        assert team.get('/api/v1/remote/config').status_code==404

def seed_sync(installation,monkeypatch,checkpoint=None):
    cfg,app,client,headers,vault=installation
    remote._save_config(cfg,{'url':'https://team.example.com','device_id':'laptop','user_id':'remote-member'})
    remote.set_token(cfg,'https://team.example.com','device-secret')
    sid,rid,jid,syncid=[db.new_id() for _ in range(4)]
    with app.state.engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=sid,owner_id='local',title='sample',native_id='same-filename',source_kind='cursor_jsonl',current_revision=rid,status='ready'))
        conn.execute(db.revisions.insert().values(id=rid,session_id=sid,state='ready'))
        conn.execute(db.jobs.insert().values(id=jid,owner_id='local',kind='sync',state='running',session_id=sid,revision_id=rid,checkpoint_json=checkpoint or {},payload_json={'session_id':sid,'revision_id':rid,'remote_user_id':'remote-member','device_id':'laptop','server_url':'https://team.example.com','sync_id':syncid}))
        conn.execute(db.syncs.insert().values(id=syncid,owner_id='local',device_id='laptop',session_id=sid,job_id=jid,state='queued'))
    import hub.bundles
    monkeypatch.setattr(hub.bundles,'build_bundle',lambda config,session,revision,path,excluded:Path(path).write_bytes(b'small sync fixture'))
    return sid,rid,jid,syncid

def test_sync_waits_for_server_publication(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    sid,rid,jid,syncid=seed_sync(installation,monkeypatch)
    observed=[];status_reads=0
    def server(request):
        nonlocal status_reads
        observed.append(request)
        if request.url.path.endswith('/auth/me'):return httpx.Response(200,json={'id':'remote-member'})
        if request.url.path.endswith('/uploads'):
            # Logical identity is local session ID, never a collision-prone filename.
            assert sid in json.loads(request.content)['session_key']
            return httpx.Response(200,json={'id':'upload-id','chunk_size':4194304,'received_chunks':[]})
        if '/chunks/' in request.url.path:
            assert request.headers['x-chunk-sha256']==hashlib.sha256(request.content).hexdigest()
            return httpx.Response(200,json={'ok':True})
        if request.url.path.endswith('/complete'):return httpx.Response(200,json={'job':{'id':'server-job','state':'queued'},'session_id':'shared-session'})
        if request.url.path.endswith('/jobs/server-job'):
            status_reads+=1
            with app.state.engine.connect() as conn:
                assert conn.execute(select(db.sessions.c.sync_status).where(db.sessions.c.id==sid)).scalar()!='synced'
            return httpx.Response(200,json={'state':'queued' if status_reads==1 else 'succeeded'})
        raise AssertionError(str(request.url))
    intercept_sync(monkeypatch,server)
    monkeypatch.setattr(remote.time,'sleep',lambda _:None)
    result=remote.run_sync_job(cfg,jid)
    assert result['remote_session_id']=='shared-session' and status_reads==2
    with app.state.engine.connect() as conn:
        assert conn.execute(select(db.sessions.c.sync_status).where(db.sessions.c.id==sid)).scalar()=='synced'
        assert conn.execute(select(db.syncs.c.state).where(db.syncs.c.id==syncid)).scalar()=='succeeded'

def test_changed_remote_account_cannot_receive_queued_sync(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    sid,rid,jid,syncid=seed_sync(installation,monkeypatch)
    requests=[]
    def server(request):
        requests.append(request)
        return httpx.Response(200,json={'id':'different-account'})
    intercept_sync(monkeypatch,server)
    with pytest.raises(RuntimeError,match='团队账号已改变'):remote.run_sync_job(cfg,jid)
    assert len(requests)==1
    with app.state.engine.connect() as conn:
        assert conn.execute(select(db.sessions.c.sync_status).where(db.sessions.c.id==sid)).scalar()=='failed'

def test_local_proxy_anonymous_invitation_then_authenticated_access(installation,monkeypatch):
    cfg,app,client,headers,vault=installation
    remote._save_config(cfg,{'url':'https://team.example.com','device_id':'laptop'})
    observed=[]
    def server(request):
        observed.append(request)
        if request.url.path.endswith('/auth/register'):
            assert 'authorization' not in request.headers
            return httpx.Response(200,json={'user':{'id':'new-member'}})
        assert request.headers['authorization']=='Bearer device-secret'
        return httpx.Response(200,json={'items':[],'next_cursor':None})
    original=httpx.AsyncClient
    monkeypatch.setattr(remote.httpx,'AsyncClient',lambda *args,**kwargs:original(*args,**kwargs,transport=httpx.MockTransport(server)))
    result=client.post('/api/v1/remote/api/auth/register',headers=headers,json={'token':'invitation-token','username':'new-member','display_name':'New Member','password':'password-12345'})
    assert result.status_code==200,result.text
    assert client.get('/api/v1/remote/api/sessions',headers=headers).status_code==401
    remote.set_token(cfg,'https://team.example.com','device-secret')
    assert client.get('/api/v1/remote/api/sessions',headers=headers).status_code==200
