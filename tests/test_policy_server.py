from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import policy_server  # noqa: E402


class PolicyServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.static_root = Path(self.temp_dir.name)
        (self.static_root / "index.html").write_text(
            "<!doctype html><title>办事不白跑</title>", encoding="utf-8"
        )
        self.server = policy_server.create_server(port=0, static_root=self.static_root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: object,
        *,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={"Content-Type": content_type},
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    @staticmethod
    def valid_payload() -> dict[str, object]:
        return {
            "city": "天津市",
            "matter": "居住证办理",
            "facts": {
                "has_identity": True,
                "has_registration": None,
            },
        }

    def assert_error(self, body: bytes, code: str) -> None:
        value = json.loads(body)
        self.assertEqual(set(value), {"error"})
        self.assertEqual(value["error"]["code"], code)
        self.assertIsInstance(value["error"]["message"], str)

    def test_analyze_returns_report_without_echoing_user_facts(self) -> None:
        report = {
            "matter": "居住证办理",
            "checklist": {"status": "needs_confirmation", "items": []},
            "sources": ["https://ga.tj.gov.cn/zcjd/1.html"],
        }
        with patch.object(
            policy_server, "analyze_policy_request", return_value=report
        ) as analyze:
            status, headers, body = self.request(
                "POST", "/api/analyze", self.valid_payload()
            )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), report)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(b"has_identity", body)
        analyze.assert_called_once_with(
            city="天津市",
            matter="居住证办理",
            facts={"has_identity": True, "has_registration": None},
        )

    def test_rejects_unsupported_city_before_analysis(self) -> None:
        payload = self.valid_payload()
        payload["city"] = "北京市"

        with patch.object(policy_server, "analyze_policy_request") as analyze:
            status, _, body = self.request("POST", "/api/analyze", payload)

        self.assertEqual(status, 422)
        self.assert_error(body, "unsupported_city")
        analyze.assert_not_called()

    def test_rejects_missing_extra_or_wrong_typed_fields(self) -> None:
        valid = self.valid_payload()
        cases = (
            {"city": "天津市", "facts": {}},
            {**valid, "debug": True},
            {**valid, "matter": ["居住证办理"]},
            {**valid, "facts": []},
            {**valid, "facts": {"has_identity": "yes"}},
        )

        for payload in cases:
            with self.subTest(payload=payload):
                with patch.object(policy_server, "analyze_policy_request") as analyze:
                    status, _, body = self.request("POST", "/api/analyze", payload)
                self.assertEqual(status, 400)
                self.assert_error(body, "invalid_request")
                analyze.assert_not_called()

    def test_rejects_sensitive_fact_names_without_reflecting_values(self) -> None:
        sensitive_value = "120101199001011234"
        for field in ("id_number", "phone", "real_name", "api_key"):
            payload = self.valid_payload()
            payload["facts"] = {field: sensitive_value}
            with self.subTest(field=field):
                with patch.object(policy_server, "analyze_policy_request") as analyze:
                    status, _, body = self.request("POST", "/api/analyze", payload)
                self.assertEqual(status, 400)
                self.assert_error(body, "sensitive_input")
                self.assertNotIn(sensitive_value.encode(), body)
                analyze.assert_not_called()

    def test_rejects_unknown_fact_keys_instead_of_collecting_more_data(self) -> None:
        payload = self.valid_payload()
        payload["facts"] = {"employer": True}

        with patch.object(policy_server, "analyze_policy_request") as analyze:
            status, _, body = self.request("POST", "/api/analyze", payload)

        self.assertEqual(status, 400)
        self.assert_error(body, "invalid_request")
        analyze.assert_not_called()

    def test_requires_json_and_rejects_unknown_routes(self) -> None:
        status, _, body = self.request(
            "POST", "/api/analyze", self.valid_payload(), content_type="text/plain"
        )
        self.assertEqual(status, 415)
        self.assert_error(body, "unsupported_media_type")

        status, _, body = self.request("POST", "/api/missing", self.valid_payload())
        self.assertEqual(status, 404)
        self.assert_error(body, "not_found")

    def test_internal_errors_are_safe_and_do_not_expose_provider_details(self) -> None:
        with patch.object(
            policy_server,
            "analyze_policy_request",
            side_effect=RuntimeError("provider leaked sk-private-value"),
        ):
            status, _, body = self.request("POST", "/api/analyze", self.valid_payload())

        self.assertEqual(status, 500)
        self.assert_error(body, "analysis_failed")
        self.assertNotIn(b"sk-private-value", body)
        self.assertNotIn(b"Traceback", body)


if __name__ == "__main__":
    unittest.main()
