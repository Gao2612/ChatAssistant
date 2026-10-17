"""OpenAI Sign in with ChatGPT (local OSS ChatGPT plan usage flow).

Credential ownership lives in :mod:`liteapp.account_store` (T2): registrations,
refresh single-flight, compare-and-swap persistence and sign-out are handled there.
This module keeps the wire details of the official flow (browser PKCE + loopback
callback + token exchange + identity verification) and the Responses API stream, and
routes any settings dict that carries a ``registration_id`` through the account
service instead of mutating its own snapshot copy of the credentials.
"""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import webbrowser

import urllib.error
import urllib.request

from .account_store import (AUTH_BASE, AUTHORIZATION_PATH, API_BASE, RESOURCE,
                            SCOPE_DIRECT, SCOPES, TOKEN_PATH, authorize_params)
from .analysis import NoRedirectHandler, parse_result, build_payload
from .contracts import CancellationToken

ISSUER = AUTH_BASE
JWKS_URL = AUTH_BASE + "/.well-known/jwks.json"
_refresh_lock = threading.Lock()


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _request(url, data=None, access_token=None, timeout=30, limit=2 * 1024 * 1024):
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if isinstance(data, bytes) else "application/json"
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=timeout) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("OpenAI 服务响应过大。")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            raise ValueError("OpenAI 服务返回了不支持的重定向。") from None
        if e.code in (401, 403):
            raise ValueError("ChatGPT 授权已失效或未获准使用套餐额度，请重新连接。") from None
        if e.code == 429:
            raise ValueError("ChatGPT 账户当前用量受限，请在 ChatGPT Usage 页面查看。") from None
        raise ValueError(f"OpenAI 服务暂不可用（HTTP {e.code}）。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("OpenAI 服务连接失败或超时。") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("OpenAI 服务返回了无效数据。") from None


def _verify_identity(id_token, client_id, nonce):
    try:
        import jwt
        signing_key = jwt.PyJWKClient(JWKS_URL, timeout=15).get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, signing_key, algorithms=["RS256", "ES256"],
                            audience=client_id, issuer=ISSUER, options={"require": ["iss", "sub", "aud", "exp", "nonce"]})
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError("ChatGPT 登录校验未通过，请重试。")
        return {"subject": claims["sub"], "email": claims.get("email", "")}
    except ImportError:
        raise ValueError("缺少安全登录组件，请修复或重新安装应用。") from None
    except ValueError:
        raise
    except Exception:
        raise ValueError("ChatGPT 身份校验失败，请重新登录。") from None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path != "/auth/callback":
            self.send_error(404)
            return
        self.server.callback = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        body = ("<html><meta charset='utf-8'><title>ChatAssistant</title>"
                "<p>授权结果已返回。可以关闭此页面并回到微信实时助手。</p></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return  # Never log callback query data or authorization codes.


def wait_for_callback(server, seconds=900):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and getattr(server, "callback", None) is None:
        server.timeout = min(1, max(0.01, deadline - time.monotonic()))
        server.handle_request()
    return getattr(server, "callback", None)


def sign_in(host_id, current=None, on_registration=None):
    """Open the official browser flow; return one fully validated credential record.

    Kept as the blocking compatibility path for the current settings UI. New code
    should use :class:`liteapp.account_store.AuthAttempt`, which owns the persistent
    registration and supports cancellation/transaction expiry.
    """
    if not host_id.startswith(("urn:uuid:", "urn:ietf:params:oauth:jwk-thumbprint:", "did:key:")):
        raise ValueError("本机授权标识无效，请重新启动登录。")
    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.timeout = 1
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = _b64url(secrets.token_bytes(48))
    issued_client = (current or {}).get("client_id")
    params = authorize_params(
        host_id=host_id,
        client_id=issued_client or "dynamic_agent_client",
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        verifier=verifier,
        id_token_hint=(current or {}).get("id_token", "") if issued_client else "",
        login_hint=(current or {}).get("email", "") if issued_client else "",
        agent_name_hint="" if issued_client else "ChatAssistant Windows",
    )
    try:
        url = AUTH_BASE + AUTHORIZATION_PATH + "?" + urlencode(params)
        if not webbrowser.open(url):
            raise ValueError("无法打开系统浏览器，请检查默认浏览器设置。")
        callback = wait_for_callback(server)
    finally:
        server.server_close()
    if callback is None:
        raise ValueError("等待 ChatGPT 授权超时，请重试。")
    if callback.get("state", [""])[0] != state:
        raise ValueError("ChatGPT 授权校验失败，请重新开始登录。")
    if callback.get("error"):
        raise ValueError("你未授权 ChatGPT 套餐访问。可稍后重新连接。")
    code = callback.get("code", [""])[0]
    client_id = callback.get("client_id", [issued_client or ""])[0]
    if not code or not client_id or (issued_client and client_id != issued_client):
        raise ValueError("ChatGPT 登录没有返回有效授权，请重试。")
    if not issued_client and on_registration is not None:
        on_registration(client_id)
    token = _request(AUTH_BASE + TOKEN_PATH, data=urlencode({
        "grant_type": "authorization_code", "client_id": client_id,
        "code": code, "code_verifier": verifier,
        "redirect_uri": redirect_uri, "resource": RESOURCE,
    }).encode("ascii"), timeout=30)
    scopes = str(token.get("scope", "")).split()
    if SCOPE_DIRECT not in scopes:
        # Identity is valid but the account did not grant plan usage; the account
        # service stores this state as "login only" instead of discarding it.
        raise ValueError("此 ChatGPT 登录没有授予套餐使用权限。请重新连接并同意该权限。")
    id_token = token.get("id_token", "")
    identity = _verify_identity(id_token, client_id, nonce)
    if current and current.get("subject") and current["subject"] != identity["subject"]:
        raise ValueError("本次登录账户与已选择账户不同；为保护原账户，请重新选择登录流程。")
    now = time.time()
    return {
        **identity, "client_id": client_id,
        "access_token": token.get("access_token", ""),
        "refresh_token": token.get("refresh_token", ""),
        "id_token": id_token, "token_type": token.get("token_type", "Bearer"),
        "scopes": scopes, "access_expires_at": now + int(token.get("expires_in", 3600)),
    }


def _account_store_for(config):
    """The account service that owns this settings dict, when it has a registration."""
    registration_id = config.get("registration_id") or config.get("active_registration_id")
    if not registration_id:
        return None, None
    from .account_store import AccountStore
    folder = config.get("_data_dir")
    return (AccountStore(folder) if folder else AccountStore()), registration_id


def _legacy_access_token(config):
    """Read-only fallback for in-memory configs without a persistent registration."""
    session = config.get("subscription")
    if not session or not session.get("access_token") or not session.get("refresh_token"):
        raise ValueError("请先点击“Continue with ChatGPT”并完成套餐授权。")
    with _refresh_lock:
        if float(session.get("access_expires_at", 0)) <= time.time() + 60:
            refreshed = _request(AUTH_BASE + TOKEN_PATH, data=urlencode({
                "grant_type": "refresh_token", "client_id": session["client_id"],
                "refresh_token": session["refresh_token"], "resource": RESOURCE,
            }).encode("ascii"), timeout=30)
            session.update({
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed.get("refresh_token", session["refresh_token"]),
                "access_expires_at": time.time() + int(refreshed.get("expires_in", 3600)),
            })
        if SCOPE_DIRECT not in session.get("scopes", []):
            raise ValueError("当前授权未包含 ChatGPT 套餐使用权限，请重新连接。")
        return session["access_token"]


def access_token(config):
    """Return a bearer token; refreshes through the single account-service writer."""
    store, registration_id = _account_store_for(config)
    if store is not None and registration_id:
        return store.access_token(registration_id, CancellationToken())
    return _legacy_access_token(config)


def _remember_model_catalog(config, models):
    """Best-effort model catalog version bump for the active registration."""
    store, registration_id = _account_store_for(config)
    if store is None or not registration_id:
        return
    try:
        store.update_model_catalog(registration_id, models)
    except (OSError, ValueError, TypeError):
        return  # A concurrent account edit must not break a model refresh.


def list_models(config):
    data = _request(API_BASE + "/models", access_token=access_token(config), timeout=30)
    models = model_catalog(data)
    _remember_model_catalog(config, models)
    return models


def model_catalog(data):
    values = data.get("models", []) if isinstance(data, dict) else []
    return [(x["display_name"], x["slug"]) for x in values
            if isinstance(x, dict) and x.get("visibility") == "list"
            and isinstance(x.get("display_name"), str) and isinstance(x.get("slug"), str)]


def analyze(config, text):
    token = access_token(config)
    model = config.get("model", "").strip()
    if not model or model not in {slug for _name, slug in config.get("subscription_models", [])}:
        raise ValueError("请选择当前 ChatGPT 账号模型目录中的模型。")
    shared = build_payload(text, config.get("relationship", "普通联系人"),
                           config.get("style", "自然、简短"), model)
    prompt = shared["messages"][1]["content"]
    payload = json.dumps({
        "model": model, "instructions": shared["messages"][0]["content"],
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "store": False, "stream": True,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_BASE + "/responses", data=payload, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "Accept": "text/event-stream",
    })
    text_parts, seen = [], 0
    try:
        with urllib.request.build_opener(NoRedirectHandler()).open(request, timeout=60) as response:
            event_type, data_lines = "", []
            for raw_line in response:
                seen += len(raw_line)
                if seen > 1024 * 1024:
                    raise ValueError("模型响应过大。")
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if not data_lines:
                        event_type = ""
                        continue
                    try:
                        event = json.loads("\n".join(data_lines))
                    except ValueError:
                        raise ValueError("ChatGPT 流返回了无效事件。") from None
                    kind = event_type or event.get("type", "")
                    if kind == "response.output_text.delta":
                        text_parts.append(event.get("delta", ""))
                    elif kind == "response.failed" or kind == "error":
                        details = event.get("error")
                        code = event.get("code") or (details.get("code", "") if isinstance(details, dict) else "")
                        if code in ("subscription_sharing_usage_limit_exceeded", "usage_unavailable"):
                            raise ValueError("ChatGPT 套餐当前无法继续处理，请在 ChatGPT Usage 页面查看。")
                        raise ValueError("ChatGPT 分析失败，请稍后重试。")
                    elif kind == "response.completed":
                        if text_parts:
                            return parse_result("".join(text_parts))
                        output = event.get("response", {}).get("output", [])
                        collected = "".join(part.get("text", "") for item in output
                                            for part in item.get("content", [])
                                            if part.get("type") == "output_text")
                        return parse_result(collected)
                    event_type, data_lines = "", []
                elif line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        raise ValueError("ChatGPT 分析流未正常完成，请重试。")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            raise ValueError("ChatGPT 服务返回了不支持的重定向。") from None
        if e.code == 429:
            raise ValueError("ChatGPT 账户当前用量受限，请在 ChatGPT Usage 页面查看。") from None
        raise ValueError(f"ChatGPT 分析失败（HTTP {e.code}）。") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("ChatGPT 分析连接失败或超时。") from None
