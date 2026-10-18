"""T1 验收用例：同一 data_dir 单实例写者，第二个实例只发送激活消息。

实现使用 data_dir 下的文件锁判定写者身份（跨进程、跨 Windows 会话，崩溃后由
操作系统释放），并用回环 socket + 随机令牌投递“激活已有窗口”消息。测试同时覆盖
同进程两个 guard 与真正的两个 Python 子进程，且只用临时目录，不碰用户正式数据。
"""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from liteapp.single_instance import InstanceGuard

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

CHILD_SCRIPT = """
import sys
from liteapp.single_instance import InstanceGuard

guard = InstanceGuard(startup_timeout=5.0)
owned = guard.acquire_or_activate(sys.argv[1])
print("OWNED" if owned else "ACTIVATED")
print("ACTIVATION_SENT=%s" % str(guard.activation_sent).lower())
guard.release()
"""

HOLD_LOCK_SCRIPT = """
import msvcrt
import sys
import time
from pathlib import Path

folder = Path(sys.argv[1])
folder.mkdir(parents=True, exist_ok=True)
handle = open(folder / "instance.lock", "a+b")
handle.seek(0, 2)
if handle.tell() == 0:
    handle.write(b"0")
handle.seek(0)
handle.flush()
msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
print("LOCKED", flush=True)
time.sleep(60)
"""


