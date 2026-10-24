from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Callable
import unicodedata
import uuid

from cryptography.fernet import Fernet, InvalidToken


PASSWORD_ITERATIONS = 600_000
MAX_ACCOUNTS = 100
SESSION_SECONDS = 7 * 24 * 3600
USERNAME = re.compile(r"[a-zA-Z0-9_-]{3,32}\Z")
USER_ID = re.compile(r"[a-f0-9]{32}\Z")
SESSION_TOKEN = re.compile(r"[a-zA-Z0-9_-]{43}\Z")
KEY_CHECK = b"benefit-radar account encryption v1"
BOOTSTRAP_LOCK = threading.RLock()


class AccountError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _password_hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def _public_user(row) -> dict:
    return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])}


class AccountStore:
    def __init__(self, root: Path | str, *, clock: Callable[[], float] | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db_path = self.root / "accounts.sqlite3"
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self._attempts: dict[str, list[float]] = {}
        with BOOTSTRAP_LOCK:
            existing = self.db_path.exists()
            master = self._load_master(existing)
            self._fernet = Fernet(master)
            self._csrf_key = hmac.new(master, b"benefit-radar csrf", hashlib.sha256).digest()
            self._initialize(existing)

    def _load_master(self, existing: bool) -> bytes:
        target = self.root / "master.key"
        if not target.exists():
            if existing:
                raise AccountError("key_unavailable", "账户加密主密钥缺失，请恢复原密钥后启动。", 503)
            try:
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(Fernet.generate_key())
                    output.flush()
                    os.fsync(output.fileno())
        os.chmod(target, 0o600)
        try:
            key = target.read_bytes()
            Fernet(key)
            return key
        except (OSError, ValueError, TypeError):
            raise AccountError("key_unavailable", "账户加密主密钥无效，请恢复原密钥后启动。", 503) from None

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        os.chmod(self.db_path, 0o600)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self, existing: bool) -> None:
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash BLOB NOT NULL, password_salt BLOB NOT NULL,
                    password_iterations INTEGER NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, api_key TEXT, api_tail TEXT,
                    key_status TEXT NOT NULL DEFAULT 'not_configured', key_verified_at TEXT,
                    key_last_error TEXT, key_revision INTEGER NOT NULL DEFAULT 0, key_request_id TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS account_meta (key TEXT PRIMARY KEY, value BLOB NOT NULL);
            """)
            row = db.execute("SELECT value FROM account_meta WHERE key = 'key_check'").fetchone()
            if row is None and not existing:
                db.execute("INSERT INTO account_meta VALUES ('key_check', ?)", (self._fernet.encrypt(KEY_CHECK),))
                return
            try:
                if row is None or not hmac.compare_digest(self._fernet.decrypt(row[0]), KEY_CHECK):
                    raise InvalidToken()
            except (InvalidToken, ValueError, TypeError):
                raise AccountError("key_unavailable", "主密钥与账户数据不匹配，请恢复原密钥后启动。", 503) from None

    def _now(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")

    def _user_row(self, db, user_id: str):
        if not isinstance(user_id, str) or not USER_ID.fullmatch(user_id):
            raise AccountError("not_found", "未找到账户。", 404)
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise AccountError("not_found", "未找到账户。", 404)
        return row

    @staticmethod
    def _validate_credentials(username, password) -> str:
        if not isinstance(username, str) or not USERNAME.fullmatch(username):
            raise AccountError("invalid_username", "用户名需为3至32位字母、数字、下划线或短横线。")
        if not isinstance(password, str) or not 6 <= len(password) <= 128 or any(unicodedata.category(character).startswith("C") for character in password):
            raise AccountError("invalid_password", "密码需为6至128位，且不能包含控制字符。")
        return username.lower()

    def _create_user(self, username, password, *, admin=False) -> dict:
        normalized = self._validate_credentials(username, password)
        with self.lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM users WHERE username = ?", (normalized,)).fetchone()
            if row is not None:
                if admin and row["is_admin"]:
                    return _public_user(row)
                raise AccountError("username_unavailable", "无法创建此用户名，请尝试其他用户名。", 409)
            if admin and db.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone():
                raise AccountError("admin_exists", "管理员已存在，请使用原管理员账户。", 409)
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= MAX_ACCOUNTS:
                raise AccountError("account_limit", "账户数量已达到当前服务上限。", 429)
            user_id, salt = uuid.uuid4().hex, secrets.token_bytes(16)
            db.execute("INSERT INTO users (id,username,password_hash,password_salt,password_iterations,is_admin,created_at) VALUES (?,?,?,?,?,?,?)",
                       (user_id, normalized, _password_hash(password, salt, PASSWORD_ITERATIONS), salt, PASSWORD_ITERATIONS, int(admin), self._now()))
            return {"id": user_id, "username": normalized, "is_admin": admin}

    def register(self, username, password) -> dict:
        return self._create_user(username, password)

    def ensure_admin(self, username, password) -> dict:
        return self._create_user(username, password, admin=True)

    def has_admin(self) -> bool:
        with self.lock, self._connection() as db:
            return db.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone() is not None

    def list_user_ids(self) -> list[str]:
        with self.lock, self._connection() as db:
            return [row[0] for row in db.execute("SELECT id FROM users ORDER BY created_at, id")]

    def login(self, username, password) -> dict:
        try:
            normalized = self._validate_credentials(username, password)
        except AccountError:
            raise AccountError("invalid_credentials", "用户名或密码错误。", 401) from None
        with self.lock:
            now = self.clock()
            recent = {name: [stamp for stamp in stamps if now - stamp < 60] for name, stamps in self._attempts.items()}
            self._attempts = {name: stamps for name, stamps in recent.items() if stamps}
            attempts = self._attempts.get(normalized, [])
            if len(attempts) >= 8:
                raise AccountError("rate_limited", "尝试次数过多，请稍后再试。", 429)
            self._attempts = {**dict(list(self._attempts.items())[-511:]), normalized: [*attempts, now]}
            with self._connection() as db:
                row = db.execute("SELECT * FROM users WHERE username = ?", (normalized,)).fetchone()
        calculated = _password_hash(password, row["password_salt"] if row else bytes(16), row["password_iterations"] if row else PASSWORD_ITERATIONS)
        if row is None or not hmac.compare_digest(calculated, row["password_hash"]):
            raise AccountError("invalid_credentials", "用户名或密码错误。", 401)
        with self.lock:
            self._attempts = {name: stamps for name, stamps in self._attempts.items() if name != normalized}
        return _public_user(row)

    def _csrf(self, token: str) -> str:
        return hmac.new(self._csrf_key, token.encode("ascii"), hashlib.sha256).hexdigest()

    def create_session(self, user_id: str) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        with self.lock, self._connection() as db:
            self._user_row(db, user_id)
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (self.clock(),))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode("ascii")).hexdigest(), user_id, self.clock() + SESSION_SECONDS))
        return token, self._csrf(token)

    def get_session(self, token) -> dict | None:
        if not isinstance(token, str) or not SESSION_TOKEN.fullmatch(token):
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.lock, self._connection() as db:
            row = db.execute("SELECT users.*, sessions.expires_at FROM sessions JOIN users ON users.id=sessions.user_id WHERE token_hash = ?", (digest,)).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= self.clock():
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                return None
        return {"user": _public_user(row), "csrf_token": self._csrf(token)}

    def delete_session(self, token) -> None:
        if isinstance(token, str) and SESSION_TOKEN.fullmatch(token):
            with self.lock, self._connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode("ascii")).hexdigest(),))

    @staticmethod
    def _metadata(row) -> dict:
        configured = row["api_key"] is not None
        return {"configured": configured, "status": row["key_status"] if configured else "not_configured",
                "masked_key": "••••" + row["api_tail"] if configured else None,
                "verified_at": row["key_verified_at"] if configured else None,
                "last_error": row["key_last_error"] if configured else None, "revision": row["key_revision"]}

    def api_metadata(self, user_id: str) -> dict:
        with self.lock, self._connection() as db:
            return self._metadata(self._user_row(db, user_id))

    def set_api_key(self, user_id: str, key) -> dict:
        if not isinstance(key, str) or not 8 <= len(key) <= 512 or any(not 33 <= ord(character) <= 126 for character in key):
            raise AccountError("invalid_api_key", "请输入8至512位且不含空白字符的服务API Key。")
        encrypted = self._fernet.encrypt(key.encode("ascii")).decode("ascii")
        with self.lock, self._connection() as db:
            self._user_row(db, user_id)
            db.execute("UPDATE users SET api_key=?, api_tail=?, key_status='saved_unverified', key_verified_at=NULL, key_last_error=NULL, key_request_id=NULL, key_revision=key_revision+1 WHERE id=?", (encrypted, key[-4:], user_id))
        return self.api_metadata(user_id)

    def get_api_key(self, user_id: str) -> str:
        with self.lock, self._connection() as db:
            encrypted = self._user_row(db, user_id)["api_key"]
        if encrypted is None:
            return ""
        try:
            return self._fernet.decrypt(encrypted.encode("ascii")).decode("ascii")
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise AccountError("key_unavailable", "保存的密钥暂不可读取，请重新配置。", 503) from None

    def delete_api_key(self, user_id: str) -> dict:
        with self.lock, self._connection() as db:
            self._user_row(db, user_id)
            db.execute("UPDATE users SET api_key=NULL, api_tail=NULL, key_status='not_configured', key_verified_at=NULL, key_last_error=NULL, key_request_id=NULL, key_revision=key_revision+1 WHERE id=?", (user_id,))
        return self.api_metadata(user_id)

    def record_verification(self, user_id: str, *, revision: int, success: bool, request_id=None) -> dict:
        if type(revision) is not int or type(success) is not bool:
            raise AccountError("invalid_request", "验证记录格式无效。")
        safe_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", request_id) else None
        with self.lock, self._connection() as db:
            self._user_row(db, user_id)
            db.execute("UPDATE users SET key_status=?, key_verified_at=?, key_last_error=?, key_request_id=? WHERE id=? AND key_revision=? AND api_key IS NOT NULL",
                       ("verified" if success else "error", self._now() if success else None,
                        None if success else "元宝验证失败，请检查密钥、服务开通情况或账户权限。", safe_id if success else None, user_id, revision))
        return self.api_metadata(user_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Create the initial Benefit Radar administrator without exposing a password in command history.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--create-admin", metavar="USERNAME", required=True)
    args = parser.parse_args(argv)
    password = getpass.getpass("管理员初始密码（至少6位）：")
    if password != getpass.getpass("再次输入密码："):
        parser.error("两次密码不一致。")
    try:
        user = AccountStore(args.data_dir).ensure_admin(args.create_admin, password)
    except AccountError as error:
        parser.error(str(error))
    print("管理员账户已就绪：" + user["username"] + "（已有密码保持不变）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
