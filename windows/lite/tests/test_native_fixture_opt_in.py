"""Desktop fixtures must never open windows during default regression runs."""
import os
import unittest
from unittest.mock import patch

from tests.fixtures import native_chat_fixture as native


class NativeFixtureOptInTests(unittest.TestCase):
    def test_default_start_skips_before_spawning_process(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(native.subprocess, 'Popen',
                             side_effect=AssertionError('unexpected desktop launch')) as launch:
            with self.assertRaises(unittest.SkipTest):
                native.NativeChatFixture().start()
            launch.assert_not_called()

    def test_explicit_opt_in_reaches_mocked_launcher(self):
        with patch.dict(os.environ, {'CHATASSISTANT_NATIVE_DESKTOP_TESTS': '1'}), \
                patch.object(native.subprocess, 'Popen',
                             side_effect=RuntimeError('mock launch only')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'mock launch only'):
                native.NativeChatFixture().start()
            launch.assert_called_once()
