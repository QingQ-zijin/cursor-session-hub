"""Paginated PDF output: one indexed event at a time, no whole-session story list."""
from collections import deque
import html
import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle, Image

from .documents import markdown_renderer, readable_blocks, block_markdown


def fonts():
    folder = Path(__file__).parent / 'fonts'
    for weight in ('Regular', 'Bold'):
        name = 'Hub' + weight
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(folder / ('NotoSansSC-' + weight + '.ttf'))))
    pdfmetrics.registerFontFamily('HubRegular', normal='HubRegular', bold='HubBold', italic='HubRegular', boldItalic='HubBold')


def inline(tokens):
    out = []
    links = []
    for token in tokens or []:
        kind = token.type
        if kind in ('text', 'code_inline', 'math_inline', 'math_inline_double'):
            text = html.escape(token.content)
            if kind.startswith('math'): text = '$' + text + '$'
            out.append(text)
        elif kind in ('softbreak', 'hardbreak'): out.append('<br/>')
        elif kind == 'strong_open': out.append('<b>')
        elif kind == 'strong_close': out.append('</b>')
        elif kind == 'em_open': out.append('<i>')
        elif kind == 'em_close': out.append('</i>')
        elif kind == 's_open': out.append('<strike>')
        elif kind == 's_close': out.append('</strike>')
        elif kind == 'link_open':
            href = token.attrGet('href') or ''
            allowed = href.startswith(('https://', 'http://', 'mailto:'))
            links.append(allowed)
            if allowed: out.append('<link href="' + html.escape(href, quote=True) + '" color="#3569d6">')
        elif kind == 'link_close':
            if links and links.pop(): out.append('</link>')
        elif kind == 'image': out.append('[图片：' + html.escape(token.content) + ']')
        elif kind == 'html_inline':
            # tasklists_plugin emits disabled inputs; no HTML is interpreted here.
            if 'checkbox' in token.content: out.append('[x] ' if 'checked' in token.content else '[ ] ')
        elif kind.endswith('_open') or kind.endswith('_close'): pass
        elif token.content: out.append(html.escape(token.content))
    return ''.join(out)


