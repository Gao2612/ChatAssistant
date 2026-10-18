"""Unified ChatGPT account registrations and authorization lifecycle.

T2 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md``.

One installed application keeps a collection of *registration* records instead of
the 0.2 single ``subscription`` blob. A registration stores:

* a stable ``registration_id`` and the verified ``subject`` / ``client_id`` /
  ``host_id`` mapping;
* DPAPI-encrypted credentials (never plaintext tokens on disk);
* the permission status derived from the granted scopes (plan vs login-only);
* the cached model catalog and its version;
* a monotonically increasing ``revision`` used for compare-and-swap writes.

Design guarantees implemented here:

* **Single writer / atomic save**: every mutation takes a per-data-directory
  re-entrant lock and rewrites ``settings.json`` through a temp file +
  ``os.replace``. The current credential record is always re-read from disk
  before a refresh, so a UI snapshot can never write an old token back.
* **Refresh once**: parallel callers serialize on the same lock; the first one
  refreshes and persists, the rest adopt the new authoritative token.
* **Temporary network failure keeps credentials**, only an explicit invalidation
  (``invalid_grant``/401/403) clears the tokens and keeps the registration
  mapping for a later re-authorization.
* **Auth attempt isolation**: every attempt owns its own loopback listener,
  ``state`` and ``nonce``. Only a callback whose ``state`` matches the current
  transaction can complete it; unrelated paths, stale callbacks from an earlier
  attempt and callbacks after the 15 minute transaction expiry are ignored. The
  listener is closed as soon as the attempt is cancelled or finished.
* **Sign out only touches the selected registration** and reports whether the
  remote session was really revoked or the removal is local-only.

The module is UI-free. :class:`RegistrationInfo` deliberately exposes no token
field; only :class:`AccountStore` internals ever hold decrypted credentials.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from . import settings
from .contracts import CancellationToken, CancelledError

AUTH_BASE = "https://auth.openai.com"
API_BASE = "https://api.openai.com/v1"
RESOURCE = API_BASE
TOKEN_PATH = "/api/accounts/oauth/token"
REVOKE_PATH = "/api/accounts/oauth/revoke"
AUTHORIZATION_PATH = "/api/accounts/authorize"
CALLBACK_PATH = "/auth/callback"
SCOPE_DIRECT = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
TOKEN_TIMEOUT = 30
REVOKE_TIMEOUT = 15
MAX_AUTH_RESPONSE = 256 * 1024
DEFAULT_ATTEMPT_SECONDS = 15 * 60

STATUS_CONNECTED = "connected"
STATUS_LOGIN_ONLY = "login_only"
STATUS_REAUTH = "reauth_required"

_MODES = ("new", "reauth")


class AuthTransientError(ValueError):
    """Temporary authorization/network failure; credentials are kept."""


class ReauthRequiredError(ValueError):
    """The registration can no longer be used and needs a new authorization."""


class EntitlementError(ValueError):
    """Identity is valid but the ChatGPT plan permission was not granted."""


class AuthTransportError(ValueError):
    """Structured transport error used to classify refresh/exchange failures."""

    def __init__(self, message: str, *, transient: bool, status: int | None = None,
                 code: str = "") -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status
        self.code = code


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward authorization codes, tokens or chat data to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _error_code(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        error = error.get("code") or error.get("type") or ""
    if isinstance(data.get("code"), str):
        error = data["code"]
    return error if isinstance(error, str) else ""


def _post_form(url: str, fields: dict[str, str], *, timeout: int = TOKEN_TIMEOUT) -> dict:
    """POST a form to the authorization service, refusing redirects and logging nothing."""
    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(url, data=body, headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_AUTH_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        prefix = _error_code(exc.read(4096) if hasattr(exc, "read") else b"")
        if exc.code in (301, 302, 303, 307, 308):
            raise AuthTransportError("授权服务返回了不支持的重定向。",
                                     transient=False, status=exc.code) from None
        if exc.code in (401, 403):
            raise AuthTransportError("ChatGPT 授权已失效或未获准使用套餐额度，请重新连接。",
                                     transient=False, status=exc.code, code=prefix) from None
        if exc.code == 429:
            raise AuthTransportError("ChatGPT 授权服务当前限流，请稍后重试。",
                                     transient=True, status=exc.code, code=prefix) from None
        raise AuthTransportError(f"ChatGPT 授权服务暂不可用（HTTP {exc.code}）。",
                                 transient=exc.code >= 500, status=exc.code,
                                 code=prefix) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AuthTransportError("ChatGPT 授权服务连接失败或超时。",
                                 transient=True) from None
    if len(raw) > MAX_AUTH_RESPONSE:
        raise AuthTransportError("ChatGPT 授权服务响应过大。", transient=True)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AuthTransportError("ChatGPT 授权服务返回了无效数据。", transient=True) from None
    if not isinstance(data, dict):
        raise AuthTransportError("ChatGPT 授权服务返回了无效数据。", transient=True)
    return data


def _definitive_refresh_failure(error: AuthTransportError, data_error: str = "") -> bool:
    """Whether a failed refresh means the stored credentials are unusable for good."""
    if error.transient:
        return False
    code = (error.code or data_error or "").lower()
    if error.status in (401, 403):
        return True
    return code in {"invalid_grant", "invalid_token", "invalid_client", "invalid_refresh_token",
                    "refresh_token_expired", "consent_required", "unauthorized"}


def _registration_id_for(host_id: str, client_id: str, subject: str) -> str:
    """Deterministic identity for a registration so migration/retries are idempotent."""
    seed = "\x00".join((host_id or "", client_id or "", subject or ""))
    if not seed.strip("\x00"):
        return "reg-" + uuid.uuid4().hex
    return "reg-" + uuid.uuid5(uuid.NAMESPACE_URL, "chatassistant:" + seed).hex


def _mask_email(email: Any) -> str:
    text = str(email or "")
    if "@" not in text:
        return ""
    name, _, domain = text.partition("@")
    shown = name[:2] + "…" if len(name) > 2 else name[:1] + "…"
    return shown + "@" + domain


def _base_label(credentials: dict) -> str:
    return _mask_email((credentials or {}).get("email")) or "ChatGPT 账号"


def _disambiguated_label(base: str, existing: list[str]) -> str:
    if base not in existing:
        return base
    number = 2
    while f"{base}（{number}）" in existing:
        number += 1
    return f"{base}（{number}）"


def _normalize_catalog(models: Any) -> list[list[str]]:
    catalog: list[list[str]] = []
    seen: set[str] = set()
    if not isinstance(models, (list, tuple)):
        return catalog
    for row in models:
        if isinstance(row, (list, tuple)) and len(row) == 2:
            display, slug = str(row[0]).strip(), str(row[1]).strip()
        elif isinstance(row, dict):
            display = str(row.get("display") or row.get("display_name") or "").strip()
            slug = str(row.get("slug") or row.get("id") or "").strip()
        else:
            continue
        if not display or not slug or slug in seen:
            continue
        seen.add(slug)
        # Only labels may be shortened; preserve the full opaque routing ID on disk.
        catalog.append([display[:120], slug])
    return catalog


def _encrypt_credentials(credentials: dict | None) -> str:
    if not credentials:
        return ""
    payload = json.dumps(credentials, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(settings.crypt(payload)).decode("ascii")


def _decrypt_credentials(blob: Any) -> dict | None:
    if not isinstance(blob, str) or not blob:
        return None
    try:
        raw = settings.crypt(base64.b64decode(blob), True)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _permission_status(credentials: dict | None) -> str:
    credentials = credentials or {}
    if not credentials.get("access_token") and not credentials.get("refresh_token"):
        return STATUS_REAUTH
    scopes = credentials.get("scopes") or []
    if SCOPE_DIRECT not in scopes:
        return STATUS_LOGIN_ONLY
    return STATUS_CONNECTED


def _token_live(credentials: dict, now: float) -> bool:
    if not credentials.get("access_token"):
        return False
    try:
        expires_at = float(credentials.get("access_expires_at") or 0)
    except (TypeError, ValueError):
        return False
    return expires_at > now + 60


@dataclass(frozen=True)
class RegistrationInfo:
    """Public, token-free view of one registration for settings UI code."""

    registration_id: str
    label: str
    status: str
    model_catalog_version: int
    revision: int
    selected: bool

    @property
    def requires_authorization(self) -> bool:
        return self.status == STATUS_REAUTH


@dataclass(frozen=True)
class SignOutResult:
    """Outcome of signing out one registration: revoked remotely or local only."""

    registration_id: str
    revoked: bool
    local_only: bool
    message: str

    def __post_init__(self) -> None:
        if self.revoked and self.local_only:
            raise ValueError("revoked and local_only are mutually exclusive")


@dataclass(frozen=True)
class AuthAttemptInfo:
    """Handle returned by :meth:`AuthAttempt.start`; contains no secret token."""

    registration_id: str | None
    mode: str
    redirect_uri: str
    authorization_url: str
    expires_at: float

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"unknown auth attempt mode: {self.mode!r}")


class _Registration:
    __slots__ = ("registration_id", "host_id", "client_id", "subject", "label",
                 "model_catalog", "model_catalog_version", "revision", "credentials")

    def __init__(self, registration_id: str, host_id: str = "", client_id: str = "",
                 subject: str = "", label: str = "", model_catalog: list[list[str]] | None = None,
                 model_catalog_version: int = 0, revision: int = 1,
                 credentials: dict | None = None) -> None:
        self.registration_id = registration_id
        self.host_id = host_id
        self.client_id = client_id
        self.subject = subject
        self.label = label or "ChatGPT 账号"
        self.model_catalog = model_catalog or []
        self.model_catalog_version = model_catalog_version
        self.revision = revision
        self.credentials = credentials


class _State:
    __slots__ = ("active_registration_id", "registrations")

    def __init__(self) -> None:
        self.active_registration_id: str | None = None
        self.registrations: list[_Registration] = []

    def find(self, registration_id: str | None) -> _Registration | None:
        return next((reg for reg in self.registrations
                     if reg.registration_id == registration_id), None)

    def active(self) -> _Registration | None:
        return self.find(self.active_registration_id)


def _create_registration(state: _State, credentials: dict, host_id: str) -> _Registration:
    base = _base_label(credentials)
    label = _disambiguated_label(base, [reg.label for reg in state.registrations])
    return _Registration(
        registration_id=_registration_id_for(
            host_id, str(credentials.get("client_id") or ""),
            str(credentials.get("subject") or "")),
        host_id=host_id, client_id=str(credentials.get("client_id") or ""),
        subject=str(credentials.get("subject") or ""), label=label,
        credentials=dict(credentials))


def _update_registration(registration: _Registration, credentials: dict,
                         *, host_id: str | None = None,
                         all_labels: list[str] | None = None) -> None:
    merged = dict(registration.credentials or {})
    merged.update(credentials)
    registration.credentials = merged
    if credentials.get("subject"):
        registration.subject = str(credentials["subject"])
    if credentials.get("client_id"):
        registration.client_id = str(credentials["client_id"])
    if host_id:
        registration.host_id = host_id
    base = _base_label(merged)
    if base != registration.label:
        others = [label for label in (all_labels or []) if label != registration.label]
        registration.label = _disambiguated_label(base, others)


def _record_from_legacy(credentials: dict, host_id: str) -> _Registration:
    label = _base_label(credentials)
    return _Registration(
        registration_id=_registration_id_for(
            host_id, str(credentials.get("client_id") or ""),
            str(credentials.get("subject") or "")),
        host_id=host_id, client_id=str(credentials.get("client_id") or ""),
        subject=str(credentials.get("subject") or ""), label=label,
        credentials=dict(credentials))


def _state_from_raw(raw: dict) -> _State:
    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    if isinstance(accounts, dict):
        return _parse_accounts(accounts)
    return _migrate_legacy(raw if isinstance(raw, dict) else {})


def _parse_accounts(accounts: dict) -> _State:
    state = _State()
    rows = accounts.get("registrations")
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        registration_id = row.get("registration_id")
        if not isinstance(registration_id, str) or not registration_id:
            continue
        catalog = _normalize_catalog(row.get("model_catalog"))
        try:
            revision = int(row.get("revision", 1))
            catalog_version = int(row.get("model_catalog_version", 1 if catalog else 0))
        except (TypeError, ValueError):
            revision, catalog_version = 1, 1 if catalog else 0
        state.registrations.append(_Registration(
            registration_id=registration_id,
            host_id=str(row.get("host_id") or ""),
            client_id=str(row.get("client_id") or ""),
            subject=str(row.get("subject") or ""),
            label=str(row.get("label") or "ChatGPT 账号"),
            model_catalog=catalog,
            model_catalog_version=max(0, catalog_version),
            revision=max(1, revision),
            credentials=_decrypt_credentials(row.get("protected_credentials"))))
    active = accounts.get("active_registration_id")
    state.active_registration_id = (
        active if state.find(active) is not None
        else (state.registrations[0].registration_id if state.registrations else None))
    return state


def _migrate_legacy(raw: dict) -> _State:
    """Turn the 0.2 single ``protected_subscription`` record into the first registration."""
    state = _State()
    credentials = _decrypt_credentials(raw.get("protected_subscription"))
    if not credentials:
        return state
    host_id = raw.get("host_id") if isinstance(raw.get("host_id"), str) else ""
    catalog = _normalize_catalog(raw.get("subscription_models"))
    registration = _record_from_legacy(credentials, host_id)
    registration.model_catalog = catalog
    registration.model_catalog_version = 1 if catalog else 0
    state.registrations.append(registration)
    state.active_registration_id = registration.registration_id
    return state


def _serialize(state: _State) -> dict:
    rows = []
    for registration in state.registrations:
        rows.append({
            "registration_id": registration.registration_id,
            "host_id": registration.host_id,
            "client_id": registration.client_id,
            "subject": registration.subject,
            "label": registration.label,
            "model_catalog": [list(row) for row in registration.model_catalog],
            "model_catalog_version": registration.model_catalog_version,
            "revision": registration.revision,
            "protected_credentials": _encrypt_credentials(registration.credentials),
        })
    return {
        "version": 1,
        "active_registration_id": state.active_registration_id,
        "registrations": rows,
    }


def _registration_info(registration: _Registration, active_id: str | None) -> RegistrationInfo:
    return RegistrationInfo(
        registration_id=registration.registration_id,
        label=registration.label,
        status=_permission_status(registration.credentials),
        model_catalog_version=registration.model_catalog_version,
        revision=registration.revision,
        selected=registration.registration_id == active_id)


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def folder_lock(folder: Any) -> threading.RLock:
    """One writer lock per data directory, shared by every store in this process."""
    key = str(Path(folder).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def settings_view(raw: dict, folder: Any) -> dict:
    """Compatibility view for :func:`liteapp.settings.load` (never persists anything)."""
    state = _state_from_raw(raw)
    active = state.active()
    return {
        "subscription": dict(active.credentials) if active and active.credentials else None,
        "subscription_models": [list(row) for row in active.model_catalog] if active else [],
        "registration_id": active.registration_id if active else None,
        "active_registration_id": state.active_registration_id,
        "registrations": [
            {"registration_id": info.registration_id, "label": info.label,
             "status": info.status, "model_catalog_version": info.model_catalog_version,
             "revision": info.revision, "selected": info.selected}
            for info in (_registration_info(reg, state.active_registration_id)
                         for reg in state.registrations)],
        "_account_revision": active.revision if active else 0,
        "_data_dir": str(folder),
    }


def write_settings_view(payload: dict, raw: dict, snapshot: dict) -> None:
    """Persist the authoritative credential state for a settings save.

    ``snapshot`` is a caller (UI) settings dict. Its ``subscription`` block is only
    accepted when it is a first-time credential save, completes a provisional
    record, or was loaded from the same revision; a stale snapshot therefore can
    never overwrite a token that a background refresh already persisted.
    """
    try:
        state = _state_from_raw(raw)
    except (OSError, ValueError, TypeError):
        # Never destroy unreadable credential data: keep it byte-for-byte.
        for key in ("accounts", "protected_subscription", "subscription_models"):
            if key in raw:
                payload[key] = raw[key]
        return
    credentials = snapshot.get("subscription")
    if isinstance(credentials, dict) and credentials:
        _apply_settings_snapshot(state, credentials, snapshot)
    else:
        _apply_catalog_snapshot(state, snapshot)
    active = state.active()
    payload["accounts"] = _serialize(state)
    if active is not None:
        if not isinstance(payload.get("host_id"), str) or not payload.get("host_id"):
            payload["host_id"] = active.host_id
        payload["protected_subscription"] = _encrypt_credentials(active.credentials)
        payload["subscription_models"] = [list(row) for row in active.model_catalog]
    else:
        payload["protected_subscription"] = ""
        payload["subscription_models"] = []


def _apply_settings_snapshot(state: _State, credentials: dict, snapshot: dict) -> None:
    """Accept a settings snapshot credential only when it is still authoritative."""
    snapshot_revision = snapshot.get("_account_revision")
    snapshot_registration_id = snapshot.get("registration_id")
    host_id = str(snapshot.get("host_id") or "")
    target = (state.find(snapshot_registration_id)
              if isinstance(snapshot_registration_id, str) else None)
    try:
        previous_revision = (int(snapshot_revision)
                             if snapshot_revision is not None else None)
    except (TypeError, ValueError):
        previous_revision = None
    subject = str(credentials.get("subject") or "")
    if target is None:
        candidate = state.active() or (state.registrations[0]
                                       if state.registrations else None)
        if candidate is None:
            # First credential save for this data directory (0.2-style UI path).
            registration = _create_registration(state, credentials, host_id=host_id)
            state.registrations.append(registration)
            if state.active_registration_id is None:
                state.active_registration_id = registration.registration_id
            _set_catalog(registration, snapshot.get("subscription_models"))
            return
        if candidate.subject and subject and candidate.subject != subject:
            # A snapshot for another account must not overwrite the active one.
            registration = _create_registration(state, credentials, host_id=host_id)
            state.registrations.append(registration)
            _set_catalog(registration, snapshot.get("subscription_models"))
            return
        if candidate.subject and not subject:
            return
        target = candidate  # matches the verified subject or completes a provisional record
    if target.subject and subject and target.subject != subject:
        return  # A stale snapshot for another account must not overwrite this one.
    completing_provisional = not target.subject and bool(subject)
    revision_matches = previous_revision == target.revision
    if not completing_provisional and not revision_matches:
        return  # Stale settings snapshot: keep the authoritative (possibly refreshed) token.
    _update_registration(target, credentials,
                         all_labels=[reg.label for reg in state.registrations])
    target.revision += 1
    _set_catalog(target, snapshot.get("subscription_models"))


def _apply_catalog_snapshot(state: _State, snapshot: dict) -> None:
    """Persist a refreshed model catalog only for the current record revision."""
    target = state.active()
    if target is None:
        return
    try:
        previous_revision = (int(snapshot.get("_account_revision"))
                             if snapshot.get("_account_revision") is not None else None)
    except (TypeError, ValueError):
        previous_revision = None
    if previous_revision != target.revision:
        return
    if _set_catalog(target, snapshot.get("subscription_models")):
        target.revision += 1


def _set_catalog(registration: _Registration, models: Any) -> bool:
    normalized = _normalize_catalog(models)
    if not normalized or normalized == registration.model_catalog:
        return False
    registration.model_catalog = normalized
    registration.model_catalog_version += 1
    return True


class AccountStore:
    """Single-writer store for the application's ChatGPT registrations."""

    def __init__(self, folder: Any = None) -> None:
        self.folder = Path(folder) if folder is not None else settings.data_dir()
        self._lock = folder_lock(self.folder)

    # -- raw settings access -------------------------------------------------
    def _read_state(self) -> _State:
        return _state_from_raw(settings.read_raw(self.folder))

    def _save_state(self, state: _State) -> None:
        raw = settings.read_raw(self.folder)
        raw["accounts"] = _serialize(state)
        if not isinstance(raw.get("host_id"), str) or not raw.get("host_id"):
            active = state.active()
            host_id = (active.host_id if active is not None
                       else settings.load(self.folder)["host_id"])
            raw["host_id"] = host_id or ("urn:uuid:" + str(uuid.uuid4()))
        active = state.active()
        raw["protected_subscription"] = (
            _encrypt_credentials(active.credentials) if active is not None else "")
        raw["subscription_models"] = (
            [list(row) for row in active.model_catalog] if active is not None else [])
        settings.write_raw(raw, self.folder)

    def host_id(self) -> str:
        with self._lock:
            raw = settings.read_raw(self.folder)
            value = raw.get("host_id")
            if isinstance(value, str) and value.strip():
                return value
            return settings.load(self.folder)["host_id"]

    def _load_record(self, registration_id: str) -> _Registration | None:
        with self._lock:
            return self._read_state().find(registration_id)

    # -- reading -------------------------------------------------------------
    def list_registrations(self) -> list[RegistrationInfo]:
        with self._lock:
            state = self._read_state()
            return [_registration_info(reg, state.active_registration_id)
                    for reg in state.registrations]

    def active_registration_id(self) -> str | None:
        with self._lock:
            return self._read_state().active_registration_id

    def model_catalog(self, registration_id: str) -> list[tuple[str, str]]:
        with self._lock:
            registration = self._require(self._read_state(), registration_id)
            return [(str(row[0]), str(row[1])) for row in registration.model_catalog]

    def select(self, registration_id: str) -> None:
        with self._lock:
            state = self._read_state()
            self._require(state, registration_id)
            raw = settings.read_raw(self.folder)
            already_active = state.active_registration_id == registration_id
            if already_active and isinstance(raw.get("accounts"), dict):
                return
            state.active_registration_id = registration_id
            self._save_state(state)

    # -- credential writes ---------------------------------------------------
    @staticmethod
    def _require(state: _State, registration_id: str) -> _Registration:
        registration = state.find(registration_id)
        if registration is None:
            raise ValueError("ChatGPT 注册不存在，请重新连接账号。")
        return registration

    def _match_registration(self, state: _State, credentials: dict) -> _Registration | None:
        subject = str(credentials.get("subject") or "")
        client_id = str(credentials.get("client_id") or "")
        if subject:
            existing = next((reg for reg in state.registrations
                             if reg.subject == subject), None)
            if existing is not None:
                return existing
        if client_id:
            # A provisional record only kept client_id before the token exchange.
            provisional = next((reg for reg in state.registrations
                                if reg.client_id == client_id and not reg.subject), None)
            if provisional is not None:
                return provisional
        return None

    def save_credentials(self, registration_id: str | None, credentials: dict,
                         *, expected_revision: int | None = None,
                         host_id: str | None = None) -> RegistrationInfo:
        """Persist a completed (or provisional) credential record.

        ``registration_id=None`` deduplicates by verified ``subject`` first and by a
        provisional ``client_id`` second, so retries/re-authorizations update one
        record while two different accounts that share an email stay separate.
        """
        if not isinstance(credentials, dict) or not credentials:
            raise ValueError("授权凭据格式无效。")
        with self._lock:
            state = self._read_state()
            if registration_id is not None:
                registration = self._require(state, registration_id)
            else:
                registration = self._match_registration(state, credentials)
            if (registration is not None and expected_revision is not None
                    and registration.revision != expected_revision):
                raise ValueError("注册记录已在其它任务中更新，请刷新后重试。")
            if registration is None:
                registration = _create_registration(
                    state, credentials,
                    host_id=host_id or (state.active().host_id if state.active() else "")
                    or self.host_id())
                state.registrations.append(registration)
                if state.active_registration_id is None:
                    state.active_registration_id = registration.registration_id
            else:
                _update_registration(
                    registration, credentials, host_id=host_id,
                    all_labels=[reg.label for reg in state.registrations])
            registration.revision += 1
            self._save_state(state)
            return _registration_info(registration, state.active_registration_id)

    def save_refreshed(self, registration_id: str, expected_revision: int,
                       credentials: dict) -> bool:
        """CAS write of refreshed credentials; False when another writer was faster."""
        if not isinstance(credentials, dict) or not credentials:
            raise ValueError("刷新后的授权凭据无效。")
        if not credentials.get("access_token"):
            raise ValueError("刷新后的授权凭据缺少访问令牌。")
        with self._lock:
            state = self._read_state()
            registration = state.find(registration_id)
            if registration is None:
                raise ValueError("ChatGPT 注册不存在，请重新连接账号。")
            if registration.revision != expected_revision:
                return False
            merged = dict(registration.credentials or {})
            merged.update(credentials)
            registration.credentials = merged
            registration.revision += 1
            self._save_state(state)
            return True

    def _clear_credentials(self, state: _State, registration: _Registration,
                           reason: str = "") -> None:
        registration.credentials = {}
        registration.revision += 1
        self._save_state(state)

    def update_model_catalog(self, registration_id: str, models: Any,
                             *, expected_revision: int | None = None) -> RegistrationInfo:
        normalized = _normalize_catalog(models)
        with self._lock:
            state = self._read_state()
            registration = self._require(state, registration_id)
            if (expected_revision is not None
                    and registration.revision != expected_revision):
                raise ValueError("注册记录已在其它任务中更新，请刷新后重试。")
            if normalized != registration.model_catalog:
                registration.model_catalog = normalized
                registration.model_catalog_version += 1
                registration.revision += 1
                self._save_state(state)
            return _registration_info(registration, state.active_registration_id)

    # -- token access --------------------------------------------------------
    def access_token(self, registration_id: str, cancel: CancellationToken) -> str:
        """Return a usable bearer token, refreshing at most once across threads."""
        if not isinstance(cancel, CancellationToken):
            raise TypeError("cancel must be CancellationToken")
        cancel.raise_if_cancelled()
        with self._lock:
            cancel.raise_if_cancelled()
            state = self._read_state()
            registration = self._require(state, registration_id)
            credentials = dict(registration.credentials or {})
            if not credentials.get("access_token") and not credentials.get("refresh_token"):
                raise ReauthRequiredError("ChatGPT 授权已失效，请重新连接账号。")
            if SCOPE_DIRECT not in (credentials.get("scopes") or []):
                raise EntitlementError(
                    "当前 ChatGPT 注册仅完成身份登录，未授予套餐使用权限，请重新授权。")
            if _token_live(credentials, time.time()):
                return str(credentials["access_token"])
            refresh_token = str(credentials.get("refresh_token") or "")
            if not refresh_token:
                self._clear_credentials(state, registration)
                raise ReauthRequiredError("ChatGPT 授权已失效，请重新连接账号。")
            cancel.raise_if_cancelled()
            try:
                refreshed = _post_form(AUTH_BASE + TOKEN_PATH, {
                    "grant_type": "refresh_token",
                    "client_id": str(credentials.get("client_id") or ""),
                    "refresh_token": refresh_token,
                    "resource": RESOURCE,
                })
            except AuthTransportError as exc:
                if cancel.is_cancelled():
                    raise CancelledError("token refresh cancelled") from None
                if _definitive_refresh_failure(exc):
                    self._clear_credentials(state, registration, str(exc))
                    raise ReauthRequiredError(
                        "ChatGPT 授权已失效，请重新连接账号。") from None
                raise AuthTransientError(
                    "ChatGPT 令牌刷新暂时失败，已保留本机授权，请稍后重试。") from None
            cancel.raise_if_cancelled()
            merged = dict(credentials)
            access = refreshed.get("access_token")
            if not isinstance(access, str) or not access:
                raise AuthTransientError(
                    "ChatGPT 令牌服务返回了无效数据，已保留本机授权，请稍后重试。")
            merged["access_token"] = access
            if isinstance(refreshed.get("refresh_token"), str) and refreshed["refresh_token"]:
                merged["refresh_token"] = refreshed["refresh_token"]
            if isinstance(refreshed.get("id_token"), str) and refreshed["id_token"]:
                merged["id_token"] = refreshed["id_token"]
            try:
                expires_in = int(refreshed.get("expires_in", 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            merged["access_expires_at"] = time.time() + max(60, expires_in)
            scope_value = refreshed.get("scope")
            if isinstance(scope_value, str) and scope_value.strip():
                merged["scopes"] = scope_value.split()
            if self.save_refreshed(registration_id, registration.revision, merged):
                return access
            # Another writer (thread/process) refreshed first: adopt its token.
            newest = self._read_state().find(registration_id)
            newest_credentials = dict(newest.credentials or {}) if newest else {}
            if _token_live(newest_credentials, time.time()):
                return str(newest_credentials["access_token"])
            raise AuthTransientError(
                "ChatGPT 令牌刷新与其他任务冲突，已保留本机授权，请稍后重试。")

    # -- sign out ------------------------------------------------------------
    def sign_out(self, registration_id: str, cancel: CancellationToken) -> SignOutResult:
        """Sign out only the selected registration; distinguish revoke vs local only."""
        if not isinstance(cancel, CancellationToken):
            raise TypeError("cancel must be CancellationToken")
        cancel.raise_if_cancelled()
        with self._lock:
            cancel.raise_if_cancelled()
            state = self._read_state()
            registration = self._require(state, registration_id)
            credentials = dict(registration.credentials or {})
            has_tokens = bool(credentials.get("refresh_token") or credentials.get("access_token"))
            revoked = False
            if has_tokens:
                try:
                    revoked = self._revoke(credentials)
                except CancelledError:
                    raise
                except (ValueError, OSError):
                    revoked = False
                if cancel.is_cancelled():
                    raise CancelledError("sign out cancelled")
            state.registrations = [reg for reg in state.registrations
                                   if reg.registration_id != registration_id]
            if state.active_registration_id == registration_id:
                state.active_registration_id = (
                    state.registrations[0].registration_id
                    if state.registrations else None)
            self._save_state(state)
        if not has_tokens:
            message = "该 ChatGPT 注册没有可撤销的令牌，已仅在本机退出。"
        elif revoked:
            message = "已撤销该 ChatGPT 注册的会话并退出。"
        else:
            message = "无法确认远程撤销，已仅在本机退出该注册。"
        return SignOutResult(registration_id=registration_id, revoked=revoked,
                             local_only=not revoked, message=message)

    def _revoke(self, credentials: dict) -> bool:
        token = credentials.get("refresh_token") or credentials.get("access_token")
        if not token:
            return False
        _post_form(AUTH_BASE + REVOKE_PATH, {
            "token": str(token),
            "token_type_hint": "refresh_token",
            "client_id": str(credentials.get("client_id") or ""),
        }, timeout=REVOKE_TIMEOUT)
        return True


# -- authorization attempt ---------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Loopback callback page; never logs query data or authorization codes."""

    def do_GET(self):  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_error(404)
            return
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        server = self.server
        lock = getattr(server, "callback_lock", None)
        callbacks = getattr(server, "callbacks", None)
        if lock is None or callbacks is None:
            self.send_error(500)
            return
        with lock:
            callbacks.append(query)
        body = ("<html><meta charset='utf-8'><title>ChatAssistant</title>"
                "<p>授权结果已返回。可以关闭此页面并回到微信实时助手。</p></html>"
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *_args):
        return  # never log callback query data


def authorize_params(*, host_id: str, client_id: str, redirect_uri: str, state: str,
                     nonce: str, verifier: str, id_token_hint: str = "",
                     login_hint: str = "", agent_name_hint: str = "") -> dict[str, str]:
    """OAuth/PKCE parameters for the official browser flow (no token is included)."""
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "ext_agent_host_id": host_id,
    }
    if id_token_hint:
        params["id_token_hint"] = id_token_hint
    if login_hint:
        params["login_hint"] = login_hint
    if agent_name_hint:
        params["agent_name_hint"] = agent_name_hint
    return params


def default_verify_identity(id_token: str, client_id: str, nonce: str) -> dict:
    """Verify the official ID token; lazy import keeps the dependency out of tests."""
    from .subscription import _verify_identity
    return _verify_identity(id_token, client_id, nonce)


class AuthAttempt:
    """One cancellable, single-use browser authorization transaction.

    ``start`` prepares the PKCE parameters, opens the browser and starts the
    loopback listener; ``wait`` blocks until a callback whose ``state`` matches
    this transaction completes the exchange. Any other callback is ignored, and
    the listener is closed as soon as the attempt finishes, expires or is
    cancelled, so a late browser callback can never complete a newer attempt.
    """

    def __init__(self, store: AccountStore, *, opener: Callable[[str], bool] | None = None,
                 verify_identity: Callable[[str, str, str], dict] | None = None,
                 deadline_seconds: float = DEFAULT_ATTEMPT_SECONDS,
                 monotonic: Callable[[], float] | None = None) -> None:
        if not isinstance(store, AccountStore):
            raise TypeError("store must be AccountStore")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be > 0")
        self.store = store
        self._opener = opener or webbrowser.open
        self._verify_identity = verify_identity or default_verify_identity
        self._deadline_seconds = float(deadline_seconds)
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._status = "idle"
        self._server: HTTPServer | None = None
        self._info: AuthAttemptInfo | None = None
        self._registration_id: str | None = None
        self._mode = "new"
        self._host_id = ""
        self._issued_client = ""
        self._id_token_hint = ""
        self._login_hint = ""
        self._expected_subject = ""
        self._state = ""
        self._nonce = ""
        self._verifier = ""
        self._redirect_uri = ""

    # -- public state --------------------------------------------------------
    @property
    def info(self) -> AuthAttemptInfo | None:
        return self._info

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    @property
    def status(self) -> str:
        return self._status

    @property
    def pending(self) -> bool:
        return self._status == "pending"

    def start(self, registration_id: str | None = None) -> AuthAttemptInfo:
        with self._lock:
            if self._status != "idle":
                raise RuntimeError("authorization attempt already started")
            self._prepare(registration_id)
            server = self._open_listener()
            try:
                authorization_url = self._authorization_url()
                if not self._opener(authorization_url):
                    raise ValueError("无法打开系统浏览器，请检查默认浏览器设置。")
            except BaseException:
                self._status = "failed"
                self._close_listener()
                raise
            self._status = "pending"
            self._info = AuthAttemptInfo(
                registration_id=registration_id, mode=self._mode,
                redirect_uri=self._redirect_uri, authorization_url=authorization_url,
                expires_at=time.time() + self._deadline_seconds)
            assert self._server is server
            return self._info

    def wait(self, timeout: float | None = None) -> RegistrationInfo:
        """Block until the current transaction completes; raises on cancel/timeout."""
        if self._status != "pending":
            if self._status == "cancelled":
                raise CancelledError("authorization attempt cancelled")
            if self._status == "completed":
                raise RuntimeError("authorization attempt already completed")
            raise ValueError("当前授权事务已结束，请重新开始登录。")
        deadline = self._monotonic() + (timeout if timeout is not None
                                        else self._deadline_seconds)
        try:
            while True:
                if self._cancel.is_set():
                    raise CancelledError("authorization attempt cancelled")
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    self._status = "expired"
                    raise ValueError("等待 ChatGPT 授权超时，请重试。")
                server = self._server
                if server is None:
                    raise CancelledError("authorization listener closed")
                try:
                    server.timeout = min(0.2, max(0.01, remaining))
                    server.handle_request()
                except (OSError, ValueError):
                    if self._cancel.is_set():
                        raise CancelledError("authorization attempt cancelled") from None
                    continue
                for callback in self._take_callbacks():
                    result = self._handle_callback(callback)
                    if result is not None:
                        self._status = "completed"
                        return result
            raise AssertionError("unreachable")
        except CancelledError:
            if self._status == "pending":
                self._status = "cancelled"
            raise
        except ValueError:
            if self._status == "pending":
                self._status = "failed"
            raise
        finally:
            self._close_listener()

    def cancel(self) -> None:
        """Stop listening immediately; a late callback can no longer complete this attempt."""
        self._cancel.set()
        with self._lock:
            if self._status in ("idle", "pending"):
                self._status = "cancelled"
        self._close_listener()

    # -- internals -----------------------------------------------------------
    def _prepare(self, registration_id: str | None) -> None:
        record = None
        if registration_id is not None:
            record = self.store._load_record(registration_id)
            if record is None:
                raise ValueError("要重新授权的 ChatGPT 注册不存在，请重新开始登录。")
            self._mode = "reauth"
            self._host_id = record.host_id
            self._issued_client = record.client_id
            credentials = record.credentials or {}
            self._id_token_hint = str(credentials.get("id_token") or "")
            self._login_hint = str(credentials.get("email") or "")
            self._expected_subject = record.subject
        else:
            self._mode = "new"
            self._host_id = self.store.host_id()
            self._issued_client = ""
        self._registration_id = registration_id
        self._state = secrets.token_urlsafe(32)
        self._nonce = secrets.token_urlsafe(32)
        self._verifier = _b64url(secrets.token_bytes(48))

    def _open_listener(self) -> HTTPServer:
        try:
            server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        except OSError:
            raise ValueError("无法启动本机授权回调监听，请检查端口占用后重试。") from None
        server.timeout = 1
        server.callbacks = []
        server.callback_lock = threading.Lock()
        self._server = server
        self._redirect_uri = (
            f"http://127.0.0.1:{server.server_address[1]}{CALLBACK_PATH}")
        return server

    def _authorization_url(self) -> str:
        client_id = self._issued_client or "dynamic_agent_client"
        params = authorize_params(
            host_id=self._host_id, client_id=client_id,
            redirect_uri=self._redirect_uri, state=self._state,
            nonce=self._nonce, verifier=self._verifier,
            id_token_hint=self._id_token_hint, login_hint=self._login_hint,
            agent_name_hint="" if self._issued_client else "ChatAssistant Windows")
        return AUTH_BASE + AUTHORIZATION_PATH + "?" + urllib.parse.urlencode(params)

    def _take_callbacks(self) -> list[dict]:
        server = self._server
        if server is None:
            return []
        lock = getattr(server, "callback_lock", None)
        callbacks = getattr(server, "callbacks", None)
        if lock is None or callbacks is None:
            return []
        with lock:
            taken = list(callbacks)
            callbacks.clear()
        return taken

    def _handle_callback(self, callback: dict) -> RegistrationInfo | None:
        state_value = str(callback.get("state", [""])[0])
        if not secrets.compare_digest(state_value, self._state):
            return None  # Stale/foreign callback: the current transaction stays pending.
        if callback.get("error"):
            self._status = "failed"
            raise ValueError("你未授权 ChatGPT 套餐访问。可稍后重新连接。")
        code = str(callback.get("code", [""])[0])
        if not code:
            self._status = "failed"
            raise ValueError("ChatGPT 登录没有返回有效授权，请重试。")
        client_id = str(callback.get("client_id", [""])[0]) or self._issued_client
        if self._issued_client and client_id != self._issued_client:
            self._status = "failed"
            raise ValueError("ChatGPT 登录返回的客户端标识不一致，请重试。")
        if not client_id:
            self._status = "failed"
            raise ValueError("ChatGPT 登录没有返回有效授权，请重试。")
        self._close_listener()
        return self._complete(code, client_id)

    def _complete(self, code: str, client_id: str) -> RegistrationInfo:
        if self._cancel.is_set():
            raise CancelledError("authorization attempt cancelled")
        try:
            token = _post_form(AUTH_BASE + TOKEN_PATH, {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "code_verifier": self._verifier,
                "redirect_uri": self._redirect_uri,
                "resource": RESOURCE,
            })
        except AuthTransportError as exc:
            if self._cancel.is_set():
                raise CancelledError("authorization attempt cancelled") from None
            if exc.transient:
                raise AuthTransientError(
                    "ChatGPT 授权令牌交换暂时失败，请检查网络后重试。") from None
            raise ValueError("ChatGPT 登录没有返回有效授权，请重试。") from None
        if self._cancel.is_set():
            raise CancelledError("authorization attempt cancelled")
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("ChatGPT 登录没有返回有效授权，请重试。")
        id_token = token.get("id_token") if isinstance(token.get("id_token"), str) else ""
        try:
            identity = self._verify_identity(id_token, client_id, self._nonce)
        except ValueError:
            raise
        except Exception:
            raise ValueError("ChatGPT 身份校验失败，请重新登录。") from None
        if not isinstance(identity, dict) or not identity.get("subject"):
            raise ValueError("ChatGPT 身份校验失败，请重新登录。")
        subject = str(identity["subject"])
        if self._expected_subject and subject != self._expected_subject:
            raise ValueError(
                "本次登录账户与已选择账户不同；为保护原账户，请重新选择登录流程。")
        if self._cancel.is_set():
            raise CancelledError("authorization attempt cancelled")
        scope_value = token.get("scope")
        scopes = scope_value.split() if isinstance(scope_value, str) else []
        try:
            expires_in = int(token.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        credentials = {
            "subject": subject,
            "email": str(identity.get("email") or ""),
            "client_id": client_id,
            "access_token": access_token,
            "refresh_token": str(token.get("refresh_token") or ""),
            "id_token": id_token,
            "token_type": str(token.get("token_type") or "Bearer"),
            "scopes": scopes,
            "access_expires_at": time.time() + max(60, expires_in),
        }
        try:
            information = self.store.save_credentials(
                self._registration_id, credentials, host_id=self._host_id)
        except ValueError:
            raise
        if self._mode == "new":
            self.store.select(information.registration_id)
        return information

    def _close_listener(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server is None:
            return
        try:
            server.server_close()
        except OSError:
            pass
