"""T2 acceptance tests: ChatGPT authorization attempt lifecycle.

The fake authorization service runs on loopback; identity verification is
injected, so no real browser flow / JWKS / account is touched. Chat text and real
credentials never appear in these tests.

Covers: only a callback matching the current ``state`` may complete the current
authorization; unrelated paths, stale/expired transactions and late callbacks from
an earlier attempt cannot complete a newer one; cancellation stops the listener
immediately; identity valid but plan permission missing is persisted as
"login only"; a re-authorization keeps the previous credentials when the token
exchange fails transiently and rejects a different account subject.
"""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
import urllib.error
import urllib.request

from liteapp import account_store, settings
from liteapp.contracts import CancellationToken, CancelledError

TOKEN_PATH = "/api/accounts/oauth/token"
SCOPE_DIRECT = "chatgpt.tokens.use.direct"


def token_payload(*, access="auth-access", refresh="auth-refresh",
                  expires_in=3600, plan=True, id_token="synthetic-id-token"):
    scopes = "openid profile email offline_access resource.invoke"
    if plan:
        scopes += " " + SCOPE_DIRECT
    return {"access_token": access, "refresh_token": refresh, "id_token": id_token,
            "token_type": "Bearer", "expires_in": expires_in, "scope": scopes}


def seeded_credentials(*, subject="user-1", email="person@example.test",
                       client_id="oaiapp_test", access="access-1",
                       refresh="refresh-1", expires_in=3600, plan=True):
    scopes = ["openid", "profile", "email", "offline_access", "resource.invoke"]
    if plan:
        scopes.append(SCOPE_DIRECT)
    return {"subject": subject, "email": email, "client_id": client_id,
            "access_token": access, "refresh_token": refresh,
            "id_token": "synthetic-id-token", "token_type": "Bearer",
            "scopes": scopes, "access_expires_at": time.time() + expires_in}


class FakeAuthorizationService:
    """Loopback stand-in for the token endpoint of the authorization service."""

    def __init__(self):
        self.token_requests = []
        self.token_status = 200
        self.token_response = token_payload()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw_body = self.rfile.read(length).decode("utf-8") if length else ""
                from urllib.parse import unquote_plus
                fields = {}
                for pair in raw_body.split("&"):
                    if not pair:
                        continue
                    key, _, value = pair.partition("=")
                    fields[key] = unquote_plus(value)
                if self.path != TOKEN_PATH:
                    status, payload = 404, {"error": "not_found"}
                else:
                    outer.token_requests.append({"path": self.path, "fields": fields,
                                                 "headers": dict(self.headers)})
                    status, payload = outer.token_status, outer.token_response
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class WaitBox:
    """Run ``AuthAttempt.wait`` off-thread and record its outcome."""

    def __init__(self, attempt):
        self.attempt = attempt
        self.info = None
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self.info = self.attempt.wait()
        except BaseException as exc:  # noqa: BLE001 - asserted by the caller
            self.error = exc

    def start(self):
        self.thread.start()
        return self

    def join(self, timeout=10):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError("AuthAttempt.wait did not return in time")
        return self


class AuthLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="lite-auth-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.service = FakeAuthorizationService()
        self.addCleanup(self.service.close)
        patcher = patch.object(account_store, "AUTH_BASE", self.service.base_url)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = account_store.AccountStore(self.folder)
        self.verifier_calls = []

    def verifier(self, subject="user-1", email="person@example.test"):
        def verify(id_token, client_id, nonce):
            self.verifier_calls.append({"id_token": id_token, "client_id": client_id,
                                        "nonce": nonce})
            return {"subject": subject, "email": email}
        return verify

    def start_attempt(self, registration_id=None, *, deadline=10.0, opener=None,
                      verifier=None):
        attempt = account_store.AuthAttempt(
            self.store, opener=opener or (lambda url: True),
            verify_identity=verifier or self.verifier(),
            deadline_seconds=deadline)
        attempt.start(registration_id)
        return attempt

    @staticmethod
    def auth_param(attempt, name):
        query = parse_qs(urlsplit(attempt.info.authorization_url).query,
                         keep_blank_values=True)
        return query[name][0]

    @staticmethod
    def base_url(attempt):
        parts = urlsplit(attempt.redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"

    def request(self, url):
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def deliver(self, attempt, *, state=None, code="auth-code",
                client_id="dynamic-client", error=None):
        query = {
            "state": [state if state is not None else self.auth_param(attempt, "state")],
            "client_id": [client_id],
        }
        if code is not None:
            query["code"] = [code]
        if error:
            query["error"] = [error]
        url = attempt.redirect_uri + "?" + urlencode(query, doseq=True)
        return self.request(url)

    def raw_settings(self):
        return json.loads((Path(self.folder) / "settings.json").read_text(encoding="utf-8"))


class SuccessfulAuthorizationTests(AuthLifecycleTestCase):
    def test_new_authorization_completes_after_matching_state_callback(self):
        opened = []
        attempt = account_store.AuthAttempt(
            self.store, opener=lambda url: opened.append(url) or True,
            verify_identity=self.verifier(), deadline_seconds=10)
        info = attempt.start(None)
        self.assertEqual(info.mode, "new")
        self.assertIsNone(info.registration_id)
        self.assertEqual(opened, [info.authorization_url])
        self.assertIn("code_challenge_method=S256", info.authorization_url)
        self.assertEqual(self.auth_param(attempt, "client_id"), "dynamic_agent_client")
        self.assertIn(SCOPE_DIRECT, self.auth_param(attempt, "scope"))
        self.assertNotIn("auth-access", repr(info))

        box = WaitBox(attempt).start()
        status, _ = self.deliver(attempt, client_id="dynamic_agent_client")
        self.assertEqual(status, 200)
        box.join()
        self.assertIsNone(box.error)

        completed = box.info
        self.assertEqual(completed.status, account_store.STATUS_CONNECTED)
        self.assertEqual(completed.label, "pe…@example.test")
        self.assertEqual(len(self.store.list_registrations()), 1)
        self.assertTrue(self.store.list_registrations()[0].selected)
        self.assertEqual(self.store.access_token(completed.registration_id,
                                                 CancellationToken()), "auth-access")

        request = self.service.token_requests[0]["fields"]
        self.assertEqual(request["grant_type"], "authorization_code")
        self.assertEqual(request["code"], "auth-code")
        self.assertEqual(request["client_id"], "dynamic_agent_client")
        self.assertEqual(request["redirect_uri"], attempt.redirect_uri)
        self.assertEqual(request["resource"], account_store.RESOURCE)
        self.assertTrue(request["code_verifier"])
        self.assertEqual(self.verifier_calls[0]["nonce"],
                         self.auth_param(attempt, "nonce"))
        self.assertEqual(self.verifier_calls[0]["client_id"], "dynamic_agent_client")
    def test_identity_without_plan_permission_is_saved_as_login_only(self):
        self.service.token_response = token_payload(plan=False)
        attempt = self.start_attempt()
        box = WaitBox(attempt).start()
        self.deliver(attempt)
        box.join()
        self.assertIsNone(box.error)
        self.assertEqual(box.info.status, account_store.STATUS_LOGIN_ONLY)
        infos = self.store.list_registrations()
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].status, account_store.STATUS_LOGIN_ONLY)
        with self.assertRaises(account_store.EntitlementError):
            self.store.access_token(box.info.registration_id, CancellationToken())
        self.assertEqual(self.service.token_requests[0]["fields"]["grant_type"],
                         "authorization_code")


