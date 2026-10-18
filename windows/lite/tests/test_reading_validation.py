import json
import unittest
from liteapp import reading

class RemoteSnapshotValidationTests(unittest.TestCase):
    def test_missing_title_is_failure_so_ui_can_fall_back_to_ocr(self):
        for title in (None, '', '   '):
            with self.subTest(title=title), self.assertRaises(ValueError):
                reading.parse_snapshot(json.dumps({'title': title, 'messages': [
                    {'side': 'me', 'text': 'synthetic reply'}]}))

    def test_unrecognized_speaker_is_not_silently_attributed_to_contact(self):
        for side in (None, '', 'system', 'ME'):
            with self.subTest(side=side), self.assertRaises(ValueError):
                reading.parse_snapshot(json.dumps({'title': 'Synthetic', 'messages': [
                    {'side': side, 'text': 'synthetic message'}]}))

    def test_one_invalid_speaker_rejects_entire_snapshot(self):
        with self.assertRaises(ValueError):
            reading.parse_snapshot(json.dumps({'title': 'Synthetic', 'messages': [
                {'side': 'me', 'text': 'valid'}, {'side': 'system', 'text': 'unknown'}]}))
