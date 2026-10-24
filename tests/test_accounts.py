from __future__ import annotations

import concurrent.futures
from contextlib import closing
import json
import os
import secrets
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from policy_accounts import AccountError, AccountStore


PASSWORD = secrets.token_urlsafe(24)
FIRST_KEY = "example-provider-key-first"
SECOND_KEY = "example-provider-key-second"


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "accounts"
        self.now = 1_790_000_000.0
        self.store = AccountStore(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def user(self, name="alice"):
        return self.store.register(name, PASSWORD)

    def test_password_is_salted_hashed_and_public_user_has_no_secrets(self):
        first, second = self.user(), self.user("bob")
        self.assertEqual(set(first), {"id", "username", "is_admin"})
        self.assertFalse(first["is_admin"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.store.login("ALICE", PASSWORD), first)
        with closing(sqlite3.connect(self.root / "accounts.sqlite3")) as db:
            rows = db.execute("SELECT password_hash, password_salt, password_iterations FROM users").fetchall()
        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertNotEqual(rows[0][1], rows[1][1])
        self.assertGreaterEqual(rows[0][2], 600_000)
        self.assertNotIn(PASSWORD.encode(), (self.root / "accounts.sqlite3").read_bytes())

    def test_username_and_password_validation_and_case_insensitive_duplicates(self):
        for username in ("a", "has space", "../alice", "汉字账户", "a" * 33, None):
            with self.subTest(username=username), self.assertRaises(AccountError):
                self.store.register(username, PASSWORD)
        for password in ("short", "a" * 129, "long-password\n", "valid-password-\u0085", "valid-password-\ud800", None):
            with self.subTest(password=password), self.assertRaises(AccountError):
                self.store.register("alice", password)
        self.user()
        with self.assertRaises(AccountError) as error:
            self.user("ALICE")
        self.assertEqual(error.exception.status, 409)

    def test_six_digit_passwords_can_register_and_login_including_leading_zero(self):
        passwords = (
            ("numeric", str(100_000 + secrets.randbelow(900_000))),
            ("leadingzero", "0" + str(10_000 + secrets.randbelow(90_000))),
        )
        for username, password in passwords:
            with self.subTest(username=username):
                user = self.store.register(username, password)
                self.assertEqual(self.store.login(username, password), user)
                self.assertEqual(AccountStore(self.root).login(username, password), user)

    def test_five_digit_password_is_still_rejected_without_creating_account(self):
        password = str(10_000 + secrets.randbelow(90_000))
        with self.assertRaises(AccountError) as error:
            self.store.register("toosmall", password)
        self.assertEqual(error.exception.code, "invalid_password")
        self.assertEqual(self.store.list_user_ids(), [])

    def test_existing_long_password_can_still_login_after_restarting_store(self):
        user = self.user("legacy")
        restarted = AccountStore(self.root, clock=lambda: self.now)
        self.assertEqual(restarted.login("legacy", PASSWORD), user)

    def test_admin_bootstrap_is_explicit_idempotent_and_never_promotes_existing_user(self):
        self.assertFalse(self.store.has_admin())
        ordinary = self.user()
        with self.assertRaises(AccountError):
            self.store.ensure_admin("alice", PASSWORD)
        self.assertFalse(self.store.login("alice", PASSWORD)["is_admin"])
        admin = self.store.ensure_admin("admin", PASSWORD)
        self.assertTrue(admin["is_admin"])
        self.assertTrue(self.store.has_admin())
        self.assertEqual(self.store.ensure_admin("admin", "Another-valid-password"), admin)
        self.assertEqual(self.store.login("admin", PASSWORD), admin)
        with self.assertRaises(AccountError):
            self.store.ensure_admin("otheradmin", PASSWORD)
        self.assertEqual(len(self.store.list_user_ids()), 2)
        self.assertIn(ordinary["id"], self.store.list_user_ids())

    def test_invalid_login_errors_are_generic_and_attempts_are_bounded(self):
        self.user()
        errors = []
        for username in ("alice", "missing"):
            with self.assertRaises(AccountError) as error:
                self.store.login(username, "Wrong-valid-password")
            errors.append((error.exception.code, str(error.exception), error.exception.status))
        self.assertEqual(errors[0], errors[1])
        for _ in range(7):
            try:
                self.store.login("alice", "Wrong-valid-password")
            except AccountError:
                pass
        with self.assertRaises(AccountError) as error:
            self.store.login("alice", PASSWORD)
        self.assertEqual(error.exception.status, 429)
        self.now += 61
        self.assertEqual(self.store.login("alice", PASSWORD)["username"], "alice")

    def test_session_tokens_are_hashed_revocable_and_restart_safe(self):
        user = self.user()
        token, csrf = self.store.create_session(user["id"])
        self.assertGreaterEqual(len(token), 40)
        self.assertGreaterEqual(len(csrf), 40)
        self.assertNotEqual(token, csrf)
        self.assertNotIn(token.encode(), (self.root / "accounts.sqlite3").read_bytes())
        restarted = AccountStore(self.root, clock=lambda: self.now)
        self.assertEqual(restarted.get_session(token), {"user": user, "csrf_token": csrf})
        restarted.delete_session(token)
        self.assertIsNone(self.store.get_session(token))
        self.store.delete_session(token)

    def test_expired_or_invalid_sessions_are_rejected(self):
        token, _ = self.store.create_session(self.user()["id"])
        self.now += 7 * 24 * 3600 + 1
        self.assertIsNone(self.store.get_session(token))
        for invalid in (None, "", "wrong", "x" * 1000):
            self.assertIsNone(self.store.get_session(invalid))
            self.store.delete_session(invalid)
        with self.assertRaises(AccountError):
            self.store.create_session("0" * 32)

    def test_keys_are_encrypted_scoped_and_do_not_inherit_environment(self):
        alice, bob = self.user(), self.user("bob")
        with patch.dict(os.environ, {"TENCENTCLOUD_WSA_APIKEY": "environment-example-key"}):
            self.assertEqual(self.store.get_api_key(bob["id"]), "")
        initial = self.store.api_metadata(alice["id"])
        self.assertEqual(initial["status"], "not_configured")
        saved = self.store.set_api_key(alice["id"], FIRST_KEY)
        self.assertEqual(saved["status"], "saved_unverified")
        self.assertTrue(saved["configured"])
        self.assertIsNone(saved["verified_at"])
        self.assertGreater(saved["revision"], initial["revision"])
        self.assertEqual(self.store.get_api_key(alice["id"]), FIRST_KEY)
        self.assertEqual(self.store.get_api_key(bob["id"]), "")
        self.assertNotIn(FIRST_KEY, json.dumps(saved))
        self.assertNotIn(FIRST_KEY.encode(), (self.root / "accounts.sqlite3").read_bytes())
        self.assertEqual(AccountStore(self.root).get_api_key(alice["id"]), FIRST_KEY)

    def test_key_validation_and_unknown_user_fail_without_storage_changes(self):
        user = self.user()
        for invalid in ("", "short", "x" * 513, "example key with spaces", "bad\nexample-key", None):
            with self.subTest(key=invalid), self.assertRaises(AccountError):
                self.store.set_api_key(user["id"], invalid)
        self.assertFalse(self.store.api_metadata(user["id"])["configured"])
        for method in (self.store.api_metadata, self.store.get_api_key, self.store.delete_api_key):
            with self.assertRaises(AccountError):
                method("0" * 32)

    def test_verification_belongs_to_key_revision_and_delete_removes_activation(self):
        user = self.user()
        first = self.store.set_api_key(user["id"], FIRST_KEY)
        verified = self.store.record_verification(user["id"], revision=first["revision"], success=True, request_id="test-request-id")
        self.assertEqual(verified["status"], "verified")
        self.assertIsNotNone(verified["verified_at"])
        second = self.store.set_api_key(user["id"], SECOND_KEY)
        stale = self.store.record_verification(user["id"], revision=first["revision"], success=True)
        self.assertEqual(stale, second)
        failed = self.store.record_verification(user["id"], revision=second["revision"], success=False)
        self.assertEqual(failed["status"], "error")
        self.assertIsNone(failed["verified_at"])
        self.assertNotIn(SECOND_KEY, json.dumps(failed))
        deleted = self.store.delete_api_key(user["id"])
        self.assertEqual(deleted["status"], "not_configured")
        self.assertFalse(deleted["configured"])
        self.assertGreater(deleted["revision"], failed["revision"])
        self.assertEqual(self.store.get_api_key(user["id"]), "")
        self.assertEqual(self.store.record_verification(user["id"], revision=second["revision"], success=True), deleted)

    def test_missing_or_wrong_master_key_fails_closed(self):
        user = self.user()
        self.store.set_api_key(user["id"], FIRST_KEY)
        key_path = self.root / "master.key"
        original = key_path.read_bytes()
        key_path.unlink()
        with self.assertRaises(AccountError):
            AccountStore(self.root)
        self.assertFalse(key_path.exists())
        key_path.write_bytes(b"not-a-valid-fernet-key")
        with self.assertRaises(AccountError):
            AccountStore(self.root)
        from cryptography.fernet import Fernet
        key_path.write_bytes(Fernet.generate_key())
        with self.assertRaises(AccountError):
            AccountStore(self.root)
        key_path.write_bytes(original)
        self.assertEqual(AccountStore(self.root).get_api_key(user["id"]), FIRST_KEY)

    def test_tampered_ciphertext_is_never_returned_or_replaced_with_environment(self):
        user = self.user()
        self.store.set_api_key(user["id"], FIRST_KEY)
        with closing(sqlite3.connect(self.root / "accounts.sqlite3")) as db, db:
            db.execute("UPDATE users SET api_key = ? WHERE id = ?", ("invalid-ciphertext", user["id"]))
        with self.assertRaises(AccountError) as error:
            self.store.get_api_key(user["id"])
        self.assertNotIn("invalid-ciphertext", str(error.exception))

    def test_concurrent_duplicate_registration_is_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.store.register, "alice", PASSWORD) for _ in range(4)]
        successes = [future for future in futures if future.exception() is None]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(self.store.list_user_ids()), 1)

    def test_account_limit_includes_admin_and_is_atomic(self):
        self.store.ensure_admin("admin", PASSWORD)
        with patch("policy_accounts.MAX_ACCOUNTS", 3):
            self.user()
            self.user("bob")
            with self.assertRaises(AccountError) as error:
                self.user("carol")
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(len(self.store.list_user_ids()), 3)

    @unittest.skipUnless(os.name == "posix", "POSIX private modes are verified on the deployment host")
    def test_storage_permissions_are_private(self):
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "master.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "accounts.sqlite3").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
