"""Synthetic regression for a visual-only frame superseding remote reading."""
import queue
import unittest

from tests import test_remote_reading_ui as fixtures


class SupersededRemoteReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RemoteReadingUiTests.setUpClass()

    def test_visual_only_update_does_not_leave_remote_gate_idle_forever(self):
        fixture = fixtures.RemoteReadingUiTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        window = fixture.window
        # Worker already requested a remote read for this OCR content at frame 5.
        window.reading_busy = True
        window.frame_freshness.observe(6)
        window.state.mark_pending()
        window.capture_q = queue.Queue()
        window.capture_q.put({
            'kind': 'snapshot', 'session': 'source', 'frame_seq': 6,
            'title': 'OCR 会话', 'messages': [('her', '', 'OCR 旧文本')],
        })
        window.tick()
        # Since OCR content is unchanged, worker's last_read_key suppresses a
        # second read_frame. The older result must still be rejected safely.
        window.reading_done(5, ('Stale title', [('other', '', 'Stale text')]), '')
        self.assertEqual(window.state.title, 'OCR 会话')
        self.assertEqual(window.last_ocr_snapshot[2], 6)
        self.assertTrue(
            window.reading_snapshot_ready() or window.reading_busy
            or window.pending_read is not None,
            'Fresh OCR exists but stale remote completion leaves no accepted '
            'fallback or retry; stable capture can never unlock auto analysis.',
        )

