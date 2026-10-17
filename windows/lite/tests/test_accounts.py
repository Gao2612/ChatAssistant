"""T2 acceptance tests: multi-registration account store and credential lifecycle.

Every test uses synthetic credentials and a loopback fake authorization service;
no real token, chat content or network endpoint is involved.
"""
import base64
import dataclasses
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from liteapp import account_store, settings
from liteapp.contracts import CancellationToken, CancelledError

TOKEN_PATH = "/api/accounts/oauth/token"
REVOKE_PATH = "/api/accounts/oauth/revoke"
SCOPE_DIRECT = "chatgpt.tokens.use.direct"


def credentials(*, subject="user-1", email="person@example.test", client_id="oaiapp_test",
                access="access-1", refresh="refresh-1", expires_in=3600, plan=True,
                id_token="synthetic-id-token"):
    scopes = ["openid", "profile", "email", "offline_access", "resource.invoke"]
    if plan:
        scopes.append(SCOPE_DIRECT)
    return {"subject": subject, "email": email, "client_id": client_id,
            "access_token": access, "refresh_token": refresh, "id_token": id_token,
            "token_type": "Bearer", "scopes": scopes,
            "access_expires_at": time.time() + expires_in}


class FakeAuthServer:
    """Loopback fake for the token refresh and session revocation endpoints."""

    def __init__(self):
        self.token_requests = []
        self.revoke_requests = []
        self.token_status = 200
        self.token_delay = 0.0
        self.token_response = {
            "access_token": "fresh-access", "refresh_token": "fresh-refresh",
            "expires_in": 3600,
            "scope": "openid profile email offline_access resource.invoke " + SCOPE_DIRECT,
        }
        self.revoke_status = 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                fields = dict()
                for pair in body.split("&"):
                    if not pair:
                        continue
                    key, _, value = pair.partition("=")
                    from urllib.parse import unquote_plus
                    fields[key] = unquote_plus(value)
                record = {"path": self.path, "fields": fields,
                          "headers": dict(self.headers)}
                if self.path == TOKEN_PATH:
                    outer.token_requests.append(record)
                    if outer.token_delay:
                        time.sleep(outer.token_delay)
                    status, payload = outer.token_status, outer.token_response
                elif self.path == REVOKE_PATH:
                    outer.revoke_requests.append(record)
                    status, payload = outer.revoke_status, {"revoked": True}
                else:
                    status, payload = 404, {"error": "not_found"}
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
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


class AccountStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="lite-accounts-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.auth = FakeAuthServer()
        self.addCleanup(self.auth.close)
        patcher = patch.object(account_store, "AUTH_BASE", self.auth.base_url)
        patcher.start()
        self.addCleanup(patcher.stop)

    def store(self):
        return account_store.AccountStore(self.folder)

    def raw_settings(self):
        return json.loads((Path(self.folder) / "settings.json").read_text(encoding="utf-8"))


class LegacyMigrationTests(AccountStoreTestCase):
    def write_legacy_settings(self, host_id="urn:uuid:legacy-host"):
        legacy = credentials()
        raw = {
            "host_id": host_id,
            "backend": "chatgpt",
            "model": "gpt-test",
            "chatgpt_model": "gpt-test",
            "subscription_models": [["GPT Test", "gpt-test"]],
            "protected_subscription": base64.b64encode(
                settings.crypt(json.dumps(legacy).encode("utf-8"))).decode("ascii"),
        }
        (Path(self.folder) / "settings.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return legacy

    def test_legacy_single_account_migration_keeps_client_and_host_ids(self):
        legacy_host = "urn:uuid:legacy-host"
        self.write_legacy_settings(legacy_host)

        loaded = settings.load(self.folder)
        self.assertEqual(loaded["host_id"], legacy_host)
        self.assertEqual(loaded["subscription"]["client_id"], "oaiapp_test")

        store = self.store()
        infos = store.list_registrations()
        self.assertEqual(len(infos), 1)
        self.assertTrue(infos[0].selected)
        self.assertEqual(infos[0].status, account_store.STATUS_CONNECTED)
        registration_id = infos[0].registration_id
        self.assertEqual(store.access_token(registration_id, CancellationToken()), "access-1")

        # The first write promotes the in-memory legacy record to the account schema.
        store.select(registration_id)
        raw = self.raw_settings()
        stored = raw["accounts"]["registrations"][0]
        self.assertEqual(stored["registration_id"], registration_id)
        self.assertEqual(stored["client_id"], "oaiapp_test")
        self.assertEqual(stored["host_id"], legacy_host)
        self.assertNotIn("access-1", json.dumps(raw, ensure_ascii=False))

        # Migration is deterministic: legacy loads before the first write agree.
        self.assertEqual(account_store.AccountStore(self.folder).list_registrations()[0]
                         .registration_id, registration_id)
        self.assertEqual(settings.load(self.folder)["host_id"], legacy_host)

    def test_migration_is_not_repeated_after_accounts_are_persisted(self):
        self.write_legacy_settings()
        store = self.store()
        first = store.list_registrations()[0]
        store.save_credentials(first.registration_id, credentials(access="newer-access"))
        store2 = self.store()
        infos = store2.list_registrations()
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].registration_id, first.registration_id)
        self.assertEqual(store2.access_token(first.registration_id, CancellationToken()),
                         "newer-access")


