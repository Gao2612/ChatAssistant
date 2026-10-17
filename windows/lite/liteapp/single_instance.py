"""Single writable instance per data directory for Windows ChatAssistant Lite.

Mechanism choice (T1):

* **Ownership** is an exclusive byte-range file lock (``msvcrt.locking``) on
  ``<data_dir>/instance.lock``. Compared with a ``Local\\`` named mutex, a file
  lock is scoped to the profile directory instead of the Windows terminal
  session, so two RDP sessions of the same user still cannot write the same
  profile; it needs no privileges; and Windows releases the lock automatically if
  the owner crashes (no stale mutex/manual cleanup). A named mutex was the other
  candidate and is simpler, but it would allow one instance per *session* rather
  than per data directory and would need extra handling for abandoned mutexes.
* **Activation** uses a loopback socket: the owner listens on ``127.0.0.1`` (an
  ephemeral port) and writes ``{pid, port, token}`` to
  ``<data_dir>/instance.json``. A second instance only opens a connection, sends
  ``activate <token>`` and returns ``False``; it never reads/writes
  ``settings.json``, the database or chat data. The random token stops unrelated
  local processes from triggering window activation.

Testing uses ``tempfile`` data directories only; the product's real
``%LOCALAPPDATA%/ChatAssistantLite`` is never touched by the test suite.
"""

from __future__ import annotations

import json
import msvcrt
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Callable

__all__ = ["InstanceGuard", "LOCK_NAME", "INSTANCE_NAME", "ACTIVATION_COMMAND"]

LOCK_NAME = "instance.lock"
INSTANCE_NAME = "instance.json"
ACTIVATION_COMMAND = "activate"
DEFAULT_STARTUP_TIMEOUT = 3.0


