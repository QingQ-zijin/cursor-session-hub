"""Synthetic-only coverage of legacy viewer migration and startup boundaries."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from hub import db, migration
from hub.api import create_app
from hub.config import Config
from hub.ingest import ingest_path, hydrate_event


def legacy_fixture(base, native_id='fixture-session', title='已有的自定义标题'):
    root = base / 'legacy' / '.local'
    folder = root / 'imports' / 'fixture-content-hash'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (native_id + '.jsonl')
    records = [
        {'role': 'user', 'message': {'content': [{'type': 'text', 'text': '请继续研究这个示例任务。'}]}},
        {'role': 'assistant', 'message': {'content': [{'type': 'text', 'text': '已完成第一步。\n下一步核对结果。'}]}},
    ]
    data = ('\r\n'.join(json.dumps(row, ensure_ascii=False) for row in records) + '\r\n').encode('utf-8')
    path.write_bytes(data)
    (root / 'names.json').write_text(json.dumps({'cursor:' + native_id: title}, ensure_ascii=False), encoding='utf-8')
    return root, path, data


def setup_db(home, **kwargs):
    config = Config(home=home, local_token='migration-test-token', worker_external=True, **kwargs)
    engine = db.get_engine(config)
    db.init_db(engine)
    return config, engine


def test_explicit_legacy_copy_queues_local_only_and_preserves_titles(tmp_path, monkeypatch):
    root, original, original_bytes = legacy_fixture(tmp_path)
    monkeypatch.setenv('CSH_LEGACY_DIR', str(root))
    config, engine = setup_db(tmp_path / 'installation')
    try:
        assert migration.migrate_legacy(config, engine) == {'migrated': 1}
        with engine.connect() as conn:
            session = conn.execute(select(db.sessions)).mappings().one()
            job = conn.execute(select(db.jobs)).mappings().one()
            assert session['owner_id'] == 'local'
            assert session['title'] == '已有的自定义标题'
            assert session['sync_status'] == 'local_only'
            assert job['owner_id'] == 'local' and job['kind'] == 'ingest' and job['state'] == 'queued'
            assert conn.execute(select(func.count()).select_from(db.syncs)).scalar_one() == 0
            assert conn.execute(select(func.count()).select_from(db.uploads)).scalar_one() == 0
            copied = Path(job['payload_json']['path'])
            assert copied.resolve().is_relative_to(config.home)
            assert copied.read_bytes() == original_bytes
            assert original.read_bytes() == original_bytes
            expected_alias = 'legacy:' + hashlib.sha256(str(original.resolve()).encode()).hexdigest()
            assert session['source_id'] == expected_alias
            assert session['metadata_json']['legacy_alias'] == expected_alias
            assert session['metadata_json']['custom_title'] == '已有的自定义标题'
        ingest_path(config, job['id'])
        with engine.connect() as conn:
            after = conn.execute(select(db.sessions).where(db.sessions.c.id == session['id'])).mappings().one()
            assert after['title'] == session['title']
            assert after['source_id'] == expected_alias
            assert after['metadata_json']['legacy_id'] == original.stem
            assert after['metadata_json']['legacy_alias'] == expected_alias
            assert after['sync_status'] == 'local_only'
            rows = conn.execute(select(db.events.c.event_json).where(db.events.c.revision_id == after['current_revision']).order_by(db.events.c.seq)).all()
            assert len(rows) == 2
            assert hydrate_event(conn, rows[1][0])['blocks'][0]['text'] == '已完成第一步。\n下一步核对结果。'
    finally:
        engine.dispose()


def test_repeated_startup_migration_is_idempotent(tmp_path, monkeypatch):
    root, original, expected = legacy_fixture(tmp_path)
    monkeypatch.setenv('CSH_LEGACY_DIR', str(root))
    config = Config(home=tmp_path / 'installation', local_token='migration-test-token', worker_external=True)
    ids = []
    for _ in range(3):
        app = create_app(config)
        with TestClient(app):
            with app.state.engine.connect() as conn:
                sessions = conn.execute(select(db.sessions)).mappings().all()
                jobs = conn.execute(select(db.jobs)).mappings().all()
                assert len(sessions) == len(jobs) == 1
                assert jobs[0]['kind'] == 'ingest'
                ids.append((sessions[0]['id'], jobs[0]['id']))
    assert len(set(ids)) == 1
    assert original.read_bytes() == expected
    assert len(list((config.home / 'imports').glob('*.jsonl'))) == 1


def test_cloud_ignores_even_explicit_legacy_directory(tmp_path, monkeypatch):
    root, _, _ = legacy_fixture(tmp_path)
    monkeypatch.setenv('CSH_LEGACY_DIR', str(root))
    config, engine = setup_db(tmp_path / 'cloud', mode='cloud')
    try:
        assert migration.migrate_legacy(config, engine) == {'migrated': 0}
        with engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(db.sessions)).scalar_one() == 0
            assert conn.execute(select(func.count()).select_from(db.jobs)).scalar_one() == 0
        assert list((config.home / 'imports').iterdir()) == []
    finally:
        engine.dispose()


def test_development_home_does_not_scan_personal_legacy_data(tmp_path, monkeypatch):
    monkeypatch.delenv('CSH_LEGACY_DIR', raising=False)
    monkeypatch.setattr(migration, 'default_home', lambda: tmp_path / 'standard-installation')
    config, engine = setup_db(tmp_path / 'separate-dev-installation')
    try:
        # The early return must happen before resolving or probing user folders.
        def unexpected_home():
            raise AssertionError('Development installation attempted personal-folder discovery')
        monkeypatch.setattr(Path, 'home', staticmethod(unexpected_home))
        assert migration.migrate_legacy(config, engine) == {'migrated': 0}
        with engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(db.sessions)).scalar_one() == 0
            assert conn.execute(select(func.count()).select_from(db.sources)).scalar_one() == 0
    finally:
        engine.dispose()


def test_migration_respects_queue_limit_then_continues_next_startup(tmp_path, monkeypatch):
    root, original, expected = legacy_fixture(tmp_path)
    (original.parent / 'second-session.jsonl').write_bytes(expected)
    monkeypatch.setenv('CSH_LEGACY_DIR', str(root))
    config, engine = setup_db(tmp_path / 'installation', max_user_queue=1)
    try:
        assert migration.migrate_legacy(config, engine)['migrated'] == 1
        assert migration.migrate_legacy(config, engine)['migrated'] == 0
        with engine.begin() as conn:
            conn.execute(db.jobs.update().values(state='succeeded'))
        assert migration.migrate_legacy(config, engine)['migrated'] == 1
        with engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(db.sessions)).scalar_one() == 2
            assert conn.execute(select(func.count()).select_from(db.jobs)).scalar_one() == 2
    finally:
        engine.dispose()
