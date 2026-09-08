import json
import sqlite3
from pathlib import Path
import zipfile
import pytest
from sqlalchemy import select, func
from hub.config import Config
from hub import db
from hub.ingest import ingest_path, iter_jsonl_records, iter_cli_events, normalize_jsonl, hydrate_event, tokenize, MAX_LINE
from hub.bundles import build_bundle, import_bundle, export_job


def installation(tmp_path, name='local'):
    config = Config(home=tmp_path / name, local_token='test-token')
    engine = db.get_engine(config)
    db.init_db(engine)
    return config, engine


def queue(engine, path=None, session_id=None, kind='ingest', **payload):
    with engine.begin() as conn:
        if not session_id:
            session_id = db.new_id()
            conn.execute(db.sessions.insert().values(id=session_id, owner_id='local', title='示例会话', native_id='sample'))
        job_id = db.new_id()
        if path:
            payload['path'] = str(path)
        conn.execute(db.jobs.insert().values(id=job_id, session_id=session_id, owner_id='local', kind=kind, state='running', payload_json=payload))
    return session_id, job_id


def write_records(path, texts=('你好世界', '结论：可以实现')):
    records = [{'role': 'user' if i % 2 == 0 else 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}} for i, text in enumerate(texts)]
    path.write_text(''.join(json.dumps(rec, ensure_ascii=False) + '\n' for rec in records), encoding='utf-8')
    return records


def full_events(engine, session_id):
    with engine.connect() as conn:
        revision_id = conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == session_id)).scalar_one()
        return [hydrate_event(conn, row[0]) for row in conn.execute(select(db.events.c.event_json).where(db.events.c.revision_id == revision_id).order_by(db.events.c.seq))]


