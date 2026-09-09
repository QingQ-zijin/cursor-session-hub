"""Document adapters run in the bounded worker, never inside upload requests."""
from __future__ import annotations
from collections import deque
from html.parser import HTMLParser
import html
import json
from pathlib import Path
import re

MAX_MESSAGE = 8 * 1024**2
SUFFIXES = {'.jsonl': 'cursor_jsonl', '.json': 'cursor_jsonl', '.md': 'markdown',
            '.markdown': 'markdown', '.html': 'html', '.htm': 'html', '.pdf': 'pdf'}
ROLES = {'user': 'user', '用户': 'user', 'assistant': 'assistant', 'agent': 'assistant',
         'cursor': 'assistant', '助手': 'assistant', 'notice': 'notice', '记录': 'notice'}


def message(kind, text, **extra):
    if kind in ('user', 'assistant'):
        return {'kind': kind, 'blocks': [{'type': 'text', 'text': text}], **extra}
    return {'kind': 'notice', 'label': '导入文档', 'text': text, **extra}


def readable_event(event):
    """Recover recognized envelopes in old diagnostic records without inventing text."""
    from .ingest import normalize_jsonl
    for _ in range(4):
        if event.get('kind') != 'raw':
            return event
        payload = event.get('payload')
        if isinstance(payload, str) and len(payload) <= MAX_MESSAGE:
            try: payload = json.loads(payload)
            except ValueError: return event
        if not isinstance(payload, dict):
            return event
        recovered = normalize_jsonl(payload)
        if recovered.get('kind') == 'raw':
            return event
        event = recovered
    return event


def readable_blocks(event):
    event = readable_event(event)
    if event.get('blocks'):
        return event['blocks']
    if event.get('kind') == 'tool':
        return [dict(event, type='tool_use')]
    if isinstance(event.get('text'), str):
        return [{'type': 'thinking' if event.get('kind') == 'reasoning' else 'text', 'text': event['text']}]
    return [{'type': 'text', 'text': '解析说明：' + str(event.get('record_type') or event.get('label') or event.get('kind')),
             'diagnostic': True}]


