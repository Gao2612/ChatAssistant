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


def resolve_data_dir():
    """Default writable data directory (portable/isolated runs override LOCALAPPDATA)."""
    from liteapp.settings import data_dir
    return data_dir()


def claim_instance(data_dir=None, on_activate=None):
    """Try to become the single writable instance for one data directory.

    Returns ``(guard, owned)``. When ``owned`` is False the already running
    instance has been asked to activate its window; the caller must exit without
    writing settings, history or chat data. ``guard.release()`` stays the caller's
    responsibility for both outcomes.
    """
    from liteapp.single_instance import InstanceGuard
    target = Path(data_dir) if data_dir is not None else resolve_data_dir()
    guard = InstanceGuard(data_dir=target, on_activate=on_activate)
    return guard, guard.acquire_or_activate()


def install_activation_poll(guard, window, interval_ms=250):
    """Poll the activation channel from the Qt main thread.

    The loopback listener runs on a worker thread, so the second-instance request
    is only recorded there; touching Qt widgets happens here, on the main thread.
    Returns the QTimer so callers/tests can stop it when needed.
    """
    from PySide6.QtCore import QTimer

    timer = QTimer(window)
    timer.setInterval(interval_ms)

    def poll():
        if not guard.take_activation_request():
            return
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()

    timer.timeout.connect(poll)
    timer.start()
    return timer


def start_recovery_supervisor(data_dir):
    """Start the crash-recovery guard child; ``None`` when it cannot start.

    The guard only holds signed window-identity/geometry records (never chat
    data) and restores the original rectangle if the main process dies without a
    clean shutdown. A guard failure must not block the application: the user
    simply keeps the manual restore path.
    """
    from liteapp.recovery_guard import RecoverySupervisor
    supervisor = RecoverySupervisor(data_dir)
    return supervisor if supervisor.start() else None


def main():
    configure_frozen_dll_search()
    # Recovery guard child mode: no Qt, no instance lock, no user data reads
    # beyond the signed geometry records it was started for.
    if "--recovery-guard" in sys.argv[1:]:
        from liteapp.recovery_guard import supervisor_main
        return supervisor_main(sys.argv[1:])
    data_dir = resolve_data_dir()
    guard, owned = claim_instance(data_dir)
    if not owned:
        print("ChatAssistant Lite 已在运行，已请求激活已有窗口。", file=sys.stderr)
        guard.release()
        return 0
    supervisor = None
    try:
        supervisor = start_recovery_supervisor(data_dir)
        if supervisor is None:
            print("窗口恢复守护进程启动失败；主程序异常退出后将无法自动恢复微信窗口位置。",
                  file=sys.stderr)
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer, QEvent
        from liteapp.ui import MainWindow
        app = QApplication(sys.argv)
        app.setApplicationName("ChatAssistantLite")
        smoke = "--smoke" in sys.argv
        window = MainWindow(scanning=not smoke)
        # A second launch only asks for activation; never open a second window.
        install_activation_poll(guard, window)
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
    finally:
        if supervisor is not None:
            supervisor.stop_clean()
        guard.release()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
