"""Small session indexes and bounded round pages for the browser."""
from __future__ import annotations

import hashlib
import threading

PAGE_SIZE = 40


def round_index(events: list[dict]) -> list[dict]:
    starts = [i for i, event in enumerate(events)
              if event.get('kind') == 'user' and not event.get('is_sidechain')]
    if events and (not starts or starts[0] != 0):
        starts.insert(0, 0)
    result = []
    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(events)
        event = events[start]
        prompt = '\n'.join(b.get('text') or '' for b in event.get('blocks', [])
                           if b.get('type') == 'text')
        preview = ' '.join(prompt.split())[:180] or '会话开始 / 上下文'
        result.append({'index': number, 'start': start, 'end': end,
                       'count': end - start, 'preview': preview, 'ts': event.get('ts')})
    return result


class SessionPages:
    """Cache only the most recently requested parsed session, guarded for threads."""
    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.data = None
        self.rounds = []

    def response(self, file_id, stamp, loader, view, round_number=0, offset=0, revision=None):
        if view not in ('index', 'round') or round_number < 0 or offset < 0:
            raise ValueError('Invalid session page request')
        with self.lock:
            key = (file_id, stamp)
            current_revision = hashlib.sha256(repr(key).encode()).hexdigest()[:20]
            if revision is not None and revision != current_revision:
                raise ValueError('会话已更新，请刷新后重试。')
            if key != self.key:
                data = loader()
                if data is None:
                    return None
                self.key, self.data = key, data
                self.rounds = round_index(data.get('events', []))
            if view == 'index':
                return {**{k: v for k, v in self.data.items() if k != 'events'},
                        'events': [], 'paging': True, 'rounds': self.rounds,
                        'total_events': len(self.data.get('events', [])),
                        'revision': current_revision}
            if round_number >= len(self.rounds):
                raise ValueError('Round does not exist')
            selected = self.rounds[round_number]
            if offset > selected['count']:
                raise ValueError('Offset is outside this round')
            start = selected['start'] + offset
            end = min(start + PAGE_SIZE, selected['end'])
            return {'events': self.data['events'][start:end], 'offset': offset,
                    'next_offset': end - selected['start'],
                    'has_more': end < selected['end'], 'revision': current_revision}
