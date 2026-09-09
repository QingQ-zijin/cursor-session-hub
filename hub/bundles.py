"""Streaming, versioned sync packages and background HTML/Markdown exports."""
from __future__ import annotations
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile
from sqlalchemy import select
from . import db
from .ingest import file_hash, hydrate_event, index_events, iter_jsonl_records, json_bytes, text_values, JobInterrupted, ParseDiagnostic


def _refs(value, key):
    if isinstance(value, dict):
        if value.get(key):
            yield value[key]
        for name, child in value.items():
            if not name.startswith('_original'):
                yield from _refs(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child, key)


def _portable(value, excluded):
    if isinstance(value, list):
        return [_portable(child, excluded) for child in value]
    if not isinstance(value, dict):
        return value
    if value.get('asset_id') in excluded:
        return {'type': 'image', 'data_uri': '', 'missing': True, 'reason': 'Image excluded by sender'}
    # Normalized content, unknown blocks and diagnostics survive. Local parser
    # copies of original records can contain excluded base64 images, so they
    # never leave the client as an implicit second attachment channel.
    return {key: _portable(child, excluded) for key, child in value.items() if key not in ('_original_record', '_full_content_id', '_raw_path', '_image_source')}


def build_bundle(config, session_id, revision_id, output_path, excluded_asset_ids=None):
    """Return manifest; write ZIP with bounded event pages and streamed files."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    excluded = set(excluded_asset_ids or [])
    temp_events = output_path.with_suffix('.events.partial')
    engine = db.get_engine(config)
    asset_ids, raw_ids = set(), set()
    try:
        with engine.connect() as conn:
            session = conn.execute(select(db.sessions).where(db.sessions.c.id == session_id, db.sessions.c.revoked == False)).mappings().one()
            revision_id = revision_id or session['current_revision']
            revision = conn.execute(select(db.revisions).where(db.revisions.c.id == revision_id, db.revisions.c.session_id == session_id)).mappings().one()
            if revision['state'] not in ('ready', 'ready_with_diagnostics', 'complete'):
                raise ValueError('Only published revisions can be synchronized')
            seq = 0
            total = 0
            with temp_events.open('wb') as stream:
                while True:
                    rows = conn.execute(select(db.events.c.seq, db.events.c.event_json).where(db.events.c.revision_id == revision_id, db.events.c.seq > seq).order_by(db.events.c.seq).limit(40)).all()
                    if not rows:
                        break
                    for number, value in rows:
                        event = _portable(hydrate_event(conn, value), excluded)
                        asset_ids.update(_refs(event, 'asset_id'))
                        raw_ids.update(_refs(event, 'raw_content_id'))
                        line = json_bytes(event) + b'\n'
                        stream.write(line)
                        total += len(line)
                        if total > config.max_package_bytes:
                            raise ValueError('Sync package exceeds 512 MiB uncompressed limit')
                    seq = rows[-1][0]
            manifest = {'format': 'csh-bundle-v1', 'source_kind': session['source_kind'], 'native_id': session['native_id'] or session_id, 'title': session['title'], 'project': session['project'], 'device_id': 'local', 'source_hash': revision['source_hash'], 'events_file': 'events.jsonl', 'event_count': revision['event_count'], 'assets': [], 'contents': []}
            files = []
            for aid in sorted(asset_ids - excluded):
                asset = conn.execute(select(db.assets).where(db.assets.c.id == aid)).mappings().first()
                if not asset or asset['status'] != 'ready' or not asset['path'] or not Path(asset['path']).is_file():
                    manifest['assets'].append({'id': aid, 'status': 'missing', 'name': asset['name'] if asset else 'image'})
                    continue
                name = 'assets/' + asset['sha256']
                total += asset['bytes']
                manifest['assets'].append({'id': aid, 'path': name, 'sha256': asset['sha256'], 'bytes': asset['bytes'], 'mime': asset['mime'], 'name': asset['name'], 'status': 'ready'})
                files.append((asset['path'], name))
            for cid in sorted(raw_ids):
                content = conn.execute(select(db.contents).where(db.contents.c.id == cid)).mappings().one()
                # Raw diagnostics are standalone files, not packed event slices.
                if content['kind'] != 'raw':
                    raise ValueError('Unexpected raw content reference')
                name = 'contents/' + content['sha256']
                total += content['bytes']
                manifest['contents'].append({'id': cid, 'path': name, 'sha256': content['sha256'], 'bytes': content['bytes'], 'kind': 'raw'})
                files.append((content['path'], name))
            if total > config.max_package_bytes:
                raise ValueError('Sync package including images exceeds 512 MiB limit')
            manifest_raw = json_bytes(manifest)
            if len(manifest_raw) > 8 * 1024**2:
                raise ValueError('Too many attachments in one sync package')
            partial = output_path.with_suffix('.partial')
            with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
                archive.writestr('manifest.json', manifest_raw)
                archive.write(temp_events, 'events.jsonl')
                written = set()
                for path, name in files:
                    if name not in written:
                        archive.write(path, name)
                        written.add(name)
            os.replace(partial, output_path)
            return manifest
    finally:
        temp_events.unlink(missing_ok=True)
        engine.dispose()


def _safe_name(name):
    path = PurePosixPath(name)
    return bool(name) and '\\' not in name and ':' not in name and not path.is_absolute() and all(part not in ('..', '.') for part in path.parts)


def _replace_refs(value, assets, contents):
    if isinstance(value, list):
        return [_replace_refs(child, assets, contents) for child in value]
    if not isinstance(value, dict):
        return value
    result = {key: _replace_refs(child, assets, contents) for key, child in value.items() if key not in ('_full_content_id', '_raw_path', '_original_record')}
    if value.get('asset_id'):
        old = value['asset_id']
        if old in assets:
            result['asset_id'] = assets[old]
        else:
            raise ValueError('Event references image absent from manifest')
    if value.get('raw_content_id'):
        if value['raw_content_id'] not in contents:
            raise ValueError('Event references undeclared raw content')
        result['raw_content_id'] = contents[value['raw_content_id']]
    return result


def import_bundle(config, job_id):
    engine = db.get_engine(config)
    with engine.begin() as conn:
        job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
        path = Path(job['payload_json']['path'])
        revision_id = job['revision_id'] or db.new_id()
        if not job['revision_id']:
            conn.execute(db.revisions.insert().values(id=revision_id, session_id=job['session_id'], source_hash=file_hash(path), source_size=path.stat().st_size))
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(revision_id=revision_id))
    folder = config.home / 'raw' / revision_id
    folder.mkdir(parents=True, exist_ok=True)
    asset_map, content_map = {}, {}
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 20000 or len({item.filename for item in members}) != len(members):
                raise ValueError('Duplicate paths or excessive files in sync package')
            if sum(item.file_size for item in members) > config.max_package_bytes:
                raise ValueError('Uncompressed sync package exceeds resource limit')
            for item in members:
                if not _safe_name(item.filename) or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('Unsafe sync package path')
            info = archive.getinfo('manifest.json')
            if info.file_size > 8 * 1024**2:
                raise ValueError('Oversized manifest')
            manifest = json.loads(archive.read(info))
            if manifest.get('format') != 'csh-bundle-v1' or manifest.get('events_file') != 'events.jsonl':
                raise ValueError('Unsupported sync package format')
            declared = {'manifest.json', 'events.jsonl'}
            for group in ('assets', 'contents'):
                for entry in manifest.get(group, []):
                    if entry.get('status') == 'missing':
                        continue
                    expected = group + '/' + entry.get('sha256', '')
                    if entry.get('path') != expected or not re_hash(entry.get('sha256')):
                        raise ValueError('Invalid attachment path or checksum')
                    declared.add(expected)
            if {item.filename for item in members} != declared:
                raise ValueError('Undeclared files in sync package')
            event_path = folder / 'bundle-events.jsonl'
            with archive.open('events.jsonl') as src, event_path.open('wb') as out:
                shutil.copyfileobj(src, out, 256 * 1024)
            with engine.begin() as conn:
                # Retry maps retain already imported assets rather than growing
                # duplicates. They live in the unpublished revision metadata.
                rev = conn.execute(select(db.revisions).where(db.revisions.c.id == revision_id)).mappings().one()
                old_meta = rev['metadata_json'] or {}
                asset_map = old_meta.get('asset_map', {})
                content_map = old_meta.get('content_map', {})
                for group, table, mapping in (('assets', db.assets, asset_map), ('contents', db.contents, content_map)):
                    ids = set()
                    for entry in manifest.get(group, []):
                        original_id = entry.get('id')
                        if not isinstance(original_id, str) or original_id in ids:
                            raise ValueError('Invalid or duplicate attachment id')
                        ids.add(original_id)
                        if original_id in mapping:
                            continue
                        new_id = db.new_id()
                        mapping[original_id] = new_id
                        if entry.get('status') == 'missing':
                            if group != 'assets':
                                raise ValueError('Missing raw diagnostic content')
                            conn.execute(table.insert().values(id=new_id, revision_id=revision_id, status='missing', name=str(entry.get('name', 'image'))[:200]))
                            continue
                        expected_bytes = int(entry['bytes'])
                        info = archive.getinfo(entry['path'])
                        if info.file_size != expected_bytes or expected_bytes > config.max_package_bytes:
                            raise ValueError('Attachment size does not match manifest')
                        target = config.home / group / (entry['sha256'] if group == 'assets' else new_id)
                        temporary_asset = target.with_name(target.name + '.' + new_id + '.partial')
                        digest = hashlib.sha256()
                        with archive.open(entry['path']) as src, temporary_asset.open('wb') as out:
                            while block := src.read(256 * 1024):
                                digest.update(block)
                                out.write(block)
                        if digest.hexdigest() != entry['sha256']:
                            temporary_asset.unlink(missing_ok=True)
                            raise ValueError('Attachment checksum mismatch')
                        values = {'id': new_id, 'revision_id': revision_id, 'path': str(target), 'bytes': expected_bytes, 'sha256': entry['sha256']}
                        if group == 'assets':
                            mime = str(entry.get('mime', ''))
                            if not mime.startswith('image/') or mime == 'image/svg+xml':
                                raise ValueError('Unsupported image MIME type')
                            from PIL import Image
                            try:
                                with Image.open(temporary_asset) as probe:
                                    mime = Image.MIME.get(probe.format, mime)
                                    probe.verify()
                            except Exception as exc:
                                temporary_asset.unlink(missing_ok=True)
                                raise ValueError('Invalid image data in sync package') from exc
                            values.update(mime=mime, name=str(entry.get('name', 'image'))[:200])
                        else:
                            values['kind'] = 'raw'
                        # Validate before replacing any content-addressed file:
                        # a bad new upload cannot damage an older readable image.
                        os.replace(temporary_asset, target)
                        conn.execute(table.insert().values(**values))
                publish_metadata = {'title': str(manifest.get('title', '未命名会话'))[:2000], 'original_title': str(manifest.get('title', ''))[:2000], 'project': str(manifest.get('project', ''))[:2000], 'native_id': str(manifest.get('native_id', ''))[:300], 'source_kind': str(manifest.get('source_kind', 'cursor_jsonl'))[:40]}
                conn.execute(db.revisions.update().where(db.revisions.c.id == revision_id).values(metadata_json={'asset_map': asset_map, 'content_map': content_map, 'format': manifest['format'], 'publish_metadata': publish_metadata}))
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(total=event_path.stat().st_size))
        # Re-mapped assets are accepted by writer through an identity map only;
        # filesystem lookup is disabled on the server.
        def bundle_events():
            count = 0
            for pos, event in iter_jsonl_records(event_path, folder):
                count += 1
                yield pos, event if isinstance(event, ParseDiagnostic) else _replace_refs(event, asset_map, content_map)
            expected = manifest.get('event_count')
            if expected is not None and count != expected:
                raise ValueError('Event count does not match bundle manifest')
        iterator = bundle_events()
        index_events(config, job_id, iterator, revision_id=revision_id, asset_map={value: value for value in asset_map.values()})
        with engine.begin() as conn:
            conn.execute(db.sessions.update().where(db.sessions.c.id == job['session_id']).values(sync_status='synced'))
            conn.execute(db.syncs.update().where(db.syncs.c.job_id == job_id).values(state='succeeded', revision_id=revision_id, updated_at=db.now()))
    finally:
        engine.dispose()


def re_hash(value):
    import re
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def export_job(config, job_id):
    engine = db.get_engine(config)
    try:
        with engine.connect() as conn:
            job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
            session = conn.execute(select(db.sessions).where(db.sessions.c.id == job['session_id'], db.sessions.c.revoked == False)).mappings().one()
            revision_id = job['payload_json'].get('revision_id') or session['current_revision']
            fmt = job['payload_json'].get('format', 'markdown')
            from .export_names import FORMATS, filename
            target = config.home / 'exports' / (job_id + FORMATS[fmt][0])
            temp = target.with_suffix('.partial')
            from .documents import readable_event, readable_blocks, block_markdown, markdown_renderer, write_math_assets
            if fmt == 'pdf':
                from .pdf_export import PDFWriter
                from .ingest import _check_job
                writer = PDFWriter(temp, session['title'], lambda: _check_job(conn, job_id))
                writer.heading(session['title'], 1)
                last = 0
                while True:
                    rows = conn.execute(select(db.events).where(db.events.c.revision_id == revision_id, db.events.c.seq > last).order_by(db.events.c.seq).limit(40)).mappings().all()
                    if not rows: break
                    for row in rows:
                        event = readable_event(hydrate_event(conn, row['event_json']))
                        writer.event(event, lambda aid: conn.execute(select(db.assets).where(db.assets.c.id == aid)).mappings().first() if aid else None)
                    last = rows[-1]['seq']; _check_job(conn, job_id)
                    conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(progress=last, total=session['event_count'], updated_at=db.now()))
                    conn.commit()
                writer.finish(); os.replace(temp, target)
                conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state='succeeded', result_json={'path': str(target), 'filename': filename(session['title'], fmt), 'mime': FORMATS[fmt][1]}, updated_at=db.now()))
                conn.commit()
                return
            render = markdown_renderer()
            with temp.open('w', encoding='utf-8') as out:
                math_assets = False
                if fmt == 'html':
                    out.write('<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; font-src data:; base-uri \'none\'"><title>' + html.escape(session['title']) + '</title><style>body{max-width:980px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;color:#202630}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6fa;padding:16px}section{border-bottom:1px solid #ddd;padding:20px 0}img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:8px}blockquote{border-left:3px solid #ccd;padding-left:16px}template{display:none}</style>')
                    math_assets = write_math_assets(out)
                    out.write('</head><body><h1>' + html.escape(session['title']) + '</h1>')
                else:
                    out.write('# ' + session['title'] + '\n\n')
                last = 0
                while True:
                    rows = conn.execute(select(db.events).where(db.events.c.revision_id == revision_id, db.events.c.seq > last).order_by(db.events.c.seq).limit(40)).mappings().all()
                    if not rows:
                        break
                    for row in rows:
                        event = readable_event(hydrate_event(conn, row['event_json']))
                        kind = event.get('kind', 'notice')
                        title = {'user':'用户','assistant':'Cursor','tool':'工具','reasoning':'思考','notice':'记录'}.get(kind, '解析记录')
                        if fmt == 'html':
                            out.write('<section data-csh-event="' + html.escape(kind, quote=True) + '"><h2>' + title + '</h2>')
                        else:
                            out.write('## ' + title + '\n\n<!-- csh-event:' + kind + ' -->\n')
                        blocks = readable_blocks(event)
                        for block in blocks:
                            if block.get('type') == 'image':
                                aid = block.get('asset_id')
                                asset = conn.execute(select(db.assets).where(db.assets.c.id == aid)).mappings().first() if aid else None
                                if fmt == 'html' and asset and asset['status'] == 'ready':
                                    import base64
                                    out.write('<img src="data:' + html.escape(asset['mime'], quote=True) + ';base64,')
                                    with Path(asset['path']).open('rb') as src:
                                        while data := src.read(3 * 16384):
                                            out.write(base64.b64encode(data).decode('ascii'))
                                    out.write('">')
                                else:
                                    out.write('[图片：' + (asset['name'] if asset else '未记录') + ']\n')
                                continue
                            text = block_markdown(block)
                            if fmt == 'html':
                                block_type = block.get('type', 'text')
                                raw_text = json.dumps(block, ensure_ascii=False) if block_type == 'tool_use' else block.get('text', '')
                                out.write('<template data-csh-block="' + html.escape(block_type, quote=True) + '">' + html.escape(raw_text) + '</template>')
                                body = render.render(text)
                                if block_type in ('tool_use','thinking'):
                                    out.write('<details open><summary>' + ('工具记录' if block_type == 'tool_use' else '已保存的思考') + '</summary>' + body + '</details>')
                                else: out.write(body)
                            else: out.write(text + '\n\n')
                        if event.get('raw_content_id'):
                            out.write('<p>原始诊断数据保存在会话库中。</p>' if fmt == 'html' else '\n原始诊断数据保存在会话库中。\n')
                        if fmt == 'html':
                            out.write('</section>')
                        else: out.write('<!-- /csh-event -->\n\n')
                    last = rows[-1]['seq']
                    state = conn.execute(select(db.jobs.c.state, db.jobs.c.cancel_requested).where(db.jobs.c.id == job_id)).one()
                    if state.cancel_requested or state.state in ('paused', 'cancelled'):
                        raise JobInterrupted(state.state)
                    conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(progress=last, total=session['event_count'], updated_at=db.now()))
                    conn.commit()
                if fmt == 'html':
                    if math_assets:
                        out.write('<script>renderMathInElement(document.body,{delimiters:[{left:"$$",right:"$$",display:true},{left:"$",right:"$",display:false},{left:"\\\\(",right:"\\\\)",display:false},{left:"\\\\[",right:"\\\\]",display:true}],throwOnError:false,trust:false,ignoredTags:["script","noscript","style","textarea","pre","code","template"]});</script>')
                    out.write('</body></html>')
            os.replace(temp, target)
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state='succeeded', result_json={'path': str(target), 'filename': filename(session['title'], fmt), 'mime': FORMATS[fmt][1]}, updated_at=db.now()))
            conn.commit()
    finally:
        engine.dispose()
