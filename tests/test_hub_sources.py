"""Discovery must use the key index and never materialize oversized metadata."""
import json
import sqlite3

from hub import sources


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
