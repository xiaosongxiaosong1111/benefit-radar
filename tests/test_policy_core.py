from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from policy_core import (  # noqa: E402
    PolicyValidationError,
    build_checklist,
    build_in_app_reminders,
    decompose_policy,
    detect_policy_change,
    is_official_tianjin_source,
    policy_fingerprint,
)


def policy(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "title": "天津市居住证办理实施办法",
        "document_number": "津政规〔2026〕1号",
        "issuer": "天津市人民政府",
        "published_at": "2026-09-01",
        "effective_at": "2026-10-01",
        "applies_to": ["在天津市居住的非本市户籍人员"],
        "what_changed": ["线上申请入口调整"],
        "actions": ["准备有效身份证件", "确认居住登记已满规定期限"],
        "body": "申请人可通过天津网上办事大厅提交申请。",
        "source_url": "https://www.tj.gov.cn/zwgk/szfwj/tjz/202609/t20260901_1.html",
    }
    value.update(overrides)
    return value


class OfficialSourceTests(unittest.TestCase):
    def test_accepts_https_tianjin_government_hosts(self) -> None:
        accepted = (
            "https://www.tj.gov.cn/zwgk/zcwj/1.html",
            "https://ga.tj.gov.cn/xxgk/zcjd/1.html",
            "https://zwfw.tj.gov.cn/service/1",
        )

        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(is_official_tianjin_source(url))

    def test_rejects_non_https_credentials_and_deceptive_suffixes(self) -> None:
        rejected = (
            "http://www.tj.gov.cn/zwgk/zcwj/1.html",
            "https://tj.gov.cn@example.com/1",
            "https://tj.gov.cn.evil.example/1",
            "https://tj-gov.cn/1",
            "https://www.beijing.gov.cn/1",
            "javascript:alert(1)",
            "not a url",
        )

        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(is_official_tianjin_source(url))

    def test_rejects_control_characters_nonstandard_ports_and_malformed_urls(self):
        for url in (None, "https://ga.tj.gov.cn:444/", "https://ga.tj.gov.cn:bad/",
                    "https://ga.tj.gov.cn/\n", "https://ga.tj.gov.cn\\@evil.example/",
                    "https://[invalid", "https://ga.tj.gov.cn/" + "x" * 4096):
            self.assertFalse(is_official_tianjin_source(url))


class PolicyChangeTests(unittest.TestCase):
    def test_query_identity_and_english_word_boundaries_are_not_discarded(self):
        first = policy(source_url="https://publicservices.hrss.tj.gov.cn/policy?fileid=1", body="not eligible")
        second = {**first, "source_url": "https://publicservices.hrss.tj.gov.cn/policy?fileid=2"}
        self.assertNotEqual(policy_fingerprint(first), policy_fingerprint(second))
        self.assertNotEqual(policy_fingerprint(first), policy_fingerprint({**first, "body": "noteligible"}))

    def test_timestamp_only_refresh_is_not_a_policy_change(self):
        self.assertFalse(detect_policy_change(policy(source_checked_at="2026-10-21"),
                                             policy(source_checked_at="2026-10-24"))["changed"])

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(PolicyValidationError):
            policy_fingerprint([])
        with self.assertRaises(PolicyValidationError):
            policy_fingerprint(policy(body=object()))

    def test_fingerprint_ignores_formatting_and_tracking_parameters(self) -> None:
        original = policy()
        reformatted = policy(
            title="  天津市居住证办理实施办法  ",
            body="申请人可通过天津网上办事大厅\n提交申请。",
            source_url=(
                "https://www.tj.gov.cn/zwgk/szfwj/tjz/202609/"
                "t20260901_1.html?utm_source=notice#section"
            ),
        )

        self.assertEqual(policy_fingerprint(original), policy_fingerprint(reformatted))

    def test_fingerprint_changes_when_normative_content_changes(self) -> None:
        original = policy()
        revised = policy(
            effective_at="2026-11-01",
            body="申请人须先完成居住登记，再通过天津网上办事大厅提交申请。",
        )

        self.assertNotEqual(policy_fingerprint(original), policy_fingerprint(revised))

    def test_detects_changed_fields_without_treating_formatting_as_a_change(self) -> None:
        original = policy()
        revised = policy(
            effective_at="2026-11-01",
            body="申请人须先完成居住登记，再通过天津网上办事大厅提交申请。",
        )

        unchanged = detect_policy_change(original, policy(title=" 天津市居住证办理实施办法 "))
        changed = detect_policy_change(original, revised)

        self.assertFalse(unchanged["changed"])
        self.assertEqual(unchanged["changed_fields"], [])
        self.assertTrue(changed["changed"])
        self.assertEqual(set(changed["changed_fields"]), {"body", "effective_at"})
        self.assertNotEqual(changed["previous_fingerprint"], changed["current_fingerprint"])