def test_bounded_index_and_exact_readback(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    texts = ['你好世界', '长结论' * 50000]
    write_records(path, texts)
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    events = full_events(engine, sid)
    assert [e['blocks'][0]['text'] for e in events] == texts
    with engine.connect() as conn:
        rows = conn.execute(select(db.events.c.event_json, db.events.c.byte_size)).all()
        assert max(r[1] for r in rows) < 128 * 1024
        assert rows[1][0]['blocks'][0]['has_more']
        assert conn.execute(select(db.search_tokens.c.token).where(db.search_tokens.c.token == '你好')).first()
        content = conn.execute(select(db.contents).where(db.contents.c.kind == 'event')).mappings().all()
        assert len({r['path'] for r in content}) == 1
        assert content[1]['metadata_json']['offset'] > 0


def test_incremental_append_rewrite_and_duplicate(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    write_records(path)
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    with engine.connect() as conn:
        first = conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one()
    with path.open('a', encoding='utf-8') as out:
        out.write(json.dumps({'role': 'user', 'message': {'content': '下一步'}}) + '\n')
    _, jid = queue(engine, path, sid)
    ingest_path(config, jid)
    assert len(full_events(engine, sid)) == 3
    with engine.connect() as conn:
        rev = conn.execute(select(db.revisions).where(db.revisions.c.id == conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one())).mappings().one()
        assert rev['metadata_json']['incremental'] is True
        assert rev['id'] != first
    _, duplicate = queue(engine, path, sid)
    ingest_path(config, duplicate)
    with engine.connect() as conn:
        assert conn.execute(select(db.jobs.c.result_json).where(db.jobs.c.id == duplicate)).scalar_one()['unchanged']
    write_records(path, ('改写', '新结论'))
    _, jid = queue(engine, path, sid)
    ingest_path(config, jid)
    assert full_events(engine, sid)[0]['blocks'][0]['text'] == '改写'


def test_corrupt_unknown_oversized_and_incomplete_preserved(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'bad.jsonl'
    with path.open('wb') as out:
        out.write(b'not json\n{"vendor_future":true}\n')
        out.write(b'x' * (MAX_LINE + 512) + b'\n')
        out.write(b'{"role":')
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    events = full_events(engine, sid)
    assert len(events) == 4
    assert all(e['_diagnostic'] for e in events)
    with engine.connect() as conn:
        status = conn.execute(select(db.sessions.c.status).where(db.sessions.c.id == sid)).scalar_one()
        assert status == 'ready_with_diagnostics'
        paths = conn.execute(select(db.contents.c.path).where(db.contents.c.kind == 'raw')).scalars().all()
        assert len(paths) == 3
        assert max(Path(p).stat().st_size for p in paths) > MAX_LINE


def test_bundle_roundtrip_and_images_excluded(tmp_path):
    from PIL import Image
    config, engine = installation(tmp_path)
    image_path = tmp_path / 'attached.png'
    Image.new('RGB', (2, 2), 'blue').save(image_path)
    path = tmp_path / 'source.jsonl'
    path.write_text(json.dumps({'role': 'user', 'message': {'content': [{'type': 'text', 'text': '看一下'}, {'type': 'image', 'path': str(image_path)}]}}), encoding='utf-8')
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    with engine.connect() as conn:
        aid = conn.execute(select(db.assets.c.id)).scalar_one()
    bundle = tmp_path / 'conversation.csh'
    manifest = build_bundle(config, sid, None, bundle)
    assert manifest['assets'][0]['id'] == aid
    cloud, cloud_engine = installation(tmp_path, 'cloud')
    cloud_sid, cloud_job = queue(cloud_engine, bundle, kind='bundle_import')
    import_bundle(cloud, cloud_job)
    received = full_events(cloud_engine, cloud_sid)
    assert received[0]['blocks'][0]['text'] == '看一下'
    assert received[0]['blocks'][1]['asset_id'] != aid
    manifest = build_bundle(config, sid, None, bundle, [aid])
    assert manifest['assets'] == []
    with zipfile.ZipFile(bundle) as archive:
        event = json.loads(archive.read('events.jsonl'))
        assert event['blocks'][1]['missing']
        assert '_original_record' not in event


def test_bundle_rejects_path_traversal(tmp_path):
    config, engine = installation(tmp_path)
    bundle = tmp_path / 'bad.csh'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('../oops', 'bad')
        archive.writestr('manifest.json', '{}')
    sid, jid = queue(engine, bundle, kind='bundle_import')
    with pytest.raises(ValueError, match='Unsafe'):
        import_bundle(config, jid)
    assert not (tmp_path / 'oops').exists()


def test_cli_tool_results_match_legacy(tmp_path):
    import cursor_parser
    from tests.fixture_builders import _write_cli_store
    path = _write_cli_store(tmp_path)
    raw_dir = tmp_path / 'scratch'
    raw_dir.mkdir()
    expected = cursor_parser.parse_cli_store(path)['events']
    actual = [event for _, event in iter_cli_events(path, raw_dir)]
    assert [e['kind'] for e in actual] == [e['kind'] for e in expected]
    assert [e['blocks'] for e in actual] == [e['blocks'] for e in expected]


def test_golden_jsonl_legacy_compatibility(tmp_path):
    import cursor_parser
    from tests.fixture_builders import _write_cli_session
    path = _write_cli_session(tmp_path)
    expected = cursor_parser.parse_cli_session(path)['events']
    actual = [normalize_jsonl(rec) for _, rec in iter_jsonl_records(path)]
    for got, want in zip(actual, expected):
        assert got['blocks'] == want['blocks']


def test_export_escapes_html_and_contains_long_text(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    write_records(path, ('hello', '<script>alert(1)</script>' + '结论' * 2000))
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    _, export_id = queue(engine, session_id=sid, kind='export', format='html')
    export_job(config, export_id)
    text = (config.home / 'exports' / (export_id + '.html')).read_text(encoding='utf-8')
    assert '<script>alert' not in text
    assert '&lt;script&gt;' in text
    assert '结论' * 2000 in text


def test_paused_job_keeps_old_revision(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    write_records(path)
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    with engine.connect() as conn:
        old = conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one()
    write_records(path, ('replacement', 'answer'))
    _, jid = queue(engine, path, sid)
    with engine.begin() as conn:
        conn.execute(db.jobs.update().where(db.jobs.c.id == jid).values(state='paused'))
    from hub.ingest import JobInterrupted
    with pytest.raises(JobInterrupted):
        ingest_path(config, jid)
    with engine.connect() as conn:
        assert conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one() == old


def test_chinese_index_works_for_substrings():
    assert {'协', '作', '协作', '办公'} <= set(tokenize('团队协作办公'))


def test_ide_disk_recovery_matches_legacy(tmp_path):
    from tests.test_cursor_parser import CursorOrphanRecoveryTests
    import cursor_parser
    from hub.ingest import iter_ide_events
    fixture = CursorOrphanRecoveryTests()
    fixture.setUp()
    try:
        fixture._write(*fixture._rebuilt_session())
        expected = cursor_parser.parse_session_by_id(fixture.cid)['events']
        actual = [event for _, event in iter_ide_events(fixture.db_path, fixture.cid, tmp_path)]
        for event in actual:
            event.pop('_original_record', None)
        assert actual == expected
    finally:
        fixture.tearDown()


def test_transactional_checkpoint_resumes_without_duplicate_events(tmp_path):
    from hub.ingest import index_events
    config, engine = installation(tmp_path)
    sid, jid = queue(engine)
    revision = db.new_id()
    with engine.begin() as conn:
        conn.execute(db.revisions.insert().values(id=revision, session_id=sid))
        conn.execute(db.jobs.update().where(db.jobs.c.id == jid).values(revision_id=revision))
    def events(fail=False):
        for i in range(1, 451):
            if fail and i == 210:
                raise RuntimeError('simulated interruption')
            yield i, {'kind': 'user' if i % 10 == 1 else 'assistant', 'blocks': [{'type': 'text', 'text': 'event ' + str(i)}]}
    with pytest.raises(RuntimeError):
        index_events(config, jid, events(True), revision_id=revision)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(db.events)).scalar_one() == 200
        assert conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one() is None
    index_events(config, jid, events(), revision_id=revision)
    full = full_events(engine, sid)
    assert len(full) == 450
    assert full[-1]['blocks'][0]['text'] == 'event 450'


def test_two_supervisors_enforce_one_parser_and_clean_shutdown(tmp_path):
    import time
    from hub.worker import start_worker
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    write_records(path, tuple('message ' + str(i) for i in range(600)))
    job_ids = []
    for _ in range(2):
        _, jid = queue(engine, path)
        job_ids.append(jid)
    with engine.begin() as conn:
        conn.execute(db.jobs.update().values(state='queued'))
    workers = [start_worker(config), start_worker(config)]
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                states = conn.execute(select(db.jobs.c.state)).scalars().all()
            assert states.count('running') <= 1
            if all(state == 'succeeded' for state in states):
                break
            assert not any(state == 'failed' for state in states)
            time.sleep(.05)
        else:
            pytest.fail('supervisor did not finish jobs')
    finally:
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join()
    assert all(not worker.thread.is_alive() for worker in workers)


def test_supervisor_memory_guard_retains_published_revision(tmp_path):
    import time
    from hub.worker import start_worker
    config, engine = installation(tmp_path)
    path = tmp_path / 'source.jsonl'
    write_records(path)
    sid, jid = queue(engine, path)
    ingest_path(config, jid)
    with engine.connect() as conn:
        previous = conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one()
    write_records(path, tuple('record ' + str(i) for i in range(10000)))
    _, jid = queue(engine, path, sid)
    with engine.begin() as conn:
        conn.execute(db.jobs.update().where(db.jobs.c.id == jid).values(state='queued'))
    config.worker_memory_bytes = 1024**2  # Deliberately tiny to exercise the kill path.
    worker = start_worker(config)
    try:
        for _ in range(200):
            with engine.connect() as conn:
                row = conn.execute(select(db.jobs).where(db.jobs.c.id == jid)).mappings().one()
            if row['state'] == 'failed':
                assert 'memory limit' in row['error']
                break
            time.sleep(.05)
        else:
            pytest.fail('memory guard did not stop isolated child')
        with engine.connect() as conn:
            assert conn.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one() == previous
    finally:
        worker.stop()
        worker.join()


def test_import_derives_title_but_keeps_explicit_rename(tmp_path):
    config, engine = installation(tmp_path)
    path = tmp_path / 'session-id.jsonl'
    write_records(path, ('明确的任务目标', 'first answer'))
    sid, jid = queue(engine, path)
    with engine.begin() as conn:
        conn.execute(db.sessions.update().where(db.sessions.c.id == sid).values(title='session-id', original_title='session-id', native_id='session-id'))
    ingest_path(config, jid)
    with engine.begin() as conn:
        row = conn.execute(select(db.sessions).where(db.sessions.c.id == sid)).mappings().one()
        assert row['title'] == '明确的任务目标'
        conn.execute(db.sessions.update().where(db.sessions.c.id == sid).values(title='我的标题', metadata_json={'custom_title': '我的标题'}))
    write_records(path, ('新的任务目标', 'new answer'))
    _, jid = queue(engine, path, sid)
    ingest_path(config, jid)
    with engine.connect() as conn:
        assert conn.execute(select(db.sessions.c.title).where(db.sessions.c.id == sid)).scalar_one() == '我的标题'
