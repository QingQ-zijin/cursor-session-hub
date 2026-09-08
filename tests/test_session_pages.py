import unittest
from session_pages import SessionPages, round_index, PAGE_SIZE


def fixture(rounds=8):
    events = [{'kind': 'notice', 'text': 'initial context'}]
    for r in range(rounds):
        events.append({'kind': 'user', 'blocks': [{'type': 'text', 'text': f'Question {r}'}]})
        events.extend({'kind': 'assistant', 'blocks': [{'type': 'text', 'text': f'Reply {r}/{i}'},
                       {'type': 'tool_use', 'input': {'secret': 'large tool body'}}]} for i in range(65))
    return {'title': 'Test', 'events': events}


class SessionPageTests(unittest.TestCase):
    def test_index_is_light_and_partitions_every_event(self):
        data = fixture()
        index = SessionPages().response('test', 1, lambda: data, 'index')
        self.assertEqual(index['events'], [])
        self.assertEqual(index['total_events'], len(data['events']))
        self.assertNotIn('large tool body', str(index))
        covered = [i for r in index['rounds'] for i in range(r['start'], r['end'])]
        self.assertEqual(covered, list(range(len(data['events']))))

    def test_round_pages_have_no_gaps_or_overlap(self):
        cache = SessionPages(); data = fixture(); calls = []
        def load(): calls.append(1); return data
        index = cache.response('test', 1, load, 'index')
        round_number = len(index['rounds']) - 1
        offset = 0; received = []
        while True:
            page = cache.response('test', 1, load, 'round', round_number, offset, index['revision'])
            self.assertLessEqual(len(page['events']), PAGE_SIZE)
            received.extend(page['events']); offset = page['next_offset']
            if not page['has_more']: break
        self.assertEqual(received, data['events'][index['rounds'][-1]['start']:])
        self.assertEqual(len(calls), 1)

    def test_changed_file_invalidates_snapshot(self):
        cache = SessionPages(); old = fixture(1); new = fixture(2)
        index = cache.response('test', 1, lambda: old, 'index')
        with self.assertRaises(ValueError):
            cache.response('test', 2, lambda: new, 'round', 0, 0, index['revision'])
        updated = cache.response('test', 2, lambda: new, 'index')
        self.assertEqual(updated['total_events'], len(new['events']))

    def test_invalid_pages_and_unreadable_source(self):
        cache = SessionPages(); data = fixture(1)
        for view, r, offset in [('bad', 0, 0), ('round', -1, 0), ('round', 0, -1), ('round', 99, 0), ('round', 0, 999)]:
            with self.subTest(view=view, r=r, offset=offset), self.assertRaises(ValueError):
                cache.response('test', 1, lambda: data, view, r, offset)
        self.assertIsNone(cache.response('denied', 1, lambda: None, 'index'))

    def test_no_user_events_and_empty_sessions(self):
        self.assertEqual(round_index([]), [])
        self.assertEqual(round_index([{'kind': 'tool'}])[0]['count'], 1)


if __name__ == '__main__': unittest.main()