class RegistrationIsolationTests(AccountStoreTestCase):
    def test_same_email_registrations_do_not_overwrite_each_other(self):
        store = self.store()
        info_a = store.save_credentials(None, credentials(
            subject="subject-a", client_id="client-a", access="token-a",
            refresh="refresh-a", email="same@example.test"))
        info_b = store.save_credentials(None, credentials(
            subject="subject-b", client_id="client-b", access="token-b",
            refresh="refresh-b", email="same@example.test"))

        self.assertNotEqual(info_a.registration_id, info_b.registration_id)
        self.assertEqual(len(store.list_registrations()), 2)
        self.assertEqual(store.access_token(info_a.registration_id, CancellationToken()), "token-a")
        self.assertEqual(store.access_token(info_b.registration_id, CancellationToken()), "token-b")
        labels = [info.label for info in store.list_registrations()]
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(self.auth.token_requests, [])

    def test_same_verified_identity_updates_in_place_instead_of_duplicating(self):
        store = self.store()
        first = store.save_credentials(None, credentials())
        again = store.save_credentials(None, credentials(access="token-2", refresh="refresh-2"))
        self.assertEqual(again.registration_id, first.registration_id)
        self.assertEqual(len(store.list_registrations()), 1)
        self.assertEqual(store.access_token(first.registration_id, CancellationToken()), "token-2")

    def test_select_switches_active_registration_and_rejects_unknown_id(self):
        store = self.store()
        info_a = store.save_credentials(None, credentials(subject="a", client_id="ca"))
        info_b = store.save_credentials(None, credentials(subject="b", client_id="cb"))
        store.select(info_b.registration_id)
        self.assertEqual(store.active_registration_id(), info_b.registration_id)
        selected = [info.registration_id for info in store.list_registrations() if info.selected]
        self.assertEqual(selected, [info_b.registration_id])
        loaded = settings.load(self.folder)
        self.assertEqual(loaded["registration_id"], info_b.registration_id)
        self.assertEqual(loaded["subscription"]["subject"], "b")
        with self.assertRaises(ValueError):
            store.select("reg-does-not-exist")

    def test_registration_info_never_exposes_tokens(self):
        store = self.store()
        store.save_credentials(None, credentials(access="synthetic-access-token",
                                                 refresh="synthetic-refresh-token"))
        info = store.list_registrations()[0]
        fields = {field.name for field in dataclasses.fields(account_store.RegistrationInfo)}
        self.assertEqual(fields, {"registration_id", "label", "status",
                                  "model_catalog_version", "revision", "selected"})
        rendered = repr(info)
        self.assertNotIn("synthetic-access-token", rendered)
        self.assertNotIn("synthetic-refresh-token", rendered)
        raw = (Path(self.folder) / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-access-token", raw)
        self.assertNotIn("synthetic-refresh-token", raw)
        self.assertNotIn("person@example.test", raw)


class RefreshLifecycleTests(AccountStoreTestCase):
    def test_parallel_refresh_happens_once_and_stale_snapshot_cannot_write_old_token(self):
        store = self.store()
        info = store.save_credentials(None, credentials(
            access="old-access", refresh="old-refresh", expires_in=-10))
        snapshot = settings.load(self.folder)  # UI-style snapshot held before the refresh
        self.assertEqual(snapshot["subscription"]["access_token"], "old-access")

        tokens, errors = [], []
        barrier = threading.Barrier(6)

        def worker():
            try:
                barrier.wait(timeout=5)
                tokens.append(account_store.AccountStore(self.folder).access_token(
                    info.registration_id, CancellationToken()))
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(set(tokens), {"fresh-access"})
        self.assertEqual(len(self.auth.token_requests), 1)

        # A stale settings snapshot must not write the previous access token back.
        settings.save(snapshot, self.folder)
        restored = settings.load(self.folder)
        self.assertEqual(restored["subscription"]["access_token"], "fresh-access")
        self.assertEqual(restored["subscription"]["refresh_token"], "fresh-refresh")
        self.assertEqual(store.access_token(info.registration_id, CancellationToken()),
                         "fresh-access")
        self.assertEqual(len(self.auth.token_requests), 1)

    def test_save_refreshed_rejects_stale_revision_and_keeps_newest_token(self):
        store = self.store()
        info = store.save_credentials(None, credentials(expires_in=-10))
        expires = time.time() + 3600
        self.assertTrue(store.save_refreshed(
            info.registration_id, info.revision,
            {"access_token": "winner", "refresh_token": "refresh-1",
             "access_expires_at": expires}))
        self.assertFalse(store.save_refreshed(
            info.registration_id, info.revision,
            {"access_token": "loser", "refresh_token": "refresh-1",
             "access_expires_at": expires}))
        self.assertEqual(store.access_token(info.registration_id, CancellationToken()), "winner")

    def test_transient_network_failure_keeps_credentials(self):
        store = self.store()
        info = store.save_credentials(None, credentials(
            access="old-access", refresh="old-refresh", expires_in=-10))
        self.auth.token_status = 503
        self.auth.token_response = {"error": "temporarily_unavailable"}
        with self.assertRaises(account_store.AuthTransientError):
            store.access_token(info.registration_id, CancellationToken())

        retained = settings.load(self.folder)["subscription"]
        self.assertEqual(retained["refresh_token"], "old-refresh")
        self.assertEqual(retained["access_token"], "old-access")
        self.assertEqual(store.list_registrations()[0].status, account_store.STATUS_CONNECTED)

        self.auth.token_status = 200
        self.auth.token_response = {
            "access_token": "recovered", "refresh_token": "refresh-2", "expires_in": 3600,
            "scope": "openid offline_access " + SCOPE_DIRECT,
        }
        self.assertEqual(store.access_token(info.registration_id, CancellationToken()), "recovered")

    def test_definitive_refresh_failure_clears_tokens_but_keeps_registration_mapping(self):
        store = self.store()
        info = store.save_credentials(None, credentials(expires_in=-10))
        self.auth.token_status = 401
        self.auth.token_response = {"error": "invalid_grant"}
        with self.assertRaises(account_store.ReauthRequiredError):
            store.access_token(info.registration_id, CancellationToken())

        infos = store.list_registrations()
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].registration_id, info.registration_id)
        self.assertEqual(infos[0].status, account_store.STATUS_REAUTH)
        stored = self.raw_settings()["accounts"]["registrations"][0]
        self.assertEqual(stored["client_id"], "oaiapp_test")
        self.assertEqual(stored["subject"], "user-1")

        # An explicitly invalidated registration must not keep hitting the network.
        self.auth.token_requests.clear()
        with self.assertRaises(account_store.ReauthRequiredError):
            store.access_token(info.registration_id, CancellationToken())
        self.assertEqual(self.auth.token_requests, [])

    def test_identity_without_plan_permission_is_saved_as_login_only(self):
        store = self.store()
        info = store.save_credentials(None, credentials(plan=False))
        self.assertEqual(info.status, account_store.STATUS_LOGIN_ONLY)
        self.assertIn(info.label, ("pe…@example.test", "person@example.test"))
        with self.assertRaises(account_store.EntitlementError) as raised:
            store.access_token(info.registration_id, CancellationToken())
        self.assertIn("套餐", str(raised.exception))
        stored = self.raw_settings()["accounts"]["registrations"][0]
        self.assertEqual(stored["subject"], "user-1")
        self.assertEqual(stored["client_id"], "oaiapp_test")

    def test_cancelled_token_request_does_not_touch_network_or_credentials(self):
        store = self.store()
        info = store.save_credentials(None, credentials(expires_in=-10))
        cancel = CancellationToken()
        cancel.cancel()
        with self.assertRaises(CancelledError):
            store.access_token(info.registration_id, cancel)
        self.assertEqual(self.auth.token_requests, [])
        self.assertEqual(settings.load(self.folder)["subscription"]["access_token"], "access-1")


