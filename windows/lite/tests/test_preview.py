import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import numpy as np
from PIL import Image

from liteapp.worker import encode_preview, significant_visual_change


class PreviewTests(unittest.TestCase):
    def test_cursor_blink_does_not_invalidate_but_new_message_does(self):
        original = np.zeros((800, 1200, 3), dtype=np.uint8)
        cursor = original.copy()
        cursor[600:630, 900:902] = 255
        message = original.copy()
        message[300:350, 600:900] = 255
        self.assertFalse(significant_visual_change(original, cursor))
        self.assertTrue(significant_visual_change(original, message))

    def test_preview_is_decodable_and_width_bounded(self):
        source = np.zeros((800, 1600, 3), dtype=np.uint8)
        source[:, :800] = (120, 240, 10)
        encoded = encode_preview(source, max_width=800)
        image = Image.open(io.BytesIO(encoded))
        self.assertEqual(image.size, (800, 400))
        self.assertEqual(image.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
