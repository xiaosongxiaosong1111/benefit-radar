from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy_catalog import get_catalog, get_policy, match_policies
from policy_core import PolicyValidationError, is_official_tianjin_source


def match(policy_id: str, profile: dict, today: date = date(2026, 10, 24)) -> dict:
    return next(item for item in match_policies(profile, today=today) if item["id"] == policy_id)["match"]


class PolicyCatalogTests(unittest.TestCase):
    def test_catalog_has_real_official_evidence_and_independent_copies(self):
        items = get_catalog()
        self.assertEqual({item["id"] for item in items}, {
            "job-seeking-grant", "flexible-employment", "residence-permit",
        })
        for item in items:
            self.assertTrue(is_official_tianjin_source(item["source_url"]))
            self.assertTrue(item["materials"])
            self.assertTrue(item["conditions"])
            self.assertTrue(item["body"])
        items[0]["materials"].clear()
        self.assertTrue(get_catalog()[0]["materials"])
        with self.assertRaises(PolicyValidationError):
            get_policy("not-a-policy")

    def test_grant_requires_every_condition_and_expires_after_deadline(self):
        profile = {"graduation_year": 2027, "tianjin_college": True,
                   "support_condition": True, "seeking_work": True,
                   "grant_not_received": True}
        self.assertEqual(match("job-seeking-grant", profile)["status"], "possible")
        self.assertEqual(match("job-seeking-grant", {**profile, "support_condition": None})["status"], "needs_info")
        self.assertEqual(match("job-seeking-grant", {**profile, "support_condition": False})["status"], "unlikely")
        self.assertEqual(match("job-seeking-grant", profile, date(2026, 10, 31))["status"], "possible")
        self.assertEqual(match("job-seeking-grant", profile, date(2026, 11, 1))["status"], "expired")
        self.assertEqual(match("job-seeking-grant", {**profile, "graduation_year": 2026})["status"], "unlikely")

    def test_flexible_employment_accepts_tianjin_origin_or_tianjin_college(self):
        profile = {"stage": "flexible", "tianjin_college": False,
                   "tianjin_origin": True, "within_two_years": True,
                   "first_employment": True, "registered_flexible": True,
                   "paid_social": True, "ordinary_degree": True}
        self.assertEqual(match("flexible-employment", profile)["status"], "possible")
        self.assertEqual(match("flexible-employment", {**profile, "tianjin_origin": None})["status"], "needs_info")
        self.assertEqual(match("flexible-employment", {**profile, "tianjin_origin": False})["status"], "unlikely")
        self.assertEqual(match("flexible-employment", profile, date(2027, 12, 31))["status"], "possible")
        self.assertEqual(match("flexible-employment", profile, date(2028, 1, 1))["status"], "expired")

    def test_monthly_application_window_does_not_expire_policy(self):
        profile = {"stage": "flexible", "tianjin_college": True,
                   "within_two_years": True, "first_employment": True,
                   "registered_flexible": True, "paid_social": True,
                   "ordinary_degree": True}
        self.assertEqual(match("flexible-employment", profile, date(2026, 10, 24))["status"], "possible")
        self.assertEqual(get_policy("flexible-employment")["recurring_deadline_day"], 20)

    def test_residence_permit_handles_beijing_hebei_exception(self):
        profile = {"nonlocal_registration": True, "stable_basis": True,
                   "residence_half_year": False, "beijing_hebei_permit": True}
        self.assertEqual(match("residence-permit", profile)["status"], "possible")
        self.assertEqual(match("residence-permit", {**profile, "beijing_hebei_permit": False})["status"], "unlikely")
        self.assertEqual(match("residence-permit", {**profile, "residence_half_year": True})["status"], "possible")
        self.assertEqual(match("residence-permit", {**profile, "nonlocal_registration": False})["status"], "unlikely")
        self.assertEqual(match("residence-permit", profile, date(2027, 1, 1))["status"], "expired")

    def test_unknown_and_false_are_distinct_and_inputs_are_not_mutated(self):
        original = {"graduation_year": 2027, "tianjin_college": True}
        before = dict(original)
        result = match("job-seeking-grant", original)
        self.assertEqual(original, before)
        self.assertEqual(result["status"], "needs_info")
        self.assertIn("support_condition", [q["key"] for q in result["questions"]])
        self.assertNotIn("已获批", " ".join(result["reasons"]))

    def test_unrelated_graduates_are_not_asked_sensitive_support_question(self):
        result = match("job-seeking-grant", {"graduation_year": 2020})
        self.assertEqual(result["status"], "unlikely")
        self.assertNotIn("support_condition", [q["key"] for q in result["questions"]])

    def test_profile_boundary_rejects_unrequested_or_wrong_typed_data(self):
        for profile in ({"phone": "13800000000"}, {"stage": "admin"},
                        {"stage": []}, {"stage": None},
                        {"paid_social": 1}, {"graduation_year": True},
                        {"graduation_year": "2027"}, [], None):
            with self.subTest(profile=profile):
                with self.assertRaises(PolicyValidationError):
                    match_policies(profile)


if __name__ == "__main__":
    unittest.main()