class SignOutTests(AccountStoreTestCase):
    def setUp(self):
        super().setUp()
        self.store_ = self.store()
        self.info_a = self.store_.save_credentials(None, credentials(
            subject="a", client_id="ca", access="token-a", refresh="refresh-a",
            email="a@example.test"))
        self.info_b = self.store_.save_credentials(None, credentials(
            subject="b", client_id="cb", access="token-b", refresh="refresh-b",
            email="b@example.test"))
        self.store_.select(self.info_a.registration_id)

    def test_sign_out_only_affects_selected_registration(self):
        result = self.store_.sign_out(self.info_a.registration_id, CancellationToken())
        self.assertTrue(result.revoked)
        self.assertFalse(result.local_only)
        self.assertEqual([info.registration_id for info in self.store_.list_registrations()],
                         [self.info_b.registration_id])
        self.assertEqual(self.store_.active_registration_id(), self.info_b.registration_id)
        self.assertEqual(self.store_.access_token(self.info_b.registration_id,
                                                  CancellationToken()), "token-b")
        self.assertEqual(len(self.auth.revoke_requests), 1)
        self.assertEqual(self.auth.revoke_requests[0]["fields"]["token"], "refresh-a")
        loaded = settings.load(self.folder)
        self.assertEqual(loaded["registration_id"], self.info_b.registration_id)
        self.assertEqual(loaded["subscription"]["subject"], "b")

    def test_failed_revoke_is_reported_as_local_only(self):
        self.auth.revoke_status = 500
        result = self.store_.sign_out(self.info_a.registration_id, CancellationToken())
        self.assertFalse(result.revoked)
        self.assertTrue(result.local_only)
        self.assertIn("本机", result.message)
        self.assertNotIn(self.info_a.registration_id,
                         [info.registration_id for info in self.store_.list_registrations()])

    def test_registration_without_tokens_is_removed_locally_without_revoke(self):
        pending = self.store_.save_credentials(None, {"client_id": "oaiapp_pending"})
        revoke_count = len(self.auth.revoke_requests)
        result = self.store_.sign_out(pending.registration_id, CancellationToken())
        self.assertFalse(result.revoked)
        self.assertTrue(result.local_only)
        self.assertEqual(len(self.auth.revoke_requests), revoke_count)

    def test_cancelled_sign_out_leaves_every_registration_untouched(self):
        cancel = CancellationToken()
        cancel.cancel()
        with self.assertRaises(CancelledError):
            self.store_.sign_out(self.info_a.registration_id, cancel)
        self.assertEqual(len(self.store_.list_registrations()), 2)
        self.assertEqual(self.auth.revoke_requests, [])


