import tempfile
import unittest
from pathlib import Path
from liteapp import settings


class BackendSettingsTests(unittest.TestCase):
    def test_new_install_reads_locally_but_has_separate_analysis_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
        self.assertEqual(cfg.get('reading_backend'), 'ocr')
        self.assertEqual(cfg.get('backend'), 'chatgpt')

    def test_reading_and_analysis_models_and_keys_roundtrip_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg.update(reading_backend='api', reading_model='vision-model',
                       reading_base_url='https://vision.example.test/v1',
                       reading_api_key='synthetic-vision-secret', backend='local',
                       model='local-chat', base_url='http://127.0.0.1:11434/v1', api_key='')
            settings.save(cfg, folder)
            raw = (Path(folder) / 'settings.json').read_text(encoding='utf-8')
            restored = settings.load(folder)
        self.assertEqual(restored.get('reading_model'), 'vision-model')
        self.assertEqual(restored.get('model'), 'local-chat')
        self.assertEqual(restored.get('reading_api_key'), 'synthetic-vision-secret')
        self.assertNotIn('synthetic-vision-secret', raw)


if __name__ == '__main__': unittest.main()
