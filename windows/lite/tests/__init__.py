"""Test-process profile isolation.

Importing the ``tests`` package (which unittest does before importing test
modules) points LOCALAPPDATA at a throw-away directory. Some UI tests exercise
code paths that call ``settings.save()`` without patching it; without this guard
they would overwrite the real user's installed profile (observed once during
development: a test config replaced the real saved model).
"""
import os
import tempfile

_TEST_PROFILE_ROOT = tempfile.mkdtemp(prefix="chatassistant-lite-tests-")
os.environ["LOCALAPPDATA"] = _TEST_PROFILE_ROOT
