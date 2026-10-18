"""C2 takeover probe: write/read a synthetic ChatAssistant Lite profile with one
specific version of the application code.

Runs against whatever ``liteapp`` package is first on ``sys.path``; the C2 driver
sets ``PYTHONPATH``/cwd to an archived version (0.4.3 or 0.5.7) and
``LOCALAPPDATA`` to an isolated throw-away directory. It never touches the real
user profile. All settings values, chats and tokens here are synthetic.

Usage (normally only invoked by tools/c2_upgrade_verify.py):
  python c2_profile_probe.py write --result <json>
  python c2_profile_probe.py read  --expected <json> --result <json> [--resave]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

SYNTHETIC = {
    "host_id": "urn:uuid:11111111-2222-3333-4444-555555555555",
    "api_key": "c2-synthetic-old-api-key-001",
    "reading_api_key": "c2-synthetic-old-reading-key-002",
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    "base_url": "https://api.example.invalid/v1",
    "api_provider": "custom",
    "backend": "api",
    "model": "c2-old-model-1",
    "api_model": "c2-old-model-1",
    "reading_backend": "ocr",
    "style": "C2 synthetic style",
    "auto_connect": False,
    "auto_analysis": False,
    "subscription": {
        "access_token": "c2-synthetic-old-access-token",
        "refresh_token": "c2-synthetic-old-refresh-token",
        "id_token": "c2-synthetic-old-id-token",
        "client_id": "c2-synthetic-client-id",
        "subject": "c2-synthetic-subject",
        "email": "c2-synthetic@example.invalid",
        "token_type": "Bearer",
        "scopes": ["chatgpt.tokens.use.direct"],
        "access_expires_at": 4102444800.0,
    },
    "subscription_models": [["C2 Old Chat Model", "c2-old-model-1"]],
}

RECORD_TEXTS = [
    "C2 synthetic record: 客户询问产品规格",
    "C2 synthetic record: 我们周五前给报价",
    "C2 synthetic record: 交期大约两周",
    "C2 synthetic record: 售后条款已确认",
]

# Filled from the CLI before any profile access. The probe only ever writes into
# the explicitly expected isolated LOCALAPPDATA root (P2-7 review hardening).
EXPECTED_LOCALAPPDATA: Path | None = None
FORBIDDEN_PROFILE: Path | None = None


def _code_info() -> dict:
    """Identify which archived application code actually created/read the data."""
    import liteapp
    from liteapp import build_info
    from liteapp.history_models import SCHEMA_VERSION
    return {
        "package_root": str(Path(liteapp.__file__).resolve().parent.parent),
        "app_version": getattr(build_info, "APP_VERSION", None),
        "history_schema_version": SCHEMA_VERSION,
    }


def _profile_dir() -> Path:
    raw = os.environ.get("LOCALAPPDATA", "")
    if not raw.strip():
        raise SystemExit("LOCALAPPDATA must point at the isolated profile root")
    local = Path(raw).resolve()
    if EXPECTED_LOCALAPPDATA is None:
        raise SystemExit("--isolated-localappdata is required; refusing to guess")
    expected = EXPECTED_LOCALAPPDATA.resolve()
    if local != expected:
        raise SystemExit(
            f"LOCALAPPDATA mismatch: got {local}, expected isolated root {expected}")
    profile = local / "ChatAssistantLite"
    if FORBIDDEN_PROFILE is not None and profile.resolve() == FORBIDDEN_PROFILE.resolve():
        raise SystemExit(f"refusing to touch the real profile: {profile}")
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_profile(result_path: Path) -> dict:
    from liteapp import history, settings

    profile = _profile_dir()
    config = settings.load(profile)
    config.update({
        "host_id": SYNTHETIC["host_id"],
        "api_key": SYNTHETIC["api_key"],
        "reading_api_key": SYNTHETIC["reading_api_key"],
        "wechat_path": SYNTHETIC["wechat_path"],
        "base_url": SYNTHETIC["base_url"],
        "api_provider": SYNTHETIC["api_provider"],
        "backend": SYNTHETIC["backend"],
        "model": SYNTHETIC["model"],
        "api_model": SYNTHETIC["api_model"],
        "reading_backend": SYNTHETIC["reading_backend"],
        "style": SYNTHETIC["style"],
        "auto_connect": SYNTHETIC["auto_connect"],
        "auto_analysis": SYNTHETIC["auto_analysis"],
        "subscription": SYNTHETIC["subscription"],
        "subscription_models": SYNTHETIC["subscription_models"],
    })
    settings.save(config, profile)

    store = history.HistoryStore(profile / "memory.sqlite3")
    scope = store.create_person("C2 合成联系人", "c2-synthetic-account")
    from liteapp.history_models import ImportSource, ImportedRecord
    sources = [
        ImportSource(digest=_digest_text("c2-source-a"), kind="html",
                     name="c2_source_a.html", page_count=1),
        ImportSource(digest=_digest_text("c2-source-b"), kind="html",
                     name="c2_source_b.html", page_count=1),
    ]
    first = store.import_records(scope, sources[0], [
        ImportedRecord(text=RECORD_TEXTS[0], ordinal=0, record_type="message",
                       sender="客户", sent_at="2026-01-01 10:00",
                       locator={"kind": "html", "section": 1}),
        ImportedRecord(text=RECORD_TEXTS[1], ordinal=1, record_type="message",
                       sender="我", sent_at="2026-01-01 10:01",
                       locator={"kind": "html", "section": 2}),
    ])
    second = store.import_records(scope, sources[1], [
        ImportedRecord(text=RECORD_TEXTS[2], ordinal=0, record_type="fragment",
                       locator={"kind": "html", "section": 1}),
        ImportedRecord(text=RECORD_TEXTS[3], ordinal=1, record_type="fragment",
                       locator={"kind": "html", "section": 2}),
    ])
    store.save_derived_memory(scope, "c2_report", "C2 synthetic derived memory")

    reloaded = settings.load(profile)
    stats = store.stats(scope)
    rows = store.list_messages(scope, limit=100)
    texts = [row["text"] for row in rows]
    snapshot = store.snapshot(scope)
    evidence = store.read_evidence(snapshot.ranges[0]) if snapshot.ranges else None
    result = {
        "probe": "write",
        "version_file": None,
        "code": _code_info(),
        "profile_dir": str(profile),
        "settings": {
            "host_id": SYNTHETIC["host_id"],
            "api_key_digest": _digest_text(SYNTHETIC["api_key"]),
            "reading_key_digest": _digest_text(SYNTHETIC["reading_api_key"]),
            "backend": SYNTHETIC["backend"],
            "model": SYNTHETIC["model"],
            "base_url": SYNTHETIC["base_url"],
            "wechat_path": SYNTHETIC["wechat_path"],
            "style": SYNTHETIC["style"],
            "subscription_subject": SYNTHETIC["subscription"]["subject"],
            "subscription_client_id": SYNTHETIC["subscription"]["client_id"],
            "subscription_email": SYNTHETIC["subscription"]["email"],
            "subscription_models": SYNTHETIC["subscription_models"],
            "registrations": reloaded.get("registrations") or [],
        },
        "write_checks": {
            "reloaded_subscription": bool(reloaded.get("subscription")),
            "reloaded_registrations": len(reloaded.get("registrations") or []),
        },
        "history": {
            "schema_version": getattr(store, "schema_version", None),
            "scope": [scope.wechat_account_id, scope.person_id],
            "texts": texts,
            "texts_digest": _digest_text("\x00".join(texts)),
            "record_count": stats.record_count,
            "char_count": stats.char_count,
            "source_count": stats.source_count,
            "source_version": stats.source_version,
            "memory_version": stats.memory_version,
            "first_source_id": first.source_id,
            "second_source_id": second.source_id,
            "derived_memory_count": len(store.list_derived_memory(scope)),
            "evidence_text_digest": (
                _digest_text(evidence.text) if evidence is not None else None),
            "migration": {
                "migrated": getattr(store.last_migration, "migrated", None),
                "created": getattr(store.last_migration, "created", None),
                "from_version": getattr(store.last_migration, "from_version", None),
                "to_version": getattr(store.last_migration, "to_version", None),
            },
        },
    }
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return result


def read_profile(expected_path: Path, result_path: Path, resave: bool) -> dict:
    from liteapp import history, settings

    expected = json.loads(Path(expected_path).read_text(encoding="utf-8"))
    profile = _profile_dir()
    loaded = settings.load(profile)
    if resave:
        # Simulate the first normal save of the upgraded app (account migration path).
        settings.save(loaded, profile)
        loaded = settings.load(profile)
    observed_settings = {
        "host_id": loaded.get("host_id"),
        "api_key_digest": _digest_text(str(loaded.get("api_key") or "")),
        "reading_key_digest": _digest_text(str(loaded.get("reading_api_key") or "")),
        "backend": loaded.get("backend"),
        "model": loaded.get("model"),
        "base_url": loaded.get("base_url"),
        "wechat_path": loaded.get("wechat_path"),
        "style": loaded.get("style"),
        "subscription_subject": (loaded.get("subscription") or {}).get("subject"),
        "subscription_client_id": (loaded.get("subscription") or {}).get("client_id"),
        "subscription_email": (loaded.get("subscription") or {}).get("email"),
        "subscription_models": loaded.get("subscription_models"),
        "registrations": loaded.get("registrations") or [],
        "warning": loaded.get("_warning") or "",
    }
    store = history.HistoryStore(profile / "memory.sqlite3")
    expected_history = expected["history"]
    from liteapp.contracts import ScopeKey
    scope = ScopeKey(*expected_history["scope"])
    stats = store.stats(scope)
    rows = store.list_messages(scope, limit=100)
    texts = [row["text"] for row in rows]
    snapshot = store.snapshot(scope)
    evidence = store.read_evidence(snapshot.ranges[0]) if snapshot.ranges else None
    observed_history = {
        "texts": texts,
        "texts_digest": _digest_text("\x00".join(texts)),
        "record_count": stats.record_count,
        "char_count": stats.char_count,
        "source_count": stats.source_count,
        "source_version": stats.source_version,
        "memory_version": stats.memory_version,
        "derived_memory_count": len(store.list_derived_memory(scope)),
        "evidence_text_digest": (
            _digest_text(evidence.text) if evidence is not None else None),
        "migration": {
            "migrated": getattr(store.last_migration, "migrated", None),
            "created": getattr(store.last_migration, "created", None),
            "from_version": getattr(store.last_migration, "from_version", None),
            "to_version": getattr(store.last_migration, "to_version", None),
        },
    }
    result = {"probe": "read", "resave": resave, "code": _code_info(),
              "settings": observed_settings, "history": observed_history,
              "ok": True, "mismatches": []}

    def compare(name, actual, wanted):
        if actual != wanted:
            result["mismatches"].append(
                {"field": name, "expected": wanted, "observed": actual})

    for key, wanted in expected["settings"].items():
        if key in ("registrations",):
            continue
        compare(key, observed_settings.get(key), wanted)
    # The migrated 0.5.7 account view must keep the same verified identity.
    registrations = observed_settings["registrations"]
    if not registrations or not any(
            row.get("registration_id") for row in registrations):
        result["mismatches"].append(
            {"field": "registrations", "expected": "non-empty", "observed": registrations})
    for key in ("texts", "texts_digest", "record_count", "char_count",
                "source_count", "derived_memory_count",
                "evidence_text_digest"):
        compare("history." + key, observed_history.get(key),
                expected_history.get(key))
    if observed_history["source_version"] < expected_history["source_version"]:
        result["mismatches"].append(
            {"field": "history.source_version", "expected": ">=",
             "observed": [observed_history["source_version"],
                          expected_history["source_version"]]})
    result["ok"] = not result["mismatches"]
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return result


def main() -> int:
    global EXPECTED_LOCALAPPDATA, FORBIDDEN_PROFILE
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "read"))
    parser.add_argument("--result", required=True)
    parser.add_argument("--expected", default="")
    parser.add_argument("--resave", action="store_true")
    parser.add_argument("--isolated-localappdata", required=True,
                        help="exact LOCALAPPDATA root this probe may write into")
    parser.add_argument("--forbidden-profile", default="",
                        help="real %LOCALAPPDATA%\\ChatAssistantLite, never touched")
    args = parser.parse_args()
    EXPECTED_LOCALAPPDATA = Path(args.isolated_localappdata)
    FORBIDDEN_PROFILE = Path(args.forbidden_profile) if args.forbidden_profile else None
    if args.mode == "write":
        write_profile(Path(args.result))
        return 0
    if not args.expected:
        raise SystemExit("--expected is required for read mode")
    result = read_profile(Path(args.expected), Path(args.result), args.resave)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
