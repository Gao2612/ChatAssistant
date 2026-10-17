"""Isolated self-test (T11).

Runs only local, synthetic checks: writable data directory, DPAPI settings
roundtrip, SQLite read/write, and required dependency imports. It never enumerates
WeChat windows, never opens the real profile, never reads real chat data and never
sends a model request.

R-09 hardening: an explicit ``--data-dir`` is never written directly. A directory
that looks like the real profile (``settings.json`` / ``memory.sqlite3`` /
instance or recovery markers, or the default ``ChatAssistantLite`` name) is
refused; otherwise the checks run inside a random ``selftest-<hex>`` subdirectory
that is removed afterwards and the report marks ``isolated=false``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import uuid

SCHEMA = 1
REQUIRED_IMPORTS = ("PySide6", "numpy", "PIL", "sqlite3", "pypdf",
                    "rapidocr_onnxruntime", "windows_capture")
OPTIONAL_IMPORTS = ("cryptography", "pypdfium2", "jwt")

PROFILE_DIR_NAMES = ("ChatAssistantLite",)
PROFILE_MARKERS = ("settings.json", "memory.sqlite3", "instance.json",
                   "instance.lock", "recovery.key", "history-backups")


def _looks_like_real_profile(base: Path) -> bool:
    if base.name in PROFILE_DIR_NAMES:
        return True
    return any((base / marker).exists() for marker in PROFILE_MARKERS)


def _check(checks, name, function):
    try:
        detail = function()
        checks.append({"name": name, "ok": True, "detail": detail or ""})
    except Exception as exc:  # noqa: BLE001 - self-test reports every failure as data
        checks.append({"name": name, "ok": False,
                       "detail": f"{type(exc).__name__}: {str(exc)[:300]}"})


def _collect_checks(data: Path) -> list[dict]:
    checks = []

    def writable():
        probe = data / "probe.txt"
        probe.write_text("self-test", encoding="utf-8")
        value = probe.read_text(encoding="utf-8")
        probe.unlink()
        if value != "self-test":
            raise RuntimeError("数据目录读写内容不一致")
        return "数据目录可写"

    def settings_roundtrip():
        from . import settings
        encrypted = settings.crypt(b"synthetic-self-test")
        if settings.crypt(encrypted, True) != b"synthetic-self-test":
            raise RuntimeError("DPAPI 加解密结果不一致")
        config = settings.load(data)
        config["api_key"] = "synthetic-self-test-key"
        config["reading_api_key"] = "synthetic-reading-key"
        settings.save(config, data)
        restored = settings.load(data)
        if restored.get("api_key") != "synthetic-self-test-key":
            raise RuntimeError("设置 Key 往返失败")
        if restored.get("reading_api_key") != "synthetic-reading-key":
            raise RuntimeError("读取 Key 往返失败")
        if restored.get("_warning"):
            raise RuntimeError("设置读取给出警告")
        return "DPAPI 设置往返正常"

    def sqlite_roundtrip():
        path = data / "selftest.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS probe(id INTEGER PRIMARY KEY, body TEXT)")
            db.execute("INSERT INTO probe(body) VALUES (?)", ("synthetic",))
            row = db.execute("SELECT body FROM probe").fetchone()
        if not row or row[0] != "synthetic":
            raise RuntimeError("SQLite 往返失败")
        db.close()
        path.unlink()
        return "SQLite 读写正常"

    def history_import():
        from . import history  # noqa: F401 - availability check only
        return "历史库模块可导入"

    def import_module(name):
        def load():
            __import__(name)
            return f"{name} 可导入"
        return load

    _check(checks, "data_dir", writable)
    _check(checks, "settings_dpapi", settings_roundtrip)
    _check(checks, "sqlite", sqlite_roundtrip)
    _check(checks, "history_module", history_import)
    for name in REQUIRED_IMPORTS:
        _check(checks, f"import:{name}", import_module(name))
    for name in OPTIONAL_IMPORTS:
        try:
            __import__(name)
            checks.append({"name": f"import:{name}", "ok": True, "detail": "可选依赖可导入"})
        except Exception:  # noqa: BLE001 - optional dependency may be absent
            checks.append({"name": f"import:{name}", "ok": True, "detail": "可选依赖未安装"})
    return checks


def _isolation_failure(detail: str) -> dict:
    return {"schema": SCHEMA, "ok": False, "isolated": False,
            "checks": [{"name": "isolated_data_dir", "ok": False, "detail": detail}]}


def run(data_dir=None):
    """Run the synthetic self-test; returns a JSON-serialisable report."""
    temporary = None
    work_dir = None
    isolated = data_dir is None
    if data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="chatassistant-selftest-")
        work_dir = Path(temporary.name)
    else:
        base = Path(data_dir)
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _isolation_failure(
                f"无法创建隔离自检目录：{type(exc).__name__}: {str(exc)[:300]}")
        if _looks_like_real_profile(base):
            return _isolation_failure(
                "拒绝在疑似正式数据目录上运行自检（发现 settings.json/"
                "memory.sqlite3 等标记）；请使用空白的隔离目录。")
        work_dir = base / f"selftest-{uuid.uuid4().hex[:8]}"
    try:
        data = work_dir / "data"
        data.mkdir(parents=True, exist_ok=True)
        checks = _collect_checks(data)
        return {"schema": SCHEMA, "ok": all(item["ok"] for item in checks),
                "isolated": isolated, "checks": checks}
    finally:
        if temporary is not None:
            temporary.cleanup()
        elif work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ChatAssistant Lite isolated self-test")
    parser.add_argument("--data-dir", default=None,
                        help="isolated directory for the self-test (default: temporary)")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    args = parser.parse_args(argv)
    report = run(args.data_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
