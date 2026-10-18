"""GUI helper launches must not create visible Windows console windows."""
import unittest
from unittest.mock import Mock, patch
from liteapp import build_info, codex_backend

class HiddenHelperTests(unittest.TestCase):
    def test_codex_app_server_requests_no_console_window(self):
        process = Mock()
        with patch.object(codex_backend.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.object(codex_backend.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(codex_backend, '_workspace', return_value='synthetic-workspace'), \
             patch.object(codex_backend.threading.Thread, 'start'), \
             patch.object(codex_backend.CodexAppServer, '_request', return_value={}), \
             patch.object(codex_backend.CodexAppServer, '_notify'):
            codex_backend.CodexAppServer(command=['synthetic-codex', 'app-server']).start()
        self.assertEqual(spawn.call_args.kwargs.get('creationflags'), 0x08000000)

    def test_git_metadata_requests_no_console_window(self):
        with patch.object(build_info.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
             patch.object(build_info, '_git_executable', return_value='synthetic-git'), \
             patch.object(build_info.subprocess, 'run') as run:
            build_info._run_git(['status'], 'synthetic-repo')
        self.assertEqual(run.call_args.kwargs.get('creationflags'), 0x08000000)