def child_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_child(data_dir, timeout=60):
    return subprocess.run(
        [PYTHON, "-c", CHILD_SCRIPT, str(data_dir)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, env=child_env(),
    )


def read_instance_info(data_dir):
    return json.loads((Path(data_dir) / "instance.json").read_text(encoding="utf-8"))


def wait_for_activation(guard, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if guard.take_activation_request():
            return True
        time.sleep(0.02)
    return False


class _TempDataDir(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "test-output"
        scratch.mkdir(exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(dir=scratch)
        self.data_dir = Path(self._temp.name) / "profile"
        self.second_dir = Path(self._temp.name) / "profile-2"

    def tearDown(self):
        # The just-finished cross-process child may still be releasing
        # instance.lock for a few milliseconds; a teardown-only PermissionError
        # must not fail the suite.
        for _attempt in range(10):
            try:
                self._temp.cleanup()
                return
            except PermissionError:
                time.sleep(0.05)
        self._temp.cleanup(ignore_cleanup_errors=True)


class InProcessSingleInstanceTests(_TempDataDir):
    def test_same_data_dir_second_instance_only_sends_activation(self):
        owner = InstanceGuard()
        self.assertTrue(owner.acquire_or_activate(self.data_dir))
        try:
            before = read_instance_info(self.data_dir)
            second = InstanceGuard()
            self.assertFalse(second.acquire_or_activate(self.data_dir))
            self.assertFalse(second.is_owner)
            self.assertTrue(second.activation_sent)
            self.assertTrue(wait_for_activation(owner))
            self.assertEqual(read_instance_info(self.data_dir), before,
                             "第二个实例不得改写 data_dir 中的实例信息")
            self.assertFalse(second.take_activation_request())
            second.release()
        finally:
            owner.release()

    def test_different_isolated_data_dirs_are_independent(self):
        first, second = InstanceGuard(), InstanceGuard()
        self.assertTrue(first.acquire_or_activate(self.data_dir))
        self.assertTrue(second.acquire_or_activate(self.second_dir))
        try:
            self.assertFalse(first.activation_sent)
            self.assertFalse(second.activation_sent)
            self.assertNotEqual(read_instance_info(self.data_dir)["port"],
                                read_instance_info(self.second_dir)["port"])
        finally:
            second.release()
            first.release()

    def test_release_allows_the_next_instance_to_become_writer(self):
        first = InstanceGuard()
        self.assertTrue(first.acquire_or_activate(self.data_dir))
        first.release()
        second = InstanceGuard()
        self.assertTrue(second.acquire_or_activate(self.data_dir))
        second.release()

    def test_crashed_owner_leftovers_do_not_block_new_owner(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "instance.json").write_text(json.dumps(
            {"pid": 999999, "port": 9, "token": "stale-token"}), encoding="utf-8")
        guard = InstanceGuard(startup_timeout=0.5)
        self.assertTrue(guard.acquire_or_activate(self.data_dir))
        try:
            info = read_instance_info(self.data_dir)
            self.assertNotEqual(info["token"], "stale-token")
            self.assertGreater(info["port"], 0)
        finally:
            guard.release()

    def test_listener_ignores_activation_with_wrong_token(self):
        owner = InstanceGuard()
        self.assertTrue(owner.acquire_or_activate(self.data_dir))
        try:
            info = read_instance_info(self.data_dir)
            with socket.create_connection(("127.0.0.1", info["port"]), timeout=2) as conn:
                conn.sendall(b"activate wrong-token\n")
                conn.recv(16)
            self.assertFalse(wait_for_activation(owner, timeout=0.3))
            with socket.create_connection(("127.0.0.1", info["port"]), timeout=2) as conn:
                conn.sendall(("activate " + info["token"] + "\n").encode("utf-8"))
                conn.recv(16)
            self.assertTrue(wait_for_activation(owner))
        finally:
            owner.release()


class CrossProcessSingleInstanceTests(_TempDataDir):
    def test_second_process_activates_owner_and_exits_without_writing(self):
        owner = InstanceGuard()
        self.assertTrue(owner.acquire_or_activate(self.data_dir))
        try:
            before = read_instance_info(self.data_dir)
            result = run_child(self.data_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ACTIVATED", result.stdout)
            self.assertIn("ACTIVATION_SENT=true", result.stdout)
            self.assertTrue(wait_for_activation(owner))
            self.assertEqual(read_instance_info(self.data_dir), before)
        finally:
            owner.release()

    def test_isolated_dirs_let_two_processes_own_independently(self):
        owner = InstanceGuard()
        self.assertTrue(owner.acquire_or_activate(self.data_dir))
        try:
            result = run_child(self.second_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OWNED", result.stdout)
            self.assertIn("ACTIVATION_SENT=false", result.stdout)
            self.assertFalse(wait_for_activation(owner, timeout=0.3))
        finally:
            owner.release()

    def test_lock_holder_without_instance_info_fails_closed(self):
        self.data_dir.mkdir(parents=True)
        holder = subprocess.Popen(
            [PYTHON, "-c", HOLD_LOCK_SCRIPT, str(self.data_dir)],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=child_env(),
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
            guard = InstanceGuard(startup_timeout=0.4)
            started = time.monotonic()
            self.assertFalse(guard.acquire_or_activate(self.data_dir))
            self.assertLess(time.monotonic() - started, 10)
            self.assertFalse(guard.is_owner)
            self.assertFalse(guard.activation_sent)
            guard.release()
        finally:
            holder.terminate()
            try:
                holder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait(timeout=10)
            for stream in (holder.stdout, holder.stderr):
                try:
                    stream.close()
                except (AttributeError, OSError):
                    pass


class MainIntegrationTests(_TempDataDir):
    def test_main_claim_helper_returns_guard_and_ownership(self):
        import main
        owner, owned = main.claim_instance(self.data_dir)
        self.assertTrue(owned)
        try:
            second, second_owned = main.claim_instance(self.data_dir)
            self.assertFalse(second_owned)
            self.assertTrue(second.activation_sent)
            self.assertTrue(wait_for_activation(owner))
            second.release()
        finally:
            owner.release()

    def test_importing_main_does_not_import_qt(self):
        code = ("import sys; import main; "
                "raise SystemExit(0 if 'PySide6' not in sys.modules else 3)")
        result = subprocess.run([PYTHON, "-c", code], cwd=str(ROOT),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60, env=child_env())
        self.assertEqual(result.returncode, 0,
                         "main 模块导入不应触发 Qt：%s" % result.stderr)


if __name__ == "__main__":
    unittest.main()
