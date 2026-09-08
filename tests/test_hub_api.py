"""Product access boundaries and bounded indexed I/O; no real transcripts."""
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update
from hub.api import create_app
from hub.config import Config
from hub import auth,db

@pytest.fixture
def cloud(tmp_path):
    cfg=Config(mode='cloud',home=tmp_path,database_url='',worker_external=True,cookie_secure=False,min_free_bytes=0,min_free_ratio=0)
    app=create_app(cfg)
    admin=auth.bootstrap_admin(app.state.engine,'admin','Admin','administrator-password')
    with TestClient(app) as client:
        result=client.post('/api/v1/auth/device-login',json={'username':'admin','password':'administrator-password'})
        headers={'Authorization':'Bearer '+result.json()['token']}
        yield app,client,headers,admin

def member(client,admin_headers,name):
    invite=client.post('/api/v1/invites',headers=admin_headers,json={}).json()['token']
    result=client.post('/api/v1/auth/register',json={'token':invite,'username':name,'display_name':name,'password':'member-password-123'})
    assert result.status_code==200,result.text
    ident=result.json()['user']['id']
    client.cookies.clear()
    result=client.post('/api/v1/auth/device-login',json={'username':name,'password':'member-password-123'})
    return ident,{'Authorization':'Bearer '+result.json()['token']}

def seed(app,owner,number=5,large=False):
    sid,rid=db.new_id(),db.new_id()
    with app.state.engine.begin() as conn:
        conn.execute(db.sessions.insert().values(id=sid,owner_id=owner,title='研究结论',original_title='研究结论',status='ready',current_revision=rid,event_count=number,round_count=number))
        conn.execute(db.revisions.insert().values(id=rid,session_id=sid,state='ready',event_count=number,round_count=number))
        for seq in range(number):
            eid=db.new_id()
            conn.execute(db.events.insert().values(id=eid,revision_id=rid,seq=seq,round_number=seq,kind='user',event_json={'kind':'user','text':'正文 '+str(seq)+('x'*30000 if large else '')}))
            conn.execute(db.rounds.insert().values(id=db.new_id(),revision_id=rid,number=seq,start_seq=seq,end_seq=seq,count=1,preview='问题 '+str(seq)))
    return sid,rid,eid

def test_cloud_auth_invites_and_stop_user(cloud):
    app,client,headers,admin=cloud
    assert client.get('/api/v1/sessions').status_code==401
    assert client.get('/api/v1/capabilities').json()['mode']=='cloud'
    invitation=client.post('/api/v1/invites',headers=headers,json={}).json()
    payload={'token':invitation['token'],'username':'alice','display_name':'Alice','password':'member-password-123'}
    result=client.post('/api/v1/auth/register',json=payload)
    uid=result.json()['user']['id']
    assert 'httponly' in result.headers['set-cookie'].lower()
    assert client.post('/api/v1/auth/register',json={**payload,'username':'bob'}).status_code==410
    client.cookies.clear()
    token=client.post('/api/v1/auth/device-login',json={'username':'alice','password':payload['password']}).json()['token']
    mh={'Authorization':'Bearer '+token}
    assert client.post('/api/v1/invites',headers=mh,json={}).status_code==403
    assert client.patch('/api/v1/members/'+uid,headers=headers,json={'active':False}).status_code==200
    assert client.get('/api/v1/auth/me',headers=mh).status_code==401
    assert client.post('/api/v1/auth/device-login',json={'username':'alice','password':payload['password']}).status_code==401
    assert all(u['id']!='local' for u in client.get('/api/v1/members',headers=headers).json()['items'])
    assert client.patch('/api/v1/members/'+admin,headers=headers,json={'active':False}).status_code==409

