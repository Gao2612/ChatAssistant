"""Test-process profile isolation + Qt teardown guard.

Importing the ``tests`` package (which unittest does before importing test
modules) points LOCALAPPDATA at a throw-away directory. Some UI tests exercise
code paths that call ``settings.save()`` without patching it; without this guard
they would overwrite the real user's installed profile (observed once during
development: a test config replaced the real saved model).

The second guard fixes the PySide6 6.12 / Qt 6.12 interpreter-exit abort
(0xC0000409) seen when closed-but-not-deleted top-level widgets survive into
Python finalization, e.g.::

    python -m unittest tests.test_ui tests.test_analysis_models -q
    Ran 25 tests ... OK
    <exit -1073740791>

Deleting leftover top-level widgets while QApplication is still alive (atexit
runs before module finalization) removes the crash; a Qt-less test run is a
no-op.
"""
import atexit
import gc
import os
import tempfile

_TEST_PROFILE_ROOT = tempfile.mkdtemp(prefix="chatassistant-lite-tests-")
os.environ["LOCALAPPDATA"] = _TEST_PROFILE_ROOT


def _cleanup_qt_widgets() -> None:
    """Delete top-level widgets before Python tears down the Qt bindings."""
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:  # Qt is not needed by a non-UI test process
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        try:
            widget.close()
            widget.deleteLater()
        except (RuntimeError, AttributeError):
            continue
    try:
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
    except RuntimeError:
        pass
    gc.collect()


if os.environ.get("CHATASSISTANT_TEST_QT_CLEANUP", "1") != "0":
    atexit.register(_cleanup_qt_widgets)


def _patch_unittest_runner() -> None:
    """Run the widget cleanup inline after every TextTestRunner.run().

    An atexit hook alone runs too late: the abort happens while Python finalizes
    the Qt extension modules, so widgets must already be gone *before*
    ``unittest.main`` returns. The cleanup is skipped when the escape-hatch env
    var is 0 (used to reproduce the crash).
    """
    import unittest

    original_run = unittest.TextTestRunner.run
    if getattr(original_run, "_chatassistant_qt_cleanup", False):
        return

    def run_with_qt_cleanup(self, test, *args, **kwargs):
        try:
            return original_run(self, test, *args, **kwargs)
        finally:
            if os.environ.get("CHATASSISTANT_TEST_QT_CLEANUP", "1") != "0":
                _cleanup_qt_widgets()

    run_with_qt_cleanup._chatassistant_qt_cleanup = True
    unittest.TextTestRunner.run = run_with_qt_cleanup


_patch_unittest_runner()
