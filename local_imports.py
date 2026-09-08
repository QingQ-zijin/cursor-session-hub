"""Viewer-owned, read-only-after-import Cursor JSONL copies."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import common
import cursor_parser

ROOT = Path(__file__).resolve().parent / '.local' / 'imports'
SUMMARY_CACHE = common.SummaryCache()
MAX_BYTES = 32 * 1024 * 1024


def owns(path: Path) -> bool:
    path = path.resolve()
    return path.suffix == '.jsonl' and path.parent.parent == ROOT.resolve()


def summary(path: Path) -> dict:
    def read():
        data = cursor_parser._cli_session_summary_uncached(path)
        data.update(cursor_source='imported-jsonl', cwd=str(ROOT))
        return data
    return common.cached_summary(SUMMARY_CACHE, str(path), common.file_identity(path), read)


def list_sessions() -> list[dict]:
    result = []
    for path in ROOT.glob('*/*.jsonl'):
        if owns(path):
            try:
                result.append(summary(path))
            except (OSError, ValueError, TypeError):
                continue
    return result


def parse_session(path: Path) -> dict | None:
    if not owns(path):
        return None
    data = cursor_parser.parse_cli_session(path)
    if data is not None:
        data['cursor_source'] = 'imported-jsonl'
        data['meta'] = {'cwd': str(ROOT)}
    return data


def import_jsonl(name: str, text: str) -> dict:
    if not isinstance(name, str) or not name.lower().endswith('.jsonl'):
        raise ValueError('请选择 .jsonl 文件。')
    if not isinstance(text, str):
        raise ValueError('文件内容必须是 UTF-8 文本。')
    text = text.lstrip('\ufeff')
    payload = text.encode('utf-8')
    if len(payload) > MAX_BYTES:
        raise ValueError('文件超过 32 MB，请拆分后再导入。')
    messages = 0
    for line_number, line in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'第 {line_number} 行不是有效 JSON。') from error
        if not isinstance(record, dict):
            raise ValueError(f'第 {line_number} 行必须是 JSON 对象。')
        if record.get('role') in ('user', 'assistant'):
            message = record.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'), (str, list)):
                raise ValueError(f'第 {line_number} 行不符合 Cursor 会话格式。')
            messages += 1
    if not messages:
        raise ValueError('没有找到 Cursor 用户消息或 AI 回复。')
    # File names come entirely from the content hash, never from an upload path.
    # Reimporting identical content opens the same saved copy.
    digest = hashlib.sha256(payload).hexdigest()
    target = ROOT / digest / (digest + '.jsonl')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        handle, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
        try:
            with os.fdopen(handle, 'wb') as output:
                output.write(payload)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return summary(target)
