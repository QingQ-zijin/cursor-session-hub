"""Cheap, explicit Cursor source discovery. Never parse transcripts in a scan."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import re
import sqlite3
from urllib.parse import unquote, urlparse
from sqlalchemy import select
from . import db
import common

def named_title(title):
    value=title.strip() if isinstance(title,str) else ''
    return bool(value and value.lower() not in ('未命名 cursor 会话','未命名会话','未命名记录','untitled','new chat','new conversation','无法读取的 cursor 会话') and not re.fullmatch(r'[0-9a-fA-F-]{32,36}',value))

def sidebar_catalog(ide):
    """Cursor's current Agents sidebar. Read bounded headers, never bubbles."""
    if not ide.is_file():return None,[]
    conn=common.connect_ro(ide)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='composerHeaders'").fetchone():return None,[]
        def item(key):
            row=conn.execute('SELECT value FROM ItemTable WHERE key=? AND octet_length(value)<=8388608',(key,)).fetchone()
            return common.loads_or_none(row[0]) if row else None
        projects=item('glass.localAgentProjects.v1');membership=item('glass.localAgentProjectMembership.v1')
        if not isinstance(projects,list) or not isinstance(membership,dict):return None,[]
        visible={p['id']:p for p in projects if isinstance(p,dict) and p.get('id') and not p.get('isArchived')}
        project_list=[{'project':workspace_path((p.get('workspace') or {}).get('uri') or p.get('workspace')),'label':p.get('name') or '', 'position':i} for i,p in enumerate(visible.values())]
        headers={}
        for ident,pid in membership.items():
            if pid not in visible:continue
            row=conn.execute('SELECT isArchived,isSubagent,value FROM composerHeaders WHERE composerId=? AND octet_length(value)<=8388608',(ident,)).fetchone()
            if not row or row[0]:continue
            data=common.loads_or_none(row[2]) or {};p=visible[pid]
            headers[ident]={'title':data.get('name') or '', 'project':workspace_path((p.get('workspace') or {}).get('uri') or p.get('workspace')), 'is_subagent':bool(row[1]),'in_sidebar':not bool(row[1])}
        return headers,project_list
    except (sqlite3.Error,ValueError,TypeError,AttributeError):return None,[]
    finally:conn.close()


def workspace_path(value):
    if isinstance(value, dict):
        return workspace_path(value.get('fsPath') or value.get('path') or value.get('uri') or '')
    if not isinstance(value, str):
        return ''
    if value.startswith('file:'):
        uri = urlparse(value)
        value = ('//' + uri.netloc if uri.netloc else '') + unquote(uri.path)
    if re.match(r'^/[A-Za-z]:', value):
        value = value[1:]
    return value


def composer_project(data):
    workspace = data.get('workspaceIdentifier') or {}
    value = workspace_path(workspace)
    if value:
        return value
    repos = data.get('trackedGitRepos') or []
    return workspace_path(repos[0].get('repoPath', '')) if repos and isinstance(repos[0], dict) else ''


