import sqlite3,json
import pytest
from sqlalchemy import select
from hub.sources import sidebar_catalog,discover_sources
from hub.config import Config
from hub import db

def make_cursor(tmp_path):
    ide=tmp_path/'Cursor/User/globalStorage/state.vscdb';ide.parent.mkdir(parents=True)
    with sqlite3.connect(ide) as c:
        c.execute('create table ItemTable(key text primary key,value blob)');c.execute('create table cursorDiskKV(key text primary key,value blob)')
        c.execute('create table composerHeaders(composerId text primary key,isArchived integer,isSubagent integer,value blob)')
        projects=[{'id':'project','name':'Research','workspace':{'uri':{'fsPath':'D:/Research'}}},{'id':'empty','name':'Empty','workspace':{'uri':{'fsPath':'D:/Empty'}}},{'id':'archived','name':'Archived','isArchived':True,'workspace':{'uri':{'fsPath':'D:/Archived'}}}]
        members={n:'project' for n in ['visible','unnamed','archived','child']}
        c.executemany('insert into ItemTable values(?,?)',[('glass.localAgentProjects.v1',json.dumps(projects)),('glass.localAgentProjectMembership.v1',json.dumps(members))])
        for name in ['visible','unnamed','archived','child','orphan']:
            meta={'name':'Original title' if name!='unnamed' else '', 'composerId':name,'workspaceIdentifier':{'path':'WRONG-PATH'}}
            c.execute('insert into cursorDiskKV values(?,?)',('composerData:'+name,json.dumps(meta)))
            c.execute('insert into composerHeaders values(?,?,?,?)',(name,int(name=='archived'),int(name=='child'),json.dumps({'name':meta['name']})))
    return ide,tmp_path/'projects',tmp_path/'chats'

def test_modern_sidebar_membership_beats_guessed_paths_and_excludes_archived_children(tmp_path):
    paths=make_cursor(tmp_path);catalog,projects=sidebar_catalog(paths[0]);assert catalog['visible']['project']=='D:/Research'
    assert catalog['child']['is_subagent'] and not catalog['child']['in_sidebar'];assert 'archived' not in catalog and 'orphan' not in catalog
    assert [p['label'] for p in projects]==['Research','Empty']
    cfg=Config(home=tmp_path/'library');result=discover_sources(cfg,paths);engine=db.get_engine(cfg)
    with engine.connect() as c:
        rows=c.execute(select(db.sources.c.native_id,db.source_catalog.c.in_sidebar,db.source_catalog.c.named).join(db.source_catalog,db.source_catalog.c.source_id==db.sources.c.id)).all()
        assert {r.native_id for r in rows if r.in_sidebar and r.named}=={'visible'}
        assert {r['project'] for r in c.execute(select(db.cursor_projects)).mappings()}=={'D:/Research','D:/Empty'}
    engine.dispose()

@pytest.mark.parametrize('custom',[False,True])
def test_index_publication_keeps_sidebar_workspace_and_native_name(tmp_path,custom):
    from hub.ingest import ingest_path
    cfg=Config(home=tmp_path/'library');engine=db.get_engine(cfg);db.init_db(engine)
    path=tmp_path/'data.jsonl';path.write_text(json.dumps({'role':'user','message':{'content':[{'type':'text','text':'body cannot rename sidebar'}]}})+'\n',encoding='utf-8')
    sid,source,jid=db.new_id(),db.new_id(),db.new_id()
    with engine.begin() as c:
        c.execute(db.sources.insert().values(id=source,path=str(path),source_kind='cursor_jsonl',project='D:/Official',title='Native sidebar name',metadata_json={'in_sidebar':True},session_id=sid))
        c.execute(db.sessions.insert().values(id=sid,owner_id='local',source_id=source,source_kind='cursor_jsonl',title='My title' if custom else 'Old title',project='Wrong path',metadata_json={'custom_title':custom}))
        c.execute(db.jobs.insert().values(id=jid,session_id=sid,owner_id='local',kind='ingest',state='running',payload_json={'path':str(path),'source_id':source,'source_kind':'cursor_jsonl'}))
    ingest_path(cfg,jid)
    with engine.connect() as c:
        s=c.execute(select(db.sessions).where(db.sessions.c.id==sid)).mappings().one();assert s['project']=='D:/Official'
        assert s['title']==('My title' if custom else 'Native sidebar name')
    engine.dispose()
