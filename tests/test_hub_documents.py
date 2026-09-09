import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hub import db
from hub.api import create_app
from hub.config import Config
from hub.ingest import ingest_path, hydrate_event, normalize_jsonl
from hub.bundles import export_job
from hub.documents import iter_document, readable_event, markdown_renderer


def queue(engine, path=None, sid=None, kind='ingest', **payload):
    with engine.begin() as connection:
        sid = sid or db.new_id()
        if not connection.execute(select(db.sessions.c.id).where(db.sessions.c.id == sid)).first():
            connection.execute(db.sessions.insert().values(id=sid, owner_id='local', title='文档测试'))
        jid = db.new_id()
        if path: payload['path'] = str(path)
        connection.execute(db.jobs.insert().values(id=jid, owner_id='local', session_id=sid, kind=kind, state='running', payload_json=payload))
        return sid, jid


def events(engine, sid):
    with engine.connect() as connection:
        rev = connection.execute(select(db.sessions.c.current_revision).where(db.sessions.c.id == sid)).scalar_one()
        return [hydrate_event(connection, value) for value in connection.execute(select(db.events.c.event_json).where(db.events.c.revision_id == rev).order_by(db.events.c.seq)).scalars()]


@pytest.fixture
def library(tmp_path):
    cfg = Config(home=tmp_path / 'library', local_token='test-token', worker_external=True, min_free_bytes=0, min_free_ratio=0)
    cfg.prepare(); engine = db.get_engine(cfg); db.init_db(engine)
    yield cfg, engine
    engine.dispose()


def test_jsonl_envelopes_and_old_raw_export_recovery():
    for role in ('user', 'assistant'):
        record = {'type': role, 'message': {'role': role, 'content': [{'type': 'text', 'text': '**完整文字**'}]}}
        assert normalize_jsonl(record)['kind'] == role
        recovered = readable_event({'kind': 'raw', 'payload': record})
        assert recovered['blocks'][0]['text'] == '**完整文字**'


@pytest.mark.parametrize('fmt', ['html', 'markdown'])
def test_export_is_readable_and_reimports_in_order(library, tmp_path, fmt):
    cfg, engine = library
    texts = ['第一轮问题', '# 结果\n\n**加粗**\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint("ok")\n```\n\n$x^2$',
             '第二轮问题', '最后一段完整文字\n\n' + '中文长内容' * 6000]
    path = tmp_path / 'input.jsonl'
    path.write_text('\n'.join(json.dumps({'type': 'user' if i % 2 == 0 else 'assistant', 'message': {'role': 'user' if i % 2 == 0 else 'assistant', 'content': text}}, ensure_ascii=False) for i, text in enumerate(texts)) + '\n', encoding='utf-8')
    sid, jid = queue(engine, path); ingest_path(cfg, jid)
    _, job = queue(engine, sid=sid, kind='export', format=fmt); export_job(cfg, job)
    output = cfg.home / 'exports' / (job + ('.html' if fmt == 'html' else '.md'))
    content = output.read_text(encoding='utf-8')
    assert '"role": "assistant"' not in content and '"_original_record"' not in content
    if fmt == 'html':
        assert '<strong>加粗</strong>' in content and '<table>' in content and '<code class="language-python">' in content
    else: assert '**加粗**' in content and '```python' in content
    imported = [event for _, event in iter_document(output, fmt)]
    assert [e['kind'] for e in imported] == ['user', 'assistant', 'user', 'assistant']
    assert [e['blocks'][0]['text'] for e in imported] == texts


def pdf_bytes(blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for n in (1, 2):
        page = writer.add_blank_page(width=600, height=800)
        if not blank:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject(); stream.set_data(f'BT /F1 12 Tf 40 700 Td (PDF complete page {n}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
    target = io.BytesIO(); writer.write(target); return target.getvalue()


def test_pdf_pages_preserve_text_and_scans_are_diagnostics(library, tmp_path):
    cfg, engine = library
    for blank in (False, True):
        path = tmp_path / f'example-{blank}.pdf'; path.write_bytes(pdf_bytes(blank))
        sid, jid = queue(engine, path, source_kind='pdf'); ingest_path(cfg, jid)
        output = events(engine, sid)
        assert len(output) == 2 and [e['document_page'] for e in output] == [1, 2]
        if blank: assert all(e['_diagnostic'] for e in output)
        else: assert [e['text'] for e in output] == ['PDF complete page 1', 'PDF complete page 2']
        with engine.connect() as connection:
            row = connection.execute(select(db.sessions).where(db.sessions.c.id == sid)).mappings().one()
            assert row['round_count'] == 2
            assert row['status'] == ('ready_with_diagnostics' if blank else 'ready')


@pytest.mark.parametrize('suffix,body', [('.md', b'## User\n\nQuestion\n\n## Assistant\n\nAnswer'), ('.html', b'<h2>user</h2><p>Question</p><h2>assistant</h2><p>Answer</p>'), ('.pdf', None)])
def test_upload_and_native_path_share_document_adapter(library, tmp_path, suffix, body):
    cfg, engine = library; body = body if body is not None else pdf_bytes()
    with TestClient(create_app(cfg)) as client:
        headers = {'Authorization': 'Bearer test-token'}
        response = client.post('/api/v1/imports', headers=headers, files={'file': ('example' + suffix, body)})
        assert response.status_code == 200, response.text
        item = response.json(); ingest_path(cfg, item['job']['id'])
        assert len(events(engine, item['session_id'])) >= 2
        native = tmp_path / ('中文路径' + suffix); native.write_bytes(body)
        second = client.post('/api/v1/imports/path', headers=headers, json={'path': str(native)})
        assert second.status_code == 200 and second.json()['unchanged']


def test_html_never_executes_or_fetches_script_images_and_formats_text(tmp_path):
    source = tmp_path / 'page.html'
    source.write_text('<html><head><script>bad()</script></head><body><p>Hello <strong>bold</strong></p><img src="https://example.invalid/image"><script>secret()</script></body></html>')
    text = '\n'.join(event['text'] for _, event in iter_document(source, 'html'))
    assert 'Hello **bold**' in text and 'bad()' not in text and 'secret()' not in text
    assert '<script>' not in markdown_renderer().render('<script>bad()</script>')


def test_truncated_html_and_encrypted_pdf_fail_explicitly(tmp_path):
    path = tmp_path / 'broken.html'; path.write_text('<section data-csh-event="assistant"><template data-csh-block="text">unfinished')
    with pytest.raises(ValueError, match='未完整结束'): list(iter_document(path, 'html'))
    from pypdf import PdfWriter
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.encrypt('private-test-only')
    path = tmp_path / 'encrypted.pdf'
    with path.open('wb') as target: writer.write(target)
    with pytest.raises(ValueError, match='加密'): list(iter_document(path, 'pdf'))


def test_old_html_export_pre_is_restored_as_markdown(tmp_path):
    source = tmp_path / 'old.html'
    source.write_text('<section><h2>user</h2><pre>Question</pre></section><section><h2>assistant</h2><pre>**Conclusion**\n\n```python\nprint(1)\n```</pre></section>')
    output = [e for _, e in iter_document(source, 'html')]
    assert [e['kind'] for e in output] == ['user','assistant']
    assert output[1]['blocks'][0]['text'] == '**Conclusion**\n\n```python\nprint(1)\n```'
