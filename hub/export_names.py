"""The download name, UI label and MIME always describe the same format."""
import re

FORMATS = {'html': ('.html', 'text/html'), 'markdown': ('.md', 'text/markdown'), 'pdf': ('.pdf', 'application/pdf')}


def filename(title, fmt):
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', title or 'Cursor session').strip(' .')[:100]
    while len(stem.encode('utf-8')) > 180: stem = stem[:-1]
    if not stem or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', stem.split('.')[0], re.I):
        stem = 'Cursor session'
    return stem + FORMATS[fmt][0]


def export_metadata(job):
    saved = job.get('result_json') or {}
    fmt = (job.get('payload_json') or {}).get('format', 'markdown')
    if fmt not in FORMATS: fmt = 'markdown'
    name = saved.get('filename')
    if not isinstance(name, str) or not name.endswith(FORMATS[fmt][0]):
        name = 'cursor-session' + FORMATS[fmt][0]
    # Public metadata never exposes the server output path or arbitrary fields.
    name = re.split(r'[\\/]', name)[-1]
    return {'export_format': fmt, 'download_filename': name, 'download_mime': FORMATS[fmt][1]}
