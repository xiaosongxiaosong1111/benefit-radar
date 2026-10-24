from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy_sources
from policy_service import PolicyService, ServiceValidationError
from policy_tenants import TenantServices


USER_A = "a" * 32
USER_B = "b" * 32
KEY_A = "fixture-A"
KEY_B = "fixture-B"
POLICY = "residence-permit"


class AccountsFixture:
    def __init__(self, root):
        self.root = Path(root)
        self.records = {USER_A: {"key": KEY_A, "revision": 1}, USER_B: {"key": "", "revision": 0}}

    def list_user_ids(self):
        return list(self.records)

    def api_metadata(self, user_id):
        record = self.records[user_id]
        return {"configured": bool(record["key"]), "revision": record["revision"]}

    def get_api_key(self, user_id):
        return self.records[user_id]["key"]

    def set_key(self, user_id, value):
        self.records = {**self.records, user_id: {"key": value, "revision": self.records[user_id]["revision"] + 1}}


class TenantTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.accounts = AccountsFixture(self.temporary.name)
        self.pool = TenantServices(self.accounts)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.pool.close)

    def test_follows_materials_and_statuses_are_account_private(self):
        first = self.pool.service_for(USER_A)
        second = self.pool.service_for(USER_B)
        first.follow(POLICY)
        first.update_follow(POLICY, {"status": "submitted", "checked_materials": ["identity"]})
        self.assertEqual(second.list_follows(), [])
        second.follow(POLICY)
        self.assertEqual(second.list_follows()[0]["checked_materials"], [])
        self.assertEqual(second.list_follows()[0]["status"], "preparing")
        self.assertEqual(first.list_follows()[0]["status"], "submitted")
        self.assertNotEqual(first.db_path, second.db_path)

    def test_profile_persists_only_for_hosted_account_and_does_not_cross(self):
        with patch("policy_tenants.search_official", return_value={"status": "not_configured", "results": []}):
            self.pool.service_for(USER_A).discover({"stage": "student", "support_condition": True})
        self.assertEqual(self.pool.service_for(USER_A).bootstrap()["profile"]["stage"], "student")
        self.assertIsNone(self.pool.service_for(USER_B).bootstrap()["profile"])
        self.pool.close()
        restarted = TenantServices(self.accounts)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.service_for(USER_A).bootstrap()["profile"]["support_condition"], True)
        local = PolicyService(Path(self.temporary.name) / "local.sqlite3")
        with patch("policy_service.search_official", return_value={"status": "not_configured", "results": []}):
            local.discover({"support_condition": True})
        self.assertNotIn("profile", local.bootstrap())
        self.assertIsNone(local._get_setting("profile"))

    def test_source_snapshots_notifications_and_read_flags_do_not_cross(self):
        first = self.pool.service_for(USER_A)
        second = self.pool.service_for(USER_B)
        first.follow(POLICY)
        second.follow(POLICY)
        notice = {"id": "private-notice", "policy_id": POLICY, "title": "测试提醒", "summary": "账户专属", "kind": "review"}
        first._add_notification(notice)
        self.assertEqual(second.notifications(), [])
        second._add_notification(notice)
        first.mark_notification(notice["id"], True)
        self.assertFalse(second.notifications()[0]["read"])
        with patch("policy_service.fetch_document", return_value={"body": "测试原文", "fingerprint": "fixture", "validity": "有效"}):
            first._check_document({"id": POLICY, "title": "测试政策", "source_url": "https://www.tj.gov.cn/policy"})
        policy = {"id": POLICY}
        self.assertEqual(first.enrich_policies([policy])[0]["source_review"]["status"], "baseline")
        self.assertEqual(second.enrich_policies([policy])[0]["source_review"]["status"], "not_checked")

    def test_provider_never_borrows_global_admin_key_and_caches_are_private(self):
        calls = []

        def search(query, freshness=None, *, api_key=None):
            calls.append(api_key)
            return {"status": "ok" if api_key else "not_configured", "query": query, "request_id": "fixture-" + str(len(calls)), "results": []}

        with patch.dict(os.environ, {policy_sources.API_KEY_ENV: "globaladm1"}), patch("policy_tenants.search_official", side_effect=search):
            first = self.pool.service_for(USER_A).discover({})
            second = self.pool.service_for(USER_B).discover({})
            again = self.pool.service_for(USER_A).discover({})
        self.assertEqual(calls, [KEY_A, ""])
        self.assertFalse(first["search"]["cached"])
        self.assertFalse(second["search"]["cached"])
        self.assertTrue(again["search"]["cached"])
        self.assertFalse(self.pool.service_for(USER_B).bootstrap()["service"]["search_configured"])
        self.assertNotIn(KEY_A, json.dumps(first))
        self.assertNotIn("globaladm1", json.dumps(second))

    def test_credential_rotation_cannot_reuse_the_previous_keys_cache(self):
        with patch("policy_tenants.search_official", return_value={"status": "ok", "results": []}) as search:
            service = self.pool.service_for(USER_A)
            service.discover({})
            self.accounts.set_key(USER_A, KEY_B)
            second = service.discover({})
            self.accounts.set_key(USER_A, "")
            self.pool.invalidate(USER_A)
            third = service.discover({})
        self.assertEqual(search.call_count, 3)
        self.assertEqual([call.kwargs["api_key"] for call in search.call_args_list], [KEY_A, KEY_B, ""])
        self.assertFalse(second["search"]["cached"])
        self.assertFalse(third["search"]["cached"])

    def test_unknown_account_or_path_is_rejected_before_creating_storage(self):
        for value in ("../escape", "c" * 32, None, "A" * 32):
            with self.subTest(value=value), self.assertRaises(ServiceValidationError):
                self.pool.service_for(value)
        self.assertEqual(list((self.accounts.root / "users").iterdir()), [])
        self.pool.invalidate(USER_A)

    def test_monitor_restores_both_accounts_without_global_credentials(self):
        seen = []
        done = threading.Event()

        def checked(service):
            seen.append(service.db_path.parent.name)
            if len(seen) == 2:
                done.set()
            return {}

        with patch.object(PolicyService, "check_updates", checked):
            monitor = TenantServices(self.accounts, start_monitor=True)
            try:
                self.assertTrue(done.wait(2))
                self.assertTrue(monitor.monitor_running)
            finally:
                monitor.close()
        self.assertEqual(set(seen), {USER_A, USER_B})
        self.assertFalse(monitor.monitor_running)

    def test_monitor_error_keeps_other_accounts_running(self):
        done = threading.Event()

        def checked(service):
            if service.db_path.parent.name == USER_A:
                raise RuntimeError("private-provider-value")
            done.set()
            return {}

        with patch.object(PolicyService, "check_updates", checked):
            monitor = TenantServices(self.accounts, start_monitor=True)
            try:
                self.assertTrue(done.wait(2))
            finally:
                monitor.close()
        value = self.pool.service_for(USER_A)._get_setting("monitor_error_at")
        self.assertIsNotNone(value)
        self.assertNotIn("private-provider-value", value)

    def test_closed_pool_cannot_start_new_services(self):
        self.pool.close()
        self.pool.close()
        with self.assertRaises(RuntimeError):
            self.pool.service_for(USER_A)

    def test_one_inaccessible_account_directory_does_not_stop_other_monitors(self):
        done = threading.Event()
        original = TenantServices.service_for

        def load(pool, user_id):
            if user_id == USER_A:
                raise OSError("controlled-account-storage-error")
            return original(pool, user_id)

        with patch.object(TenantServices, "service_for", load), patch.object(PolicyService, "check_updates", side_effect=lambda: done.set()):
            monitor = TenantServices(self.accounts, start_monitor=True)
            try:
                self.assertTrue(done.wait(1), "An account storage error must not stop another account's monitoring")
            finally:
                monitor.close()

    def test_concurrent_partial_updates_preserve_materials_and_status(self):
        service = self.pool.service_for(USER_A)
        service.follow(POLICY)
        original = service.list_follows
        simultaneous_reads = threading.Barrier(2)

        def read():
            result = original()
            if threading.current_thread().name.startswith("tenant-race") and not service.lock._is_owned():
                simultaneous_reads.wait(2)
            return result

        with patch.object(service, "list_follows", side_effect=read):
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="tenant-race") as pool:
                first = pool.submit(service.update_follow, POLICY, {"status": "submitted"})
                second = pool.submit(service.update_follow, POLICY, {"checked_materials": ["identity"]})
                first.result(timeout=4)
                second.result(timeout=4)
        follow = service.list_follows()[0]
        self.assertEqual((follow["status"], follow["checked_materials"]), ("submitted", ["identity"]))


class ExplicitProviderTests(unittest.TestCase):
    def test_empty_explicit_key_disables_env_credential_without_network(self):
        with patch.dict(os.environ, {policy_sources.API_KEY_ENV: "globaladm1"}), patch.object(policy_sources, "_open_search") as request:
            self.assertFalse(policy_sources.source_status(api_key="")["configured"])
            result = policy_sources.search_official("天津政策", api_key="")
        request.assert_not_called()
        self.assertEqual(result["status"], "not_configured")

    def test_explicit_key_is_the_only_authorization_credential(self):
        captured = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b'{"Response":{"RequestId":"fixture","Pages":[]}}'

        def reply(request, timeout):
            captured.append(request.get_header("Authorization"))
            return Reply()

        with patch.dict(os.environ, {policy_sources.API_KEY_ENV: "globaladm1"}), patch.object(policy_sources, "_open_search", side_effect=reply):
            result = policy_sources.search_official("天津政策", api_key=KEY_B)
        self.assertEqual(captured, ["Bearer " + KEY_B])
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
