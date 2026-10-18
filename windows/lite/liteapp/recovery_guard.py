"""Crash recovery guard for window geometry docked by this assistant only.

The main program writes a small signed record *before* it moves the selected
WeChat window (executable path, PID, process creation time, HWND, original
rectangle/minimized state).  A tiny child process -- this module's
``supervisor_main`` -- holds no chat data, watches the main process handle and
the clean-shutdown event, and restores the original geometry only when the
main process exits abnormally.

Safety rules (PLAN T4):

* only records created and HMAC-signed by this assistant are accepted; plain or
  forged records are ignored;
* before touching a window the *process instance* is re-verified (executable
  path + PID + process creation time); a closed window or an HWND reused by
  another process is never operated on;
* a clean shutdown clears the records without restoring anything;
* the guard state file contains counts and error classes only -- no titles,
  screenshots or chat content.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import Path

from .contracts import SourceKey

APP_ID = "chatassistant-lite-window-recovery"
SCHEMA_VERSION = 1
KEY_FILENAME = "recovery.key"
RECORDS_FILENAME = "recovery-records.json"
CLEAN_FILENAME = "recovery.clean"
STATE_FILENAME = "recovery-guard.json"

EVENT_LOCAL_PREFIX = "Local\\ChatAssistantLiteRecovery-"
CREATE_NO_WINDOW = 0x08000000
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_RESTORE_STATES = ("restored", "window_gone", "identity_mismatch",
                   "bad_record", "error")


class RecoveryError(RuntimeError):
    """Configuration/I-O failure that must leave windows untouched."""


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


@dataclass(frozen=True)
class RecoveryRecord:
    """One window the assistant moved; geometry only, never chat content."""

    source: SourceKey
    original_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    original_minimized: bool = False
    recorded_at: float = 0.0
    app_id: str = APP_ID
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if not isinstance(self.source, SourceKey):
            raise TypeError("source must be SourceKey")
        if not isinstance(self.original_rect, tuple) or len(self.original_rect) != 4:
            raise TypeError("original_rect must be a 4-tuple")
        left, top, right, bottom = (int(value) for value in self.original_rect)
        if right < left or bottom < top:
            raise ValueError("original_rect has inverted edges")
        if isinstance(self.recorded_at, bool) or not isinstance(self.recorded_at,
                                                               (int, float)):
            raise TypeError("recorded_at must be a number")
        if not isinstance(self.original_minimized, bool):
            raise TypeError("original_minimized must be bool")
        if not isinstance(self.app_id, str) or not isinstance(self.schema_version, int):
            raise TypeError("record metadata is invalid")

    def payload(self) -> dict:
        return {
            "app_id": self.app_id,
            "schema_version": self.schema_version,
            "source": {
                "exe_path": self.source.exe_path,
                "pid": self.source.pid,
                "process_started": self.source.process_started,
                "hwnd": self.source.hwnd,
            },
            "original_rect": [int(value) for value in self.original_rect],
            "original_minimized": bool(self.original_minimized),
            "recorded_at": float(self.recorded_at),
        }


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class RecoveryStore:
    """Signed recovery records under one application data directory."""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.key_path = self.data_dir / KEY_FILENAME
        self.records_path = self.data_dir / RECORDS_FILENAME
        self.clean_path = self.data_dir / CLEAN_FILENAME
        self.state_path = self.data_dir / STATE_FILENAME
        self._key_cache: bytes | None = None

    # -- signing key ---------------------------------------------------------
    def _key(self) -> bytes:
        if self._key_cache is not None:
            return self._key_cache
        try:
            raw = bytes.fromhex(self.key_path.read_text(encoding="ascii").strip())
        except (FileNotFoundError, ValueError, OSError):
            raw = b""
        if len(raw) != 32:
            raw = os.urandom(32)
            _atomic_write_text(self.key_path, raw.hex())
        self._key_cache = raw
        return raw

    def _signature(self, payload: dict) -> str:
        return hmac.new(self._key(), _canonical(payload), hashlib.sha256).hexdigest()

    # -- records -------------------------------------------------------------
    @staticmethod
    def _identity(source: SourceKey):
        return (source.exe_path, source.pid, source.process_started, source.hwnd)

    def remember(self, source, original_rect, original_minimized=False, *, at=None):
        """Write/refresh the signed record for one window before moving it."""
        if not isinstance(source, SourceKey):
            raise TypeError("source must be SourceKey")
        record = RecoveryRecord(
            source=source,
            original_rect=tuple(int(value) for value in original_rect),
            original_minimized=bool(original_minimized),
            recorded_at=float(at) if at is not None else time.time(),
        )
        records = [item for item in self.load()
                   if self._identity(item.source) != self._identity(source)]
        records.append(record)
        self._write(records)
        return record

    def forget(self, source):
        if not isinstance(source, SourceKey):
            return False
        records = self.load()
        remaining = [item for item in records
                     if self._identity(item.source) != self._identity(source)]
        if len(remaining) == len(records):
            return False
        self._write(remaining)
        return True

    def clear(self):
        try:
            self.records_path.unlink()
        except FileNotFoundError:
            pass

    def _write(self, records):
        document = {
            "app_id": APP_ID,
            "schema_version": SCHEMA_VERSION,
            "records": [{"payload": item.payload(),
                         "signature": self._signature(item.payload())}
                        for item in records],
        }
        _atomic_write_text(self.records_path,
                           json.dumps(document, ensure_ascii=True, sort_keys=True))

    def load(self):
        return self.load_with_rejections()[0]

    def load_with_rejections(self):
        """Read records; returns ``(valid_records, rejected_count)``.

        Anything that is not an intact record signed by this assistant's key is
        rejected -- a foreign application cannot create a restore record.
        """
        try:
            text = self.records_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], 0
        except OSError:
            return [], 1
        try:
            document = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return [], 1
        if not isinstance(document, dict) or document.get("app_id") != APP_ID \
                or document.get("schema_version") != SCHEMA_VERSION:
            return [], 1
        items = document.get("records")
        if not isinstance(items, list):
            return [], 1
        key = self._key()
        records, rejected = [], 0
        for item in items:
            try:
                if not isinstance(item, dict):
                    raise ValueError("record entry is not an object")
                payload = item.get("payload")
                signature = item.get("signature")
                if not isinstance(payload, dict) or not isinstance(signature, str):
                    raise ValueError("missing payload/signature")
                expected = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError("signature mismatch")
                if payload.get("app_id") != APP_ID \
                        or payload.get("schema_version") != SCHEMA_VERSION:
                    raise ValueError("foreign record metadata")
                source_data = payload["source"]
                source = SourceKey(
                    exe_path=source_data["exe_path"],
                    pid=int(source_data["pid"]),
                    process_started=float(source_data["process_started"]),
                    hwnd=int(source_data["hwnd"]),
                )
                rect = tuple(int(value) for value in payload["original_rect"])
                if len(rect) != 4 or rect[2] < rect[0] or rect[3] < rect[1]:
                    raise ValueError("invalid rect")
                records.append(RecoveryRecord(
                    source=source, original_rect=rect,
                    original_minimized=bool(payload.get("original_minimized", False)),
                    recorded_at=float(payload.get("recorded_at", 0.0))))
            except (KeyError, TypeError, ValueError, OSError):
                rejected += 1
        return records, rejected

    # -- shutdown / guard state ---------------------------------------------
    def mark_clean(self):
        """Normal shutdown: the main program restored windows itself."""
        try:
            _atomic_write_text(self.clean_path, "clean\n")
        except OSError:
            pass
        self.clear()

    def consume_clean(self):
        if not self.clean_path.exists():
            return False
        try:
            self.clean_path.unlink()
        except OSError:
            pass
        return True

    def write_state(self, **fields):
        payload = {"app_id": APP_ID, "schema_version": SCHEMA_VERSION,
                   "updated_at": time.time()}
        payload.update(fields)
        try:
            _atomic_write_text(self.state_path,
                               json.dumps(payload, ensure_ascii=True, sort_keys=True))
        except OSError:
            pass

    def read_state(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}


# ---------------------------------------------------------------------------
# Restoring verified windows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RestoreOutcome:
    hwnd: int = 0
    status: str = "error"
    detail: str = ""

    def __post_init__(self):
        if self.status not in _RESTORE_STATES:
            raise ValueError(f"invalid restore status {self.status!r}")


@dataclass(frozen=True)
class RestoreReport:
    outcomes: tuple[RestoreOutcome, ...] = ()

    @property
    def restored_count(self):
        return sum(1 for outcome in self.outcomes if outcome.status == "restored")

    @property
    def skipped_count(self):
        return len(self.outcomes) - self.restored_count


def restore_records(records, *, api=None, validate=None):
    """Restore geometry for records whose window is still the same process instance."""
    from . import discovery
    from .window_dock import NativeWindows, GEOMETRY_TOLERANCE_PX

    api = api if api is not None else NativeWindows()
    validate = validate if validate is not None else discovery.valid_source
    outcomes = []
    for record in records:
        if not isinstance(record, RecoveryRecord):
            outcomes.append(RestoreOutcome(0, "bad_record", "type"))
            continue
        if record.app_id != APP_ID or record.schema_version != SCHEMA_VERSION:
            outcomes.append(RestoreOutcome(int(record.source.hwnd) if record.source else 0,
                                           "bad_record", "metadata"))
            continue
        hwnd = int(record.source.hwnd)
        try:
            if not api.is_window(hwnd):
                outcomes.append(RestoreOutcome(hwnd, "window_gone"))
                continue
            if not validate(record.source):
                # HWND reused by another process or another window: never touch it.
                outcomes.append(RestoreOutcome(hwnd, "identity_mismatch"))
                continue
            if not record.original_minimized and bool(api.is_minimized(hwnd)):
                api.set_minimized(hwnd, False)
            if not api.set_pos(hwnd, None, record.original_rect):
                outcomes.append(RestoreOutcome(hwnd, "error", "move_failed"))
                continue
            actual = tuple(api.rect(hwnd))
            if any(abs(a - b) > GEOMETRY_TOLERANCE_PX
                   for a, b in zip(actual, record.original_rect)):
                outcomes.append(RestoreOutcome(hwnd, "error", "geometry_mismatch"))
                continue
            if record.original_minimized and not bool(api.is_minimized(hwnd)):
                api.set_minimized(hwnd, True)
            outcomes.append(RestoreOutcome(hwnd, "restored"))
        except OSError as exc:
            outcomes.append(RestoreOutcome(hwnd, "error", type(exc).__name__))
    return RestoreReport(tuple(outcomes))


# ---------------------------------------------------------------------------
# Small Win32 layer for the guard child process
# ---------------------------------------------------------------------------

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_u32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None

if _u32 is not None:
    _u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _u32.SetProcessDpiAwarenessContext.restype = W.BOOL


def ensure_dpi_awareness():
    """Read/write window geometry in physical pixels (same as the main app).

    The guard is its own process, so it must opt into Per-Monitor V2 awareness
    itself; otherwise restoring a recorded physical rectangle would be
    virtualized by Windows and land at the wrong position/size.
    """
    if sys.platform != "win32":
        return False
    try:
        return bool(_u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except (AttributeError, OSError, ValueError):
        return False


if _k32 is not None:
    _k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    _k32.OpenProcess.restype = W.HANDLE
    _k32.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME),
                                     ctypes.POINTER(W.FILETIME),
                                     ctypes.POINTER(W.FILETIME),
                                     ctypes.POINTER(W.FILETIME)]
    _k32.GetProcessTimes.restype = W.BOOL
    _k32.CloseHandle.argtypes = [W.HANDLE]
    _k32.CloseHandle.restype = W.BOOL
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
    _k32.CreateEventW.restype = W.HANDLE
    _k32.SetEvent.argtypes = [W.HANDLE]
    _k32.SetEvent.restype = W.BOOL
    _k32.WaitForMultipleObjects.argtypes = [W.DWORD, ctypes.POINTER(W.HANDLE),
                                            W.BOOL, W.DWORD]
    _k32.WaitForMultipleObjects.restype = W.DWORD


def _close_handle(handle):
    if handle:
        try:
            _k32.CloseHandle(handle)
        except (OSError, ValueError):
            pass


def _created_from_handle(handle):
    created, exited = W.FILETIME(), W.FILETIME()
    kernel, user = W.FILETIME(), W.FILETIME()
    if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                ctypes.byref(kernel), ctypes.byref(user)):
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def process_created_for(pid):
    """Process creation FILETIME ticks for an alive process, else ``None``."""
    if sys.platform != "win32" or not pid:
        return None
    handle = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                              False, int(pid))
    if not handle:
        return None
    try:
        return _created_from_handle(handle)
    finally:
        _close_handle(handle)


def supervisor_command():
    """Command line prefix that re-enters this program in guard mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    return [sys.executable, str(main_path)]


