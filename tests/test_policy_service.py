from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy_service import PolicyService, ServiceValidationError


POLICY = {
    "id": "job-seeking-grant", "title": "一次性求职补贴", "deadline": "2026-10-31",
    "source_url": "https://www.tj.gov.cn/official.html",
    "materials": [{"id": "identity", "label": "身份证复印件"}],
    "queries": ["天津高校毕业生求职补贴"],
}


class PolicyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.now = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
        self.service = PolicyService(self.path, clock=lambda: self.now)
        self.catalog = patch("policy_service.get_catalog", return_value=[dict(POLICY)])
        self.single = patch("policy_service.get_policy", side_effect=lambda value: dict(POLICY) if value == POLICY["id"] else (_ for _ in ()).throw(ServiceValidationError("invalid_policy", "不存在的政策")))
        self.catalog.start()
        self.single.start()

    def tearDown(self):
        self.catalog.stop()
        self.single.stop()
        self.temp.cleanup()

    def test_follows_and_materials_persist_and_repeated_follow_is_idempotent(self):
        first = self.service.follow(POLICY["id"])
        self.service.update_follow(POLICY["id"], {"status": "submitted", "checked_materials": ["identity"]})
        again = self.service.follow(POLICY["id"])
        restarted = PolicyService(self.path, clock=lambda: self.now)
        self.assertEqual(first["created_at"], again["created_at"])
        self.assertEqual(restarted.list_follows()[0]["status"], "submitted")
        self.assertEqual(restarted.list_follows()[0]["checked_materials"], ["identity"])
        self.assertEqual(len(restarted.list_follows()), 1)

    def test_unknown_material_and_status_are_rejected_without_changing_state(self):
        self.service.follow(POLICY["id"])
        for value in ({"status": "approved"}, {"checked_materials": ["unasked"]}, {"checked_materials": ["identity", "identity"]}):
            with self.subTest(value=value), self.assertRaises(ServiceValidationError):
                self.service.update_follow(POLICY["id"], value)
        self.assertEqual(self.service.list_follows()[0]["status"], "preparing")

    def test_deadline_notifications_are_unique_and_completed_items_stop_reminders(self):
        self.service.follow(POLICY["id"])
        notices = self.service.notifications()
        self.service.notifications()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "deadline")
        self.service.mark_notification(notices[0]["id"], True)
        self.assertTrue(self.service.notifications()[0]["read"])
        self.service.update_follow(POLICY["id"], {"status": "completed"})
        self.assertEqual(self.service.notifications(), [])

    def test_initial_snapshot_is_baseline_then_changes_are_deduplicated(self):
        self.service.follow(POLICY["id"])
        document = {"title": "通知", "body": "第一条材料\n第二条材料", "fingerprint": "one", "checked_at": self.now.isoformat()}
        with patch("policy_service.fetch_document", return_value=document):
            initial = self.service.check_updates()
        self.assertEqual(initial["results"][0]["status"], "baseline")
        self.assertFalse(any(n["kind"] == "source_changed" for n in initial["notifications"]))
        revised = {**document, "body": "第一条材料\n调整后的第二条材料", "fingerprint": "two"}
        with patch("policy_service.fetch_document", return_value=revised):
            changed = self.service.check_updates()
            repeated = self.service.check_updates()
        changes = [n for n in changed["notifications"] if n["kind"] == "source_changed"]
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]["diff"])
        self.assertIn("复核", changes[0]["summary"])
        self.assertEqual(len([n for n in repeated["notifications"] if n["kind"] == "source_changed"]), 1)
        self.assertEqual(self.service.list_follows()[0]["status"], "preparing")

    def test_source_error_does_not_replace_baseline_or_leak_exception(self):
        self.service.follow(POLICY["id"])
        with patch("policy_service.fetch_document", side_effect=RuntimeError("sk-private-key")):
            result = self.service.check_updates()
        self.assertEqual(result["results"][0]["status"], "error")
        self.assertNotIn("sk-private-key", str(result))

    def test_unfollow_removes_visible_notifications_and_avoids_fetch(self):
        self.service.follow(POLICY["id"])
        self.assertTrue(self.service.unfollow(POLICY["id"]))
        with patch("policy_service.fetch_document") as fetch:
            result = self.service.check_updates()
        fetch.assert_not_called()
        self.assertEqual(result["notifications"], [])

    def test_initial_invalid_source_requires_review_and_preserves_expired_match(self):
        self.service.follow(POLICY["id"])
        document = {"body": "官方原文正文", "fingerprint": "invalid-one", "validity": "已废止"}
        with patch("policy_service.fetch_document", return_value=document):
            result = self.service.check_updates()
        self.assertTrue(result["results"][0]["needs_review"])
        policies = [{**POLICY, "match": {"status": status, "reasons": [], "questions": []}} for status in ("expired", "unlikely", "possible")]
        self.assertEqual([value["match"]["status"] for value in self.service.enrich_policies(policies)], ["expired", "unlikely", "needs_info"])

    def test_initial_snapshot_different_from_reviewed_source_requires_confirmation(self):
        self.service.follow(POLICY["id"])
        reviewed = {**POLICY, "reviewed_source_fingerprint": "original-reviewed-fingerprint"}
        current = {"body": "更新后的官方正文", "fingerprint": "new-source-fingerprint", "validity": "有效"}
        with patch("policy_service.get_policy", return_value=reviewed), patch("policy_service.fetch_document", return_value=current):
            result = self.service.check_updates()
        self.assertEqual(result["results"][0]["status"], "baseline")
        self.assertTrue(result["results"][0]["needs_review"])
        notice = next(value for value in result["notifications"] if value["kind"] == "review")
        self.assertIn("资料整理时不同", notice["summary"])
        self.assertEqual(notice["diff"], [])
        possible = {**reviewed, "match": {"status": "possible", "reasons": [], "questions": []}}
        self.assertEqual(self.service.enrich_policies([possible])[0]["match"]["status"], "needs_info")

    def test_initial_snapshot_matching_reviewed_source_is_only_a_baseline(self):
        self.service.follow(POLICY["id"])
        reviewed = {**POLICY, "reviewed_source_fingerprint": "same-reviewed-fingerprint"}
        current = {"body": "官方正文", "fingerprint": "same-reviewed-fingerprint", "validity": "有效"}
        with patch("policy_service.get_policy", return_value=reviewed), patch("policy_service.fetch_document", return_value=current):
            result = self.service.check_updates()
        self.assertEqual(result["results"][0]["status"], "baseline")
        self.assertFalse(result["results"][0]["needs_review"])
        self.assertFalse(any(value["kind"] == "review" for value in result["notifications"]))
        review = self.service.enrich_policies([reviewed])[0]["source_review"]
        self.assertEqual(review["status"], "baseline")
        self.assertFalse(review["rules_verified"])

    def test_validity_only_changes_produce_explicit_metadata_diff(self):
        self.service.follow(POLICY["id"])
        document = {"body": "官方原文正文", "fingerprint": "valid-one", "validity": "有效"}
        with patch("policy_service.fetch_document", return_value=document):
            self.service.check_updates()
        with patch("policy_service.fetch_document", return_value={**document, "fingerprint": "invalid-one", "validity": "失效"}):
            result = self.service.check_updates()
        notice = next(value for value in result["notifications"] if value["kind"] == "source_changed")
        self.assertEqual(notice["diff"], [{"before": "官方标注有效性：有效", "after": "官方标注有效性：失效"}])

    def test_published_review_matching_valid_source_clears_pending_review(self):
        self.service.follow(POLICY["id"])
        reviewed = {**POLICY, "reviewed_source_fingerprint": "original"}
        original = {"body": "原官方正文", "fingerprint": "original", "validity": "有效"}
        current = {"body": "更新后的官方正文", "fingerprint": "current", "validity": "有效"}
        with patch("policy_service.fetch_document", side_effect=[original, current, current]):
            self.service._check_document(reviewed)
            changed = self.service._check_document(reviewed)
            self.assertTrue(changed["needs_review"])
            published = {**reviewed, "reviewed_source_fingerprint": "current"}
            confirmed = self.service._check_document(published)
        self.assertFalse(confirmed["needs_review"])
        possible = {**published, "match": {"status": "possible", "reasons": [], "questions": []}}
        result = self.service.enrich_policies([possible])[0]
        self.assertEqual(result["match"]["status"], "possible")
        self.assertFalse(result["source_review"]["rules_verified"])
        notices = [item for item in self.service.notifications() if item["kind"] == "source_changed"]
        self.assertEqual(len(notices), 1)
        self.assertIn("并不自动证明", notices[0]["summary"])

    def test_pending_review_remains_without_matching_review_and_explicit_validity(self):
        cases = [(None, "有效"), ("original", "有效"), ("current", "未标明"),
                 ("current", "失效"), ("current", "部分失效"), ("current", None)]
        for number, (fingerprint, validity) in enumerate(cases):
            with self.subTest(fingerprint=fingerprint, validity=validity):
                service = PolicyService(self.path.with_name(f"review-{number}.sqlite3"), clock=lambda: self.now)
                original = {"body": "原官方正文", "fingerprint": "original", "validity": "有效"}
                current = {"body": "更新后的官方正文", "fingerprint": "current",
                           **({"validity": validity} if validity is not None else {})}
                reviewed = {**POLICY, "reviewed_source_fingerprint": "original"}
                with patch("policy_service.fetch_document", side_effect=[original, current, current]):
                    service._check_document(reviewed)
                    service._check_document(reviewed)
                    result = service._check_document({**POLICY, "reviewed_source_fingerprint": fingerprint})
                self.assertTrue(result["needs_review"])

    def test_local_match_refreshes_review_status_without_search_or_profile_writes(self):
        service = PolicyService(self.path, clock=lambda: self.now, persist_profile=True)
        profile = {"stage": "student", "graduation_year": 2027, "tianjin_college": True,
                   "support_condition": True, "seeking_work": True, "grant_not_received": True}
        service._setting("profile", profile)
        reviewed = {**POLICY, "reviewed_source_fingerprint": "original"}
        original = {"body": "原官方正文", "fingerprint": "original", "validity": "有效"}
        current = {"body": "更新后的官方正文", "fingerprint": "current", "validity": "有效"}
        with patch("policy_service.fetch_document", side_effect=[original, current, current]), \
                patch.object(service, "_search") as search, patch("policy_service.search_official") as provider:
            service._check_document(reviewed)
            service._check_document(reviewed)
            pending = next(item for item in service.match_profile(profile) if item["id"] == POLICY["id"])
            self.assertEqual(pending["match"]["status"], "needs_info")
            service._check_document({**reviewed, "reviewed_source_fingerprint": "current"})
            refreshed = next(item for item in service.match_profile(profile) if item["id"] == POLICY["id"])
            self.assertEqual(refreshed["match"]["status"], "possible")
            incomplete = next(item for item in service.match_profile({**profile, "support_condition": None})
                              if item["id"] == POLICY["id"])
            self.assertEqual(incomplete["match"]["status"], "needs_info")
            self.assertIn("support_condition", {question["key"] for question in incomplete["match"]["questions"]})
        search.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(service._get_setting("profile"), profile)
        self.assertIsNone(service._get_setting("search_runs"))

    def test_search_cache_does_not_send_private_answers_or_repeat_provider_calls(self):
        response = {"status": "ok", "request_id": "request-1", "results": [], "query": "public query"}
        with patch("policy_service.match_policies", return_value=[]), patch("policy_service.search_official", return_value=response) as search:
            first = self.service.discover({"support_condition": True})
            second = self.service.discover({"support_condition": False})
        self.assertFalse(first["search"]["cached"])
        self.assertTrue(second["search"]["cached"])
        self.assertEqual(second["search"]["request_id"], "request-1")
        search.assert_called_once()
        self.assertNotIn("support_condition", str(search.call_args))

    def test_search_candidates_are_source_excerpts_for_review_not_declared_new_policy(self):
        self.service.follow(POLICY["id"])
        document = {"body": "官方原文正文", "fingerprint": "one", "source_url": POLICY["source_url"]}
        first = {"status": "ok", "request_id": "first", "results": [{"url": POLICY["source_url"], "title": "原通知"}]}
        second = {"status": "ok", "request_id": "second", "results": [*first["results"], {"url": "https://hrss.tj.gov.cn/new.html", "title": "相关补充通知"}]}
        digest = {"reviewed": False, "summary": "原文摘录", "requirements": ["申请要求"]}
        with patch("policy_service.source_status", return_value={"configured": True}), patch("policy_service.fetch_document", return_value=document), patch("policy_service.decompose_document", return_value=digest), patch("policy_service.search_official", side_effect=[first, second]):
            baseline = self.service.check_updates()
            self.service.search_cache.clear()
            updated = self.service.check_updates()
        self.assertFalse(any(n["kind"] == "review" for n in baseline["notifications"]))
        notice = next(n for n in updated["notifications"] if n["kind"] == "review")
        self.assertEqual(notice["digest"], digest)
        self.assertFalse(notice["digest"]["reviewed"])
        self.assertIn("待核对", notice["title"])
        self.assertEqual(updated["search_status"]["runs"][0]["request_id"], "second")

    def test_search_candidates_are_processed_in_batches_without_marking_unprocessed_seen(self):
        self.service.follow(POLICY["id"])
        pages = [{"url": f"https://hrss.tj.gov.cn/new-{number}.html", "title": f"相关通知{number}"}
                 for number in range(5)]
        baseline = {"status": "ok", "results": [{"url": POLICY["source_url"], "title": "原通知"}]}
        current = {"status": "ok", "results": [*baseline["results"], *pages]}
        digest = {"reviewed": False, "summary": "官方原文摘录"}
        with patch.object(self.service, "_search", side_effect=[baseline, current, current, current, current]), \
                patch("policy_service.fetch_document", return_value={"body": "官方原文正文"}) as fetch, \
                patch("policy_service.decompose_document", return_value=digest):
            for count in (0, 2, 4, 5, 5):
                self.service._check_search(POLICY)
                notices = [item for item in self.service.notifications() if item["kind"] == "review"]
                self.assertEqual(len(notices), count)
                self.assertEqual(len(self.service._get_setting("seen_urls:" + POLICY["id"])), count + 1)
                self.assertEqual(fetch.call_count, count)
        self.assertEqual({call.args[0] for call in fetch.call_args_list}, {page["url"] for page in pages})

    def test_search_candidate_fetch_failure_can_retry_and_publish_digest_once(self):
        self.service.follow(POLICY["id"])
        self.service._setting("seen_urls:" + POLICY["id"], [])
        page = {"url": "https://hrss.tj.gov.cn/retry.html", "title": "相关补充通知"}
        digest = {"reviewed": False, "summary": "重试后取得官方摘录", "requirements": ["申请要求"]}
        with patch.object(self.service, "_search", return_value={"status": "ok", "results": [page]}), \
                patch("policy_service.fetch_document", side_effect=[RuntimeError("temporary"), {"body": "官方原文正文"}]) as fetch, \
                patch("policy_service.decompose_document", return_value=digest):
            self.service._check_search(POLICY)
            self.assertEqual(self.service._get_setting("seen_urls:" + POLICY["id"]), [])
            self.service._check_search(POLICY)
            self.service._check_search(POLICY)
        self.assertEqual(fetch.call_count, 2)
        notices = [item for item in self.service.notifications() if item["kind"] == "review"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["digest"], digest)
        self.assertEqual(self.service._get_setting("seen_urls:" + POLICY["id"]), [page["url"]])

    def test_failed_candidates_rotate_behind_pending_links_and_survive_restart(self):
        self.service.follow(POLICY["id"])
        self.service._setting("seen_urls:" + POLICY["id"], [])
        pages = [{"url": f"https://hrss.tj.gov.cn/new-{number}.html", "title": f"相关通知{number}"}
                 for number in range(5)]
        unavailable = {pages[0]["url"], pages[1]["url"]}

        def fetch_candidate(url):
            if url in unavailable:
                raise RuntimeError("temporary")
            return {"body": "官方原文正文"}

        responses = [{"status": "ok", "results": pages}, *[{"status": "ok", "results": []}] * 3]
        with patch.object(PolicyService, "_search", side_effect=responses), \
                patch("policy_service.fetch_document", side_effect=fetch_candidate) as fetch, \
                patch("policy_service.decompose_document", return_value={"reviewed": False}):
            self.service._check_search(POLICY)
            self.assertEqual(fetch.call_count, 2)
            restarted = PolicyService(self.path, clock=lambda: self.now)
            for attempts, count in ((4, 2), (6, 3), (8, 3)):
                restarted._check_search(POLICY)
                self.assertEqual(fetch.call_count, attempts)
                notices = [item for item in restarted.notifications() if item["kind"] == "review"]
                self.assertEqual(len(notices), count)
        self.assertEqual(set(restarted._get_setting("seen_urls:" + POLICY["id"])),
                         {page["url"] for page in pages[2:]})

    def test_invalid_updates_missing_follow_and_expired_reminders(self):
        for change in ({}, {"arbitrary": True}, {"checked_materials": "identity"}):
            with self.subTest(change=change), self.assertRaises(ServiceValidationError):
                self.service.update_follow(POLICY["id"], change)
        with self.assertRaises(ServiceValidationError):
            self.service.mark_notification("missing", True)
        with self.assertRaises(ServiceValidationError):
            self.service.mark_notification("missing", False)
        self.service.follow(POLICY["id"])
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.assertEqual(self.service.notifications(), [])

    def test_monitor_can_start_only_once_and_close_cleanly(self):
        import threading
        called = threading.Event()
        with patch.object(self.service, "check_updates", side_effect=lambda: called.set()):
            self.service.start_monitor()
            original = self.service.monitor
            self.service.start_monitor()
            self.assertIs(original, self.service.monitor)
            self.assertTrue(called.wait(timeout=1))
            self.service.close()
        self.assertFalse(self.service.monitor.is_alive())

    def test_concurrent_update_check_is_rejected(self):
        self.service.check_lock.acquire()
        try:
            with self.assertRaises(ServiceValidationError) as error:
                self.service.check_updates()
            self.assertEqual(error.exception.code, "check_in_progress")
        finally:
            self.service.check_lock.release()

    def test_parallel_search_records_preserve_each_request_id(self):
        import time
        from concurrent.futures import ThreadPoolExecutor
        read_setting = self.service._get_setting

        def slower_read(key, fallback=None):
            result = read_setting(key, fallback)
            time.sleep(0.01)
            return result

        records = [{"status": "ok", "request_id": f"req-{number}", "query": "公开政策检索"} for number in range(6)]
        with patch.object(self.service, "_get_setting", side_effect=slower_read):
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(self.service._record_search, records))
        self.assertEqual({value["request_id"] for value in read_setting("search_runs", [])}, {value["request_id"] for value in records})


if __name__ == "__main__":
    unittest.main()
