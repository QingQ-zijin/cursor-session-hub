"""Cheap, explicit Cursor source discovery. Never parse transcripts in a scan."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
from sqlalchemy import select
from . import db
import common


def cursor_locations():
    home = Path.home()
    if sys.platform == 'win32':
        app = Path(os.getenv('APPDATA', home / 'AppData/Roaming'))
    elif sys.platform == 'darwin':
        app = home / 'Library/Application Support'
    else:
        app = Path(os.getenv('XDG_CONFIG_HOME', home / '.config'))
    return app / 'Cursor/User/globalStorage/state.vscdb', home / '.cursor/projects', home / '.cursor/chats'


def iter_sources(paths=None):
    ide, projects, chats = paths or cursor_locations()
    if ide.is_file():
        conn = common.connect_ro(ide)
        try:
            # Read one composer metadata row, never bubble payloads or tool outputs.
            for key, raw in conn.execute("SELECT key,value FROM cursorDiskKV WHERE key LIKE 'composerData:%' AND length(value)<=8388608"):
                data = common.loads_or_none(raw) or {}
                import cursor_parser
                native = key.split(':', 1)[1]
                yield {'path': str(ide), 'native_id': native, 'title': data.get('name') or native,
                       'source_kind': 'cursor_ide', 'project': cursor_parser._composer_cwd(data)}
        finally:
            conn.close()
    if chats.is_dir():
        for path in chats.glob('*/*/store.db'):
            import cursor_parser
            conn = common.connect_ro(path)
            try:
                head = cursor_parser._store_header(conn, path)
                yield {'path': str(path), 'native_id': head['session_id'], 'title': head['title'] or path.parent.name,
                       'source_kind': 'cursor_cli', 'project': head['cwd']}
            except Exception:
                yield {'path': str(path), 'native_id': path.parent.name, 'title': path.parent.name,
                       'source_kind': 'cursor_cli', 'project': '', 'status': 'unreadable'}
            finally:
                conn.close()
    if projects.is_dir():
        for project in projects.iterdir():
            folder = project / 'agent-transcripts'
            if not folder.is_dir():
                continue
            for path in folder.rglob('*.jsonl'):
                yield {'path': str(path), 'native_id': path.stem, 'title': path.stem,
                       'source_kind': 'cursor_jsonl', 'project': project.name}


def discover_sources(config, paths=None):
    if config.mode != 'local':
        raise ValueError('Source discovery is available only on the local client')
    engine = db.get_engine(config)
    db.init_db(engine)
    count = 0
    try:
        for entry in iter_sources(paths):
            path = Path(entry['path'])
            stat = path.stat()
            mtime = stat.st_mtime
            wal = Path(str(path) + '-wal')
            if wal.exists():
                mtime = max(mtime, wal.stat().st_mtime)
            with engine.begin() as conn:
                existing = conn.execute(select(db.sources).where(db.sources.c.path == str(path), db.sources.c.native_id == entry['native_id'])).mappings().first()
                values = dict(entry, size=stat.st_size, mtime=mtime, updated_at=db.now())
                if existing:
                    if existing['mtime'] != mtime or existing['size'] != stat.st_size:
                        values['status'] = 'changed'
                    conn.execute(db.sources.update().where(db.sources.c.id == existing['id']).values(**values))
                else:
                    conn.execute(db.sources.insert().values(id=db.new_id(), owner_id='local', **values))
            count += 1
        return {'discovered': count}
    finally:
        engine.dispose()
