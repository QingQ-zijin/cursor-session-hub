import json
import time
import httpx
import pytest
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from hub import ai, auth, db
from hub.api import create_app
from hub.config import Config


@pytest.fixture
def service(tmp_path,monkeypatch):
    cfg=Config(mode='cloud',home=tmp_path,worker_external=True,cookie_secure=False,min_free_bytes=0,min_free_ratio=0)
    app=create_app(cfg);admin=auth.bootstrap_admin(app.state.engine,'admin','Admin','test-administrator-password')
    with TestClient(app) as client:
        def login(name,password): return {'Authorization':'Bearer '+client.post('/api/v1/auth/device-login',json={'username':name,'password':password}).json()['token']}
        headers=login('admin','test-administrator-password')
        invite=client.post('/api/v1/invites',headers=headers,json={}).json()['token']
        user=client.post('/api/v1/auth/register',json={'username':'member','display_name':'Member','password':'test-member-password','token':invite}).json()['user']
        client.cookies.clear();mh=login('member','test-member-password')
        submitted=[];monkeypatch.setattr(app.state.ai_manager.pool,'submit',lambda fn,ident:submitted.append(ident))
        yield app,client,headers,mh,user['id'],submitted


def enable(client,headers):
    response=client.patch('/api/v1/ai/settings',headers=headers,json={'enabled':True,'base_url':'https://provider.example/v1','model':'test-model','api_key':'private-test-key','context_chars':4000})
    assert response.status_code==200,response.text
    return response.json()


def seed(app,owner,count=90):
    sid,rid=db.new_id(),db.new_id()
    with app.state.engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=sid,owner_id=owner,title='引用会话',current_revision=rid,status='ready',event_count=count))
        conn.execute(db.revisions.insert().values(id=rid,session_id=sid,state='ready',event_count=count))
        for seq in range(1,count+1):
            conn.execute(db.events.insert().values(id=db.new_id(),revision_id=rid,seq=seq,round_number=1,kind='assistant',event_json={'kind':'assistant','blocks':[{'type':'text','text':f'历史正文 {seq} '+('文'*200)}]}))
    return sid,rid


def submit(client,headers,settings,refs=(),**extra):
    return client.post('/api/v1/ai/messages',headers=headers,json={'text':'总结这些记录','references':list(refs),'request_id':db.new_id(),'settings_version':settings['version'],**extra})


def test_admin_key_encrypted_private_and_destination_changes_require_key(service):
    app,client,admin,member,uid,_=service
    assert client.get('/api/v1/ai/settings').status_code==401
    assert client.patch('/api/v1/ai/settings',headers=member,json={}).status_code==403
    settings=enable(client,admin)
    public=client.get('/api/v1/ai/settings',headers=member).json()
    assert public['configured'] and 'private-test-key' not in json.dumps(public)
    with app.state.engine.connect() as conn:
        cipher=conn.execute(select(db.ai_settings.c.key_cipher)).scalar_one()
        assert 'private-test-key' not in cipher
    assert client.patch('/api/v1/ai/settings',headers=admin,json={**settings,'base_url':'https://another.example/v1'}).status_code==422
    assert client.patch('/api/v1/ai/settings',headers=admin,json={**settings,'base_url':'http://example.com/v1','api_key':'new-key'}).status_code==422


def test_context_bounded_references_checked_and_original_untouched(service):
    app,client,admin,member,uid,_=service;settings=enable(client,admin);sid,rid=seed(app,uid)
    preview=client.post('/api/v1/ai/context',headers=member,json={'references':[{'session_id':sid}]}).json()
    assert preview['references'][0]['partial'] and preview['references'][0]['characters']<4000
    assert preview['references'][0]['revision_id']==rid and preview['images_included'] is False
    assert client.post('/api/v1/ai/context',headers=member,json={'references':[{'session_id':'unknown'}]}).status_code==404
    with app.state.engine.begin() as conn:conn.execute(db.sessions.update().where(db.sessions.c.id==sid).values(revoked=True))
    assert submit(client,member,settings,[{'session_id':sid,'revision_id':rid}]).status_code==404
    with app.state.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.events)).scalar_one()==90
        assert conn.execute(select(func.count()).select_from(db.ai_messages)).scalar_one()==0


def test_streaming_real_protocol_idempotence_unicode_and_owner_boundaries(service,monkeypatch):
    app,client,admin,member,uid,submitted=service;settings=enable(client,admin);sid,rid=seed(app,uid,2)
    request_id=db.new_id();response=submit(client,member,settings,[{'session_id':sid,'revision_id':rid}],request_id=request_id)
    assert response.status_code==200,response.text
    data=response.json();aid=data['assistant']['id'];captured=[]
    real=httpx.Client
    def provider(request):
        assert str(request.url)=='https://provider.example/v1/chat/completions'
        assert request.headers['Authorization']=='Bearer private-test-key'
        body=json.loads(request.content);captured.append(body)
        assert body['stream'] is True and 'tools' not in body
        assert any('历史正文 2' in m['content'] for m in body['messages'])
        assert '历史会话' in body['messages'][0]['content']
        chunks=[{'choices':[{'delta':{'content':'中文😀'},'finish_reason':None}]},{'choices':[{'delta':{'content':'完整结尾'},'finish_reason':'stop'}]}]
        raw=b''.join(('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n').encode() for chunk in chunks)+b'data: [DONE]\n\n'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=httpx.ByteStream(raw))
    monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(provider)))
    app.state.ai_manager.generate(aid)
    result=client.get('/api/v1/ai/replies/'+aid,headers=member).json()
    assert result['state']=='complete' and result['text']=='中文😀完整结尾'
    assert result['next_offset']==7
    repeated=submit(client,member,settings,[{'session_id':sid,'revision_id':rid}],request_id=request_id).json()
    assert repeated['assistant']['id']==aid and len(submitted)==1 and len(captured)==1
    assert client.get('/api/v1/ai/threads/'+data['thread_id'],headers=admin).status_code==404
    assert client.get('/api/v1/ai/replies/'+aid,headers=admin).status_code==404
    assert client.delete('/api/v1/ai/threads/'+data['thread_id'],headers=admin).status_code==404


