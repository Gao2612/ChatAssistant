"""C2 takeover verification: 0.4.3 data -> 0.5.8 upgrade / uninstall retention / reinstall.

Isolated by construction:
* works on ``git archive`` copies of two commits under an independent work dir;
* builds portable installers inside those copies (never the repository's
  ``dist-release`` / ``package.ps1``);
* patches the archived Inno Setup script with a TEST-ONLY AppId/name so test
  installs/uninstalls can never touch the real installed 0.5.8 registration;
* exports ``LOCALAPPDATA`` of every spawned app/helper to a throw-away profile
  root and asserts the real ``%LOCALAPPDATA%\\ChatAssistantLite`` is untouched.

Only synthetic values are written (no real keys/chat). Usage (from windows/lite):

  python tools/c2_upgrade_verify.py --work-dir G:\\codex\\c2-takeover-work\\verify \
      --old-root G:\\codex\\c2-takeover-work\\old-0.4.3\\root \
      --new-root G:\\codex\\c2-takeover-work\\new-0.5.8\\root \
      --json-out test-output/c2-upgrade-result.json

Without ``--old-root/--new-root`` the tool extracts the commits into ``--work-dir``.
``--skip-build`` reuses ``dist-release/ChatAssistantLite`` in the roots;
``--skip-installer`` verifies only the source-level data fixture path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

REPO_DEFAULT = Path(r"G:\codex\ChatAssistant")
PYTHON_DEFAULT = Path(r"G:\codex\wechat-lite-venv\Scripts\python.exe")
OLD_COMMIT = "5cafa1a"   # 0.4.3
OLD_VERSION = "0.4.3"
NEW_COMMIT = "a3b2994"   # 0.5.8 baseline
NEW_VERSION = "0.5.8"
TEST_APP_ID = "{{C2TAKEOVER-0000-4000-8000-C2C2C2C2C2C2}"
TEST_APP_NAME = "ChatAssistant Lite C2 Test"
PROBE_REL = Path("windows/lite/tools/c2_profile_probe.py")
BASE_ENV = {"PYTHONIOENCODING": "utf-8", "QT_QPA_PLATFORM": "offscreen"}


# --------------------------------------------------------------------------- io
def _run(command, cwd, env, timeout, log_path=None):
    merged = dict(os.environ)
    merged.update(BASE_ENV)
    merged.update(env or {})
    started = time.time()
    try:
        result = subprocess.run([str(item) for item in command], cwd=str(cwd),
                                env=merged, timeout=timeout, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        code, out = result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired as error:
        code = "timeout"
        out = (error.stdout or "") + (error.stderr or "") if isinstance(error.stdout, (str, bytes)) else ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
    except OSError as error:
        code, out = "oserror", f"{type(error).__name__}: {error}"
    if log_path is not None:
        Path(log_path).write_text(
            f"$ {' '.join(map(str, command))}\ncwd={cwd}\nexit={code}\n"
            f"elapsed={time.time() - started:.1f}s\n{out}",
            encoding="utf-8")
    return code, out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_snapshot(folder: Path) -> dict:
    if not folder.exists():
        return {"exists": False}
    files = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            stat = path.stat()
            files[str(path.relative_to(folder))] = {
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    for name in ("settings.json", "memory.sqlite3"):
        path = folder / name
        if path.exists():
            files[name]["sha256"] = _sha256(path)
    return {"exists": True, "files": files}


def _extract_archive(repo: Path, commit: str, target: Path, git_exe: str, log_dir: Path):
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    tar_path = target.parent / f"archive-{commit}.tar"
    code, out = _run([git_exe, "archive", "--format=tar", commit, "windows/lite",
                      "-o", str(tar_path)], repo, {}, 300,
                     log_dir / f"git-archive-{commit}.log")
    if code != 0:
        raise RuntimeError(f"git archive failed for {commit}: {out[-500:]}")
    with tarfile.open(tar_path) as archive:
        archive.extractall(target, filter="data")
    tar_path.unlink(missing_ok=True)
    return target / "windows" / "lite"


# ---------------------------------------------------------------------- builds
def _find_git() -> str:
    found = shutil.which("git")
    if found:
        return found
    candidate = Path(r"C:\Program Files\Git\cmd\git.exe")
    return str(candidate) if candidate.is_file() else "git"


def _find_iscc() -> str:
    found = shutil.which("ISCC.exe")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Inno Setup 6 ISCC.exe not found")


def build_portable(root: Path, tag: str, venv_python: Path, log_dir: Path) -> dict:
    log = log_dir / f"build-portable-{tag}.log"
    env = {"CHATASSISTANT_PYTHON": str(venv_python),
           "LOCALAPPDATA": str(log_dir / f"build-profile-{tag}"),
           "QT_QPA_PLATFORM": "offscreen"}
    (log_dir / f"build-profile-{tag}").mkdir(parents=True, exist_ok=True)
    code, out = _run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(root / "build.ps1")], root, env, 1800, log)
    app = root / "dist-release" / "ChatAssistantLite" / "ChatAssistantLite.exe"
    return {"ok": code == 0 and app.is_file(), "exit": code, "app": str(app),
            "log": str(log), "tail": out.strip().splitlines()[-3:]}


def compile_test_installer(root: Path, tag: str, version: str, setups_dir: Path,
                           log_dir: Path) -> dict:
    iscc = _find_iscc()
    source = root / "installer" / "ChatAssistantLite.iss"
    text = source.read_text(encoding="utf-8")
    if f'#define AppVersion "{version}"' not in text:
        raise RuntimeError(f"unexpected installer version in {source}")
    # Replace the VerificationBuild conditional output block as a whole so the
    # patched script has exactly one OutputDir/OutputBaseFilename and always
    # produces an uninstallable test setup in the isolated setups directory.
    block_replacement = ("Uninstallable=yes\n"
                         f"OutputDir={setups_dir}\n"
                         f"OutputBaseFilename=C2-{tag}-Setup")
    text = re.sub(
        r"#if Defined\(VerificationBuild\)[\s\S]*?#endif",
        lambda _match: block_replacement,
        text, count=1)
    # Keep the test install fully isolated from the real installation:
    # * distinct AppId (already set below) and display name/shortcut group;
    # * never try to close the user's running ChatAssistantLite.exe
    #   (CloseApplications=yes aborted silently with exit code 1 while a real
    #   instance was running; it must never be closed by a test);
    # * no Start Menu/desktop icons so no shortcut of the real app is replaced.
    text = re.sub(r'#define AppName "[^"]*"',
                  lambda _match: f'#define AppName "{TEST_APP_NAME}"',
                  text, count=1)
    text = re.sub(r"(?m)^CloseApplications=yes$",
                  lambda _match: "CloseApplications=no", text)
    lines = []
    in_icons = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_icons = stripped == "[Icons]"
            lines.append(line)
            continue
        if in_icons:
            # The [Icons] block may be wrapped in #if !Defined(VerificationBuild).
            # Keep preprocessor lines (especially #endif) - dropping them makes
            # ISCC fail with "PreprocessingError: 'endif' expected".
            if stripped.startswith("#"):
                in_icons = False
            else:
                continue
        if line.startswith("AppId="):
            lines.append(f"AppId={TEST_APP_ID}")
        elif line.startswith("AppName="):
            lines.append(f"AppName={TEST_APP_NAME}")
        else:
            lines.append(line)
    patched = root / "installer" / f"C2Takeover-{tag}.iss"
    patched.write_text("\n".join(lines) + "\n", encoding="utf-8")
    setups_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"iscc-{tag}.log"
    code, out = _run([iscc, "/Qp", str(patched)], root, {}, 2400, log)
    setup = setups_dir / f"C2-{tag}-Setup.exe"
    return {"ok": code == 0 and setup.is_file(), "exit": code, "setup": str(setup),
            "log": str(log), "iscc": iscc, "tail": out.strip().splitlines()[-5:]}


def _log_tail(path: Path, lines: int = 12) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def _run_installer(exe: Path, argument_line: str, cwd: Path, timeout: int,
                   inno_log: Path, wrapper_log: Path) -> int:
    """Run Inno Setup/uninstaller through cmd.exe with a raw command line.

    Launching the setup directly through ``subprocess`` argument-list quoting makes
    Inno see ``/DIR=\\"...\\"`` and exit 1 without writing its /LOG (observed on
    this machine); the cmd.exe wrapper passes a normal Windows command line and
    works for both silent install and uninstall. The wrapper log is a separate
    file: the uninstaller may keep its own /LOG handle for a moment, so never
    rewrite the Inno log from here.
    """
    command = f'cmd.exe /c ""{exe}" {argument_line}"'
    started = time.time()
    try:
        code = subprocess.call(command, cwd=str(cwd), timeout=timeout)
    except subprocess.TimeoutExpired:
        code = "timeout"
    except OSError as error:
        code = f"{type(error).__name__}: {error}"
    Path(wrapper_log).write_text(
        f"$ {command}\ncwd={cwd}\nexit={code}\n"
        f"elapsed={time.time() - started:.1f}s\n"
        + "\n".join(_log_tail(inno_log, 15)),
        encoding="utf-8")
    return code


def cleanup_stale_test_registration(app_id: str) -> dict:
    """Remove test-only uninstall keys left by an interrupted C2 test install.

    Only HKCU entries whose name contains the C2 test marker are touched; the real
    ChatAssistant Lite uninstall key is never removed.
    """
    import winreg

    marker = app_id.strip("{}")
    removed, failed = [], []
    path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0,
                              winreg.KEY_ALL_ACCESS)
    except OSError as error:
        return {"ok": True, "removed": [], "failed": [],
                "note": f"open uninstall key: {type(error).__name__}"}
    try:
        names = []
        index = 0
        while True:
            try:
                names.append(winreg.EnumKey(root, index))
            except OSError:
                break
            index += 1
        for name in names:
            if "C2TAKEOVER" not in name.upper():
                continue
            try:
                winreg.DeleteKey(root, name)
                removed.append(name)
            except OSError as error:
                failed.append(f"{name}: {type(error).__name__}")
    finally:
        winreg.CloseKey(root)
    return {"ok": not failed, "removed": removed, "failed": failed,
            "test_app_id": app_id, "marker": marker}


def install_setup(setup: Path, install_dir: Path, log_dir: Path, tag: str):
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    inno_log = log_dir / f"inno-install-{tag}.log"
    wrapper_log = log_dir / f"install-{tag}.log"
    code = _run_installer(
        setup,
        f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS '
        f'/DIR="{install_dir}" /LOG="{inno_log}"',
        setup.parent, 900, inno_log, wrapper_log)
    exe = install_dir / "ChatAssistantLite.exe"
    uninstaller = install_dir / "unins000.exe"
    return {"ok": code == 0 and exe.is_file() and uninstaller.is_file(),
            "exit": code, "install_dir": str(install_dir),
            "exe": str(exe), "uninstaller": str(uninstaller),
            "log": str(wrapper_log), "inno_log": str(inno_log),
            "tail": _log_tail(inno_log, 10),
            "inno_log_tail": _log_tail(inno_log)}


def uninstall_setup(install_dir: Path, log_dir: Path, tag: str) -> dict:
    uninstaller = install_dir / "unins000.exe"
    wrapper_log = log_dir / f"uninstall-{tag}.log"
    inno_log = log_dir / f"inno-uninstall-{tag}.log"
    if not uninstaller.is_file():
        return {"ok": False, "error": "uninstaller missing", "log": str(wrapper_log)}
    code = _run_installer(uninstaller,
                          f'/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{inno_log}"',
                          install_dir, 900, inno_log, wrapper_log)
    deadline = time.time() + 60
    while (install_dir / "ChatAssistantLite.exe").exists() and time.time() < deadline:
        time.sleep(0.5)
    return {"ok": code == 0 and not (install_dir / "ChatAssistantLite.exe").exists(),
            "exit": code, "install_dir_removed": not install_dir.exists(),
            "main_exe_removed": not (install_dir / "ChatAssistantLite.exe").exists(),
            "dir_contents": ([item.name for item in install_dir.iterdir()]
                             if install_dir.exists() else []),
            "log": str(wrapper_log), "inno_log": str(inno_log),
            "tail": _log_tail(inno_log, 10),
            "inno_log_tail": _log_tail(inno_log)}


def run_exe(exe: Path, args, env, log_dir: Path, tag: str, timeout=180):
    log = log_dir / f"run-{tag}.log"
    return _run([exe, *args], exe.parent, env, timeout, log)


def version_json(exe: Path, log_dir: Path, tag: str):
    code, out = run_exe(exe, ["--version-json"], {}, log_dir, f"{tag}-version")
    version = None
    for line in reversed([item.strip() for item in out.splitlines() if item.strip()]):
        if line.startswith("{"):
            try:
                version = json.loads(line).get("app_version")
                break
            except ValueError:
                continue
    return {"ok": code == 0 and bool(version), "exit": code, "app_version": version,
            "log": str(log_dir / f"run-{tag}-version.log")}


def smoke_exe(exe: Path, isolated_localappdata: Path, log_dir: Path, tag: str):
    env = {"LOCALAPPDATA": str(isolated_localappdata), "QT_QPA_PLATFORM": "offscreen"}
    code, out = run_exe(exe, ["--smoke"], env, log_dir, f"{tag}-smoke", timeout=180)
    return {"ok": code == 0, "exit": code,
            "log": str(log_dir / f"run-{tag}-smoke.log")}


def self_test_exe(exe: Path, work_dir: Path, log_dir: Path, tag: str):
    data_dir = work_dir / f"selftest-{tag}"
    code, out = run_exe(exe, ["--self-test", "--data-dir", str(data_dir)], {},
                        log_dir, f"{tag}-selftest", timeout=180)
    return {"ok": code == 0, "exit": code,
            "log": str(log_dir / f"run-{tag}-selftest.log")}


def run_probe(python: Path, probe: Path, root: Path, mode: str,
              result_path: Path, isolated_localappdata: Path, log_dir: Path,
              expected_path: Path | None = None, resave: bool = False,
              forbidden_profile: Path | None = None) -> dict:
    command = [python, probe, mode, "--result", result_path,
               "--isolated-localappdata", isolated_localappdata]
    if expected_path is not None:
        command += ["--expected", expected_path]
    if resave:
        command.append("--resave")
    if forbidden_profile is not None:
        command += ["--forbidden-profile", forbidden_profile]
    env = {"LOCALAPPDATA": str(isolated_localappdata),
           "PYTHONPATH": str(root), "PYTHONIOENCODING": "utf-8",
           "QT_QPA_PLATFORM": "offscreen"}
    log = log_dir / f"probe-{mode}-{result_path.stem}.log"
    code, out = _run(command, root, env, 300, log)
    payload = None
    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            payload = None
    root_ok = bool(payload and payload.get("code", {}).get("package_root")
                   and Path(payload["code"]["package_root"]).resolve()
                   == (root.resolve()))
    return {"ok": code == 0 and isinstance(payload, dict),
            "exit": code, "result": str(result_path), "log": str(log),
            "payload": payload, "tail": out.strip().splitlines()[-4:],
            "code_matches_root": root_ok,
            "code": (payload or {}).get("code") if isinstance(payload, dict) else None}


def _code_matches(payload, root: Path, version: str) -> bool:
    code = (payload or {}).get("code") or {}
    return (Path(str(code.get("package_root") or "")).resolve() == root.resolve()
            and code.get("app_version") == version)


# ---------------------------------------------------------------- static checks
def installer_retention_check(root: Path) -> dict:
    text = (root / "installer" / "ChatAssistantLite.iss").read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped
            sections[current] = []
        elif current is not None and stripped:
            sections[current].append(stripped)
    uninstall_delete = sections.get("[UninstallDelete]", [])
    code_section = sections.get("[Code]", [])
    risky = [line for line in uninstall_delete
             if "localappdata" in line.lower() or "chatassistantlite" in line.lower()
             or "userappdata" in line.lower()]
    code_risky = [line for line in code_section
                  if re.search(r"DeleteFile|RemoveDir|DelTree", line, re.I)]
    return {
        "section_names": sorted(sections),
        "has_uninstall_delete": bool(uninstall_delete),
        "uninstall_delete_entries": uninstall_delete,
        "risky_uninstall_delete": risky,
        "code_delete_calls": code_risky,
        "data_dir_untouched_by_uninstaller": not uninstall_delete,
        "install_scope": next((line for line in text.splitlines()
                               if line.startswith("PrivilegesRequired=")), ""),
        "default_dir": next((line for line in text.splitlines()
                             if line.startswith("DefaultDirName=")), ""),
        "app_id": next((line for line in text.splitlines()
                        if line.startswith("AppId=")), ""),
    }


# ------------------------------------------------------------------------ main
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--work-dir", default="")
    parser.add_argument("--old-root", default="")
    parser.add_argument("--new-root", default="")
    parser.add_argument("--old-commit", default=OLD_COMMIT)
    parser.add_argument("--new-commit", default=NEW_COMMIT)
    parser.add_argument("--old-version", default=OLD_VERSION)
    parser.add_argument("--new-version", default=NEW_VERSION)
    parser.add_argument("--python", default=str(PYTHON_DEFAULT))
    parser.add_argument("--json-out", default="")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--keep-installed", action="store_true")
    parser.add_argument("--old-setup", default="",
                        help="reuse an already compiled C2 test installer (old)")
    parser.add_argument("--new-setup", default="",
                        help="reuse an already compiled C2 test installer (new)")
    args = parser.parse_args()

    repo = Path(args.repo)
    python = Path(args.python)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.gettempdir()) / f"c2-upgrade-{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    log_dir = work / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "work_dir": str(work),
        "old": {"commit": args.old_commit, "version": args.old_version},
        "new": {"commit": args.new_commit, "version": args.new_version},
        "python": str(python),
        "environment": {"portable_build": "PyInstaller 6.22.3", "base": BASE_ENV},
        "real_profile_guard": {},
        "steps": [],
        "source_only": args.skip_installer,
        "ok": False,
        "warnings": [],
    }

    def step(name, payload):
        entry = {"name": name, **payload}
        evidence["steps"].append(entry)
        print(f"[{'ok' if entry.get('ok') else 'FAIL'}] {name}: "
              f"{entry.get('ok')}", flush=True)
        return entry

    git_exe = _find_git()
    old_root = Path(args.old_root) if args.old_root else _extract_archive(
        repo, args.old_commit, work / "src-old", git_exe, log_dir)
    new_root = Path(args.new_root) if args.new_root else _extract_archive(
        repo, args.new_commit, work / "src-new", git_exe, log_dir)
    if not (old_root / "main.py").is_file() or not (new_root / "main.py").is_file():
        raise SystemExit(f"invalid roots: {old_root} {new_root}")
    evidence["old"]["root"] = str(old_root)
    evidence["new"]["root"] = str(new_root)
    probe = repo / PROBE_REL
    if not probe.is_file():
        raise SystemExit(f"probe not found: {probe}")

    real_localappdata = Path(os.environ.get("LOCALAPPDATA", "")) / "ChatAssistantLite"
    isolated = work / "isolated-localappdata"
    work_resolved = work.resolve()
    if work_resolved == real_localappdata.resolve() or real_localappdata.resolve() in work_resolved.parents:
        raise SystemExit(f"refusing to run inside the real profile: {work_resolved}")
    if isolated.resolve() == real_localappdata.resolve():
        raise SystemExit("refusing to use the real profile directory")
    if isolated.exists():
        shutil.rmtree(isolated)
    isolated.mkdir(parents=True)
    before_real = _profile_snapshot(real_localappdata)
    evidence["isolation"] = {
        "work_dir": str(work_resolved),
        "isolated_localappdata": str(isolated.resolve()),
        "isolated_profile": str((isolated / "ChatAssistantLite").resolve()),
        "real_profile": str(real_localappdata.resolve()),
        "localappdata_prefix_matches_workdir": (
            isolated.resolve() == (work_resolved / "isolated-localappdata").resolve()),
        "real_profile_never_used": True,
        "real_profile_before": before_real,
    }
    evidence["real_profile_guard"] = evidence["isolation"]

    # 1) old version creates a synthetic profile; old/new read it back.
    expected = work / "expected-profile.json"
    write = run_probe(python, probe, old_root, "write", expected, isolated, log_dir,
                      forbidden_profile=real_localappdata)
    write_authentic = _code_matches(write.get("payload"), old_root, args.old_version)
    step("old-0.4.3-write-profile",
         {"ok": write["ok"] and write_authentic,
          "generated_by_old_code": write_authentic,
          "code": write.get("code"), "log": write["log"]})
    old_read = run_probe(python, probe, old_root, "read", work / "read-old.json",
                         isolated, log_dir, expected_path=expected,
                         forbidden_profile=real_localappdata)
    step("old-0.4.3-reads-back",
         {"ok": old_read["ok"] and _code_matches(old_read.get("payload"), old_root,
                                                  args.old_version),
          "generated_by_old_code": _code_matches(old_read.get("payload"), old_root,
                                                  args.old_version),
          "log": old_read["log"],
          "mismatches": (old_read["payload"] or {}).get("mismatches")})
    new_read = run_probe(python, probe, new_root, "read", work / "read-new.json",
                         isolated, log_dir, expected_path=expected,
                         forbidden_profile=real_localappdata)
    new_read_authentic = _code_matches(new_read.get("payload"), new_root, args.new_version)
    new_read_history = (new_read["payload"] or {}).get("history") or {}
    evidence["data_migration"] = {
        "source_version": new_read_history.get("migration", {}).get("from_version"),
        "target_version": new_read_history.get("migration", {}).get("to_version"),
        "migrated": new_read_history.get("migration", {}).get("migrated"),
        "created": new_read_history.get("migration", {}).get("created"),
        "old_history_schema": ((write.get("payload") or {}).get("code") or {}).get(
            "history_schema_version"),
        "new_history_schema": ((new_read.get("payload") or {}).get("code") or {}).get(
            "history_schema_version"),
    }
    step("new-0.5.8-reads-old-data",
         {"ok": new_read["ok"] and new_read_authentic,
          "read_by_new_code": new_read_authentic,
          "code": new_read.get("code"),
          "log": new_read["log"],
          "mismatches": (new_read["payload"] or {}).get("mismatches"),
          "migration": evidence["data_migration"],
          "history": new_read_history})
    new_resave = run_probe(python, probe, new_root, "read", work / "read-new-resave.json",
                           isolated, log_dir, expected_path=expected, resave=True)
    step("new-0.5.8-resave-then-read", {"ok": new_resave["ok"], "log": new_resave["log"],
                                        "mismatches": (new_resave["payload"] or {}).get("mismatches")})
    new_after = run_probe(python, probe, new_root, "read", work / "read-new-after-resave.json",
                          isolated, log_dir, expected_path=expected)
    step("new-0.5.8-reads-after-migration-save",
         {"ok": new_after["ok"], "log": new_after["log"],
          "mismatches": (new_after["payload"] or {}).get("mismatches")})

    # 2) static installer retention semantics for both versions.
    for label, root in (("old", old_root), ("new", new_root)):
        check = installer_retention_check(root)
        step(f"installer-retention-{label}", {"ok": check["data_dir_untouched_by_uninstaller"],
                                              **check})

    setups_dir = work / "setups"
    old_setup = new_setup = None
    lifecycle = args.skip_installer
    if not lifecycle:
        if args.old_setup and args.new_setup:
            old_setup = Path(args.old_setup)
            new_setup = Path(args.new_setup)
            reused = {"ok": old_setup.is_file() and new_setup.is_file(),
                      "old_setup": str(old_setup), "new_setup": str(new_setup)}
            step("reuse-compiled-test-installers", reused)
            if not reused["ok"]:
                lifecycle = True
        else:
            if not args.skip_build:
                build_old = build_portable(old_root, "old", python, log_dir)
                step("build-portable-old-0.4.3", build_old)
                build_new = build_portable(new_root, "new", python, log_dir)
                step("build-portable-new-0.5.8", build_new)
                if not (build_old["ok"] and build_new["ok"]):
                    lifecycle = True
            c_old = compile_test_installer(old_root, "old", args.old_version, setups_dir, log_dir)
            step("compile-test-installer-old-0.4.3", c_old)
            c_new = compile_test_installer(new_root, "new", args.new_version, setups_dir, log_dir)
            step("compile-test-installer-new-0.5.8", c_new)
            if c_old["ok"] and c_new["ok"]:
                old_setup, new_setup = Path(c_old["setup"]), Path(c_new["setup"])
            else:
                lifecycle = True

    install_dir = work / "installed-app"
    stale = cleanup_stale_test_registration(TEST_APP_ID)
    step("cleanup-stale-test-registration", stale)
    if lifecycle:
        if not args.skip_installer:
            evidence["warnings"].append(
                "installer lifecycle skipped because portable/installer build failed; "
                "source-level fixture path only")
        evidence["installer_lifecycle"] = "skipped"
        # Simulated program-directory uninstall/reinstall still proves the data
        # directory is independent of program files.
        program = work / "simulated-program-dir"
        program.mkdir(exist_ok=True)
        (program / "ChatAssistantLite.exe").write_bytes(b"synthetic program marker")
        shutil.rmtree(program)
        after_uninstall = run_probe(python, probe, new_root, "read",
                                    work / "read-sim-uninstall.json", isolated, log_dir,
                                    expected_path=expected,
                                    forbidden_profile=real_localappdata)
        step("simulated-uninstall-keeps-data", {
            "ok": after_uninstall["ok"] and _code_matches(
                after_uninstall["payload"], new_root, args.new_version),
            "read_by_new_code": _code_matches(after_uninstall["payload"], new_root,
                                              args.new_version),
            "log": after_uninstall["log"],
            "mismatches": (after_uninstall["payload"] or {}).get("mismatches")})
        program.mkdir(exist_ok=True)
        (program / "ChatAssistantLite.exe").write_bytes(b"synthetic program marker")
        reinstalled = run_probe(python, probe, new_root, "read",
                                work / "read-sim-reinstall.json", isolated, log_dir,
                                expected_path=expected,
                                forbidden_profile=real_localappdata)
        step("simulated-reinstall-restores-data", {
            "ok": reinstalled["ok"] and _code_matches(
                reinstalled["payload"], new_root, args.new_version),
            "read_by_new_code": _code_matches(reinstalled["payload"], new_root,
                                              args.new_version),
            "log": reinstalled["log"],
            "mismatches": (reinstalled["payload"] or {}).get("mismatches")})
    else:
        evidence["installer_lifecycle"] = "running"
        profile_before_install = _profile_snapshot(isolated / "ChatAssistantLite")
        install_old = install_setup(old_setup, install_dir, log_dir, "old")
        step("install-old-0.4.3", install_old)
        if install_old["ok"]:
            old_v = version_json(Path(install_old["exe"]), log_dir, "old-installed")
            old_v["ok"] = old_v["ok"] and old_v["app_version"] == args.old_version
            step("installed-old-version-json", old_v)
            old_smoke = smoke_exe(Path(install_old["exe"]), isolated, log_dir, "old-installed")
            step("installed-old-smoke-with-old-profile", old_smoke)
            upgrade = install_setup(new_setup, install_dir, log_dir, "upgrade")
            step("upgrade-install-new-0.5.8-over-old", upgrade)
            if upgrade["ok"]:
                new_v = version_json(Path(upgrade["exe"]), log_dir, "new-installed")
                new_v["ok"] = new_v["ok"] and new_v["app_version"] == args.new_version
                step("upgraded-app-version-json", new_v)
                new_smoke = smoke_exe(Path(upgrade["exe"]), isolated, log_dir,
                                      "new-installed-upgraded")
                step("upgraded-app-smoke", new_smoke)
                new_test = self_test_exe(Path(upgrade["exe"]), work, log_dir, "new-installed")
                step("upgraded-app-self-test", new_test)
                upgraded_read = run_probe(python, probe, new_root, "read",
                                          work / "read-after-upgrade.json", isolated,
                                          log_dir, expected_path=expected,
                                          forbidden_profile=real_localappdata)
                step("upgraded-app-reads-preserved-data", {
                    "ok": upgraded_read["ok"] and _code_matches(
                        upgraded_read["payload"], new_root, args.new_version),
                    "read_by_new_code": _code_matches(upgraded_read["payload"], new_root,
                                                      args.new_version),
                    "log": upgraded_read["log"],
                    "mismatches": (upgraded_read["payload"] or {}).get("mismatches"),
                    "history": (upgraded_read["payload"] or {}).get("history")})
                profile_before_uninstall = _profile_snapshot(isolated / "ChatAssistantLite")
                uninstall = uninstall_setup(install_dir, log_dir, "after-upgrade")
                step("uninstall-default", uninstall)
                profile_after_uninstall = _profile_snapshot(isolated / "ChatAssistantLite")
                retained = (profile_after_uninstall.get("exists") and
                            "settings.json" in profile_after_uninstall.get("files", {}) and
                            "memory.sqlite3" in profile_after_uninstall.get("files", {}))
                step("uninstall-keeps-data-dir", {
                    "ok": retained and profile_after_uninstall == profile_before_uninstall,
                    "before": profile_before_uninstall,
                    "after": profile_after_uninstall})
                post_uninstall = run_probe(python, probe, new_root, "read",
                                           work / "read-after-uninstall.json", isolated,
                                           log_dir, expected_path=expected,
                                           forbidden_profile=real_localappdata)
                step("data-readable-after-uninstall", {
                    "ok": post_uninstall["ok"] and _code_matches(
                        post_uninstall["payload"], new_root, args.new_version),
                    "read_by_new_code": _code_matches(post_uninstall["payload"], new_root,
                                                      args.new_version),
                    "log": post_uninstall["log"],
                    "mismatches": (post_uninstall["payload"] or {}).get("mismatches")})
                reinstall = install_setup(new_setup, install_dir, log_dir, "reinstall")
                step("reinstall-new-0.5.8", reinstall)
                if reinstall["ok"]:
                    re_v = version_json(Path(reinstall["exe"]), log_dir, "reinstalled")
                    re_v["ok"] = re_v["ok"] and re_v["app_version"] == args.new_version
                    step("reinstalled-app-version-json", re_v)
                    re_smoke = smoke_exe(Path(reinstall["exe"]), isolated, log_dir,
                                         "reinstalled")
                    step("reinstalled-app-smoke", re_smoke)
                    restored = run_probe(python, probe, new_root, "read",
                                         work / "read-after-reinstall.json", isolated,
                                         log_dir, expected_path=expected,
                                         forbidden_profile=real_localappdata)
                    step("reinstalled-app-reads-preserved-data", {
                        "ok": restored["ok"] and _code_matches(
                            restored["payload"], new_root, args.new_version),
                        "read_by_new_code": _code_matches(restored["payload"], new_root,
                                                          args.new_version),
                        "log": restored["log"],
                        "mismatches": (restored["payload"] or {}).get("mismatches")})
                    evidence["installer_lifecycle"] = "completed"
                    if not args.keep_installed:
                        cleanup = uninstall_setup(install_dir, log_dir, "final-cleanup")
                        step("final-test-uninstall", cleanup)

    after_real = _profile_snapshot(real_localappdata)
    evidence["isolation"]["real_profile_after"] = after_real
    evidence["isolation"]["real_profile_unchanged"] = before_real == after_real
    evidence["real_profile_guard"] = evidence["isolation"]
    step("real-profile-untouched", {
        "ok": before_real == after_real,
        "real_profile": str(real_localappdata.resolve()),
        "observed_paths_isolated_under": str(isolated.resolve()),
        "localappdata_prefix_matches_workdir": evidence["isolation"][
            "localappdata_prefix_matches_workdir"],
        "unchanged": before_real == after_real})

    required = [item for item in evidence["steps"]
                if item.get("ok") is not None and not item["name"].startswith("build-")]
    evidence["ok"] = all(item.get("ok") for item in required) and not evidence["warnings"]
    result_path = Path(args.json_out) if args.json_out else work / "c2-upgrade-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({"ok": evidence["ok"], "result": str(result_path),
                      "steps": len(evidence["steps"]),
                      "failed": [item["name"] for item in evidence["steps"]
                                 if item.get("ok") is False]},
                     ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
