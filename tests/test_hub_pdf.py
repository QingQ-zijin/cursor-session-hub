import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from hub import db
from hub.api import create_app, job_public
from hub.bundles import export_job
from hub.config import Config
from hub.export_names import filename
from hub.ingest import ingest_path, JobInterrupted


@pytest.mark.parametrize('fmt,suffix,mime', [('markdown','.md','text/markdown'),('html','.html','text/html'),('pdf','.pdf','application/pdf')])
def test_export_job_metadata_and_download_match(tmp_path, fmt, suffix, mime):
    cfg=Config(home=tmp_path/'library',local_token='test',worker_external=True,min_free_bytes=0,min_free_ratio=0)
    app=create_app(cfg)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        source=tmp_path/'记录.jsonl';source.write_text(json.dumps({'role':'user','content':'中文问题'})+'\n'+json.dumps({'role':'assistant','content':'**完整结论**\n\n```python\nprint(123)\n```'})+'\n')
        imported=client.post('/api/v1/imports/path',headers=headers,json={'path':str(source)}).json()
        ingest_path(cfg,imported['job']['id'])
        response=client.post('/api/v1/sessions/'+imported['session_id']+'/exports',headers=headers,json={'format':fmt})
        assert response.status_code==200
        job=response.json()['job']; assert job['export_format']==fmt
        export_job(cfg,job['id'])
        saved=client.get('/api/v1/jobs/'+job['id'],headers=headers).json()
        assert saved['download_filename'].endswith(suffix) and saved['download_mime']==mime
        assert not any(key in saved for key in ('path','result_json','payload_json'))
        download=client.get('/api/v1/exports/'+job['id']+'/download',headers=headers)
        assert download.status_code==200 and download.headers['content-type'].startswith(mime)
        assert suffix in download.headers['content-disposition']
        if fmt=='pdf':
            import io
            assert download.content.startswith(b'%PDF-')
            text=''.join(page.extract_text() for page in PdfReader(io.BytesIO(download.content)).pages)
            assert '中文问题' in text and '完整结论' in text and 'print(123)' in text
        with app.state.engine.begin() as connection:
            connection.execute(db.sessions.update().where(db.sessions.c.id==imported['session_id']).values(revoked=True))
        assert client.get('/api/v1/exports/'+job['id']+'/download',headers=headers).status_code==404


def test_old_export_jobs_infer_format_without_leaking_server_paths():
    item=job_public({'id':'old','kind':'export','payload_json':{'format':'markdown'},'result_json':{'path':'/secret/folder/job.md','filename':'cursor-session.md','mime':'text/markdown'}})
    assert item['export_format']=='markdown' and item['download_filename']=='cursor-session.md'
    assert '/secret' not in json.dumps(item)
    assert filename('../a:b', 'pdf').endswith('.pdf') and '/' not in filename('../a:b','pdf')


def test_long_pdf_keeps_last_message_tables_and_pages(tmp_path):
    from hub.pdf_export import PDFWriter
    file=tmp_path/'long.pdf';checks=[]
    writer=PDFWriter(file,'中文长会话',lambda:checks.append(True));writer.heading('中文长会话',1)
    for number in range(60):
        writer.heading(f'对话 {number}')
        writer.markdown('**正文完整保留**\n\n| 名称 | 数值 |\n|---|---|\n| 中文 | 123 |\n\n```python\n'+'x = 123\n'*12+'```')
    writer.markdown('最终消息：END-OF-SESSION');writer.finish()
    pdf=PdfReader(file);assert len(pdf.pages)>10 and len(checks)>10
    text=''.join(page.extract_text() for page in pdf.pages)
    assert text.count('正文完整保留')==60 and 'END-OF-SESSION' in text
    assert any('/FontFile2' in str(font.get_object().get('/FontDescriptor',{}).get_object()) for page in pdf.pages[:1] for font in page['/Resources']['/Font'].values() if '/FontDescriptor' in font.get_object())


def test_pdf_cancellation_does_not_publish_file(tmp_path):
    from hub.pdf_export import PDFWriter
    target=tmp_path/'cancelled.partial'
    def cancelled(): raise JobInterrupted('cancelled')
    writer=PDFWriter(target,'取消测试',cancelled)
    with pytest.raises(JobInterrupted): writer.finish()
    assert not target.exists()


def test_pdf_keeps_web_links_as_annotations(tmp_path):
    from hub.pdf_export import PDFWriter
    path=tmp_path/'link.pdf';writer=PDFWriter(path,'链接',lambda:None)
    writer.markdown('[参考资料](https://example.com/reference)');writer.finish()
    pdf=PdfReader(path)
    assert any(a.get_object().get('/A',{}).get('/URI')=='https://example.com/reference' for a in pdf.pages[0]['/Annots'])
