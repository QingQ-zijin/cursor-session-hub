import json,zipfile
from sqlalchemy import select,func
from tests.test_hub_api import cloud,member,seed
from hub import db
from hub.bundles import build_bundle,export_job

def test_round_trash_permissions_notes_restore_and_revision_scope(cloud):
    app,c,admin,uid=cloud;owner,oh=member(c,admin,'round_owner');other,mh=member(c,admin,'round_reader')
    sid,rid,_=seed(app,owner);url=f'/api/v1/sessions/{sid}/rounds/2';body={'revision_id':rid,'deleted':True,'note':'重复尝试，可恢复'}
    assert c.patch(url,json=body).status_code==401
    assert c.patch(url,headers=mh,json=body).status_code==403
    assert c.patch(url,headers=oh,json=body).status_code==200
    rows=c.get(f'/api/v1/sessions/{sid}/rounds',headers=mh).json()['items'];assert 2 not in [r['number'] for r in rows]
    trash=c.get(f'/api/v1/sessions/{sid}/rounds?trash=true',headers=mh).json()['items'];assert len(trash)==1 and trash[0]['note']==body['note']
    assert not c.get(f'/api/v1/sessions/{sid}/events?round=2',headers=mh).json()['items']
    assert c.patch(url,headers=admin,json={**body,'note':'管理员更新说明'}).status_code==200
    assert c.patch(url,headers=mh,json={**body,'deleted':False}).status_code==403
    assert c.patch(url,headers=oh,json={**body,'revision_id':'missing'}).status_code==404
    with app.state.engine.connect() as conn:assert conn.execute(select(func.count()).select_from(db.events).where(db.events.c.revision_id==rid)).scalar_one()==5
    assert c.patch(url,headers=oh,json={**body,'deleted':False}).status_code==200
    assert len(c.get(f'/api/v1/sessions/{sid}/events?round=2',headers=mh).json()['items'])==1
    assert c.get('/api/v1/ai/settings',headers=admin).status_code==404
    assert c.post('/api/v1/ai/messages',headers=admin,json={}).status_code in (404,405)

def test_trash_pagination_export_and_sync_exclude_deleted_text(cloud,tmp_path):
    app,c,admin,uid=cloud;sid,rid,_=seed(app,uid,number=105)
    for number in (1,100):assert c.patch(f'/api/v1/sessions/{sid}/rounds/{number}',headers=admin,json={'revision_id':rid,'deleted':True,'note':'test'}).status_code==200
    page=c.get(f'/api/v1/sessions/{sid}/rounds?limit=100',headers=admin).json();assert len(page['items'])==100 and page['next_cursor']
    more=c.get(f'/api/v1/sessions/{sid}/rounds?cursor='+page['next_cursor'],headers=admin).json()['items'];assert len(more)==3
    with app.state.engine.begin() as conn:
        conn.execute(db.events.update().where(db.events.c.revision_id==rid,db.events.c.round_number==1).values(event_json={'kind':'user','text':'DELETED-UNIQUE'}))
    config=app.state.config;path=tmp_path/'filtered.zip';build_bundle(config,sid,rid,path)
    with zipfile.ZipFile(path) as bundle:
        text=bundle.read('events.jsonl').decode();manifest=json.loads(bundle.read('manifest.json'))
        assert 'DELETED-UNIQUE' not in text and manifest['event_count']==len(text.splitlines())
    for fmt,suffix in [('markdown','.md'),('html','.html'),('pdf','.pdf')]:
        job=c.post(f'/api/v1/sessions/{sid}/exports',headers=admin,json={'format':fmt}).json()['job'];export_job(config,job['id'])
        file=config.home/'exports'/(job['id']+suffix)
        if fmt=='pdf':
            from pypdf import PdfReader
            text=''.join(p.extract_text() for p in PdfReader(file).pages)
        else:text=file.read_text(encoding='utf-8')
        assert 'DELETED-UNIQUE' not in text
    assert c.patch(f'/api/v1/sessions/{sid}/rounds/1',headers=admin,json={'revision_id':rid,'deleted':False}).status_code==200
    build_bundle(config,sid,rid,tmp_path/'restored.zip')
    with zipfile.ZipFile(tmp_path/'restored.zip') as bundle:assert b'DELETED-UNIQUE' in bundle.read('events.jsonl')
