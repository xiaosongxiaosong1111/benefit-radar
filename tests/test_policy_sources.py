from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy_sources as sources


URL = "https://www.tj.gov.cn/zwgk/policy.html"
HTML = '''<html><head><meta name="ArticleTitle" content="天津支持政策">
<meta name="PubDate" content="2026-10-08"></head><body>
<nav>会变化的导航</nav><div class="trs_editor_view TRS_UEDITOR trs_word">
<p>一、申请条件</p><p>申请人应在规定时间内提交材料。</p>
<script>do_not_include()</script><p>二、申请材料</p><p>提供申请表。</p>
</div><footer>点击次数 123</footer></body></html>'''


class Reply:
    def __init__(self, body: bytes, media="text/html; charset=utf-8"):
        self.body = body
        self.headers = {"Content-Type": media}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.body[:size]


class SourceTests(unittest.TestCase):
    def test_document_parser_retains_body_not_navigation_or_scripts(self):
        doc = sources.parse_document(HTML, URL)
        self.assertEqual(doc["title"], "天津支持政策")
        self.assertEqual(doc["published_at"], "2026-10-08")
        self.assertIn("申请条件", doc["body"])
        for bad in ("会变化的导航", "do_not_include", "点击次数"):
            self.assertNotIn(bad, doc["body"])
        self.assertEqual(doc["source_url"], URL)
        self.assertEqual(len(doc["fingerprint"]), 64)

    def test_nav_and_format_changes_do_not_change_fingerprint(self):
        first = sources.parse_document(HTML, URL)
        changed = sources.parse_document(HTML.replace("123", "999").replace("申请人应", "申请人\n应"), URL)
        self.assertEqual(first["fingerprint"], changed["fingerprint"])
        actual = sources.parse_document(HTML.replace("申请表", "申请表和缴费凭证"), URL)
        self.assertNotEqual(first["fingerprint"], actual["fingerprint"])

    def test_validity_metadata_change_is_part_of_policy_fingerprint(self):
        valid = HTML.replace('<nav>', '<div>有 效 性 ：有效</div><nav>')
        invalid = valid.replace('性 ：有效', '性 ：失效')
        first = sources.parse_document(valid, URL)
        second = sources.parse_document(invalid, URL)
        self.assertEqual(first["validity"], "有效")
        self.assertEqual(second["validity"], "失效")
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_rejects_unsafe_urls_and_missing_editor(self):
        for url in ("http://www.tj.gov.cn/x", "https://tj.gov.cn.evil.test/x", "https://user@www.tj.gov.cn/x", "https://www.tj.gov.cn:8443/x"):
            with self.subTest(url=url), self.assertRaises(sources.SourceError):
                sources.parse_document(HTML, url)
        with self.assertRaises(sources.SourceError):
            sources.parse_document("<html><body>登录后访问</body></html>", URL)

    def test_fetch_checks_bounds_and_does_not_echo_network_errors(self):
        with patch.object(sources, "_ensure_public_host"):
            doc = sources.fetch_document(URL, opener=lambda request, timeout: Reply(HTML.encode()))
            self.assertIn("申请条件", doc["body"])
            with self.assertRaises(sources.SourceError):
                sources.fetch_document(URL, opener=lambda request, timeout: Reply(b"x" * (sources.MAX_DOCUMENT_BYTES + 1)))
            def failed(request, timeout):
                raise OSError("secret-provider-value")
            with self.assertRaises(sources.SourceError) as result:
                sources.fetch_document(URL, opener=failed)
            self.assertNotIn("secret-provider-value", str(result.exception))

    def test_missing_key_is_honest_without_network_call(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(sources, "build_opener") as opener:
            result = sources.search_official("天津求职补贴")
        self.assertEqual(result["status"], "not_configured")
        self.assertIsNone(result["request_id"])
        opener.assert_not_called()

    def test_search_uses_official_payload_and_filters_unofficial_results(self):
        payload = {"Response": {"RequestId": "req-real-test", "Pages": [
            json.dumps({"title": "官方政策", "url": URL, "date": "2026-10-08", "passage": "政策摘要"}, ensure_ascii=False),
            json.dumps({"title": "转载", "url": "https://example.com/copy"}),
            "invalid-json",
        ]}}
        seen = {}
        def open_request(request, timeout):
            seen["request"] = request
            return Reply(json.dumps(payload, ensure_ascii=False).encode(), "application/json")
        with patch.dict("os.environ", {sources.API_KEY_ENV: "dummy-test-key"}), patch.object(sources, "_open_search", side_effect=open_request):
            result = sources.search_official("天津求职补贴", freshness="m3")
        request_payload = json.loads(seen["request"].data)
        self.assertEqual(request_payload, {"Query": "天津求职补贴", "Site": "tj.gov.cn", "Freshness": "m3"})
        self.assertEqual(result["request_id"], "req-real-test")
        self.assertEqual([item["title"] for item in result["results"]], ["官方政策"])
        self.assertEqual(result["results"][0]["passage"], "政策摘要")
        self.assertNotIn("dummy-test-key", json.dumps(result))

    def test_provider_failure_is_not_presented_as_live_success(self):
        with patch.dict("os.environ", {sources.API_KEY_ENV: "dummy-test-key"}), patch.object(sources, "_open_search", side_effect=RuntimeError("secret-provider-value")):
            result = sources.search_official("天津求职补贴")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-provider-value", json.dumps(result))

    def test_new_document_digest_contains_only_source_excerpts(self):
        document = sources.parse_document(HTML, URL)
        digest = sources.decompose_document(document)
        self.assertFalse(digest["reviewed"])
        self.assertTrue(digest["requirements"])
        self.assertTrue(digest["materials"])
        self.assertEqual(digest["timing"], [])
        for key in ("audience", "requirements", "materials", "timing", "actions"):
            for excerpt in digest[key]:
                self.assertIn(excerpt, document["body"])


if __name__ == "__main__":
    unittest.main()