def test_origin_and_logout(cloud):
    app,client,headers,admin=cloud
    result=client.post('/api/v1/auth/login',json={'username':'admin','password':'administrator-password'})
    assert result.status_code==200
    assert client.post('/api/v1/invites',json={},headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert client.post('/api/v1/auth/logout').status_code==200
    assert client.get('/api/v1/auth/me').status_code==401

def test_owner_write_comments_anchors_favorites_and_revocation(cloud):
    app,client,admin_headers,admin=cloud
    alice,ah=member(client,admin_headers,'alice')
    bob,bh=member(client,admin_headers,'bob')
    sid,rid,eid=seed(app,alice)
    sid2,rid2,eid2=seed(app,bob)
    assert client.get('/api/v1/sessions/'+sid,headers=bh).status_code==200
    assert client.patch('/api/v1/sessions/'+sid,headers=bh,json={'title':'oops'}).status_code==403
    assert client.delete('/api/v1/sessions/'+sid,headers=bh).status_code==403
    base='/api/v1/sessions/'+sid+'/comments'
    assert client.post(base,headers=bh,json={'text':'错误锚点','revision_id':rid,'event_id':eid2}).status_code==422
    comment=client.post(base,headers=bh,json={'text':'我来接续','revision_id':rid,'event_id':eid}).json()
    assert client.patch('/api/v1/comments/'+comment['id'],headers=ah,json={'text':'cannot'}).status_code==403
    assert client.patch('/api/v1/comments/'+comment['id'],headers=bh,json={'text':'已核对'}).status_code==200
    fav=client.post('/api/v1/favorites',headers=bh,json={'session_id':sid}).json()
    assert len(client.get('/api/v1/favorites',headers=bh).json()['items'])==1
    assert len(client.get('/api/v1/favorites',headers=ah).json()['items'])==0
    assert client.get('/api/v1/sessions?favorite=true',headers=bh).json()['items'][0]['id']==sid
    assert client.delete('/api/v1/sessions/'+sid,headers=ah).status_code==200
    assert client.get(base,headers=bh).status_code==404
    assert client.get('/api/v1/favorites',headers=bh).json()['items']==[]

def test_bounded_pages_and_packed_unicode_content(cloud):
    app,client,headers,owner=cloud
    sid,rid,eid=seed(app,owner,number=50,large=True)
    root='/api/v1/sessions/'+sid
    rounds=client.get(root+'/rounds?recent=3',headers=headers).json()
    assert [r['number'] for r in rounds['items']]==[47,48,49]
    result=client.get(root+'/events?limit=999',headers=headers)
    assert result.status_code==200
    assert len(result.content)<1024**2
    first=result.json();assert len(first['items'])<40 and first['next_cursor']
    second=client.get(root+'/events?cursor='+first['next_cursor'],headers=headers).json()
    assert second['items'][0]['seq']==first['items'][-1]['seq']+1
    text='完整中文内容🙂'*20000
    content_id=db.new_id();path=app.state.config.home/'contents'/'packed'
    data=text.encode();prefix=b'not-this';path.write_bytes(prefix+data+b'not-that')
    with app.state.engine.begin() as conn:
        conn.execute(db.contents.insert().values(id=content_id,revision_id=rid,event_id=eid,kind='text',path=str(path),bytes=len(data),metadata_json={'offset':len(prefix)}))
    assembled='';cursor='0'
    while cursor is not None:
        result=client.get('/api/v1/contents/'+content_id+'?cursor='+cursor,headers=headers).json()
        assembled+=result['text'];cursor=result['next_cursor']
    assert assembled==text
    assert client.delete(root,headers=headers).status_code==200
    assert client.get('/api/v1/contents/'+content_id,headers=headers).status_code==404

def test_queue_limits_and_no_cloud_paths(cloud):
    app,client,headers,owner=cloud
    sid,rid,eid=seed(app,owner)
    for _ in range(10):
        assert client.post('/api/v1/sessions/'+sid+'/exports',headers=headers,json={'format':'markdown'}).status_code==200
    assert client.post('/api/v1/sessions/'+sid+'/exports',headers=headers,json={'format':'markdown'}).status_code==429
    assert client.post('/api/v1/imports/path',headers=headers,json={'path':'/etc/passwd'}).status_code==404
    assert client.get('/api/v1/sources',headers=headers).status_code==404

def test_chunk_upload_resume_hash_and_idempotence(cloud):
    app,client,headers,owner=cloud
    payload=b'archive-shaped-test-only'*100
    sha=hashlib.sha256(payload).hexdigest()
    desc={'filename':'session.csh','total_bytes':len(payload),'sha256':sha,'session_key':'native-id','device_id':'laptop'}
    started=client.post('/api/v1/uploads',headers=headers,json=desc)
    assert started.status_code==200,started.text
    uid=started.json()['id'];url='/api/v1/uploads/'+uid
    assert client.post('/api/v1/uploads',headers=headers,json=desc).json()['id']==uid
    other={**desc,'session_key':'another'}
    assert client.post('/api/v1/uploads',headers=headers,json=other).status_code==429
    assert client.put(url+'/chunks/0',headers={**headers,'X-Chunk-SHA256':'a'*64},content=payload).status_code==422
    assert client.post(url+'/complete',headers=headers,json={}).status_code==409
    assert client.put(url+'/chunks/0',headers={**headers,'X-Chunk-SHA256':sha},content=payload).status_code==200
    assert client.get(url,headers=headers).json()['received_chunks']==[0]
    result=client.post(url+'/complete',headers=headers,json={})
    assert result.status_code==200,result.text
    duplicate=client.post(url+'/complete',headers=headers,json={})
    assert duplicate.json()==result.json()
    assert client.get('/api/v1/jobs/'+result.json()['job']['id'],headers=headers).status_code==200
    member_id,mh=member(client,headers,'reader')
    assert client.get(url,headers=mh).status_code==404
    assert client.get('/api/v1/jobs/'+result.json()['job']['id'],headers=mh).status_code==404

def test_disk_guard(cloud):
    app,client,headers,owner=cloud
    app.state.config.min_free_bytes=10**20
    desc={'filename':'x','total_bytes':10,'sha256':'a'*64,'session_key':'s','device_id':'d'}
    assert client.post('/api/v1/uploads',headers=headers,json=desc).status_code==507

def test_local_requires_launch_token_and_streams_import(tmp_path):
    config=Config(mode='local',home=tmp_path,local_token='local-secret',worker_external=True,min_free_bytes=0,min_free_ratio=0)
    app=create_app(config)
    with TestClient(app) as client:
        assert client.get('/api/v1/sessions').status_code==401
        headers={'Authorization':'Bearer local-secret'}
        assert client.get('/api/v1/auth/me',headers=headers).json()['id']=='local'
        response=client.post('/api/v1/imports',headers=headers,files={'file':('中文.jsonl',b'{"role":"user","text":"example"}\n','application/json')})
        assert response.status_code==200,response.text
        job=response.json()['job']
        assert job['state']=='queued' and job['kind']=='ingest'
        assert client.post('/api/v1/jobs/'+job['id']+'/pause',headers=headers).json()['job']['state']=='paused'
        assert client.post('/api/v1/jobs/'+job['id']+'/resume',headers=headers).json()['job']['state']=='queued'
        assert client.post('/api/v1/jobs/'+job['id']+'/cancel',headers=headers).json()['job']['state']=='cancelled'
        assert client.post('/api/v1/jobs/'+job['id']+'/retry',headers=headers).json()['job']['state']=='queued'

def test_import_idempotence_and_changed_native_path_revision(tmp_path):
    from hub.ingest import ingest_path
    cfg=Config(mode='local',home=tmp_path/'data',local_token='launch',worker_external=True,min_free_bytes=0,min_free_ratio=0)
    app=create_app(cfg)
    headers={'Authorization':'Bearer launch'}
    raw=b'{"role":"user","message":{"content":[{"type":"text","text":"first"}]}}\n'
    source=tmp_path/'same-name.jsonl';source.write_bytes(raw)
    with TestClient(app) as client:
        first=client.post('/api/v1/imports/path',headers=headers,json={'path':str(source)}).json()
        repeat=client.post('/api/v1/imports/path',headers=headers,json={'path':str(source)}).json()
        assert repeat['session_id']==first['session_id'] and repeat['job']['id']==first['job']['id']
        ingest_path(cfg,first['job']['id'])
        original=client.get('/api/v1/sessions/'+first['session_id'],headers=headers).json()['current_revision']
        duplicate=client.post('/api/v1/imports',headers=headers,files={'file':('other-name.jsonl',raw)}).json()
        assert duplicate['session_id']==first['session_id'] and duplicate['job']['state']=='succeeded'
        source.write_bytes(raw+b'{"role":"assistant","message":{"content":[{"type":"text","text":"next"}]}}\n')
        changed=client.post('/api/v1/imports/path',headers=headers,json={'path':str(source)}).json()
        assert changed['session_id']==first['session_id'] and changed['job']['id']!=first['job']['id']
        ingest_path(cfg,changed['job']['id'])
        current=client.get('/api/v1/sessions/'+first['session_id'],headers=headers).json()
        assert current['current_revision']!=original and current['event_count']==2
        other=tmp_path/'folder'/'same-name.jsonl';other.parent.mkdir();other.write_bytes(b'{"role":"user","message":{"content":[{"type":"text","text":"unrelated"}]}}\n')
        distinct=client.post('/api/v1/imports/path',headers=headers,json={'path':str(other)}).json()
        assert distinct['session_id']!=first['session_id']
