import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QComboBox
from liteapp.ui import SettingsPage
from tests.test_analysis_models import subscription_config


class CatalogRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def page(self, **kwargs):
        page = SettingsPage(subscription_config(**kwargs), None)
        self.addCleanup(page.close)
        return page

    def test_api_catalog_saves_id_instead_of_display_name(self):
        page = self.page(backend='api')
        page.models_checked([('Vendor Friendly Name', 'vendor-supported-id')], '')
        with patch('liteapp.ui.settings.save') as save:
            page.commit()
        self.assertEqual(save.call_args[0][0]['model'], 'vendor-supported-id')

    def test_switching_backend_clears_foreign_catalog(self):
        page = self.page()
        page.backend.setCurrentIndex(page.backend.findData('api'))
        self.assertNotIn('gpt-6.1-sol', [page.model.itemData(i) for i in range(page.model.count())])

    def test_chatgpt_refresh_does_not_replace_api_catalog(self):
        page = self.page(backend='api')
        page.models_checked([('Vendor', 'vendor-id')], '')
        page.set_models([('ChatGPT', 'gpt-only')])
        self.assertEqual(page.model.itemData(0), 'vendor-id')

    def test_switching_provider_clears_foreign_model(self):
        page = self.page(backend='api')
        page.models_checked([('Vendor', 'vendor-chat')], '')
        page.provider.setCurrentIndex(page.provider.findData('openrouter'))
        self.assertEqual(page.model.currentText(), '')
        self.assertEqual(page.model.count(), 0)

    def test_codex_detection_does_not_fill_api_reading_model(self):
        page = self.page(backend='api', reading_backend='api')
        page.codex_checked({'models': [{'id': 'codex-id', 'display_name': 'Codex'}]}, '')
        value = page.reading_model.currentText() if isinstance(page.reading_model, QComboBox) else page.reading_model.text()
        self.assertEqual(value, '')

    def test_reading_models_have_dropdown_and_fetch_button(self):
        page = self.page(reading_backend='api')
        self.assertIsInstance(page.reading_model, QComboBox)
        self.assertTrue(page.fetch_reading_models_button.isEnabled())

    def test_codex_catalog_preserves_selected_id(self):
        page = self.page(backend='codex', codex_model='second')
        page.codex_checked({'models': [{'id': 'first', 'display_name': 'First'},
                                      {'id': 'second', 'display_name': 'Second'}]}, '')
        with patch('liteapp.ui.settings.save') as save:
            page.commit()
        self.assertEqual(save.call_args[0][0]['model'], 'second')

    def test_stale_catalog_response_cannot_populate_changed_endpoint(self):
        import threading
        started, release = threading.Event(), threading.Event()
        page = self.page(backend='api')
        def fetch(_config):
            started.set()
            release.wait(2)
            return [('Wrong provider', 'foreign-id')]
        with patch('liteapp.ui.analysis.list_models', side_effect=fetch):
            page.fetch_models()
            self.assertTrue(started.wait(1))
            page.base.setText('https://another.example/v1')
            release.set()
            import time
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertNotIn('foreign-id', [page.model.itemData(i) for i in range(page.model.count())])

    def test_empty_codex_catalog_clears_old_choices(self):
        page = self.page(backend='codex')
        page.codex_checked({'models': [{'id': 'old', 'display_name': 'Old'}]}, '')
        page.codex_checked({'models': []}, '')
        self.assertEqual(page.model.count(), 0)

    def test_effective_label_uses_selected_id(self):
        page = self.page(backend='api')
        page.models_checked([('Friendly', 'new-supported-id')], '')
        self.assertIn('new-supported-id', page.effective_label.text())

    def test_reading_catalog_saves_id(self):
        page = self.page(backend='api', reading_backend='api', reading_base_url='https://vision.example/v1', reading_api_key='synthetic')
        context = (page._reading_request, 'api', page.reading_base.text(), page.reading_key.text())
        page.reading_models_checked((context, [('Vision', 'vision-id')]), '')
        with patch('liteapp.ui.settings.save') as save:
            page.commit()
        self.assertEqual(save.call_args[0][0]['reading_model'], 'vision-id')

    def test_missing_saved_codex_model_requires_explicit_selection(self):
        page = self.page(backend='codex', codex_model='removed-id')
        page.codex_checked({'models': [{'id': 'available-id', 'display_name': 'Available'}]}, '')
        self.assertEqual(page.model.currentText(), '')
        with patch('liteapp.ui.settings.save') as save:
            page.commit()
        save.assert_not_called()

    def test_catalog_does_not_invent_id_from_display_name(self):
        from liteapp import analysis
        with patch('liteapp.analysis.request_get_json', return_value={'data': [{'name': 'Friendly'}]}):
            with self.assertRaises(ValueError):
                analysis.list_models({'backend': 'api', 'base_url': 'https://example.test/v1', 'api_key': 'synthetic'})

    def test_stale_codex_path_result_is_ignored(self):
        import threading
        import time
        started, release = threading.Event(), threading.Event()
        page = self.page(backend='codex', codex_path='old.exe')
        def status(_cfg):
            started.set()
            release.wait(2)
            return {'models': [{'id': 'old-id', 'display_name': 'Old'}]}
        with patch('liteapp.ui.codex_backend.status', side_effect=status):
            page.detect_codex()
            self.assertTrue(started.wait(1))
            page.codex_path.setText('new.exe')
            release.set()
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertNotIn('old-id', [page.model.itemData(i) for i in range(page.model.count())])