class InstanceGuard:
    """Owns (or fails to own) one data directory and its activation channel."""

    def __init__(self, data_dir: Path | str | None = None,
                 on_activate: Callable[[], None] | None = None,
                 startup_timeout: float = DEFAULT_STARTUP_TIMEOUT) -> None:
        self._configured_dir = Path(data_dir) if data_dir is not None else None
        self._on_activate = on_activate
        self._startup_timeout = max(0.0, float(startup_timeout))
        self._data_dir: Path | None = None
        self._lock_handle = None
        self._token: str | None = None
        self._port = 0
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._activation_lock = threading.Lock()
        self._activation_pending = False
        #: True when this (non-owner) instance delivered an activation request.
        self.activation_sent = False
        #: Last best-effort activation failure, never contains profile data.
        self.activation_error = ""

    # -- public API --------------------------------------------------------

    @property
    def is_owner(self) -> bool:
        return self._lock_handle is not None

    @property
    def data_dir(self) -> Path | None:
        return self._data_dir

    def acquire_or_activate(self, data_dir: Path | str | None = None) -> bool:
        """Become the writer for ``data_dir`` or ask the running writer to activate.

        Returns ``True`` only for the single writable instance. A ``False`` result
        means another instance owns this data directory; the activation message
        has been sent as a best effort and the caller must exit without writing
        settings, history or chat data.
        """
        if self.is_owner:
            raise RuntimeError("this InstanceGuard already owns a data directory")
        target = Path(data_dir) if data_dir is not None else self._configured_dir
        if target is None:
            raise ValueError("data_dir is required")
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
        self._data_dir = target
        self.activation_error = ""
        deadline = time.monotonic() + self._startup_timeout
        while True:
            handle = self._try_lock()
            if handle is not None:
                self._lock_handle = handle
                try:
                    self._become_owner()
                except BaseException:
                    self.release()
                    raise
                return True
            if self._request_activation():
                return False
            if time.monotonic() >= deadline:
                if not self.activation_error:
                    self.activation_error = "另一个实例持有数据目录锁但未提供激活信息"
                return False
            time.sleep(0.05)

    def take_activation_request(self) -> bool:
        """Consume one pending "activate existing window" request."""
        with self._activation_lock:
            pending, self._activation_pending = self._activation_pending, False
        return pending

    def release(self) -> None:
        """Stop the activation listener and give up ownership; safe to call twice."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(1.0)
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._remove_instance_file()
        handle, self._lock_handle = self._lock_handle, None
        if handle is not None:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            try:
                handle.close()
            except OSError:
                pass
        with self._activation_lock:
            self._activation_pending = False
        self._token = None
        self._port = 0
        self._data_dir = None

    def __enter__(self) -> "InstanceGuard":
        if self._configured_dir is None:
            raise RuntimeError("InstanceGuard needs data_dir to be used as a context manager")
        if not self.acquire_or_activate():
            raise RuntimeError("另一个实例正在使用此数据目录")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.release()
        return False

    # -- ownership ---------------------------------------------------------

    def _try_lock(self):
        assert self._data_dir is not None
        handle = open(self._data_dir / LOCK_NAME, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return handle
        except OSError:
            handle.close()
            return None

    def _become_owner(self) -> None:
        self._stop.clear()
        self._token = secrets.token_hex(16)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        sock.settimeout(0.2)
        self._socket = sock
        self._port = int(sock.getsockname()[1])
        self._write_instance_file()
        self._thread = threading.Thread(target=self._serve_activation,
                                        name="instance-activation", daemon=True)
        self._thread.start()

    def _write_instance_file(self) -> None:
        assert self._data_dir is not None
        assert self._token is not None
        info = {"pid": os.getpid(), "port": self._port, "token": self._token,
                "started": time.time()}
        temp = self._data_dir / (INSTANCE_NAME + ".tmp")
        temp.write_text(json.dumps(info, ensure_ascii=True), encoding="utf-8")
        os.replace(temp, self._data_dir / INSTANCE_NAME)

    def _remove_instance_file(self) -> None:
        """Remove the instance file only when it is still ours (never the new owner's)."""
        if self._data_dir is None or self._token is None:
            return
        info = self._read_instance_info()
        if not info or info.get("token") != self._token:
            return
        try:
            (self._data_dir / INSTANCE_NAME).unlink()
        except OSError:
            pass

    # -- activation --------------------------------------------------------

    def _serve_activation(self) -> None:
        sock = self._socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                connection, _address = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                time.sleep(0.05)
                continue
            try:
                connection.settimeout(2.0)
                payload = connection.recv(256).decode("utf-8", "replace")
                command, _, token = payload.strip().partition(" ")
                if command == ACTIVATION_COMMAND and token == self._token:
                    with self._activation_lock:
                        self._activation_pending = True
                    if self._on_activate is not None:
                        try:
                            self._on_activate()
                        except Exception:  # noqa: BLE001 - a broken callback must not kill the listener
                            pass
                    connection.sendall(b"OK\n")
                else:
                    connection.sendall(b"DENIED\n")
            except (OSError, UnicodeError):
                pass
            finally:
                try:
                    connection.close()
                except OSError:
                    pass

    def _request_activation(self) -> bool:
        """Send "activate existing window" to the current owner; True when delivered."""
        info = self._read_instance_info()
        if not info:
            self.activation_error = "未找到运行实例的激活信息"
            return False
        try:
            port, token = int(info.get("port", 0)), str(info.get("token", ""))
        except (TypeError, ValueError):
            self.activation_error = "运行实例的激活信息无效"
            return False
        if not 0 < port < 65536 or not token:
            self.activation_error = "运行实例的激活信息无效"
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as connection:
                connection.settimeout(2.0)
                connection.sendall(f"{ACTIVATION_COMMAND} {token}\n".encode("utf-8"))
                try:
                    connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                reply = connection.recv(16)
        except OSError as error:
            self.activation_error = f"无法连接已有实例：{type(error).__name__}"
            return False
        if not reply.startswith(b"OK"):
            self.activation_error = "已有实例拒绝了激活消息"
            return False
        self.activation_sent = True
        self.activation_error = ""
        return True

    def _read_instance_info(self):
        if self._data_dir is None:
            return None
        try:
            info = json.loads((self._data_dir / INSTANCE_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return info if isinstance(info, dict) else None