class PolicyDecompositionTests(unittest.TestCase):
    def test_decomposes_into_actionable_fields_with_source_evidence(self) -> None:
        digest = decompose_policy(policy())

        self.assertEqual(
            set(digest),
            {
                "title",
                "document_number",
                "issuer",
                "published_at",
                "effective_at",
                "applies_to",
                "what_changed",
                "actions",
                "source",
            },
        )
        self.assertEqual(digest["what_changed"], ["线上申请入口调整"])
        self.assertEqual(digest["actions"][0], "准备有效身份证件")
        self.assertEqual(
            digest["source"],
            {
                "url": policy()["source_url"],
                "official": True,
            },
        )

    def test_rejects_unofficial_or_incomplete_policy_evidence(self) -> None:
        cases = (
            policy(source_url="https://example.com/copied-policy"),
            policy(title=""),
            policy(actions=[]),
        )

        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(PolicyValidationError):
                    decompose_policy(value)


class ChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requirements = [
            {"id": "identity", "label": "有效身份证件", "fact": "has_identity"},
            {
                "id": "registration",
                "label": "完成居住登记",
                "fact": "has_registration",
            },
        ]

    def test_reports_missing_before_unknown_and_never_claims_eligibility(self) -> None:
        checklist = build_checklist(
            self.requirements,
            {"has_identity": False},
        )

        self.assertEqual(checklist["status"], "not_ready")
        self.assertEqual(
            [item["state"] for item in checklist["items"]],
            ["missing", "confirm"],
        )
        self.assertEqual(checklist["counts"], {"ready": 0, "missing": 1, "confirm": 1})
        self.assertNotIn("eligible", checklist)

    def test_reports_confirmation_or_ready_from_three_state_answers(self) -> None:
        needs_confirmation = build_checklist(
            self.requirements,
            {"has_identity": True, "has_registration": None},
        )
        ready = build_checklist(
            self.requirements,
            {"has_identity": True, "has_registration": True},
        )

        self.assertEqual(needs_confirmation["status"], "needs_confirmation")
        self.assertEqual(ready["status"], "ready")

    def test_rejects_unknown_answers_instead_of_silently_ignoring_them(self) -> None:
        with self.assertRaises(PolicyValidationError):
            build_checklist(self.requirements, {"unasked_private_fact": True})

    def test_rejects_bad_requirement_and_answer_shapes(self):
        for requirements, facts in (([], []), ({}, {}), ([{"id": "x"}], {}),
                                    (self.requirements, {"has_identity": 1})):
            with self.assertRaises(PolicyValidationError):
                build_checklist(requirements, facts)


class InAppReminderTests(unittest.TestCase):
    def test_rejects_future_or_invalid_reminder_dates(self):
        change = {"matter": "居住证办理", "title": "更新", "published_at": "2026-10-22",
                  "source_url": "https://ga.tj.gov.cn/1", "fingerprint": "x"}
        cases = [None, {**change}, {**change, "published_at": "not-a-date"},
                 {**change, "published_at": "2026-10-20", "expires_at": "wrong"},
                 {**change, "published_at": "2026-10-20", "fingerprint": None}]
        self.assertEqual(build_in_app_reminders(cases, followed_matters={"居住证办理"},
                                               today=date(2026, 10, 21)), [])

    def test_creates_only_relevant_official_unexpired_reminders(self) -> None:
        changes = [
            {
                "matter": "居住证办理",
                "title": "居住证线上入口调整",
                "summary": "10月起改用新入口",
                "published_at": "2026-09-20",
                "expires_at": "2026-12-31",
                "source_url": "https://ga.tj.gov.cn/zcjd/1.html",
                "fingerprint": "change-1",
            },
            {
                "matter": "居住证办理",
                "title": "转载消息",
                "summary": "非官方转载",
                "published_at": "2026-09-20",
                "source_url": "https://example.com/copied",
                "fingerprint": "change-2",
            },
            {
                "matter": "社保转移",
                "title": "无关事项",
                "summary": "不应提醒",
                "published_at": "2026-09-20",
                "source_url": "https://hrss.tj.gov.cn/zcwj/1.html",
                "fingerprint": "change-3",
            },
            {
                "matter": "居住证办理",
                "title": "过期提醒",
                "summary": "已过期",
                "published_at": "2025-01-01",
                "expires_at": "2025-12-31",
                "source_url": "https://ga.tj.gov.cn/zcjd/old.html",
                "fingerprint": "change-4",
            },
        ]

        reminders = build_in_app_reminders(
            changes,
            followed_matters={"居住证办理"},
            today=date(2026, 10, 21),
        )

        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["id"], "change-1")
        self.assertEqual(reminders[0]["channel"], "in_app")
        self.assertFalse(reminders[0]["read"])
        self.assertEqual(reminders[0]["source_url"], changes[0]["source_url"])

    def test_deduplicates_same_policy_change(self) -> None:
        change = {
            "matter": "居住证办理",
            "title": "居住证线上入口调整",
            "summary": "10月起改用新入口",
            "published_at": "2026-09-20",
            "source_url": "https://ga.tj.gov.cn/zcjd/1.html",
            "fingerprint": "same-change",
        }

        reminders = build_in_app_reminders(
            [change, dict(change)],
            followed_matters={"居住证办理"},
            today=date(2026, 10, 21),
        )

        self.assertEqual([item["id"] for item in reminders], ["same-change"])


if __name__ == "__main__":
    unittest.main()
