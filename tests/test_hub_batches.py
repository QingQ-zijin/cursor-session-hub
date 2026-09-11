import json
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from hub import db
from hub.config import Config
from hub.api import create_app
import httpx

def setup(tmp_path,count=65):
    app=create_app(Config(home=tmp_path,local_token='batch-test',worker_external=True,min_free_bytes=0,min_free_ratio=0))
    with app.state.engine.begin() as c:
        for i in range(count):
            ident=f'source-{i:04}';c.execute(db.sources.insert().values(id=ident,owner_id='local',title=f'Session {i}',native_id=str(i),project='workspace-A',path='fixture',source_kind='cursor_ide'))
            c.execute(db.source_catalog.insert().values(source_id=ident,scan_id='snapshot',in_sidebar=True,named=i!=0,is_subagent=False))
        for ident,kind,sidebar,named in [('alternate','cursor_jsonl',True,True),('orphan','cursor_ide',False,True),('subagent','cursor_ide',False,True)]:
            c.execute(db.sources.insert().values(id=ident,owner_id='local',title=ident,native_id='1' if ident=='alternate' else ident,project='workspace-A',path='fixture',source_kind=kind))
            c.execute(db.source_catalog.insert().values(source_id=ident,scan_id='snapshot',in_sidebar=sidebar,named=named,is_subagent=ident=='subagent'))
    return app

def finish_scan(app):
    with app.state.engine.begin() as c:c.execute(db.jobs.update().where(db.jobs.c.kind=='scan').values(state='succeeded',result_json={'scan_id':'snapshot'}))
    app.state.batch_controller.tick()

def test_batch_exceeds_page_and_queue_limits_but_only_feeds_one_job(tmp_path):
    app=setup(tmp_path)
    with TestClient(app) as c:
        h={'Authorization':'Bearer batch-test'}
        assert c.post('/api/v1/source-batches',json={}).status_code==401
        started=c.post('/api/v1/source-batches',headers=h,json={});assert started.status_code==200
        assert c.post('/api/v1/source-batches',headers=h,json={}).status_code==409
        finish_scan(app)
        assert c.get('/api/v1/source-batches',headers=h).json()['items'][0]['total']==64
        for _ in range(64):
            app.state.batch_controller.tick()
            with app.state.engine.begin() as conn:
                active=conn.execute(select(db.jobs).where(db.jobs.c.state.in_(('queued','running')))).mappings().all();assert len(active)==1
                conn.execute(db.jobs.update().where(db.jobs.c.id==active[0]['id']).values(state='succeeded'))
            app.state.batch_controller.tick()
        app.state.batch_controller.tick()
        state=c.get('/api/v1/source-batches',headers=h).json()['items'][0];assert state['state']=='succeeded' and state['completed']==64
        first=c.get('/api/v1/source-batches/'+state['id']+'/items',headers=h).json();assert len(first['items'])==50 and first['next_cursor']
        second=c.get('/api/v1/source-batches/'+state['id']+'/items?cursor='+first['next_cursor'],headers=h).json();assert len(second['items'])==14

def test_workspace_batch_pause_resume_restart_failure_retry_cancel(tmp_path):
    app=setup(tmp_path,3);h={'Authorization':'Bearer batch-test'}
    with TestClient(app) as c:
        ident=c.post('/api/v1/source-batches',headers=h,json={'project':'workspace-A'}).json()['id'];finish_scan(app)
        assert c.get('/api/v1/source-batches',headers=h).json()['items'][0]['total']==3
        app.state.batch_controller.tick()
        assert c.patch('/api/v1/source-batches/'+ident,headers=h,json={'action':'pause'}).status_code==200
        app.state.batch_controller.tick()
        assert c.get('/api/v1/source-batches',headers=h).json()['items'][0]['state']=='paused'
        assert c.patch('/api/v1/source-batches/'+ident,headers=h,json={'action':'resume'}).status_code==200
        for _ in range(3):
            app.state.batch_controller.tick()
            with app.state.engine.begin() as conn:conn.execute(db.jobs.update().where(db.jobs.c.kind=='ingest',db.jobs.c.state=='queued').values(state='failed',error='test failure'))
            app.state.batch_controller.tick()
        app.state.batch_controller.tick()
        state=c.get('/api/v1/source-batches',headers=h).json()['items'][0];assert state['failed']==3 and state['state']=='completed_with_errors'
        assert c.patch('/api/v1/source-batches/'+ident,headers=h,json={'action':'retry'}).status_code==200
    reopened=create_app(app.state.config)
    with TestClient(reopened) as c:
        reopened.state.batch_controller.tick()
        assert c.get('/api/v1/source-batches',headers=h).json()['items'][0]['state']=='indexing'
        assert c.patch('/api/v1/source-batches/'+ident,headers=h,json={'action':'cancel'}).status_code==200
        assert c.get('/api/v1/source-batches',headers=h).json()['items'][0]['state']=='cancelled'

def test_server_batch_pins_account_and_chains_index_then_sync(tmp_path,monkeypatch):
    from hub import remote
    app=setup(tmp_path,2);h={'Authorization':'Bearer batch-test'}
    monkeypatch.setattr(remote,'_credentials',lambda config:('https://team.example',{'Authorization':'Bearer synthetic-token'}))
    monkeypatch.setattr(remote,'_read_config',lambda config:{'device_id':'test-device'})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':'team-member'}))))
    with TestClient(app) as c:
        response=c.post('/api/v1/source-batches',headers=h,json={'destination':'server'});assert response.status_code==200
        finish_scan(app);app.state.batch_controller.tick()
        with app.state.engine.begin() as conn:
            job=conn.execute(select(db.jobs).where(db.jobs.c.kind=='ingest')).mappings().one()
            conn.execute(db.sessions.update().where(db.sessions.c.id==job['session_id']).values(current_revision='test-revision'))
            conn.execute(db.jobs.update().where(db.jobs.c.id==job['id']).values(state='succeeded'))
        app.state.batch_controller.tick()
        with app.state.engine.connect() as conn:
            sync=conn.execute(select(db.jobs).where(db.jobs.c.kind=='sync')).mappings().one()
            assert sync['payload_json']['remote_user_id']=='team-member' and sync['payload_json']['device_id']=='test-device'
            assert sync['payload_json']['revision_id']=='test-revision'
        assert 'synthetic-token' not in c.get('/api/v1/source-batches',headers=h).text
        with app.state.engine.begin() as conn:conn.execute(db.jobs.update().where(db.jobs.c.id==sync['id']).values(state='failed',error='connection lost',checkpoint_json={'upload_id':'existing-upload','bundle_ready':True}))
        app.state.batch_controller.tick();app.state.batch_controller.tick()
        ident=response.json()['id'];assert c.patch('/api/v1/source-batches/'+ident,headers=h,json={'action':'retry'}).status_code==200
        app.state.batch_controller.tick()
        with app.state.engine.connect() as conn:
            batch=conn.execute(select(db.source_batches).where(db.source_batches.c.id==ident)).mappings().one();assert batch['child_job_id']==sync['id'] and batch['state']=='syncing'
            checkpoint=conn.execute(select(db.jobs.c.checkpoint_json).where(db.jobs.c.id==sync['id'])).scalar_one();assert checkpoint['upload_id']=='existing-upload'
