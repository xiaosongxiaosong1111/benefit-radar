from __future__ import annotations

import http.client
import json
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


class PolicyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.static = self.root / "static"
        self.static.mkdir()
        (self.static / "index.html").write_text("<title>天津权益雷达</title>", encoding="utf-8")
        (self.root / "private.py").write_text("secret", encoding="utf-8")
        self.server = policy_server.create_server(port=0, static_root=self.static, db_path=self.root / "data.sqlite3")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        body = json.dumps(payload or {}, ensure_ascii=False).encode()
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            client.request(method, path, body=body, headers={"Content-Type": "application/json", **(headers or {})})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def test_bootstrap_and_follow_workflow_persist_materials(self):
        status, _, body = self.request("GET", "/api/bootstrap")
        bootstrap = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(bootstrap["city"], "天津")
        policy = bootstrap["policies"][0]
        policy_id = policy["id"]
        _, _, body = self.request("POST", "/api/follows", {"policy_id": policy_id})
        self.assertEqual(json.loads(body)["follow"]["status"], "preparing")
        updates = {"status": "submitted", "checked_materials": [policy["materials"][0]["id"]]}
        status, _, body = self.request("PATCH", "/api/follows/" + policy_id, updates)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["follow"]["checked_materials"], updates["checked_materials"])
        status, _, body = self.request("GET", "/api/policies/" + policy_id)
        self.assertEqual(json.loads(body)["follow"]["status"], "submitted")
        status, _, body = self.request("DELETE", "/api/follows/" + policy_id)
        self.assertTrue(json.loads(body)["deleted"])

    def test_security_headers_and_path_traversal(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/../private.py", "/%2e%2e/private.py", "/.state.sqlite3", "/foo:secret.js", "/POLICY~1.PY", "/%5c..%5cprivate.py"):
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"secret", body)

    def test_rebinding_and_cross_origin_requests_are_denied(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/follows", {"policy_id": "job-seeking-grant"}, headers)
                self.assertEqual(status, 421)
        self.assertEqual(self.server.service.list_follows(), [])

    def test_discover_does_not_echo_or_store_profile_and_rate_limits_network(self):
        response = {"policies": [], "search": {"status": "not_configured", "results": []}}
        with patch.object(self.server.service, "discover", return_value=response) as discover:
            for _ in range(30):
                status, _, body = self.request("POST", "/api/discover", {"profile": {"stage": "student", "support_condition": None}})
                self.assertEqual(status, 200)
                self.assertNotIn(b"support_condition", body)
            status, _, body = self.request("POST", "/api/discover", {"profile": {}})
        self.assertEqual(status, 429)
        self.assertEqual(discover.call_count, 30)

    def test_bad_json_large_body_and_unknown_options_are_rejected(self):
        status, _, _ = self.request("POST", "/api/discover", {"profile": {"real_name": "private-name"}})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/discover", {"profile": {"stage": "arbitrary"}})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/discover", {"profile": {"graduation_year": True}})
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/follows", {"policy_id": "x" * 17000})
        self.assertEqual(status, 413)
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            client.request("POST", "/api/discover", body=b'{"profile":{},"profile":{}}', headers={"Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.read())["error"]["code"], "invalid_json")
        finally:
            client.close()

    def test_check_without_follows_is_honest_and_local(self):
        with patch("policy_service.fetch_document") as fetch:
            status, _, body = self.request("POST", "/api/check-updates")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["results"], [])
        fetch.assert_not_called()

    def test_unknown_policy_and_invalid_notification_do_not_crash(self):
        status, _, _ = self.request("GET", "/api/policies/not-a-policy")
        self.assertEqual(status, 404)
        status, _, _ = self.request("PATCH", "/api/notifications/unknown", {"read": True})
        self.assertEqual(status, 404)
        status, _, _ = self.request("PATCH", "/api/notifications/unknown", {"read": False})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