class RecoverySupervisor:
    """Parent-side handle for one guard child process."""

    def __init__(self, data_dir, *, parent_pid=None, parent_created=None,
                 command=None, startup_timeout=10.0):
        self.store = RecoveryStore(data_dir)
        self.parent_pid = int(parent_pid) if parent_pid is not None else os.getpid()
        if parent_created is None:
            parent_created = process_created_for(self.parent_pid)
        self.parent_created = int(parent_created) if parent_created is not None else None
        self.command = list(command) if command else supervisor_command()
        self.startup_timeout = float(startup_timeout)
        self.token = uuid.uuid4().hex
        self.clean_event_name = f"{EVENT_LOCAL_PREFIX}Clean-{self.token}"
        self.process: subprocess.Popen | None = None
        self.error = ""
        self._clean_handle = None

    # -- lifecycle -----------------------------------------------------------
    def start(self):
        if sys.platform != "win32":
            self.error = "windows_only"
            return False
        if self.parent_created is None:
            self.error = "parent_identity"
            return False
        for stale in (self.store.state_path, self.store.clean_path):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        try:
            self._clean_handle = _k32.CreateEventW(None, True, False,
                                                   self.clean_event_name)
        except (OSError, ValueError):
            self._clean_handle = None
        if not self._clean_handle:
            self.error = "clean_event_failed"
            return False
        arguments = self.command + [
            "--recovery-guard",
            "--data-dir", str(self.store.data_dir),
            "--parent-pid", str(self.parent_pid),
            "--parent-created", str(self.parent_created),
            "--clean-event", self.clean_event_name,
        ]
        try:
            self.process = subprocess.Popen(
                arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
                creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            self.error = f"spawn_failed:{type(exc).__name__}"
            self._release_clean_event()
            return False
        state = self.wait_for_state(("watching", "failed"), timeout=self.startup_timeout)
        stage = state.get("stage") if state else ""
        if stage != "watching":
            self.error = "guard_not_ready:" + (stage or "exited")
            self._terminate()
            return False
        return True

    def stop_clean(self, timeout=10.0):
        """Signal a normal shutdown; the guard clears records without restoring."""
        if self.process is None:
            return True
        try:
            self.store.mark_clean()
        except OSError:
            pass
        if self._clean_handle:
            try:
                _k32.SetEvent(self._clean_handle)
            except (OSError, ValueError):
                pass
        try:
            self.process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            self._terminate()
            return False
        finally:
            self._release_clean_event()

    def wait(self, timeout=None):
        if self.process is None:
            return None
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        finally:
            self._release_clean_event()
        return code

    def wait_for_state(self, stages, timeout=10.0):
        """Poll the guard state file until it reaches one of ``stages``."""
        deadline = time.monotonic() + float(timeout)
        last = {}
        while time.monotonic() < deadline:
            last = self.store.read_state()
            if last.get("stage") in stages:
                return last
            if self.process is not None and self.process.poll() is not None:
                return last
            time.sleep(0.05)
        return last

    def state(self):
        return self.store.read_state()

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    # -- internals -----------------------------------------------------------
    def _terminate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5.0)
        self._release_clean_event()

    def _release_clean_event(self):
        handle, self._clean_handle = self._clean_handle, None
        _close_handle(handle)


