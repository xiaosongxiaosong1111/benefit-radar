from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import policy_server

TEST_PASSWORD = "fixture-" + "password-for-http"
FIRST_KEY = "fixture-" + "key-one-for-http"
SECOND_KEY = "fixture-" + "key-two-for-http"


class AccountsHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = policy_server.create_server(port=0, accounts=True, data_dir=self.root)
        self.server.accounts.ensure_admin("admin", TEST_PASSWORD)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, *, actor=None, headers=None):
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if actor:
            request_headers.update({"Cookie": actor["cookie"], "X-CSRF-Token": actor["csrf_token"]})
        body = json.dumps(payload if payload is not None else {}).encode()
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            client.request(method, path, body=body, headers=request_headers)
            response = client.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw)
        finally:
            client.close()

    def register(self, username):
        status, headers, value = self.request("POST", "/api/auth/register", {"username": username, "password": TEST_PASSWORD})
        self.assertEqual(status, 200)
        return {**value, "cookie": headers["Set-Cookie"].split(";", 1)[0]}

    def test_anonymous_business_api_is_denied_and_session_is_public(self):
        status, _, value = self.request("GET", "/api/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(value, {"enabled": True, "user": None, "csrf_token": None, "registration_open": True})
        for method, route in [("GET", "/api/bootstrap"), ("GET", "/api/policies/job-seeking-grant"), ("POST", "/api/follows"), ("POST", "/api/account/api-key")]:
            with self.subTest(route=route):
                status, _, value = self.request(method, route)
                self.assertEqual(status, 401)
                self.assertEqual(value["error"]["code"], "authentication_required")
        self.assertEqual(self.request("GET", "/api/health")[0], 200)

    def test_registration_has_no_admin_option_and_cookie_is_http_only(self):
        status, _, _ = self.request("POST", "/api/auth/register", {"username": "attacker", "password": TEST_PASSWORD, "is_admin": True})
        self.assertEqual(status, 400)
        actor = self.register("alice")
        self.assertFalse(actor["user"]["is_admin"])
        status, headers, value = self.request("POST", "/api/auth/login", {"username": "alice", "password": TEST_PASSWORD})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        self.assertNotIn("Secure", headers["Set-Cookie"])
        self.assertNotIn("password", json.dumps(value))

    def test_data_profile_and_notification_reads_are_isolated(self):
        first = self.register("alice")
        second = self.register("bob")
        self.assertEqual(self.request("POST", "/api/follows", {"policy_id": "job-seeking-grant"}, actor=first)[0], 200)
        self.assertEqual(self.request("PATCH", "/api/follows/job-seeking-grant", {"checked_materials": ["application"]}, actor=first)[0], 200)
        with patch("policy_sources.search_official", return_value={"status": "not_configured", "results": []}):
            self.assertEqual(self.request("POST", "/api/discover", {"profile": {"stage": "student", "support_condition": True}}, actor=first)[0], 200)
        first_state = self.request("GET", "/api/bootstrap", actor=first)[2]
        second_state = self.request("GET", "/api/bootstrap", actor=second)[2]
        self.assertEqual(first_state["follows"][0]["checked_materials"], ["application"])
        self.assertEqual(first_state["profile"]["support_condition"], True)
        self.assertEqual(second_state["follows"], [])
        self.assertIsNone(second_state["profile"])
        self.assertEqual(second_state["notifications"], [])
        if first_state["notifications"]:
            status, _, _ = self.request("PATCH", "/api/notifications/" + first_state["notifications"][0]["id"], {"read": True}, actor=second)
            self.assertEqual(status, 404)
        status, _, _ = self.request("PATCH", "/api/follows/job-seeking-grant", {"status": "completed"}, actor=second)
        self.assertEqual(status, 404)
        status, _, _ = self.request("POST", "/api/follows", {"policy_id": "job-seeking-grant", "user_id": first["user"]["id"]}, actor=second)
        self.assertEqual(status, 400)

    def test_csrf_and_cross_origin_cannot_write(self):
        actor = self.register("alice")
        for headers in [{"Cookie": actor["cookie"]}, {"Cookie": actor["cookie"], "X-CSRF-Token": "wrong"}, {"Cookie": actor["cookie"], "X-CSRF-Token": "é"}]:
            status, _, value = self.request("POST", "/api/follows", {"policy_id": "job-seeking-grant"}, headers=headers)
            self.assertEqual(status, 403)
            self.assertEqual(value["error"]["code"], "csrf_failed")
        status, _, _ = self.request("POST", "/api/follows", {"policy_id": "job-seeking-grant"}, actor=actor, headers={"Origin": "https://attacker.example"})
        self.assertEqual(status, 421)
        self.assertEqual(self.request("GET", "/api/bootstrap", actor=actor)[2]["follows"], [])

    def test_saved_key_requires_real_uncached_verification_and_is_not_returned(self):
        first = self.register("alice")
        second = self.register("bob")
        with patch.dict(os.environ, {"TENCENTCLOUD_WSA_APIKEY": FIRST_KEY}):
            self.assertFalse(self.request("GET", "/api/bootstrap", actor=second)[2]["service"]["search_configured"])
        status, _, value = self.request("POST", "/api/account/api-key", {"api_key": FIRST_KEY}, actor=first)
        self.assertEqual(status, 200)
        self.assertEqual(value["api_key"]["status"], "saved_unverified")
        self.assertNotIn(FIRST_KEY, json.dumps(value))
        self.assertFalse(self.request("GET", "/api/account/api-key", actor=second)[2]["api_key"]["configured"])
        with patch("policy_sources.search_official", return_value={"status": "ok", "request_id": "controlled-http-proof", "results": []}) as search:
            status, _, value = self.request("POST", "/api/account/api-key/verify", actor=first)
        self.assertEqual(status, 200)
        self.assertEqual(value["api_key"]["status"], "verified")
        self.assertEqual(value["request_id"], "controlled-http-proof")
        self.assertEqual(search.call_args.kwargs["api_key"], FIRST_KEY)
        status, _, value = self.request("DELETE", "/api/account/api-key", actor=first)
        self.assertEqual(status, 200)
        self.assertFalse(value["api_key"]["configured"])
        self.assertFalse(self.request("GET", "/api/bootstrap", actor=first)[2]["service"]["search_configured"])

    def test_verification_failure_and_key_revision_race_are_honest(self):
        actor = self.register("alice")
        self.assertEqual(self.request("POST", "/api/account/api-key/verify", actor=actor)[0], 400)
        self.request("POST", "/api/account/api-key", {"api_key": FIRST_KEY}, actor=actor)
        with patch("policy_sources.search_official", side_effect=RuntimeError("must not leak " + FIRST_KEY)):
            status, _, value = self.request("POST", "/api/account/api-key/verify", actor=actor)
        self.assertEqual(status, 200)
        self.assertEqual(value["api_key"]["status"], "error")
        self.assertNotIn(FIRST_KEY, json.dumps(value))
        def replace_key(*args, **kwargs):
            self.server.accounts.set_api_key(actor["user"]["id"], SECOND_KEY)
            return {"status": "ok", "request_id": "stale-proof", "results": []}
        with patch("policy_sources.search_official", side_effect=replace_key):
            status, _, value = self.request("POST", "/api/account/api-key/verify", actor=actor)
        self.assertEqual(status, 200)
        self.assertEqual(value["api_key"]["status"], "saved_unverified")
        self.assertIsNone(value["request_id"])

    def test_verified_key_discards_prior_discovery_error_cache(self):
        actor = self.register("alice")
        self.request("POST", "/api/account/api-key", {"api_key": FIRST_KEY}, actor=actor)
        failed = {"status": "error", "results": []}
        available = {"status": "ok", "results": [], "request_id": "controlled-new-search"}
        with patch("policy_tenants.search_official", side_effect=[failed, available]) as discover:
            with patch("policy_sources.search_official", return_value=available):
                before = self.request("POST", "/api/discover", {"profile": {}}, actor=actor)[2]
                self.assertEqual(before["search"]["status"], "error")
                verified = self.request("POST", "/api/account/api-key/verify", actor=actor)[2]
                self.assertEqual(verified["api_key"]["status"], "verified")
                after = self.request("POST", "/api/discover", {"profile": {}}, actor=actor)[2]
        self.assertEqual(after["search"]["status"], "ok")
        self.assertFalse(after["search"]["cached"])
        self.assertEqual(discover.call_count, 2)

    def test_logout_revokes_cookie_and_login_errors_are_generic(self):
        actor = self.register("alice")
        self.assertEqual(self.request("POST", "/api/auth/logout", actor=actor)[0], 200)
        self.assertEqual(self.request("GET", "/api/bootstrap", actor=actor)[0], 401)
        unknown = self.request("POST", "/api/auth/login", {"username": "unknown", "password": TEST_PASSWORD})
        wrong = self.request("POST", "/api/auth/login", {"username": "alice", "password": TEST_PASSWORD + "wrong"})
        self.assertEqual(unknown[0], 401)
        self.assertEqual(unknown[2], wrong[2])

    def test_login_and_verification_limits_are_scoped(self):
        actor = self.register("alice")
        for _ in range(5):
            self.assertEqual(self.request("POST", "/api/auth/login", {"username": "missing", "password": TEST_PASSWORD})[0], 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": "missing", "password": TEST_PASSWORD})[0], 429)
        self.assertEqual(self.request("GET", "/api/bootstrap", actor=actor)[0], 200)

    def test_actor_header_tracks_cookie_on_reused_connection(self):
        first = self.register("alice")
        second = self.register("bob")
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            for actor in (first, second):
                client.request("GET", "/api/bootstrap", headers={"Cookie": actor["cookie"]})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("X-Account-Id"), actor["user"]["id"])
                response.read()
            client.request("GET", "/api/bootstrap")
            response = client.getresponse()
            self.assertEqual(response.status, 401)
            self.assertIsNone(response.getheader("X-Account-Id"))
            response.read()
        finally:
            client.close()

    def test_duplicate_cookies_and_invalid_settings_are_rejected(self):
        actor = self.register("alice")
        status, _, _ = self.request("GET", "/api/bootstrap", headers={"Cookie": actor["cookie"] + "; " + actor["cookie"]})
        self.assertEqual(status, 401)
        for payload in ({"api_key": FIRST_KEY, "user_id": "other"}, {"api_key": None}, {"api_key": "bad\nvalue"}):
            self.assertEqual(self.request("POST", "/api/account/api-key", payload, actor=actor)[0], 400)
        self.assertEqual(self.request("DELETE", "/api/account/api-key", {"user_id": actor["user"]["id"]}, actor=actor)[0], 400)
        self.assertEqual(self.request("PATCH", "/api/account/api-key", {}, actor=actor)[0], 404)

    def test_paid_key_verification_is_rate_limited_without_affecting_reads(self):
        actor = self.register("alice")
        self.request("POST", "/api/account/api-key", {"api_key": FIRST_KEY}, actor=actor)
        with patch("policy_sources.search_official", return_value={"status": "ok", "request_id": "controlled-proof", "results": []}) as search:
            for _ in range(3):
                self.assertEqual(self.request("POST", "/api/account/api-key/verify", actor=actor)[0], 200)
            self.assertEqual(self.request("POST", "/api/account/api-key/verify", actor=actor)[0], 429)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(self.request("GET", "/api/account/api-key", actor=actor)[0], 200)


class PublicOriginTests(unittest.TestCase):
    def test_public_https_sets_secure_cookie_and_rejects_other_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            server = policy_server.create_server(port=0, accounts=True, data_dir=directory, public_origin="https://radar.example:8443")
            server.accounts.ensure_admin("admin", TEST_PASSWORD)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps({"username": "alice", "password": TEST_PASSWORD})
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                client.request("POST", "/api/auth/register", body, {"Content-Type": "application/json", "Origin": "https://radar.example:8443"})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn("Secure", response.getheader("Set-Cookie"))
                response.read()
                client.close()
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                client.request("GET", "/", headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"})
                response = client.getresponse()
                self.assertEqual(response.status, 200, "Following an external link may load the public login page")
                response.read()
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_public_origin_configuration_is_https_and_origin_only(self):
        for origin in ("http://radar.example", "https://user:pass@radar.example", "https://radar.example/app", "https://radar.example?x=1", "https://radar.example#fragment"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                policy_server.create_server(port=0, accounts=True, public_origin=origin)
        with self.assertRaises(ValueError):
            policy_server.create_server(port=0, public_origin="https://radar.example")
        self.assertEqual(policy_server._public_origin("https://RADAR.example:443/", accounts=True), "https://radar.example")

    def test_no_anonymous_registration_before_admin_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            server = policy_server.create_server(port=0, accounts=True, data_dir=directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                client.request("POST", "/api/auth/register", json.dumps({"username": "attacker", "password": TEST_PASSWORD}), {"Content-Type": "application/json"})
                response = client.getresponse()
                self.assertEqual(response.status, 503)
                response.read()
                self.assertFalse(server.accounts.has_admin())
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
