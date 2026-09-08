"""Actual ASGI request streams, deliberately omitting Content-Length."""
import asyncio
import hashlib
import json
import pytest
from sqlalchemy import select
from hub import auth,db
from hub.api import create_app
from hub.config import Config

async def stream_request(app,path,chunks,method='POST',headers=None):
    sent=[];read=0;count=0;finished=asyncio.Event()
    chunks=iter(chunks);exhausted=False
    async def receive():
        nonlocal read,count,exhausted
        if exhausted:
            await finished.wait()
            return {'type':'http.disconnect'}
        try:
            chunk=next(chunks);read+=len(chunk);count+=1
            return {'type':'http.request','body':chunk,'more_body':True}
        except StopIteration:
            exhausted=True
            return {'type':'http.request','body':b'','more_body':False}
    async def send(message):
        sent.append(message)
        if message['type']=='http.response.body' and not message.get('more_body',False):finished.set()
    scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','root_path':'','headers':[(k.lower().encode(),v.encode()) for k,v in (headers or {}).items()],'client':('127.0.0.1',1234),'server':('testserver',80)}
    await asyncio.wait_for(app(scope,receive,send),timeout=10)
    status=next(m['status'] for m in sent if m['type']=='http.response.start')
    body=b''.join(m.get('body',b'') for m in sent if m['type']=='http.response.body')
    return status,body,read,count

@pytest.fixture
def local(tmp_path):
    return create_app(Config(mode='local',home=tmp_path,worker_external=True,local_token='launch',min_free_bytes=0,min_free_ratio=0))

def test_chunked_json_stops_at_one_mib_without_header(local):
    chunks=[b'{"ignored":"']+[b'x'*65536 for _ in range(80)]+[b'"}']
    status,body,read,count=asyncio.run(stream_request(local,'/api/v1/sessions',chunks,headers={'Content-Type':'application/json','Authorization':'Bearer launch'}))
    # This nonexistent method intentionally does not parse a body. The
    # existing JSON route below must consume it and hit the streaming cap.
    assert status==405 and read==0
    status,body,read,count=asyncio.run(stream_request(local,'/api/v1/remote/preview',chunks,headers={'Content-Type':'application/json','Authorization':'Bearer launch'}))
    assert status==413 and 'detail' in json.loads(body)
    assert read<=1024**2+65536 and count<25

def test_cloud_rejects_local_multipart_before_reading_any_bytes(tmp_path):
    app=create_app(Config(mode='cloud',home=tmp_path,worker_external=True))
    chunks=[b'x'*65536 for _ in range(100)]
    for path in ('/api/v1/imports','/api/v1/imports/path','/api/v1/sources/scan','/api/v1/sources/discovered/index'):
        status,body,read,count=asyncio.run(stream_request(app,path,chunks,headers={'Content-Type':'multipart/form-data; boundary=abc'}))
        assert status==404 and read==0 and count==0

def test_chunked_local_multipart_closes_tempfile_on_limit(local,monkeypatch):
    import starlette.formparsers
    # Keep this test small; production defaults retain the 512 MiB file cap.
    local.state.config.max_package_bytes=1024
    opened=[];original=starlette.formparsers.SpooledTemporaryFile
    def recording(*args,**kwargs):
        file=original(*args,**kwargs);opened.append(file);return file
    monkeypatch.setattr(starlette.formparsers,'SpooledTemporaryFile',recording)
    prefix=b'--abc\r\nContent-Disposition: form-data; name="file"; filename="safe.jsonl"\r\nContent-Type: application/octet-stream\r\n\r\n'
    chunks=[prefix]+[b'x'*65536 for _ in range(80)]+[b'\r\n--abc--\r\n']
    status,body,read,count=asyncio.run(stream_request(local,'/api/v1/imports',chunks,headers={'Content-Type':'multipart/form-data; boundary=abc','Authorization':'Bearer launch'}))
    assert status==413 and read<1024**2+1024+65536
    assert opened and all(file.closed for file in opened)
    with local.state.engine.connect() as conn:assert not conn.execute(select(db.jobs.c.id)).first()

def test_chunked_upload_stops_at_four_mib_and_deletes_partial(tmp_path):
    cfg=Config(mode='cloud',home=tmp_path,worker_external=True,min_free_bytes=0,min_free_ratio=0)
    app=create_app(cfg)
    owner=auth.bootstrap_admin(app.state.engine,'admin','Admin','long-test-password')
    ident=db.new_id()
    with app.state.engine.begin() as conn:
        token=auth.issue_token(conn,owner,'device')
        conn.execute(db.uploads.insert().values(id=ident,owner_id=owner,filename='sample.csh',total_bytes=5*1024**2,sha256='a'*64,session_key='s',device_id='d'))
    (cfg.home/'uploads'/ident).mkdir()
    chunks=[b'x'*65536 for _ in range(100)]
    status,body,read,count=asyncio.run(stream_request(app,f'/api/v1/uploads/{ident}/chunks/0',chunks,method='PUT',headers={'Authorization':'Bearer '+token,'Content-Type':'application/octet-stream','X-Chunk-SHA256':'b'*64}))
    assert status==413 and read==4*1024**2+65536
    assert not list((cfg.home/'uploads'/ident).iterdir())

def test_small_chunked_json_and_exact_upload_chunk_are_accepted(tmp_path):
    cfg=Config(mode='cloud',home=tmp_path,worker_external=True,min_free_bytes=0,min_free_ratio=0,cookie_secure=False)
    app=create_app(cfg)
    owner=auth.bootstrap_admin(app.state.engine,'admin','Admin','long-test-password')
    body=json.dumps({'username':'admin','password':'long-test-password'}).encode()
    status,response,read,_=asyncio.run(stream_request(app,'/api/v1/auth/device-login',[body[:15],body[15:]],headers={'Content-Type':'application/json'}))
    assert status==200 and read==len(body)
    token=json.loads(response)['token'];ident=db.new_id();data=b'x'*(4*1024**2)
    with app.state.engine.begin() as conn:conn.execute(db.uploads.insert().values(id=ident,owner_id=owner,filename='sample.csh',total_bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),session_key='s',device_id='d'))
    (cfg.home/'uploads'/ident).mkdir()
    status,_,read,_=asyncio.run(stream_request(app,f'/api/v1/uploads/{ident}/chunks/0',[data[i:i+65536] for i in range(0,len(data),65536)],method='PUT',headers={'Authorization':'Bearer '+token,'X-Chunk-SHA256':hashlib.sha256(data).hexdigest()}))
    assert status==200 and read==len(data)

def test_declared_oversize_rejected_without_reading(local):
    status,_,read,_=asyncio.run(stream_request(local,'/api/v1/remote/preview',[b'x'],headers={'Content-Length':str(1024**2+1),'Content-Type':'application/json'}))
    assert status==413 and read==0