def workspace_catalog(ide):
    """Read workspace descriptors and small UI metadata, never conversation bodies."""
    catalog = {}
    for folder in (ide.parent.parent / 'workspaceStorage').glob('*'):
        descriptor = folder / 'workspace.json'
        store = folder / 'state.vscdb'
        if not descriptor.is_file() or descriptor.stat().st_size > 65536 or not store.is_file():
            continue
        try:
            data = json.loads(descriptor.read_text(encoding='utf-8'))
            project = workspace_path(data.get('folder') or data.get('workspace'))
            connection = common.connect_ro(store)
            try:
                row = connection.execute("SELECT value FROM ItemTable WHERE key='composer.composerData' AND octet_length(value)<=8388608").fetchone()
                meta = common.loads_or_none(row[0]) if row else {}
                for composer in (meta or {}).get('allComposers', []):
                    if isinstance(composer, dict) and composer.get('composerId'):
                        catalog[composer['composerId']] = {'title': composer.get('name') or '', 'project': project}
            finally:
                connection.close()
        except (OSError, ValueError, sqlite3.Error, AttributeError):
            continue
    return catalog


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
    catalog = workspace_catalog(ide)
    modern,_=sidebar_catalog(ide)
    if modern is not None:catalog.update(modern)
    if ide.is_file():
        conn = common.connect_ro(ide)
        try:
            # A BINARY lexical prefix range uses Cursor's primary-key index;
            # default SQLite LIKE can otherwise scan the entire multi-GB store.
            # Count bytes (also for UTF-8 TEXT) before fetching any value.
            for key, size in conn.execute(
                'SELECT key,octet_length(value) FROM cursorDiskKV WHERE key>=? AND key<?',
                ('composerData:', 'composerData;'),
            ):
                native = key.split(':', 1)[1]
                if (size or 0) > 8388608:
                    meta = catalog.get(native, {})
                    yield {'path': str(ide), 'native_id': native, 'title': meta.get('title') or '未命名 Cursor 会话', 'source_kind': 'cursor_ide', 'project': meta.get('project', ''), 'status': 'oversized_metadata', 'metadata_json':{'in_sidebar':meta.get('in_sidebar',modern is None and native in catalog),'is_subagent':meta.get('is_subagent',False)}}
                    continue
                raw = conn.execute('SELECT value FROM cursorDiskKV WHERE key=?', (key,)).fetchone()[0]
                data = common.loads_or_none(raw) or {}
                if not isinstance(data, dict):
                    data = {}
                meta = catalog.get(native, {})
                meta = {'title': meta.get('title') or data.get('name') or '未命名 Cursor 会话',
                        'project': meta.get('project') or composer_project(data),
                        'metadata_json':{'in_sidebar':meta.get('in_sidebar',modern is None and native in catalog),'is_subagent':meta.get('is_subagent',bool(data.get('subagentInfo')))}}
                catalog[native] = meta
                yield {'path': str(ide), 'native_id': native, **meta, 'source_kind': 'cursor_ide'}
        finally:
            conn.close()
    if chats.is_dir():
        for path in chats.glob('*/*/store.db'):
            import cursor_parser
            conn = common.connect_ro(path)
            try:
                head = cursor_parser._store_header(conn, path)
                yield {'path': str(path), 'native_id': head['session_id'], 'title': head['title'] or '未命名 Cursor 会话',
                       'source_kind': 'cursor_cli', 'project': head['cwd']}
            except Exception:
                yield {'path': str(path), 'native_id': path.parent.name, 'title': '无法读取的 Cursor 会话',
                       'source_kind': 'cursor_cli', 'project': '', 'status': 'unreadable'}
            finally:
                conn.close()
    if projects.is_dir():
        for project in projects.iterdir():
            folder = project / 'agent-transcripts'
            if not folder.is_dir():
                continue
            for path in folder.rglob('*.jsonl'):
                meta = catalog.get(path.stem, {})
                yield {'path': str(path), 'native_id': path.stem, 'title': meta.get('title') or '未命名 Cursor 会话',
                       'source_kind': 'cursor_jsonl', 'project': meta.get('project') or project.name,'metadata_json':meta.get('metadata_json') or {'in_sidebar':meta.get('in_sidebar',False),'is_subagent':meta.get('is_subagent',False)}}


def discover_sources(config, paths=None):
    if config.mode != 'local':
        raise ValueError('Source discovery is available only on the local client')
    engine = db.get_engine(config)
    db.init_db(engine)
    count = 0
    scan_id=db.new_id()
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
                    source_id=existing['id']
                    if existing['mtime'] != mtime or existing['size'] != stat.st_size:
                        values['status'] = 'changed'
                    conn.execute(db.sources.update().where(db.sources.c.id == existing['id']).values(**values))
                    if existing['session_id']:
                        session = conn.execute(select(db.sessions).where(db.sessions.c.id == existing['session_id'])).mappings().first()
                        if session and not (session['metadata_json'] or {}).get('custom_title'):
                            changes = {'project': entry['project']}
                            if entry['title'] not in ('未命名 Cursor 会话', '无法读取的 Cursor 会话'):
                                changes.update(title=entry['title'], original_title=entry['title'])
                            conn.execute(db.sessions.update().where(db.sessions.c.id == session['id']).values(**changes))
                else:
                    source_id=db.new_id();conn.execute(db.sources.insert().values(id=source_id, owner_id='local', **values))
                meta=entry.get('metadata_json') or {}
                info={'scan_id':scan_id,'in_sidebar':bool(meta.get('in_sidebar')),'named':named_title(entry['title']),'is_subagent':bool(meta.get('is_subagent'))}
                if conn.execute(select(db.source_catalog.c.source_id).where(db.source_catalog.c.source_id==source_id)).first():conn.execute(db.source_catalog.update().where(db.source_catalog.c.source_id==source_id).values(**info))
                else:conn.execute(db.source_catalog.insert().values(source_id=source_id,**info))
            count += 1
        modern,projects=sidebar_catalog((paths or cursor_locations())[0])
        with engine.begin() as conn:
            for project in projects:
                if not project['project']:continue
                if conn.execute(select(db.cursor_projects.c.project).where(db.cursor_projects.c.project==project['project'])).first():conn.execute(db.cursor_projects.update().where(db.cursor_projects.c.project==project['project']).values(scan_id=scan_id,label=project['label'],position=project['position']))
                else:conn.execute(db.cursor_projects.insert().values(scan_id=scan_id,**project))
        return {'discovered': count,'scan_id':scan_id,'sidebar_available':modern is not None or bool(workspace_catalog((paths or cursor_locations())[0]))}
    finally:
        engine.dispose()
