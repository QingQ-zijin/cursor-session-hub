"""Idempotent local-only migration from the original viewer; never auto-upload."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from sqlalchemy import select, func
from . import db
from .config import default_home


def migrate_legacy(config, engine):
    if config.mode != 'local': return {'migrated': 0}
    explicit = os.getenv('CSH_LEGACY_DIR')
    # Test/dev homes do not silently ingest personal data.
    if not explicit and config.home != default_home().expanduser().resolve():
        return {'migrated': 0}
    root = Path(explicit).expanduser().resolve() if explicit else Path.home() / 'Downloads/cc_transcript_viewer/.local'
    imports = root / 'imports'
    if not imports.is_dir(): return {'migrated': 0}
    try: names = json.loads((root / 'names.json').read_text(encoding='utf-8'))
    except (OSError, ValueError): names = {}
    count = 0
    for original in imports.glob('*/*.jsonl'):
        legacy_path = str(original.resolve())
        alias = 'legacy:' + hashlib.sha256(legacy_path.encode()).hexdigest()
        with engine.connect() as connection:
            if connection.execute(select(db.sessions.c.id).where(db.sessions.c.source_id == alias)).first(): continue
            queued = connection.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.owner_id == 'local', db.jobs.c.state.in_(('queued','running','paused')))).scalar_one()
        if queued >= config.max_user_queue: break
        if original.stat().st_size > config.max_package_bytes: continue
        sid, jid = db.new_id(), db.new_id()
        copied = config.home / 'imports' / (sid + '.jsonl')
        copied.parent.mkdir(parents=True, exist_ok=True)
        with original.open('rb') as source, copied.open('wb') as dest:
            shutil.copyfileobj(source, dest, 1024**2)
        title = names.get('cursor:' + original.stem) or original.stem
        metadata = {'legacy_id': original.stem, 'legacy_path': legacy_path, 'legacy_alias': alias}
        if names.get('cursor:' + original.stem): metadata['custom_title'] = title
        try:
            with engine.begin() as connection:
                connection.execute(db.sessions.insert().values(id=sid,owner_id='local',title=title,original_title=title,source_kind='cursor_jsonl',native_id=original.stem,source_id=alias,status='queued',metadata_json=metadata))
                connection.execute(db.jobs.insert().values(id=jid,owner_id='local',kind='ingest',session_id=sid,payload_json={'path':str(copied),'source_kind':'cursor_jsonl'}))
                db.emit(connection,'job',owner_id='local',session_id=sid,job_id=jid,state='queued')
            count += 1
        except Exception:
            copied.unlink(missing_ok=True)
            raise
    return {'migrated': count}