def fenced(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    width = max([len(m.group()) for m in re.finditer(r'`+', text)] + [2]) + 1
    return '`' * width + '\n' + text + '\n' + '`' * width


def block_markdown(block):
    kind = block.get('type')
    if kind == 'tool_use':
        result = block.get('result')
        if isinstance(result, dict):
            result = result.get('text') if result.get('text') is not None else result
        return ('### 工具：' + str(block.get('name') or 'tool') + '\n\n输入\n\n' + fenced(block.get('input', {})) +
                ('\n\n输出\n\n' + fenced(result) if result is not None else ''))
    text = block.get('text') or ''
    if not isinstance(text, str): text = json.dumps(text, ensure_ascii=False, indent=2)
    return ('### 已保存的思考\n\n' if kind == 'thinking' else '') + text


def markdown_renderer():
    from markdown_it import MarkdownIt
    from mdit_py_plugins.dollarmath import dollarmath_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
    renderer = MarkdownIt('commonmark', {'html': False}).enable('table').enable('strikethrough')
    renderer.use(dollarmath_plugin, allow_labels=False, double_inline=True,
                 renderer=lambda content, options: html.escape(('$$' if options['display_mode'] else '$') + content + ('$$' if options['display_mode'] else '$')))
    renderer.use(tasklists_plugin)
    # Imported documents never cause remote image requests during export viewing.
    renderer.add_render_rule('image', lambda self, tokens, idx, options, env:
                             '<span class="missing-image">[图片：' + html.escape(tokens[idx].content) + ']</span>')
    return renderer


def write_math_assets(out):
    import base64
    folder = Path(__file__).parent / 'export_assets'
    if not folder.is_dir(): folder = Path(__file__).parent.parent / 'frontend/dist/export'
    if not (folder / 'katex.min.js').is_file():
        return False
    css = (folder / 'katex.min.css').read_text(encoding='utf-8')
    def embed(match):
        font = (folder / match.group(1)).resolve()
        if not font.is_relative_to(folder.resolve()) or not font.is_file(): return match.group(0)
        mime = 'font/woff2' if font.suffix == '.woff2' else 'font/woff' if font.suffix == '.woff' else 'font/ttf'
        return 'url(data:' + mime + ';base64,' + base64.b64encode(font.read_bytes()).decode('ascii') + ')'
    out.write('<style>' + re.sub(r'url\((fonts/[^)]+)\)', embed, css) + '</style>')
    for name in ('katex.min.js', 'auto-render.min.js'):
        out.write('<script>' + (folder / name).read_text(encoding='utf-8').replace('</script', '<\\/script') + '</script>')
    return True


def iter_markdown(path):
    role = 'notice'; parts = []; size = 0; fence = None; explicit = False; in_event = False
    with path.open('r', encoding='utf-8-sig') as source:
        while line := source.readline(MAX_MESSAGE + 1):
            if len(line.encode('utf-8')) > MAX_MESSAGE: raise ValueError('Markdown 单行超过 8 MiB；原文件已保留')
            marker = re.fullmatch(r'<!-- csh-event:(user|assistant|notice|tool|reasoning|raw) -->\s*', line)
            end = line.strip() == '<!-- /csh-event -->'
            heading = re.fullmatch(r'##\s+(user|assistant|agent|cursor|用户|助手|记录)\s*\n?', line, re.I) if not explicit else None
            if explicit and not in_event and not marker: continue
            if not fence and (marker or heading or (end and explicit)):
                if parts and ''.join(parts).strip():
                    text = ''.join(parts)
                    yield source.tell(), message(role, text.strip('\n'))
                parts = []; size = 0
                if marker: role = marker.group(1); explicit = True; in_event = True
                elif end: in_event = False
                elif heading: role = ROLES[heading.group(1).lower()]
                continue
            match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
            if match:
                token = match.group(1)
                if fence and token[0] == fence[0] and len(token) >= len(fence): fence = None
                elif not fence: fence = token
            # The title is metadata, not an extra chat message in our exports.
            if not parts and role == 'notice' and line.startswith('# ') and not explicit: continue
            parts.append(line); size += len(line.encode('utf-8'))
            if size > MAX_MESSAGE: raise ValueError('Markdown 单条消息超过 8 MiB；原文件已保留')
        if parts and ''.join(parts).strip(): yield path.stat().st_size, message(role, ''.join(parts).strip('\n'))


class DocumentHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = deque(); self.parts = []; self.size = 0; self.skip = 0
        self.role = 'notice'; self.event = None; self.block_type = None; self.template = False; self.template_parts = []
        self.structured = False; self.heading = None; self.pre = False; self.pre_code = False

    def add(self, text):
        self.size += len(text.encode('utf-8'))
        if self.size > MAX_MESSAGE: raise ValueError('HTML 单个内容块超过 8 MiB；原文件已保留')
        (self.template_parts if self.template else self.parts).append(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'head'):
            self.skip += 1; return
        if self.skip: return
        if tag == 'section' and 'data-csh-event' in attrs:
            self.structured = True; self.parts = []; self.size = 0
            self.role = attrs['data-csh-event']; self.event = {'kind': self.role, 'blocks': []}; return
        if tag == 'template' and 'data-csh-block' in attrs:
            self.template = True; self.block_type = attrs['data-csh-block']; self.template_parts = []; self.size = 0; return
        if tag == 'img' and self.event is not None:
            src = attrs.get('src', '')
            self.event['blocks'].append({'type': 'image', 'data_uri': src if src.startswith('data:image/') else '',
                                         'url': src if not src.startswith('data:') else ''})
            return
        if self.structured: return
        if tag == 'h2': self.flush(); self.heading = []; return
        if tag == 'pre':
            self.pre = True; self.pre_code = self.role not in ('user', 'assistant')
            if self.pre_code: self.add('\n```\n')
        elif tag == 'code' and self.pre and not self.pre_code:
            self.pre_code = True; self.add('\n```\n')
        elif tag == 'br': self.add('\n')
        elif tag in ('strong', 'b'): self.add('**')
        elif tag in ('em', 'i'): self.add('*')
        elif tag == 'li': self.add('\n- ')
        elif tag in ('h1','h2','h3','h4','h5','h6'): self.add('\n' + '#' * int(tag[1]) + ' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self.skip = max(0, self.skip - 1); return
        if self.skip: return
        if tag == 'template' and self.template:
            text = ''.join(self.template_parts)
            block = json.loads(text) if self.block_type == 'tool_use' else {'type': self.block_type, 'text': text}
            if self.event is not None: self.event['blocks'].append(block)
            self.template = False; self.template_parts = []; self.size = 0; return
        if tag == 'section' and self.event is not None:
            event = self.event; self.event = None
            if event['kind'] not in ('user', 'assistant'):
                event = message('notice', '\n\n'.join(block_markdown(b) for b in event['blocks']))
            if event.get('blocks') or event.get('text'): self.events.append(event)
            return
        if self.structured: return
        if tag == 'h2' and self.heading is not None:
            title = ''.join(self.heading).strip(); self.heading = None
            if title.lower() in ROLES: self.role = ROLES[title.lower()]
            else: self.add('## ' + title + '\n\n')
            return
        if tag == 'pre':
            if self.pre_code: self.add('\n```\n')
            self.pre = False; self.pre_code = False
        elif tag in ('strong', 'b'): self.add('**')
        elif tag in ('em', 'i'): self.add('*')
        if tag in ('p','div','pre','section','h1','h2','h3','h4','h5','h6','li','tr'):
            self.add('\n\n')
            if self.size > 65536: self.flush()

    def handle_data(self, data):
        if self.heading is not None and not self.skip: self.heading.append(data); return
        if not self.skip and (self.template or not self.structured): self.add(data)

    def flush(self):
        text = ''.join(self.parts).strip()
        if text: self.events.append(message(self.role, text))
        self.parts = []; self.size = 0


def iter_html(path):
    parser = DocumentHTMLParser()
    with path.open('r', encoding='utf-8-sig') as source:
        while chunk := source.read(32768):
            parser.feed(chunk)
            while parser.events: yield source.tell(), parser.events.popleft()
        parser.close()
        if parser.template or parser.event is not None: raise ValueError('HTML 会话内容未完整结束；原文件已保留')
        if not parser.structured: parser.flush()
        while parser.events: yield path.stat().st_size, parser.events.popleft()


def iter_pdf(path):
    from pypdf import PdfReader
    if path.stat().st_size > 64 * 1024**2: raise ValueError('PDF 导入上限为 64 MiB，请先拆分文档')
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''): raise ValueError('PDF 已加密，请先解密后导入')
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        if len(text.encode('utf-8')) > MAX_MESSAGE: raise ValueError(f'PDF 第 {number} 页文本超过 8 MiB')
        if text.strip():
            yield number, message('notice', text, label=f'PDF 第 {number} 页原文', document_page=number)
        else:
            yield number, {'kind': 'raw', 'record_type': 'pdf-no-text', 'payload': {'page': number,
                'reason': '此页没有可提取文字，可能是扫描图片；请先 OCR 后重新导入'}, '_diagnostic': True, 'document_page': number}


def iter_document(path, kind):
    iterator = iter_pdf(path) if kind == 'pdf' else iter_html(path) if kind == 'html' else iter_markdown(path)
    # Stable event ordinals ensure resumability even when one input chunk emits several events.
    ordinal = 0
    for ordinal, (_, event) in enumerate(iterator, 1):
        yield ordinal, event
    if not ordinal: raise ValueError('文档没有可提取的正文；原文件已保留')