class CallbackValidationTests(AuthLifecycleTestCase):
    def test_unrelated_path_and_wrong_state_callbacks_are_ignored(self):
        attempt = self.start_attempt()
        box = WaitBox(attempt).start()

        status, _ = self.request(self.base_url(attempt) + "/favicon.ico")
        self.assertEqual(status, 404)
        status, _ = self.request(self.base_url(attempt) + "/")
        self.assertEqual(status, 404)

        status, _ = self.deliver(attempt, state="an-old-or-foreign-state")
        self.assertEqual(status, 200)
        self.assertTrue(attempt.pending)
        self.assertEqual(self.store.list_registrations(), [])
        self.assertEqual(self.service.token_requests, [])

        self.deliver(attempt)
        box.join()
        self.assertIsNone(box.error)
        self.assertEqual(len(self.store.list_registrations()), 1)

    def test_old_attempt_callback_does_not_complete_a_new_authorization(self):
        old = self.start_attempt(deadline=5)
        old_state = self.auth_param(old, "state")
        old.cancel()

        new = self.start_attempt(deadline=5)
        box = WaitBox(new).start()
        status, _ = self.deliver(new, state=old_state, code="stale-code",
                                 client_id="stale-client")
        self.assertEqual(status, 200)
        self.assertTrue(new.pending)
        self.assertEqual(self.store.list_registrations(), [])

        self.deliver(new)
        box.join()
        self.assertIsNone(box.error)
        self.assertEqual(len(self.store.list_registrations()), 1)
        self.assertEqual(len(self.service.token_requests), 1)
        self.assertEqual(self.service.token_requests[0]["fields"]["code"], "auth-code")

    def test_denied_callback_reports_error_without_registration(self):
        attempt = self.start_attempt()
        box = WaitBox(attempt).start()
        self.deliver(attempt, code=None, error="access_denied")
        box.join()
        self.assertIsInstance(box.error, ValueError)
        self.assertIn("未授权", str(box.error))
        self.assertEqual(self.store.list_registrations(), [])
        self.assertEqual(self.service.token_requests, [])
        self.assertFalse(attempt.pending)

    def test_expired_transaction_cannot_be_completed_by_a_late_callback(self):
        attempt = self.start_attempt(deadline=0.4)
        started = time.monotonic()
        with self.assertRaises(ValueError) as raised:
            attempt.wait()
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("超时", str(raised.exception))
        self.assertFalse(attempt.pending)
        self.assertEqual(self.store.list_registrations(), [])
        self.assertEqual(self.service.token_requests, [])
        with self.assertRaises((urllib.error.URLError, OSError)):
            self.deliver(attempt)
        self.assertEqual(self.store.list_registrations(), [])

    def test_cancel_stops_listening_immediately_without_completing(self):
        attempt = self.start_attempt(deadline=30)
        box = WaitBox(attempt).start()
        deadline = time.monotonic() + 3
        while not attempt.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        attempt.cancel()
        box.join(timeout=3)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsInstance(box.error, CancelledError)
        self.assertFalse(attempt.pending)
        self.assertEqual(self.store.list_registrations(), [])
        with self.assertRaises((urllib.error.URLError, OSError)):
            self.deliver(attempt)
        self.assertEqual(self.service.token_requests, [])

    def test_cancel_before_wait_raises_without_listening(self):
        attempt = self.start_attempt(deadline=30)
        attempt.cancel()
        with self.assertRaises(CancelledError):
            attempt.wait()
        self.assertEqual(self.store.list_registrations(), [])

    def test_browser_open_failure_aborts_attempt(self):
        with self.assertRaises(ValueError):
            self.start_attempt(opener=lambda url: False)
        self.assertEqual(self.store.list_registrations(), [])
        self.assertEqual(self.service.token_requests, [])


class ReauthorizationTests(AuthLifecycleTestCase):
    def seed(self):
        return self.store.save_credentials(None, seeded_credentials())

    def test_reauth_requires_existing_registration(self):
        with self.assertRaises(ValueError):
            self.start_attempt("reg-missing")

    def test_transient_exchange_failure_keeps_previous_credentials(self):
        information = self.seed()
        self.service.token_status = 503
        self.service.token_response = {"error": "temporarily_unavailable"}
        attempt = self.start_attempt(information.registration_id)
        self.assertEqual(attempt.info.mode, "reauth")
        self.assertEqual(self.auth_param(attempt, "client_id"), "oaiapp_test")
        box = WaitBox(attempt).start()
        self.deliver(attempt, client_id="oaiapp_test")
        box.join()
        self.assertIsInstance(box.error, account_store.AuthTransientError)

        infos = self.store.list_registrations()
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].registration_id, information.registration_id)
        self.assertEqual(infos[0].status, account_store.STATUS_CONNECTED)
        retained = settings.load(self.folder)["subscription"]
        self.assertEqual(retained["refresh_token"], "refresh-1")
        self.assertEqual(self.store.access_token(information.registration_id,
                                                 CancellationToken()), "access-1")

    def test_reauth_rejects_a_different_account_subject(self):
        information = self.seed()
        attempt = self.start_attempt(information.registration_id,
                                     verifier=self.verifier(subject="another-user"))
        box = WaitBox(attempt).start()
        self.deliver(attempt, client_id="oaiapp_test")
        box.join()
        self.assertIsInstance(box.error, ValueError)
        self.assertIn("不同", str(box.error))
        stored = self.raw_settings()["accounts"]["registrations"][0]
        self.assertEqual(stored["subject"], "user-1")
        self.assertEqual(self.store.access_token(information.registration_id,
                                                 CancellationToken()), "access-1")

    def test_successful_reauth_updates_tokens_in_place(self):
        information = self.seed()
        self.service.token_response = token_payload(access="rotated-access",
                                                    refresh="rotated-refresh")
        attempt = self.start_attempt(information.registration_id)
        box = WaitBox(attempt).start()
        self.deliver(attempt, client_id="oaiapp_test")
        box.join()
        self.assertIsNone(box.error)
        self.assertEqual(box.info.registration_id, information.registration_id)
        self.assertEqual(len(self.store.list_registrations()), 1)
        self.assertEqual(self.store.access_token(information.registration_id,
                                                 CancellationToken()), "rotated-access")
        self.assertEqual(settings.load(self.folder)["subscription"]["refresh_token"],
                         "rotated-refresh")


if __name__ == "__main__":
    unittest.main()
