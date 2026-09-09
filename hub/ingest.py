"""Bounded Cursor readers and a durable, revisioned disk index.

No API request imports this module to parse a conversation. Each source record
is bounded to 8 MiB; oversized bytes survive in diagnostic files. Checkpoints
commit in the same transaction as events and search postings.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from sqlalchemy import select, func
import common
import cursor_parser as legacy
from . import db

MAX_LINE = 8 * 1024**2
PREVIEW = 2048
BATCH_EVENTS = 200
BATCH_BYTES = 1024**2


class JobInterrupted(Exception):
    pass


class ParseDiagnostic(dict):
    """Trusted parser-generated diagnostic, distinguishable from input keys."""
    pass


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8', errors='replace')


def file_hash(path, length=None):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while length is None or length > 0:
            block = stream.read(min(1024**2, length) if length is not None else 1024**2)
            if not block:
                break
            h.update(block)
            if length is not None:
                length -= len(block)
    return h.hexdigest()


def tokenize(text):
    """Bounded per-text posting generation; compatible across SQLite/Postgres."""
    # Deterministic CJK unigrams and bigrams make substring Chinese queries work
    # without a large resident dictionary in every worker.
    for match in re.finditer(r'[\u3400-\u9fff]+|[\w]+', text.lower()):
        word = match.group()
        if '\u3400' <= word[0] <= '\u9fff':
            for i, char in enumerate(word):
                yield char
                if i + 1 < len(word):
                    yield word[i:i + 2]
        else:
            yield word[:300]


def text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if key not in ('data_uri', 'data', '_full_content_id'):
                yield from text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_values(child)


def iter_jsonl_records(path, raw_dir=None, start_offset=0):
    """Yield (end-byte-offset, parsed-record-or-diagnostic), without readlines."""
    raw_dir = Path(raw_dir or Path(path).parent)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with Path(path).open('rb') as stream:
        stream.seek(start_offset)
        while True:
            start = stream.tell()
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                target = raw_dir / (f'oversize-{start}.bin')
                with target.open('wb') as out:
                    out.write(line)
                    while not line.endswith(b'\n'):
                        line = stream.readline(256 * 1024)
                        if not line:
                            break
                        out.write(line)
                yield stream.tell(), ParseDiagnostic(kind='raw', record_type='oversized-line', payload={'byte_offset': start, 'bytes': target.stat().st_size}, _raw_path=str(target), _diagnostic=True)
                continue
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError('Record is not a JSON object')
            except (UnicodeError, ValueError, RecursionError) as exc:
                target = raw_dir / (f'invalid-{start}.bin')
                target.write_bytes(line)
                record = ParseDiagnostic(kind='raw', record_type='incomplete-line' if not line.endswith(b'\n') else 'invalid-json', payload={'byte_offset': start, 'reason': str(exc)[:200]}, _raw_path=str(target), _diagnostic=True)
            yield stream.tell(), record


def normalize_jsonl(rec):
    if rec.get('kind'):
        if isinstance(rec, ParseDiagnostic):
            return rec
        def strip_internal(value):
            if isinstance(value, dict):
                return {key: strip_internal(child) for key, child in value.items() if key not in ('_raw_path', '_full_content_id', 'raw_content_id', 'asset_id', 'content_id')}
            if isinstance(value, list):
                return [strip_internal(child) for child in value]
            return value
        return strip_internal(rec)
    envelope = rec.get('message') if isinstance(rec.get('message'), dict) else {}
    role = rec.get('role') or envelope.get('role') or (rec.get('type') if rec.get('type') in ('user', 'assistant', 'tool') else None)
    if role not in ('user', 'assistant', 'tool'):
        return {'kind': 'raw', 'record_type': str(rec.get('type', role or 'unknown')), 'payload': rec, '_diagnostic': True}
    content = (rec.get('message') or {}).get('content') if isinstance(rec.get('message'), dict) else rec.get('content')
    blocks, unknown = [], []
    if isinstance(content, str):
        text = legacy._clean_cli_user_text(content) if role == 'user' else content
        if text.strip():
            blocks.append({'type': 'text', 'text': text})
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                unknown.append(block)
                continue
            kind = block.get('type')
            if kind in ('text', 'input_text', 'output_text'):
                text = block.get('text') or ''
                if role == 'user':
                    text = legacy._clean_cli_user_text(text)
                if text.strip():
                    blocks.append({'type': 'text', 'text': text})
            elif kind in ('thinking', 'reasoning'):
                text = block.get('thinking') or block.get('text') or ''
                if text.strip() or block.get('signature'):
                    blocks.append({'type': 'thinking', 'text': text})
            elif kind in ('tool_use', 'tool-call'):
                args = block.get('input') or block.get('args') or {}
                if isinstance(args, str):
                    args = common.loads_or_none(args) or {'raw': args}
                tool = legacy._normalize_cli_tool(block.get('name') or block.get('toolName') or 'tool', args if isinstance(args, dict) else {'value': args})
                tool['id'] = block.get('id') or block.get('toolCallId')
                blocks.append(tool)
            elif kind in ('image', 'input_image', 'image_url'):
                image = {'type': 'image', 'data_uri': '', '_image_source': block}
                blocks.append(image)
            elif kind in ('tool_result', 'tool-result'):
                blocks.append({'type': 'text', 'text': legacy._tool_result_text(block.get('content', block.get('result'))), 'tool_call_id': block.get('tool_use_id') or block.get('toolCallId')})
            else:
                unknown.append(block)
    if not blocks:
        return {'kind': 'raw', 'record_type': 'empty-or-unknown-message', 'payload': rec, '_diagnostic': True}
    event = {'kind': role if role != 'tool' else 'notice', 'ts': rec.get('timestamp'), 'model': rec.get('model', ''), 'blocks': blocks, 'is_sidechain': False}
    if role == 'tool':
        event.update(label='Tool result', text='\n'.join(b.get('text', '') for b in blocks))
    if unknown:
        event['_unknown_blocks'] = unknown
        event['_diagnostic'] = True
    # Preserve format-specific fields without changing legacy readable text.
    event['_original_record'] = rec
    return event


def _snapshot_sqlite(source, target, kind, native_id):
    """Copy only the selected IDE session. CLI stores already contain one chat."""
    src = common.connect_ro(source)
    dst = sqlite3.connect(target)
    try:
        if kind == 'cursor_cli':
            src.backup(dst, pages=128)
        else:
            dst.execute('CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)')
            def copy_row(key):
                source_row = src.execute('SELECT rowid,length(value) FROM cursorDiskKV WHERE key=?', (key,)).fetchone()
                if not source_row:
                    return None
                rid, size = source_row
                if size > MAX_LINE:
                    dst.execute('INSERT OR IGNORE INTO cursorDiskKV VALUES(?,zeroblob(?))', (key, size))
                    dest_id = dst.execute('SELECT rowid FROM cursorDiskKV WHERE key=?', (key,)).fetchone()[0]
                    with src.blobopen('cursorDiskKV', 'value', rid, readonly=True) as src_blob, dst.blobopen('cursorDiskKV', 'value', dest_id) as dst_blob:
                        while block := src_blob.read(256 * 1024):
                            dst_blob.write(block)
                    return None
                raw = src.execute('SELECT value FROM cursorDiskKV WHERE key=?', (key,)).fetchone()[0]
                dst.execute('INSERT OR IGNORE INTO cursorDiskKV VALUES(?,?)', (key, raw))
                return raw
            key = 'composerData:' + native_id
            initial_version = src.execute('PRAGMA data_version').fetchone()[0]
            if not src.execute('SELECT 1 FROM cursorDiskKV WHERE key=?', (key,)).fetchone():
                raise ValueError('Cursor session metadata was not found')
            initial_metadata = copy_row(key)
            prefix = 'bubbleId:' + native_id + ':'
            last = prefix
            while True:
                # Keyset pages close each source read before the next batch. WAL
                # is visible through mode=ro; no immutable=1 bypass.
                rows = src.execute('SELECT key FROM cursorDiskKV WHERE key>? AND key<? ORDER BY key LIMIT 64', (last, prefix + '\uffff')).fetchall()
                if not rows:
                    break
                for (key,) in rows:
                    value = copy_row(key)
                    bubble = common.loads_or_none(value) or {}
                    tf = bubble.get('toolFormerData') or {}
                    result = tf.get('result') if isinstance(tf, dict) else None
                    if isinstance(result, str):
                        result = common.loads_or_none(result)
                    if isinstance(result, dict):
                        for field in ('beforeContentId', 'afterContentId'):
                            cid = result.get(field)
                            if cid:
                                content_key = str(cid) if str(cid).startswith('composer.content.') else 'composer.content.' + str(cid)
                                copy_row(content_key)
                last = rows[-1][0]
                dst.commit()
            # Avoid publishing a mixed active-session snapshot.
            if src.execute('PRAGMA data_version').fetchone()[0] != initial_version:
                raise ValueError('Cursor updated this session during snapshot; retry after the turn finishes')
            dst.commit()
    finally:
        dst.close()
        src.close()


def iter_ide_events(path, native_id, raw_dir):
    """Disk-backed header order; only one bubble/result enters Python at once."""
    conn = common.connect_ro(path)
    scratch = sqlite3.connect(Path(raw_dir) / 'ide-order.db')
    try:
        def bounded_value(key):
            found = conn.execute('SELECT rowid,length(value) FROM cursorDiskKV WHERE key=?', (key,)).fetchone()
            if not found:
                return None
            rid, size = found
            if size > MAX_LINE:
                target = Path(raw_dir) / f'ide-oversize-{rid}.bin'
                with conn.blobopen('cursorDiskKV', 'value', rid, readonly=True) as src, target.open('wb') as out:
                    shutil.copyfileobj(src, out, 256 * 1024)
                return {'kind': 'raw', 'record_type': 'oversized-ide-record', 'payload': {'key': key, 'bytes': size}, '_raw_path': str(target), '_diagnostic': True}
            return common.loads_or_none(conn.execute('SELECT value FROM cursorDiskKV WHERE key=?', (key,)).fetchone()[0]) or {}
        scratch.executescript('DROP TABLE IF EXISTS ordering; CREATE TABLE ordering(bid TEXT PRIMARY KEY,pos INTEGER,header TEXT);')
        data = bounded_value('composerData:' + native_id) or {}
        if data.get('_diagnostic'):
            yield 1, data
            return
        headers = data.pop('fullConversationHeadersOnly', [])
        for pos, header in enumerate(headers):
            scratch.execute('INSERT OR IGNORE INTO ordering VALUES(?,?,?)', (header.get('bubbleId'), pos, json.dumps(header)))
        del headers
        scratch.commit()
        model = (data.get('modelConfig') or {}).get('modelName', '')
        scratch.executescript('DROP TABLE IF EXISTS recovered; DROP TABLE IF EXISTS anchors; CREATE TABLE recovered(hash TEXT PRIMARY KEY,text TEXT,stamp INTEGER,payload TEXT,pos INTEGER); CREATE TABLE anchors(pos INTEGER PRIMARY KEY,stamp INTEGER);')
        kept = Path(raw_dir) / 'kept-text.bin'
        with kept.open('wb') as out:
            for bid, pos, hraw in scratch.execute('SELECT bid,pos,header FROM ordering ORDER BY pos'):
                bubble = bounded_value(f'bubbleId:{native_id}:{bid}') or {}
                if bubble.get('text'):
                    out.write(bubble['text'].encode('utf-8', errors='replace'))
                if isinstance(bubble.get('toolFormerData'), dict):
                    start, end = legacy.cursor_binary.call_times_ms(bubble['toolFormerData'])
                    if end or start:
                        scratch.execute('INSERT INTO anchors VALUES(?,?)', (pos, end or start))
        def in_kept(text):
            needle = text.encode('utf-8', errors='replace')
            tail = b''
            with kept.open('rb') as source:
                while chunk := source.read(1024**2):
                    chunk = tail + chunk
                    if needle in chunk:
                        return True
                    tail = chunk[-max(len(needle) - 1, 0):] if len(needle) > 1 else b''
            return False
        prefix = f'bubbleId:{native_id}:'
        for (key,) in conn.execute('SELECT key FROM cursorDiskKV WHERE key>=? AND key<? ORDER BY key', (prefix, prefix + '\uffff')):
            if scratch.execute('SELECT 1 FROM ordering WHERE bid=?', (key[len(prefix):],)).fetchone():
                continue
            bubble = bounded_value(key) or {}
            text = bubble.get('text') or ''
            stamp = legacy._parse_ms(bubble.get('createdAt'))
            if bubble.get('type') != 2 or bubble.get('toolFormerData') or not text.strip() or stamp is None or in_kept(text):
                continue
            digest = hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()
            previous = scratch.execute('SELECT stamp FROM recovered WHERE hash=?', (digest,)).fetchone()
            if previous is None or stamp < previous[0]:
                scratch.execute('INSERT OR REPLACE INTO recovered(hash,text,stamp,payload) VALUES(?,?,?,?)', (digest, text, stamp, json.dumps(bubble)))
        # SQL performs substring suppression on disk, matching the legacy
        # orphan recovery semantics without a Python corpus-sized text list.
        scratch.execute('DELETE FROM recovered WHERE hash IN (SELECT a.hash FROM recovered a JOIN recovered b ON a.hash<>b.hash WHERE instr(b.text,a.text)>0)')
        last_position = scratch.execute('SELECT coalesce(max(pos),-1)+1 FROM ordering').fetchone()[0]
        for digest, stamp in scratch.execute('SELECT hash,stamp FROM recovered'):
            before = scratch.execute('SELECT max(pos) FROM anchors WHERE stamp<=?', (stamp,)).fetchone()[0]
            after = scratch.execute('SELECT min(pos) FROM anchors WHERE stamp>?', (stamp,)).fetchone()[0]
            if before is not None:
                following_user = scratch.execute("SELECT min(pos) FROM ordering WHERE pos>? AND json_extract(header,'$.type')=1", (before,)).fetchone()[0]
                position = following_user if following_user is not None else last_position
                if after is not None:
                    position = min(position, after)
            else:
                position = after if after is not None else last_position
            scratch.execute('UPDATE recovered SET pos=? WHERE hash=?', (position, digest))
        scratch.commit()
        def recovered_at(position):
            for (payload,) in scratch.execute('SELECT payload FROM recovered WHERE pos=? ORDER BY stamp', (position,)):
                bubble = json.loads(payload)
                yield {'kind': 'assistant', 'ts': bubble.get('createdAt'), 'model': model, 'blocks': [{'type': 'text', 'text': bubble['text']}], 'is_sidechain': False, 'recovered': True, '_original_record': bubble}
        seq = 0
        for bid, pos, hraw in scratch.execute('SELECT bid,pos,header FROM ordering ORDER BY pos'):
            for recovered in recovered_at(pos):
                seq += 1
                yield seq, recovered
            bubble = bounded_value(f'bubbleId:{native_id}:{bid}')
            if bubble is None:
                yield seq, {'kind': 'raw', 'record_type': 'missing-bubble', 'payload': {'bubble_id': bid}, '_diagnostic': True}
                seq += 1
                continue
            if bubble.get('_diagnostic'):
                seq += 1
                yield seq, bubble
                continue
            header = json.loads(hraw)
            model = legacy._model_from_bubble(bubble) or model
            text = bubble.get('text') or ''
            blocks = []
            if bubble.get('type') == 1:
                notice = legacy._synthetic_user_notice(text)
                if notice:
                    event = {'kind': 'notice', **notice}
                else:
                    event = {'kind': 'user', 'blocks': [{'type': 'text', 'text': text}]}
            else:
                thinking = bubble.get('thinking') or {}
                if isinstance(thinking, dict) and thinking.get('text'):
                    blocks.append({'type': 'thinking', 'text': thinking['text']})
                if text.strip():
                    blocks.append({'type': 'text', 'text': text})
                if isinstance(bubble.get('toolFormerData'), dict):
                    # Oversize file snapshots must not be expanded by the legacy
                    # edit diff helper; retain them as raw diagnostics instead.
                    tf = bubble['toolFormerData']
                    result = tf.get('result')
                    if isinstance(result, str):
                        result = common.loads_or_none(result)
                    oversized = []
                    if isinstance(result, dict):
                        for field in ('beforeContentId', 'afterContentId'):
                            cid = result.get(field)
                            if cid:
                                key = str(cid) if str(cid).startswith('composer.content.') else 'composer.content.' + str(cid)
                                found = conn.execute('SELECT length(value) FROM cursorDiskKV WHERE key=?', (key,)).fetchone()
                                if found and found[0] > MAX_LINE:
                                    oversized.append(bounded_value(key))
                        if oversized:
                            tf = dict(tf, result={k: v for k, v in result.items() if k not in ('beforeContentId', 'afterContentId')})
                    blocks.append(legacy._normalize_tool(conn, tf))
                    for diagnostic in oversized:
                        seq += 1
                        yield seq, diagnostic
                if not blocks and bubble.get('type') == 2:
                    continue
                event = {'kind': 'assistant', 'blocks': blocks} if blocks else {'kind': 'raw', 'record_type': 'unknown-bubble', 'payload': bubble, '_diagnostic': True}
            event.update(ts=header.get('createdAt') or bubble.get('createdAt'), model=model, is_sidechain=False, _original_record=bubble)
            if event['kind'] in ('user', 'notice'):
                event.pop('model', None)
            seq += 1
            yield seq, event
        for recovered in recovered_at(last_position):
            seq += 1
            yield seq, recovered
    finally:
        scratch.close()
        conn.close()


def iter_cli_events(path, raw_dir):
    """Two disk passes join CLI tool results, using SQLite for deduplication."""
    conn = common.connect_ro(path)
    scratch = sqlite3.connect(Path(raw_dir) / 'cli-messages.db')
    try:
        scratch.executescript('DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS results; CREATE TABLE messages(seq INTEGER PRIMARY KEY,hash TEXT UNIQUE,payload TEXT); CREATE TABLE results(id TEXT PRIMARY KEY,payload TEXT);')
        seq = 0
        for rowid, length in conn.execute('SELECT rowid,length(data) FROM blobs ORDER BY rowid'):
            if length > MAX_LINE:
                # blobopen reads without materializing oversized records.
                target = Path(raw_dir) / f'cli-oversize-{rowid}.bin'
                with conn.blobopen('blobs', 'data', rowid, readonly=True) as src, target.open('wb') as out:
                    shutil.copyfileobj(src, out, 256 * 1024)
                seq += 1
                event = {'kind': 'raw', 'record_type': 'oversized-cli-blob', 'payload': {'rowid': rowid, 'bytes': length}, '_raw_path': str(target), '_diagnostic': True}
                scratch.execute('INSERT INTO messages VALUES(?,?,?)', (seq, file_hash(target), json.dumps(event)))
                continue
            raw = conn.execute('SELECT data FROM blobs WHERE rowid=?', (rowid,)).fetchone()[0]
            if isinstance(raw, str):
                raw = raw.encode()
            objects = legacy._extract_json_objects(raw)
            for msg in objects:
                if not isinstance(msg, dict) or msg.get('role') not in ('user', 'assistant', 'tool'):
                    continue
                payload = json.dumps(msg, ensure_ascii=False, sort_keys=True)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                seq += 1
                scratch.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?)', (seq, digest, payload))
                if msg.get('role') == 'tool':
                    for block in msg.get('content') or []:
                        if isinstance(block, dict) and block.get('type') == 'tool-result':
                            call = block.get('toolCallId') or msg.get('id')
                            if call:
                                result = {'is_error': bool(block.get('is_error') or block.get('isError')), 'text': legacy._tool_result_text(block.get('result')), 'images': []}
                                scratch.execute('INSERT OR REPLACE INTO results VALUES(?,?)', (call, json.dumps(result)))
            if rowid % 200 == 0:
                scratch.commit()
        scratch.commit()
        previous = None
        output_position = 0
        model = legacy._read_store_meta(conn).get('lastUsedModel') or ''
        for pos, payload in scratch.execute('SELECT seq,payload FROM messages ORDER BY seq'):
            msg = json.loads(payload)
            if msg.get('kind'):
                output_position += 1
                yield output_position, msg
                continue
            if msg.get('role') == 'tool':
                continue
            if msg.get('role') == 'user':
                text = common.content_text(msg.get('content'))
                if '<user_info>' in text and not legacy._CLI_USER_QUERY_RE.search(text):
                    output_position += 1
                    yield output_position, {'kind': 'instructions', 'role': 'system', 'label': 'Cursor context', 'text': text}
                    continue
                reminder = legacy._CLI_SYSTEM_REMINDER_RE.search(text)
                if reminder and not legacy._CLI_USER_QUERY_RE.search(text):
                    output_position += 1
                    yield output_position, {'kind': 'notice', 'label': 'System reminder', 'text': reminder.group(1).strip() or text.strip(), 'is_sidechain': False}
                    text = legacy._CLI_SYSTEM_REMINDER_RE.sub('', text)
                    if not legacy._clean_cli_user_text(text):
                        continue
                    msg = dict(msg, content=text)
            event = normalize_jsonl(msg)
            if event['kind'] == 'assistant':
                event['model'] = model
            elif event['kind'] == 'user':
                event.pop('model', None)
            for block in event.get('blocks', []):
                if block.get('type') == 'tool_use' and block.get('id'):
                    result = scratch.execute('SELECT payload FROM results WHERE id=?', (block['id'],)).fetchone()
                    if result:
                        block['result'] = json.loads(result[0])
            digest = hashlib.sha256(json_bytes(event.get('blocks'))).digest()
            if event['kind'] == 'assistant' and digest == previous:
                continue
            previous = digest if event['kind'] == 'assistant' else None
            output_position += 1
            yield output_position, event
    finally:
        scratch.close()
        conn.close()


def read_content(conn, content_id):
    row = conn.execute(select(db.contents).where(db.contents.c.id == content_id)).mappings().one()
    with Path(row['path']).open('rb') as stream:
        stream.seek((row['metadata_json'] or {}).get('offset', 0))
        return stream.read(row['bytes'])


def hydrate_event(conn, event):
    ref = event.get('_full_content_id')
    return json.loads(read_content(conn, ref)) if ref else event


class IndexWriter:
    def __init__(self, config, conn, revision_id, source_base=None, asset_map=None):
        self.config, self.conn, self.revision_id = config, conn, revision_id
        self.source_base = Path(source_base) if source_base else None
        self.asset_map = asset_map or {}
        folder = config.home / 'raw' / revision_id
        folder.mkdir(parents=True, exist_ok=True)
        self.pack_path = folder / 'events.pack'
        # A killed child may have appended bytes beyond the last committed
        # checkpoint; reclaim that uncommitted tail before resuming.
        checkpoint = conn.execute(select(db.jobs.c.checkpoint_json).where(db.jobs.c.revision_id == revision_id).order_by(db.jobs.c.created_at.desc()).limit(1)).scalar_one_or_none() or {}
        if self.pack_path.exists():
            with self.pack_path.open('r+b') as stream:
                stream.truncate(checkpoint.get('pack_bytes', 0))
        self.pack = self.pack_path.open('ab')

    def close(self):
        self.pack.close()

    def content(self, payload, kind, event_id):
        cid = db.new_id()
        raw = payload.encode('utf-8', errors='replace') if isinstance(payload, str) else json_bytes(payload)
        if kind == 'event':
            offset = self.pack.tell()
            self.pack.write(raw)
            path = self.pack_path
        else:
            offset = 0
            path = self.config.home / 'contents' / cid
            path.write_bytes(raw)
        self.conn.execute(db.contents.insert().values(id=cid, revision_id=self.revision_id, event_id=event_id, kind=kind, path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), metadata_json={'offset': offset}))
        return cid

    def _image(self, block, event_id):
        existing = block.get('asset_id')
        if existing:
            if existing in self.asset_map:
                return dict(block, asset_id=self.asset_map[existing])
            # Incremental revisions may legitimately reference their own older
            # immutable assets; remote imports must have a manifest map.
            if self.source_base is None:
                return {'type': 'image', 'data_uri': '', 'missing': True, 'reason': 'Image excluded or absent from bundle'}
            return block
        source = block.get('_image_source', block)
        src = source.get('source') or {}
        url = source.get('image_url') or source.get('data_uri') or source.get('url') or src.get('url') or ''
        if isinstance(url, dict):
            url = url.get('url', '')
        raw, filename, mime, reason = None, '', '', None
        path_value = source.get('path') or source.get('file_path') or src.get('path')
        if src.get('type') == 'base64' and isinstance(src.get('data'), str):
            url = f"data:{src.get('media_type','image/png')};base64,{src['data']}"
        if isinstance(url, str) and url.startswith('data:image/'):
            try:
                prefix, b64 = url.split(',', 1)
                mime = prefix[5:].split(';', 1)[0]
                raw = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError):
                reason = 'Invalid inline image'
        elif path_value and self.source_base is not None:
            path = Path(path_value).expanduser()
            if not path.is_absolute():
                path = self.source_base / path
            filename = path.name
            mime = mimetypes.guess_type(filename)[0] or ''
            image_valid = False
            if path.is_file() and mime.startswith('image/') and mime != 'image/svg+xml' and path.stat().st_size <= self.config.max_package_bytes:
                try:
                    from PIL import Image
                    with Image.open(path) as probe:
                        mime = Image.MIME.get(probe.format, mime)
                        probe.verify()
                    image_valid = True
                except Exception:
                    reason = 'Referenced image has invalid or unsupported image data'
            if image_valid:
                digest = file_hash(path)
                target = self.config.home / 'assets' / digest
                if not target.exists():
                    with path.open('rb') as source_stream, target.open('wb') as dest:
                        shutil.copyfileobj(source_stream, dest, 256 * 1024)
                aid = db.new_id()
                self.conn.execute(db.assets.insert().values(id=aid, revision_id=self.revision_id, event_id=event_id, path=str(target), bytes=target.stat().st_size, sha256=digest, mime=mime, name=filename))
                return {'type': 'image', 'data_uri': '', 'asset_id': aid, 'name': filename}
            reason = 'Referenced image is unavailable or is not an image'
        elif url:
            reason = 'Remote image is not fetched automatically'
        else:
            reason = 'No readable image source was recorded'
        aid = db.new_id()
        if raw is not None and mime.startswith('image/'):
            try:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(raw)) as probe:
                    mime = Image.MIME.get(probe.format, 'image/png')
                    probe.verify()
            except Exception:
                raw = None
                reason = 'Invalid inline image data'
        if raw is not None and mime.startswith('image/'):
            digest = hashlib.sha256(raw).hexdigest()
            target = self.config.home / 'assets' / digest
            if not target.exists():
                target.write_bytes(raw)
            self.conn.execute(db.assets.insert().values(id=aid, revision_id=self.revision_id, event_id=event_id, path=str(target), bytes=len(raw), sha256=digest, mime=mime, name=filename or 'image'))
            return {'type': 'image', 'data_uri': '', 'asset_id': aid, 'name': filename or 'image'}
        self.conn.execute(db.assets.insert().values(id=aid, revision_id=self.revision_id, event_id=event_id, status='missing', name=filename or 'image', metadata_json={'reason': reason}))
        return {'type': 'image', 'data_uri': '', 'asset_id': aid, 'missing': True, 'reason': reason}

    def images(self, value, event_id):
        if isinstance(value, list):
            return [self.images(v, event_id) for v in value]
        if not isinstance(value, dict):
            return value
        if value.get('type') == 'image':
            return self._image(value, event_id)
        result = {}
        for key, child in value.items():
            # Original source records remain preserved, never interpreted as
            # extra attachments or scanned for arbitrary filesystem paths.
            result[key] = child if key.startswith('_original') else self.images(child, event_id)
        # Cursor may use explicit XML image attachment wrappers in user text.
        if value.get('kind') == 'user' and self.source_base:
            extra = []
            for block in value.get('blocks', []):
                if block.get('type') == 'text':
                    for match in re.finditer(r'<image\b[^>]*\bpath=["\']([^"\']+)["\'][^>]*>', block.get('text', ''), re.I):
                        extra.append(self._image({'type': 'image', 'path': match[1]}, event_id))
            if extra:
                result['blocks'] = result.get('blocks', []) + extra
        return result

    def compact(self, value, event_id):
        if isinstance(value, list):
            return [self.compact(v, event_id) for v in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, child in value.items():
            if key in ('_original_record', '_raw_path', '_image_source'):
                continue
            if isinstance(child, str) and len(child.encode('utf-8', errors='replace')) > PREVIEW:
                cid = self.content(child, 'text', event_id)
                result[key] = child[:PREVIEW // 4]
                if key == 'text':
                    result.update(content_id=cid, has_more=True)
                else:
                    result[key + '_content_id'] = cid
                    result[key + '_has_more'] = True
            elif key in ('input', 'payload', '_unknown_blocks') and isinstance(child, (dict, list)) and len(json_bytes(child)) > PREVIEW:
                cid = self.content(child, 'json', event_id)
                result[key] = {'preview': json_bytes(child)[:512].decode('utf-8', errors='replace'), 'content_id': cid, 'has_more': True, 'kind': 'json'}
            else:
                result[key] = self.compact(child, event_id)
        return result

    def add(self, event, event_id):
        from event_schema import validate_event
        errors = validate_event(event)
        if errors:
            event = {'kind': 'raw', 'record_type': 'invalid-event-shape', 'payload': event, '_diagnostic': True, 'validation_errors': errors[:10]}
        event = self.images(event, event_id)
        raw_path = event.pop('_raw_path', None)
        if raw_path:
            cid = db.new_id()
            self.conn.execute(db.contents.insert().values(id=cid, revision_id=self.revision_id, event_id=event_id, kind='raw', path=raw_path, bytes=Path(raw_path).stat().st_size, sha256=file_hash(raw_path)))
            event['raw_content_id'] = cid
        compact = self.compact(event, event_id)
        compact['_full_content_id'] = self.content(event, 'event', event_id)
        # A pathological event containing many small blocks is externalized as
        # a whole. Its exact representation remains accessible as JSON text.
        if len(json_bytes(compact)) > 128 * 1024:
            compact = {'kind': event.get('kind', 'raw'), 'text': 'Large message — load its recorded content', 'content_id': compact['_full_content_id'], 'has_more': True, '_full_content_id': compact['_full_content_id'], '_diagnostic': event.get('_diagnostic', False)}
        return compact, event


def _check_job(conn, job_id):
    row = conn.execute(select(db.jobs.c.state, db.jobs.c.cancel_requested).where(db.jobs.c.id == job_id)).one()
    if row.cancel_requested or row.state in ('cancelled', 'paused'):
        raise JobInterrupted(row.state)


def _copy_incremental(engine, job_id, revision_id):
    """Clone the prior immutable index in resumable short write transactions."""
    with engine.connect() as conn:
        checkpoint = conn.execute(select(db.jobs.c.checkpoint_json).where(db.jobs.c.id == job_id)).scalar_one()
        previous = conn.execute(select(db.revisions).where(db.revisions.c.id == checkpoint['incremental_from'])).mappings().one()
        while True:
            _check_job(conn, job_id)
            batch = conn.execute(select(db.events).where(db.events.c.revision_id == previous['id'], db.events.c.seq > checkpoint.get('clone_seq', 0)).order_by(db.events.c.seq).limit(200)).mappings().all()
            if not batch:
                break
            for row in batch:
                eid = db.new_id()
                conn.execute(db.events.insert().values(id=eid, revision_id=revision_id, seq=row['seq'], round_number=row['round_number'], kind=row['kind'], event_json=row['event_json'], byte_size=row['byte_size']))
                from sqlalchemy import literal
                conn.execute(db.search_tokens.insert().from_select(['session_id', 'revision_id', 'event_id', 'token'], select(db.search_tokens.c.session_id, literal(revision_id), literal(eid), db.search_tokens.c.token).where(db.search_tokens.c.event_id == row['id'], db.search_tokens.c.revision_id == previous['id'])))
            checkpoint = dict(checkpoint, clone_seq=batch[-1]['seq'])
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(checkpoint_json=checkpoint, lease_until=db.now() + 60, updated_at=db.now()))
            conn.commit()
        while True:
            _check_job(conn, job_id)
            rows = conn.execute(select(db.rounds).where(db.rounds.c.revision_id == previous['id'], db.rounds.c.number > checkpoint.get('clone_round', 0)).order_by(db.rounds.c.number).limit(200)).mappings().all()
            if not rows:
                break
            for row in rows:
                conn.execute(db.rounds.insert().values(**dict(row, id=db.new_id(), revision_id=revision_id)))
            checkpoint = dict(checkpoint, clone_round=rows[-1]['number'])
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(checkpoint_json=checkpoint, lease_until=db.now() + 60, updated_at=db.now()))
            conn.commit()
        checkpoint = dict(checkpoint, clone_complete=True, position=previous['source_size'], seq=previous['event_count'], round=previous['round_count'], diagnostics=previous['diagnostic_count'])
        conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(checkpoint_json=checkpoint, updated_at=db.now()))
        conn.commit()


def index_events(config, job_id, iterator, *, revision_id=None, source_base=None, asset_map=None):
    """Index normalized event iterator. Every committed checkpoint is resumable."""
    engine = db.get_engine(config)
    with engine.connect() as conn:
        job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
        revision_id = revision_id or job['revision_id']
        checkpoint = job['checkpoint_json'] or {}
        seq = checkpoint.get('seq', 0)
        round_number = checkpoint.get('round', 0)
        diagnostics = checkpoint.get('diagnostics', 0)
        writer = IndexWriter(config, conn, revision_id, source_base, asset_map)
        batch_count = batch_bytes = 0
        round_data = None
        if round_number:
            row = conn.execute(select(db.rounds).where(db.rounds.c.revision_id == revision_id, db.rounds.c.number == round_number)).mappings().first()
            round_data = dict(row) if row else None
        try:
            for position, original in iterator:
                if position <= checkpoint.get('position', -1):
                    continue
                if not batch_count:
                    _check_job(conn, job_id)
                seq += 1
                event_id = db.new_id()
                compact, event = writer.add(original, event_id)
                if event.get('kind') == 'user' or event.get('document_page') or round_number == 0:
                    if round_data:
                        conn.execute(db.rounds.update().where(db.rounds.c.id == round_data['id']).values(end_seq=seq - 1, count=seq - round_data['start_seq']))
                    round_number += 1
                    preview = ' '.join(text_values({k: v for k, v in event.items() if not k.startswith('_')}))[:200]
                    round_data = {'id': db.new_id(), 'revision_id': revision_id, 'number': round_number, 'start_seq': seq, 'end_seq': seq, 'preview': preview, 'count': 1}
                    conn.execute(db.rounds.insert().values(**round_data))
                row = {'id': event_id, 'revision_id': revision_id, 'seq': seq, 'round_number': round_number, 'kind': event.get('kind', 'raw'), 'event_json': compact, 'byte_size': len(json_bytes(compact))}
                conn.execute(db.events.insert().values(**row))
                # Dictionary size is bounded to one <=8MiB source record.
                tokens = set()
                for text in text_values({k: v for k, v in event.items() if not k.startswith('_')}):
                    tokens.update(tokenize(text))
                token_rows = [{'session_id': job['session_id'], 'revision_id': revision_id, 'event_id': event_id, 'token': token} for token in tokens]
                for start in range(0, len(token_rows), 500):
                    conn.execute(db.search_tokens.insert(), token_rows[start:start + 500])
                diagnostics += bool(event.get('_diagnostic'))
                batch_count += 1
                batch_bytes += row['byte_size']
                checkpoint = dict(checkpoint, position=position, seq=seq, round=round_number, diagnostics=diagnostics)
                if batch_count >= BATCH_EVENTS or batch_bytes >= BATCH_BYTES:
                    writer.pack.flush()
                    checkpoint['pack_bytes'] = writer.pack.tell()
                    conn.execute(db.rounds.update().where(db.rounds.c.id == round_data['id']).values(end_seq=seq, count=seq - round_data['start_seq'] + 1))
                    conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(progress=position, checkpoint_json=checkpoint, updated_at=db.now(), lease_until=db.now() + 60))
                    conn.commit()
                    batch_count = batch_bytes = 0
            writer.pack.flush()
            _check_job(conn, job_id)
            if round_data:
                conn.execute(db.rounds.update().where(db.rounds.c.id == round_data['id']).values(end_seq=seq, count=seq - round_data['start_seq'] + 1))
            state = 'ready_with_diagnostics' if diagnostics else 'ready'
            conn.execute(db.revisions.update().where(db.revisions.c.id == revision_id).values(state=state, event_count=seq, round_count=round_number, diagnostic_count=diagnostics, updated_at=db.now()))
            session = conn.execute(select(db.sessions).where(db.sessions.c.id == job['session_id'])).mappings().one()
            values = {'current_revision': revision_id, 'event_count': seq, 'round_count': round_number, 'status': state, 'updated_at': db.now()}
            revision_meta = conn.execute(select(db.revisions.c.metadata_json).where(db.revisions.c.id == revision_id)).scalar_one() or {}
            published = dict(revision_meta.get('publish_metadata') or {})
            if published:
                if (session['metadata_json'] or {}).get('custom_title'):
                    published.pop('title', None)
                values.update(published)
            if config.mode == 'local' and session['source_kind'] in ('cursor_jsonl', 'jsonl'):
                first = conn.execute(select(db.events.c.event_json).where(db.events.c.revision_id == revision_id, db.events.c.kind == 'user').order_by(db.events.c.seq).limit(1)).scalar_one_or_none()
                if first:
                    prompt = first.get('text') or next((block.get('text', '') for block in first.get('blocks', []) if block.get('type') == 'text'), '')
                    derived = common.short_title(prompt, 120)
                    if derived:
                        metadata = session['metadata_json'] or {}
                        original_title = session['original_title'] or ''
                        native_title = session['native_id'] or Path(job['payload_json'].get('path', '')).stem
                        initial = not session['title'] or session['title'] in (native_title, '未命名会话')
                        if not metadata.get('custom_title') and initial and (not original_title or session['title'] == original_title):
                            values['title'] = derived
                        if not original_title or original_title == native_title:
                            values['original_title'] = derived
            if session['sync_status'] == 'synced':
                values['sync_status'] = 'update_pending'
            conn.execute(db.sessions.update().where(db.sessions.c.id == job['session_id']).values(**values))
            if session['source_id']:
                conn.execute(db.sources.update().where(db.sources.c.id == session['source_id']).values(status='indexed'))
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state='succeeded', progress=job['total'] or checkpoint.get('position', 0), checkpoint_json=checkpoint, result_json={'revision_id': revision_id, 'event_count': seq, 'diagnostic_count': diagnostics}, updated_at=db.now()))
            db.emit(conn, 'session', owner_id=job['owner_id'], session_id=job['session_id'], revision_id=revision_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            writer.close()
    engine.dispose()


def ingest_path(config, job_id):
    engine = db.get_engine(config)
    with engine.begin() as conn:
        job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id)).mappings().one()
        payload = job['payload_json'] or {}
        source = None
        if payload.get('source_id'):
            source = conn.execute(select(db.sources).where(db.sources.c.id == payload['source_id'])).mappings().one()
        path = Path(payload.get('path') or source['path'])
        kind = payload.get('source_kind') or (source or {}).get('source_kind') or 'cursor_jsonl'
        native_id = payload.get('native_id') or (source or {}).get('native_id') or path.stem
        revision_id = job['revision_id']
        if not revision_id:
            revision_id = db.new_id()
            conn.execute(db.revisions.insert().values(id=revision_id, session_id=job['session_id'], source_path=str(path)))
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(revision_id=revision_id))
    folder = config.home / 'raw' / revision_id
    folder.mkdir(parents=True, exist_ok=True)
    document = kind in ('markdown', 'html', 'pdf')
    snapshot = folder / ('source.' + kind if document else 'source.jsonl' if kind in ('cursor_jsonl', 'jsonl') else 'source.db')
    with engine.connect() as conn:
        rev = conn.execute(select(db.revisions).where(db.revisions.c.id == revision_id)).mappings().one()
    if not rev['source_hash']:
        temporary = snapshot.with_suffix('.partial')
        if kind in ('cursor_jsonl', 'jsonl') or document:
            with path.open('rb') as src, temporary.open('wb') as dest:
                # Capture the initial length so an active file cannot make a
                # snapshot grow indefinitely while the user keeps working.
                remaining = path.stat().st_size
                while remaining:
                    chunk = src.read(min(remaining, 1024**2))
                    if not chunk:
                        break
                    dest.write(chunk)
                    remaining -= len(chunk)
        else:
            if temporary.exists():
                temporary.unlink()
            _snapshot_sqlite(path, temporary, kind, native_id)
        os.replace(temporary, snapshot)
        digest = file_hash(snapshot)
        size = snapshot.stat().st_size
        checkpoint = {'position': -1, 'seq': 0, 'round': 0, 'diagnostics': 0}
        with engine.begin() as conn:
            session = conn.execute(select(db.sessions).where(db.sessions.c.id == job['session_id'])).mappings().one()
            if kind in ('cursor_jsonl', 'jsonl') and session['current_revision']:
                previous = conn.execute(select(db.revisions).where(db.revisions.c.id == session['current_revision'])).mappings().one()
                old_size = previous['source_size']
                if old_size == size and previous['source_hash'] == digest:
                    conn.execute(db.revisions.update().where(db.revisions.c.id == revision_id).values(state='unchanged', source_hash=digest, source_size=size))
                    conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state='succeeded', progress=size, total=size, result_json={'revision_id': previous['id'], 'unchanged': True}))
                    return
                # The old final record must have a newline; otherwise appending
                # may complete that record and requires a fresh parse.
                old_path = Path(previous['metadata_json'].get('snapshot', ''))
                complete_line = False
                if old_size and old_path.is_file():
                    with old_path.open('rb') as src:
                        src.seek(-1, 2)
                        complete_line = src.read(1) == b'\n'
                if 0 < old_size < size and complete_line and file_hash(snapshot, old_size) == previous['source_hash']:
                    checkpoint['incremental_from'] = previous['id']
            conn.execute(db.revisions.update().where(db.revisions.c.id == revision_id).values(source_hash=digest, source_size=size, metadata_json={'snapshot': str(snapshot), 'source_kind': kind, 'native_id': native_id, 'incremental': 'incremental_from' in checkpoint}))
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(total=size, checkpoint_json=checkpoint))
    with engine.connect() as conn:
        checkpoint = conn.execute(select(db.jobs.c.checkpoint_json).where(db.jobs.c.id == job_id)).scalar_one() or {}
    if checkpoint.get('incremental_from') and not checkpoint.get('clone_complete'):
        _copy_incremental(engine, job_id, revision_id)
        with engine.connect() as conn:
            checkpoint = conn.execute(select(db.jobs.c.checkpoint_json).where(db.jobs.c.id == job_id)).scalar_one()
    if kind in ('cursor_jsonl', 'jsonl'):
        iterator = ((offset, normalize_jsonl(rec)) for offset, rec in iter_jsonl_records(snapshot, folder, max(checkpoint.get('position', 0), 0)))
    elif kind == 'cursor_ide':
        iterator = iter_ide_events(snapshot, native_id, folder)
    elif kind == 'cursor_cli':
        iterator = iter_cli_events(snapshot, folder)
    elif document:
        from .documents import iter_document
        iterator = iter_document(snapshot, kind)
    else:
        raise ValueError('Unsupported Cursor source kind: ' + kind)
    source_base = Path(source['path']).parent if source else path.parent
    index_events(config, job_id, iterator, revision_id=revision_id, source_base=source_base)
    engine.dispose()