class PDFWriter:
    def __init__(self, path, title, check):
        fonts()
        self.canvas = Canvas(str(path), pagesize=A4, pageCompression=1)
        self.canvas.setTitle(title); self.canvas.setAuthor('Cursor Session Hub')
        self.width, self.height = A4
        self.left = 44; self.top = self.height - 54; self.bottom = 48
        self.available = self.width - 88; self.y = self.top
        self.title = title; self.page = 1; self.check = check
        self.normal = ParagraphStyle('body', fontName='HubRegular', fontSize=10, leading=16,
                                     textColor=colors.HexColor('#252932'), wordWrap='CJK', splitLongWords=True)
        self.code = ParagraphStyle('code', parent=self.normal, fontSize=9, leading=13,
                                   backColor=colors.HexColor('#f4f5f7'), borderPadding=0, leftIndent=6, rightIndent=6)
        self.renderer = markdown_renderer()

    def footer(self):
        self.canvas.setFont('HubRegular', 8); self.canvas.setFillColor(colors.HexColor('#777d87'))
        self.canvas.drawString(self.left, 26, 'Cursor Session Hub')
        self.canvas.drawRightString(self.width - self.left, 26, f'第 {self.page} 页')

    def new_page(self):
        self.check(); self.footer(); self.canvas.showPage()
        self.page += 1; self.y = self.top

    def add(self, flowable, gap=7):
        pending = deque([flowable])
        while pending:
            item = pending.popleft()
            remaining = self.y - self.bottom
            _, height = item.wrap(self.available, remaining)
            if height <= remaining:
                item.drawOn(self.canvas, self.left, self.y - height)
                self.y -= height + gap
                continue
            split = item.split(self.available, remaining) if remaining > 20 else []
            if split:
                first, *rest = split
                _, size = first.wrap(self.available, remaining)
                if size <= remaining:
                    first.drawOn(self.canvas, self.left, self.y - size)
                    self.y -= size + gap
                    pending.extendleft(reversed(rest))
                    if rest: self.new_page()
                    continue
            if self.y == self.top:
                raise ValueError('PDF 中有无法分页的内容块，请缩小图片或表格后重试')
            pending.appendleft(item); self.new_page()

    def paragraph(self, text, style=None):
        if text.strip(): self.add(Paragraph(text, style or self.normal))

    def heading(self, text, level=2):
        if self.y - self.bottom < 65: self.new_page()
        style = ParagraphStyle('heading', parent=self.normal, fontName='HubBold',
                               fontSize=19 if level == 1 else 14 if level == 2 else 11,
                               leading=26 if level == 1 else 21)
        self.paragraph(html.escape(text), style)

    def markdown(self, text):
        tokens = self.renderer.parse(text)
        index = 0; list_depth = 0; prefix = ''
        while index < len(tokens):
            token = tokens[index]
            if token.type in ('bullet_list_open', 'ordered_list_open'): list_depth += 1
            elif token.type in ('bullet_list_close', 'ordered_list_close'): list_depth -= 1
            elif token.type == 'list_item_open': prefix = '  ' * max(0, list_depth - 1) + '- '
            elif token.type == 'heading_open' and index + 1 < len(tokens):
                self.heading(tokens[index + 1].content, int(token.tag[1])); index += 1
            elif token.type == 'inline':
                self.paragraph(html.escape(prefix) + inline(token.children)); prefix = ''
            elif token.type in ('fence', 'code_block'):
                # Short flowables keep even very long code blocks splittable and cancellable.
                for line in io.StringIO(token.content):
                    line = line.rstrip('\n').expandtabs(4)
                    for start in range(0, max(1, len(line)), 2000):
                        self.add(Paragraph(html.escape(line[start:start+2000]).replace(' ', '&#160;') or '&#160;', self.code), gap=0)
                self.y -= 8
            elif token.type.startswith('math_block'):
                self.paragraph(html.escape('$$\n' + token.content.strip() + '\n$$').replace('\n', '<br/>'), self.code)
            elif token.type == 'table_open':
                rows = []; row = []; index += 1
                while index < len(tokens) and tokens[index].type != 'table_close':
                    cell = tokens[index]
                    if cell.type == 'tr_open': row = []
                    elif cell.type == 'inline': row.append(inline(cell.children))
                    elif cell.type == 'tr_close': rows.append(row)
                    index += 1
                self.table(rows)
            elif token.type == 'hr': self.y -= 10
            index += 1

    def table(self, rows):
        if not rows: return
        columns = max(map(len, rows))
        if columns > 8:
            for row in rows[1:]:
                for column, value in enumerate(row): self.paragraph('<b>' + rows[0][column] + ':</b> ' + value)
                self.y -= 8
            return
        data = [[Paragraph(value, self.normal) for value in row] + [''] * (columns - len(row)) for row in rows]
        table = Table(data, colWidths=[self.available / columns] * columns, repeatRows=1, splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#edf1f7')),
                                   ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#dce1e8')),
                                   ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6)]))
        self.add(table)

    def image(self, asset):
        if not asset or asset['status'] != 'ready' or not Path(asset['path']).is_file():
            self.paragraph('[关联图片缺失]'); return
        from PIL import Image as PILImage
        with PILImage.open(asset['path']) as probe:
            width, height = probe.size
        if width * height > 20_000_000:
            raise ValueError('关联图片超过 2000 万像素，PDF 导出请先缩小图片')
        scale = min(self.available / width, (self.top - self.bottom - 25) / height, 1)
        self.add(Image(asset['path'], width=width * scale, height=height * scale, lazy=2))

    def event(self, event, get_asset):
        name = {'user':'用户','assistant':'Cursor','tool':'工具','reasoning':'思考'}.get(event.get('kind'), '记录')
        self.heading(name)
        for block in readable_blocks(event):
            if block.get('type') == 'image': self.image(get_asset(block.get('asset_id')))
            else: self.markdown(block_markdown(block))
        self.y -= 12

    def finish(self):
        self.check(); self.footer(); self.canvas.save()