class ModelCatalogTests(AccountStoreTestCase):
    def test_model_catalog_version_only_changes_with_the_catalog(self):
        store = self.store()
        info = store.save_credentials(None, credentials())
        self.assertEqual(info.model_catalog_version, 0)
        first = store.update_model_catalog(info.registration_id, [("GPT Test", "gpt-test")])
        self.assertEqual(first.model_catalog_version, 1)
        same = store.update_model_catalog(info.registration_id, [("GPT Test", "gpt-test")])
        self.assertEqual(same.model_catalog_version, 1)
        changed = store.update_model_catalog(
            info.registration_id, [("GPT Test", "gpt-test"), ("GPT Pro", "gpt-pro")])
        self.assertEqual(changed.model_catalog_version, 2)
        self.assertEqual(store.model_catalog(info.registration_id),
                         [("GPT Test", "gpt-test"), ("GPT Pro", "gpt-pro")])
        self.assertEqual(settings.load(self.folder)["subscription_models"],
                         [["GPT Test", "gpt-test"], ["GPT Pro", "gpt-pro"]])


class SettingsIntegrationTests(AccountStoreTestCase):
    def test_settings_save_and_load_roundtrip_keep_one_authoritative_registration(self):
        cfg = settings.load(self.folder)
        host_id = cfg["host_id"]
        cfg["subscription"] = credentials()
        cfg["subscription_models"] = [["GPT Test", "gpt-test"]]
        settings.save(cfg, self.folder)

        restored = settings.load(self.folder)
        self.assertEqual(restored["host_id"], host_id)
        self.assertEqual(restored["subscription"]["access_token"], "access-1")
        self.assertEqual(restored["subscription_models"], [["GPT Test", "gpt-test"]])
        self.assertIsNotNone(restored["registration_id"])
        raw = self.raw_settings()
        self.assertIn("accounts", raw)
        self.assertNotIn("access-1", json.dumps(raw, ensure_ascii=False))

    def test_second_settings_save_from_another_snapshot_does_not_duplicate(self):
        cfg = settings.load(self.folder)
        cfg["subscription"] = credentials()
        settings.save(cfg, self.folder)
        first = settings.load(self.folder)["registration_id"]

        cfg2 = settings.load(self.folder)
        cfg2["style"] = "另一个风格"
        settings.save(cfg2, self.folder)
        self.assertEqual(settings.load(self.folder)["registration_id"], first)
        self.assertEqual(len(account_store.AccountStore(self.folder).list_registrations()), 1)
        self.assertEqual(settings.load(self.folder)["style"], "另一个风格")


if __name__ == "__main__":
    unittest.main()
