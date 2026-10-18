"""Build traceability helpers (T11).

The packaged app ships a ``build_info.json`` describing the exact source tree it
was built from. ``write_build_info`` runs during the build; ``verify_build_info``
recomputes the digest from the source tree and refuses to accept a package whose
manifest no longer matches (or was built from a different app version).

Only hashes/versions are recorded: never API keys, tokens, chat text, user names
or absolute host paths.
"""
from __future__ import annotations

import hashlib
import json
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1
APP_VERSION = "0.5.15"

SOURCE_ROOT = Path(__file__).resolve().parent.parent  # windows/lite
INCLUDE_SUFFIXES = {".py", ".ps1", ".iss", ".txt", ".md", ".html", ".json"}
EXCLUDE_DIRS = {
    "__pycache__", ".pytest_cache", "licenses", "test-output", "preview",
    "build", "dist", "build-pyinstaller", "dist-release",
}
EXCLUDE_PREFIXES = ("build-", "dist-", "probe-", ".venv", "venv", "probe")
GIT_SCOPES = ("windows/lite", "docs/windows", "AGENTS.md", "README.md")


def _iter_source_files(root=SOURCE_ROOT):
    root = Path(root).resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDE_DIRS or part.startswith(EXCLUDE_PREFIXES)
               for part in relative.parts[:-1]):
            continue
        if relative.name.startswith(".") or relative.name == "build_info.json":
            continue
        if path.suffix.lower() not in INCLUDE_SUFFIXES:
            continue
        yield path, relative


def source_digest(root=SOURCE_ROOT):
    """Stable SHA-256 over every build-relevant source file (path + content)."""
    digest = hashlib.sha256()
    count = 0
    for path, relative in _iter_source_files(root):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def _git_executable():
    found = shutil.which("git")
    if found:
        return found
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidate = program_files / "Git" / "cmd" / "git.exe"
    return str(candidate) if candidate.is_file() else ""


def _run_git(args, repo):
    executable = _git_executable()
    if not executable:
        raise OSError("git executable not found")
    return subprocess.run([executable, "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def find_git_root(start=SOURCE_ROOT):
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return None


def git_state(repo=None, runner=None):
    """Branch/commit/dirty for the build, or blanks when git is unavailable.

    ``runner`` is injectable for tests; it receives ``(args, repo)`` and returns
    an object with ``returncode`` and ``stdout``.
    """
    if repo is None:
        repo = find_git_root()
    if repo is None:
        return {"branch": "", "commit": "", "dirty": None}
    run = runner or _run_git
    try:
        branch = run(["rev-parse", "--abbrev-ref", "HEAD"], repo).stdout.strip()
        commit = run(["rev-parse", "HEAD"], repo).stdout.strip()
        status = run(["status", "--porcelain", "--", *GIT_SCOPES], repo).stdout
        return {"branch": branch, "commit": commit, "dirty": bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"branch": "", "commit": "", "dirty": None}


def _package_version(name):
    try:
        return metadata.version(name)
    except (ImportError, ValueError):
        return ""


def collect(version=None, root=SOURCE_ROOT, repo=None):
    digest, count = source_digest(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "app_version": version or APP_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pyinstaller": _package_version("PyInstaller"),
        "source_digest": digest,
        "source_file_count": count,
        "git": git_state(repo),
    }


def write_build_info(output, version=None, root=SOURCE_ROOT, repo=None):
    info = collect(version=version, root=root, repo=repo)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return info


def verify(info_path, root=SOURCE_ROOT, expect_version=None):
    """Return {"ok": bool, "errors": [...], "warnings": [...], "info": {...}}."""
    errors, warnings = [], []
    try:
        info = json.loads(Path(info_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "errors": [f"无法读取构建清单：{type(exc).__name__}"],
                "warnings": [], "info": None}
    if not isinstance(info, dict):
        return {"ok": False, "errors": ["构建清单格式不正确。"], "warnings": [], "info": None}
    for key in ("schema_version", "app_version", "built_at", "source_digest"):
        if key not in info:
            errors.append(f"构建清单缺少字段：{key}")
    if info.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"构建清单 schema 版本不匹配：{info.get('schema_version')} != {SCHEMA_VERSION}")
    if expect_version and info.get("app_version") != expect_version:
        errors.append(f"应用版本不匹配：清单 {info.get('app_version')} != 期望 {expect_version}")
    try:
        current_digest, current_count = source_digest(root)
    except OSError:
        return {"ok": False, "errors": ["无法计算源码摘要。"], "warnings": [], "info": info}
    if info.get("source_digest") != current_digest:
        errors.append("源码摘要与构建清单不一致：构建后源码被修改。")
    if info.get("source_file_count") not in (None, current_count):
        errors.append(f"源码文件数量不一致：清单 {info.get('source_file_count')} != 当前 {current_count}")
    git = info.get("git") or {}
    if git.get("dirty"):
        warnings.append("构建来自未提交的源码（dirty=true），清单已标记。")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "info": info}
