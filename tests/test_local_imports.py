import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_imports
import server


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'imports'
        self.override = patch.object(local_imports, 'ROOT', self.root)
        self.override.start()
        self.addCleanup(self.override.stop)
        local_imports.SUMMARY_CACHE.clear()
        self.text = '\n'.join(json.dumps(r, ensure_ascii=False) for r in [
            {'role': 'user', 'message': {'content': [{'type': 'text', 'text': '<user_query>你好</user_query>'}]}},
            {'role': 'assistant', 'message': {'content': [{'type': 'text', 'text': '结论\n\n```python\nprint(1)\n```'}]}},
            {'type': 'turn_ended', 'status': 'completed'},
        ])

    def test_import_roundtrip_listing_and_repeat_are_stable(self):
        first = local_imports.import_jsonl('对话.jsonl', self.text)
        second = local_imports.import_jsonl('renamed.jsonl', self.text)
        self.assertEqual(first['file'], second['file'])
        self.assertEqual(len(local_imports.list_sessions()), 1)
        parsed = server.load_session(first['file'])
        self.assertEqual(parsed['events'][1]['blocks'][0]['text'], '结论\n\n```python\nprint(1)\n```')
        self.assertEqual(Path(first['file']).read_text(encoding='utf-8'), self.text)

    def test_bom_does_not_drop_first_message(self):
        item = local_imports.import_jsonl('bom.jsonl', '\ufeff' + self.text)
        self.assertEqual(item['n_user'], 1)
        self.assertEqual(len(server.load_session(item['file'])['events']), 2)

    def test_bad_input_creates_no_import(self):
        for name, text in [('x.json', self.text), ('x.jsonl', self.text + '\n{'), ('x.jsonl', '{}'), ('x.jsonl', '[]')]:
            with self.subTest(name=name, text=text[-12:]):
                with self.assertRaises(ValueError):
                    local_imports.import_jsonl(name, text)
        self.assertFalse(self.root.exists())

    def test_supplied_path_cannot_choose_destination(self):
        item = local_imports.import_jsonl('../../outside.jsonl', self.text)
        self.assertEqual(Path(item['file']).parent.parent, self.root.resolve())
        self.assertFalse(local_imports.owns(self.root.parent / 'outside.jsonl'))

    def test_size_limit_creates_no_import(self):
        with patch.object(local_imports, 'MAX_BYTES', 1):
            with self.assertRaises(ValueError):
                local_imports.import_jsonl('x.jsonl', self.text)
        self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
