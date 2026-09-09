"""Discovery must use the key index and never materialize oversized metadata."""
import json
import sqlite3

from hub import sources


def test_workspace_metadata_names_transcript_and_preserves_custom_titles(tmp_path):
    from hub import db
    from hub.config import Config
    from sqlalchemy import select
    storage = tmp_path / 'User'
    ide = storage / 'globalStorage/state.vscdb'; ide.parent.mkdir(parents=True)
    workspace = storage / 'workspaceStorage/hash'; workspace.mkdir(parents=True)
    (workspace / 'workspace.json').write_text(json.dumps({'folder': 'file:///D%3A/Research/%E9%A1%B9%E7%9B%AE'}))
    native = 'f0000000-0000-4000-8000-000000000001'
    with sqlite3.connect(workspace / 'state.vscdb') as connection:
        connection.execute('CREATE TABLE ItemTable(key TEXT PRIMARY KEY,value BLOB)')
        connection.execute('INSERT INTO ItemTable VALUES(?,?)', ('composer.composerData', json.dumps({'allComposers': [{'composerId': native, 'name': 'Cursor 原始标题'}]})))
    with sqlite3.connect(ide) as connection:
        connection.execute('CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)')
        connection.execute('INSERT INTO cursorDiskKV VALUES(?,?)', ('composerData:' + native, '{}'))
    projects = tmp_path / 'projects'; transcripts = projects / 'D-Research-project/agent-transcripts'; transcripts.mkdir(parents=True)
    (transcripts / (native + '.jsonl')).write_text('{}\n')
    rows = list(sources.iter_sources((ide, projects, tmp_path / 'no-chats')))
    assert len(rows) == 2
    assert all(row['title'] == 'Cursor 原始标题' and row['project'] == 'D:/Research/项目' for row in rows)
    cfg = Config(home=tmp_path / 'library', local_token='test'); cfg.prepare()
    sources.discover_sources(cfg, (ide, projects, tmp_path / 'no-chats'))
    engine = db.get_engine(cfg)
    with engine.begin() as connection:
        source = connection.execute(select(db.sources).limit(1)).mappings().one()
        sid = db.new_id()
        connection.execute(db.sessions.insert().values(id=sid, owner_id='local', title='我自己的标题', metadata_json={'custom_title': True}))
        connection.execute(db.sources.update().where(db.sources.c.id == source['id']).values(session_id=sid))
    sources.discover_sources(cfg, (ide, projects, tmp_path / 'no-chats'))
    with engine.connect() as connection:
        assert connection.execute(select(db.sessions.c.title).where(db.sessions.c.id == sid)).scalar_one() == '我自己的标题'
    engine.dispose()


def test_source_paging_workspace_filter_and_alternates(tmp_path):
    from fastapi.testclient import TestClient
    from hub.api import create_app
    from hub.config import Config
    from hub import db
    config = Config(home=tmp_path/'local', local_token='test', worker_external=True)
    app = create_app(config)
    with TestClient(app) as client:
        with app.state.engine.begin() as connection:
            for ident, native, project, kind in [('a','n1','B','cursor_ide'),('b','n2','A','cursor_ide'),('c','n1','B','cursor_jsonl'),('d','n3','A','cursor_jsonl')]:
                connection.execute(db.sources.insert().values(id=ident, owner_id='local', native_id=native, project=project, source_kind=kind, path='/test/'+ident))
        headers={'Authorization':'Bearer test'}
        first=client.get('/api/v1/sources?limit=1',headers=headers).json()
        assert first['items'][0]['id']=='b'
        second=client.get('/api/v1/sources?limit=1&cursor='+first['next_cursor'],headers=headers).json()
        assert second['items'][0]['id']=='d'
        assert [row['id'] for row in client.get('/api/v1/sources?project=B',headers=headers).json()['items']]==['a']
        assert len(client.get('/api/v1/sources?include_alternates=true',headers=headers).json()['items'])==4
        assert client.get('/api/v1/sources/workspaces',headers=headers).json()['items']==[{'project':'A','count':2},{'project':'B','count':1}]


def test_ide_discovery_uses_index_and_keeps_oversized_source(tmp_path, monkeypatch):
    path = tmp_path / 'state.vscdb'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)')
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,?)', ('composerData:a-normal', json.dumps({'name': 'Readable source'})))
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,zeroblob(?))', ('composerData:z-oversized', 8 * 1024**2 + 1))
        # These unrelated/boundary keys must never be read by discovery.
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,zeroblob(?))', ('bubbleId:unrelated:large', 16 * 1024**2))
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,?)', ('composerData;outside', '{}'))
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,?)', ('composerData9outside', '{}'))
    connect = sources.common.connect_ro
    value_fetches, range_plans = [], []
    class GuardedConnection:
        def __init__(self, real):
            self.real = real
        def execute(self, sql, parameters=()):
            if sql.lower().startswith('select value'):
                value_fetches.append(parameters[0])
                assert parameters[0] == 'composerData:a-normal', 'Oversized/unrelated value was materialized'
            if sql.lower().startswith('select key'):
                range_plans.extend(self.real.execute('EXPLAIN QUERY PLAN ' + sql, parameters).fetchall())
            return self.real.execute(sql, parameters)
        def close(self):
            self.real.close()
    monkeypatch.setattr(sources.common, 'connect_ro', lambda p: GuardedConnection(connect(p)))
    result = list(sources.iter_sources((path, tmp_path / 'no-projects', tmp_path / 'no-chats')))
    assert {row['native_id'] for row in result} == {'a-normal', 'z-oversized'}
    assert next(row for row in result if row['native_id'] == 'a-normal')['title'] == 'Readable source'
    oversized = next(row for row in result if row['native_id'] == 'z-oversized')
    assert oversized['status'] == 'oversized_metadata'
    assert oversized['source_kind'] == 'cursor_ide'
    assert value_fetches == ['composerData:a-normal']
    assert range_plans and all('SEARCH' in row[3] and 'INDEX' in row[3] for row in range_plans)


def test_ide_size_guard_counts_utf8_bytes_not_characters(tmp_path, monkeypatch):
    path = tmp_path / 'state.vscdb'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value TEXT)')
        # Three-byte characters exceed the byte limit while char count does not.
        conn.execute('INSERT INTO cursorDiskKV VALUES(?,?)', ('composerData:unicode-large', '字' * (3 * 1024**2)))
    connect = sources.common.connect_ro
    class NoValueFetch:
        def __init__(self, real):
            self.real = real
        def execute(self, sql, parameters=()):
            assert not sql.lower().startswith('select value'), 'UTF-8 byte limit was bypassed'
            return self.real.execute(sql, parameters)
        def close(self):
            self.real.close()
    monkeypatch.setattr(sources.common, 'connect_ro', lambda p: NoValueFetch(connect(p)))
    result = list(sources.iter_sources((path, tmp_path / 'no-projects', tmp_path / 'no-chats')))
    assert len(result) == 1
    assert result[0]['native_id'] == 'unicode-large'
    assert result[0]['status'] == 'oversized_metadata'