def test_cancel_disable_and_restart_never_resend(service):
    app,client,admin,member,uid,submitted=service;settings=enable(client,admin)
    first=submit(client,member,settings).json()
    assert submit(client,member,settings).status_code==409
    assert client.post('/api/v1/ai/replies/'+first['assistant']['id']+'/stop',headers=member).status_code==200
    assert client.get('/api/v1/ai/replies/'+first['assistant']['id'],headers=member).json()['state']=='cancelled'
    assert submit(client,member,settings).status_code==409
    app.state.ai_manager.generate(first['assistant']['id'])
    second=submit(client,member,settings).json();app.state.ai_manager.recover()
    assert client.get('/api/v1/ai/replies/'+second['assistant']['id'],headers=member).json()['state']=='failed'
    app.state.ai_manager.generate(second['assistant']['id'])
    third=submit(client,member,settings).json()
    client.patch('/api/v1/members/'+uid,headers=admin,json={'active':False})
    assert client.get('/api/v1/ai/replies/'+third['assistant']['id'],headers=member).status_code==401
    with app.state.engine.connect() as conn:
        assert conn.execute(select(db.ai_messages.c.state).where(db.ai_messages.c.id==third['assistant']['id'])).scalar_one()=='cancelled'
    assert len(submitted)==3


@pytest.mark.parametrize('status,body',[(401,b'secret-reflected-private-test-key'),(200,b'broken')])
def test_provider_failure_never_leaks_key_or_marks_complete(service,monkeypatch,status,body):
    app,client,admin,member,uid,_=service;settings=enable(client,admin);data=submit(client,member,settings).json()
    real=httpx.Client
    monkeypatch.setattr(ai.httpx,'Client',lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(lambda r:httpx.Response(status,stream=httpx.ByteStream(body)))))
    app.state.ai_manager.generate(data['assistant']['id'])
    response=client.get('/api/v1/ai/replies/'+data['assistant']['id'],headers=member).json()
    assert response['state']=='failed' and 'private-test-key' not in json.dumps(response)


def test_deleted_member_private_chats_removed(service):
    app,client,admin,member,uid,_=service;settings=enable(client,admin);submit(client,member,settings)
    assert client.delete('/api/v1/members/'+uid,headers=admin).status_code==200
    with app.state.engine.connect() as conn:
        assert not conn.execute(select(db.ai_threads).where(db.ai_threads.c.owner_id==uid)).first()
        assert not conn.execute(select(db.ai_messages).where(db.ai_messages.c.owner_id==uid)).first()


def test_global_limit_and_config_change_prevent_extra_calls(service):
    app,client,admin,member,uid,submitted=service;settings=enable(client,admin)
    assert submit(client,admin,settings).status_code==200
    assert submit(client,member,settings).status_code==200
    assert submit(client,member,settings).status_code==429
    newer=enable(client,admin)
    assert submit(client,member,settings).status_code==409
    assert len(submitted)==2 and newer['version']!=settings['version']
    app.state.ai_manager.generate(submitted[0])
    with app.state.engine.connect() as conn:
        row=conn.execute(select(db.ai_messages).where(db.ai_messages.c.id==submitted[0])).mappings().one()
        assert row['state']=='failed' and '配置已更改' in row['error']


def test_revocation_before_model_call_and_partial_stream_failure(service,monkeypatch):
    app,client,admin,member,uid,submitted=service;settings=enable(client,admin);sid,rid=seed(app,uid,1)
    item=submit(client,member,settings,[{'session_id':sid,'revision_id':rid}]).json()
    with app.state.engine.begin() as conn:conn.execute(db.sessions.update().where(db.sessions.c.id==sid).values(revoked=True))
    app.state.ai_manager.generate(item['assistant']['id'])
    assert client.get('/api/v1/ai/replies/'+item['assistant']['id'],headers=member).json()['state']=='failed'
    item=submit(client,member,settings).json();real=httpx.Client
    raw=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
    monkeypatch.setattr(ai.httpx,'Client',lambda **kw:real(**kw,transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=httpx.ByteStream(raw)))))
    app.state.ai_manager.generate(item['assistant']['id'])
    result=client.get('/api/v1/ai/replies/'+item['assistant']['id'],headers=member).json()
    assert result['state']=='failed' and result['text']=='partial' and '提前断开' in result['error']


def test_local_key_uses_os_store_and_never_falls_back(tmp_path,monkeypatch):
    cfg=Config(mode='local',home=tmp_path);saved={}
    monkeypatch.setattr(ai.keyring,'get_password',lambda service,name:saved.get(name))
    monkeypatch.setattr(ai.keyring,'set_password',lambda service,name,value:saved.update({name:value}))
    key=ai.master_key(cfg)
    assert key==ai.master_key(cfg) and not (tmp_path/'ai-master.key').exists()
    def fail(*args):raise RuntimeError('keychain locked')
    monkeypatch.setattr(ai.keyring,'get_password',fail)
    with pytest.raises(ValueError,match='系统凭证存储不可用'):ai.master_key(cfg)
