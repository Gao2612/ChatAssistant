import multiprocessing
import os
from pathlib import Path
import sys

_DLL_SEARCH_HANDLES = []


def configure_frozen_dll_search():
    bundle_root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not bundle_root:
        return
    bundle = Path(bundle_root)
    folders = (bundle, bundle / "PySide6", bundle / "shiboken6")
    for folder in folders:
        if folder.is_dir():
            _DLL_SEARCH_HANDLES.append(os.add_dll_directory(str(folder)))
    os.environ["PATH"] = os.pathsep.join((*map(str, folders), os.environ.get("PATH", "")))


def main():
    configure_frozen_dll_search()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer, QEvent
    from liteapp.ui import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("ChatAssistantLite")
    smoke = "--smoke" in sys.argv
    window = MainWindow(scanning=not smoke)
    window.show()
    if smoke:
        app.setQuitOnLastWindowClosed(False)
        QTimer.singleShot(1500, app.quit)
    result = app.exec()
    # Keep widget destruction ahead of QApplication teardown. Relying on local
    # variable cleanup after app.exec() can destroy the Qt application first
    # on Windows and crash inside Qt6Widgets during process shutdown.
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    return result


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