# ---------------------------------------------------------------------------
# Guard child entry point (no Qt, no chat data, stdlib + ctypes only)
# ---------------------------------------------------------------------------

def _open_verified_parent(pid, created_ticks):
    handle = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                              False, int(pid))
    if not handle:
        return None
    if _created_from_handle(handle) != int(created_ticks):
        _close_handle(handle)
        return None
    return handle


def supervisor_main(argv=None):
    """``python main.py --recovery-guard ...``: watch parent, restore on crash."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="ChatAssistant Lite window recovery guard")
    parser.add_argument("--recovery-guard", action="store_true")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--parent-created", type=int, required=True)
    parser.add_argument("--clean-event", required=True)
    args = parser.parse_args(arguments)
    if sys.platform != "win32":
        return 2
    ensure_dpi_awareness()
    store = RecoveryStore(args.data_dir)
    parent_handle = _open_verified_parent(args.parent_pid, args.parent_created)
    if not parent_handle:
        store.write_state(stage="failed", pid=os.getpid(),
                          errors=["parent_identity"])
        return 3
    clean_handle = _k32.CreateEventW(None, True, False, args.clean_event)
    if not clean_handle:
        _close_handle(parent_handle)
        store.write_state(stage="failed", pid=os.getpid(), errors=["clean_event"])
        return 4
    records, rejected = store.load_with_rejections()
    total = len(records) + rejected
    store.write_state(stage="watching", pid=os.getpid(), records=total,
                      restored=0, skipped=0, errors=[])
    handles = (W.HANDLE * 2)(parent_handle, clean_handle)
    result = _k32.WaitForMultipleObjects(2, handles, False, INFINITE)
    _close_handle(clean_handle)
    if result == WAIT_OBJECT_0 + 1:
        # Normal shutdown: the main program already restored its windows.
        store.clear()
        store.consume_clean()
        store.write_state(stage="clean", pid=os.getpid(), records=total,
                          restored=0, skipped=0, errors=[])
        _close_handle(parent_handle)
        return 0
    if result == WAIT_OBJECT_0:
        # Abnormal exit. Re-check for a clean marker written by stop_clean to
        # close the small race between the event and the marker file.
        if store.consume_clean():
            store.clear()
            store.write_state(stage="clean", pid=os.getpid(), records=total,
                              restored=0, skipped=0, errors=[])
            _close_handle(parent_handle)
            return 0
        report = restore_records(records)
        store.clear()
        error_kinds = []
        for outcome in report.outcomes:
            if outcome.status != "restored":
                label = outcome.status + (":" + outcome.detail if outcome.detail else "")
                error_kinds.append(label)
        store.write_state(stage="restored", pid=os.getpid(), records=total,
                          restored=report.restored_count,
                          skipped=len(records) - report.restored_count + rejected,
                          errors=error_kinds[:8])
        _close_handle(parent_handle)
        return 0
    store.write_state(stage="failed", pid=os.getpid(), records=total,
                      errors=["wait_failed"])
    _close_handle(parent_handle)
    return 5
