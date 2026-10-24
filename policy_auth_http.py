from __future__ import annotations

import hmac
import re
from http.cookies import CookieError, SimpleCookie


COOKIE_NAME = "benefit_session"
PUBLIC_AUTH_WRITES = frozenset({"/api/auth/register", "/api/auth/login"})


class AuthHTTPError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class AccountHTTP:
    def __init__(self, handler):
        self.handler = handler
        self.server = handler.server

    def session(self):
        values = self.handler.headers.get_all("Cookie", [])
        if len(values) != 1 or len(values[0]) > 4096:
            return None
        if sum(item.strip().startswith(COOKIE_NAME + "=") for item in values[0].split(";")) != 1:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(values[0])
        except CookieError:
            return None
        token = cookie.get(COOKIE_NAME)
        if token is None or re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token.value) is None:
            return None
        session = self.server.accounts.get_session(token.value)
        if session:
            self.handler.account_session = session
            return {**session, "token": token.value}
        return None

    def require_session(self, *, write=False):
        session = self.session()
        if not session:
            raise AuthHTTPError("authentication_required", "请先登录你的账户。", 401)
        if write:
            tokens = self.handler.headers.get_all("X-CSRF-Token", [])
            if len(tokens) != 1 or not tokens[0].isascii() or len(tokens[0]) > 256 or not hmac.compare_digest(tokens[0], session["csrf_token"]):
                raise AuthHTTPError("csrf_failed", "登录状态已变化，请刷新页面后重试。", 403)
        return session

    @staticmethod
    def _fields(payload, expected):
        if set(payload) != set(expected):
            raise AuthHTTPError("invalid_request", "请求包含不支持的字段。")

    def _cookie(self, token, *, clear=False):
        value = f"{COOKIE_NAME}={token}; Path=/; Max-Age={0 if clear else 604800}; HttpOnly; SameSite=Lax"
        if self.server.public_origin:
            value += "; Secure"
        if clear:
            value += "; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        return value

    def handle_get(self, path):
        if path == "/api/auth/session":
            enabled = self.server.accounts is not None
            session = self.session() if enabled else None
            self.handler._json(200, {"enabled": enabled, "user": session["user"] if session else None,
                                     "csrf_token": session["csrf_token"] if session else None,
                                     "registration_open": bool(enabled and self.server.accounts.has_admin())})
            return True
        if path == "/api/account/api-key" and self.server.accounts:
            session = self.require_session()
            self.handler._json(200, {"api_key": self.server.accounts.api_metadata(session["user"]["id"])})
            return True
        return False

    def handle_write(self, path, payload, session):
        if not self.server.accounts:
            return False
        if path in PUBLIC_AUTH_WRITES and self.handler.command == "POST":
            self._fields(payload, {"username", "password"})
            if path.endswith("/register"):
                if not self.server.accounts.has_admin():
                    raise AuthHTTPError("registration_closed", "管理员尚未完成初始化，请稍后再注册。", 503)
                user = self.server.accounts.register(payload["username"], payload["password"])
            else:
                user = self.server.accounts.login(payload["username"], payload["password"])
            token, csrf = self.server.accounts.create_session(user["id"])
            self.handler.account_session = {"user": user, "csrf_token": csrf}
            self.handler._json(200, {"user": user, "csrf_token": csrf}, headers={"Set-Cookie": self._cookie(token)})
            return True
        if path == "/api/auth/logout" and self.handler.command == "POST":
            self._fields(payload, {})
            self.server.accounts.delete_session(session["token"])
            self.handler._json(200, {"user": None, "csrf_token": None}, headers={"Set-Cookie": self._cookie("", clear=True)})
            return True
        if path == "/api/account/api-key":
            user_id = session["user"]["id"]
            if self.handler.command == "POST":
                self._fields(payload, {"api_key"})
                self.server.accounts.set_api_key(user_id, payload["api_key"])
            elif self.handler.command == "DELETE":
                self._fields(payload, {})
                self.server.accounts.delete_api_key(user_id)
            else:
                return False
            self.server.tenants.invalidate(user_id)
            self.handler._json(200, {"api_key": self.server.accounts.api_metadata(user_id)})
            return True
        if path == "/api/account/api-key/verify" and self.handler.command == "POST":
            self._fields(payload, {})
            self._verify(session["user"]["id"])
            return True
        return False

    def _verify(self, user_id):
        import policy_sources

        previous = self.server.accounts.api_metadata(user_id)
        key = self.server.accounts.get_api_key(user_id)
        if not key:
            raise AuthHTTPError("key_required", "请先保存你自己的元宝 API Key。")
        try:
            result = policy_sources.search_official("天津市 高校毕业生 求职补贴 官方政策", api_key=key)
        except Exception:
            result = {"status": "error"}
        success = result.get("status") == "ok"
        request_id = result.get("request_id") if success else None
        self.server.accounts.record_verification(user_id, revision=previous["revision"], success=success, request_id=request_id)
        metadata = self.server.accounts.api_metadata(user_id)
        changed = metadata["revision"] != previous["revision"]
        if success and not changed:
            self.server.tenants.invalidate(user_id)
        message = "配置已更新，请重新验证当前密钥。" if changed else "元宝搜索验证成功。" if success else "验证失败，请检查密钥、服务权限或稍后重试。"
        self.handler._json(200, {"api_key": metadata, "request_id": request_id if not changed else None, "message": message})
