from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from policy_catalog import get_policy
from policy_core import PolicyValidationError, build_checklist
from policy_service import PolicyService, ServiceValidationError
from policy_auth_http import AccountHTTP, AuthHTTPError, PUBLIC_AUTH_WRITES


MAX_REQUEST_BYTES = 16 * 1024
CLIENT_TIMEOUT_SECONDS = 2
STATIC_SUFFIXES = frozenset({".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico", ".woff2"})
SENSITIVE = re.compile(r"(?:id_number|phone|real_name|password|secret|token|api.?key|address|credential)", re.I)
PROFILE_BOOLS = frozenset({"tianjin_college", "support_condition", "within_two_years", "first_employment", "registered_flexible", "paid_social", "has_identity", "has_registration", "residence_half_year", "stable_basis", "beijing_hebei_permit", "has_photo", "has_address_proof", "tianjin_origin", "ordinary_degree", "seeking_work", "grant_not_received", "nonlocal_registration"})
PROFILE_KEYS = PROFILE_BOOLS | {"stage", "graduation_year"}
LEGACY_FACTS = frozenset({"has_identity", "has_registration", "has_photo", "has_address_proof", "residence_half_year", "stable_basis", "beijing_hebei_permit", "nonlocal_registration"})
STAGES = frozenset({"student", "graduate", "employed", "flexible", "other", "unknown"})
CSP = "; ".join(("default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:", "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'"))


class RequestError(ValueError):
    def __init__(self, code: str, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.code = code
        self.status = status


def _validate_facts(facts: object, *, legacy: bool = False) -> dict:
    if not isinstance(facts, dict):
        raise RequestError("invalid_request", "请使用有效的问答选项。")
    # Known boolean question names may contain 'address'; their values are never addresses.
    if any(isinstance(key, str) and key not in PROFILE_KEYS and SENSITIVE.search(key) for key in facts):
        raise RequestError("sensitive_input", "无需提供身份证号、姓名、电话或密钥。")
    allowed = LEGACY_FACTS if legacy else PROFILE_KEYS
    if not set(facts) <= allowed:
        raise RequestError("invalid_request", "问答包含不支持的选项。")
    for key, value in facts.items():
        if key == "stage":
            if not isinstance(value, str) or value not in STAGES:
                raise RequestError("invalid_request", "请选择有效的当前状态。")
        elif key == "graduation_year":
            if value is not None and (type(value) is not int or not 1990 <= value <= 2100):
                raise RequestError("invalid_request", "毕业年份无效。")
        elif value is not None and type(value) is not bool:
            raise RequestError("invalid_request", "问答只接受是、否或尚不确定。")
    return dict(facts)


def analyze_policy_request(*, city: str, matter: str, facts: dict) -> dict:
    policy = get_policy("residence-permit")
    requirements = [{"id": "identity", "label": "有效身份证件", "fact": "has_identity"}, {"id": "registration", "label": "完成居住登记", "fact": "has_registration"}]
    checklist = build_checklist(requirements, {key: value for key, value in facts.items() if key in {"has_identity", "has_registration"}})
    return {"city": city, "matter": matter, "checklist": checklist, "sources": [policy["source_url"]], "notice": "清单仅用于材料准备，具体受理条件以经办机构审核为准。"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class PolicyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, static_root: Path, service: PolicyService | None, *, accounts=None, tenants=None, public_origin=None):
        self.static_root = static_root.resolve(strict=True)
        self.service = service
        self.accounts = accounts
        self.tenants = tenants
        self.public_origin = public_origin
        self.account_errors = (AuthHTTPError,)
        if accounts is not None:
            from policy_accounts import AccountError
            self.account_errors += (AccountError,)
        self.rate_lock = threading.Lock()
        self.requests_by_route: dict[str, list[float]] = {}
        super().__init__(address, PolicyHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(CLIENT_TIMEOUT_SECONDS)
        return connection, address

    def server_close(self):
        if self.tenants is not None:
            self.tenants.close()
        if self.service is not None:
            self.service.close()
        super().server_close()

    def allow_request(self, path: str, *, actor=None, client="local", username="") -> bool:
        now = time.monotonic()
        # Bound paid/network operations separately from inexpensive local checklist updates.
        limit, window = (30, 60) if path == "/api/discover" else (2, 60) if path == "/api/check-updates" else (120, 60)
        key = path if path in {"/api/discover", "/api/check-updates"} else "local"
        buckets = [(key, limit)]
        if self.accounts is not None:
            if path in PUBLIC_AUTH_WRITES:
                buckets = [("auth-ip:" + client, 10), ("auth-name:" + username, 5)]
            elif path == "/api/account/api-key/verify":
                buckets = [("verify-ip:" + client, 10), ("verify-user:" + actor, 3)]
            else:
                buckets = [(key + ":" + actor, limit)]
        with self.rate_lock:
            self.requests_by_route = {name: moments for name, moments in self.requests_by_route.items() if moments and now - moments[-1] < window}
            if len(self.requests_by_route) >= 4096 and any(name not in self.requests_by_route for name, _ in buckets):
                return False
            recent = {name: [moment for moment in self.requests_by_route.get(name, []) if now - moment < window] for name, _ in buckets}
            allowed = all(len(recent[name]) < cap for name, cap in buckets)
            for name, _ in buckets:
                self.requests_by_route[name] = [*recent[name], now] if allowed else recent[name]
            return allowed


class PolicyHandler(BaseHTTPRequestHandler):
    server_version = "TianjinPolicy"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        return

    def _send(self, status: int, body: bytes, content_type: str, *, api=True, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store" if api else "no-cache")
        if getattr(self, "account_session", None):
            self.send_header("X-Account-Id", self.account_session["user"]["id"])
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                self.close_connection = True

    def _json(self, status: int, value, *, headers=None):
        self._send(status, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", headers=headers)

    def _error(self, status: int, code: str, message: str):
        self._json(status, {"error": {"code": code, "message": message}})

    def _trusted(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            return False
        origins = self.headers.get_all("Origin", [])
        if not origins:
            if self.headers.get("Sec-Fetch-Site", "") != "cross-site":
                return True
            try:
                api_path = self._path().startswith("/api/")
            except RequestError:
                return False
            return bool(self.server.public_origin and self.command in {"GET", "HEAD"}
                        and not api_path and self.headers.get("Sec-Fetch-Mode") == "navigate")
        allowed = {"http://" + hosts[0].lower()}
        if self.server.public_origin:
            allowed.add(self.server.public_origin)
        return len(origins) == 1 and origins[0].lower() in allowed

    def _path(self) -> str:
        try:
            return urlsplit(self.path).path
        except ValueError:
            raise RequestError("invalid_request", "请求地址无效。")

    def _guard(self) -> bool:
        if not self._trusted():
            self.close_connection = True
            self._error(421, "untrusted_origin", "仅接受本机同源请求。")
            return False
        return True

    def do_GET(self):
        self.account_session = None
        if not self._guard():
            return
        try:
            path = self._path()
            auth = AccountHTTP(self)
            if auth.handle_get(path):
                return
            session = auth.require_session() if self.server.accounts and path.startswith("/api/") and path != "/api/health" else None
            service = self.server.tenants.service_for(session["user"]["id"]) if session else self.server.service
            if path == "/api/health":
                self._json(200, {"status": "ok"})
            elif path == "/api/bootstrap":
                data = service.bootstrap()
                if self.server.tenants is not None:
                    data = {**data, "service": {**data["service"], "monitor_running": self.server.tenants.monitor_running}}
                self._json(200, data)
            elif path.startswith("/api/policies/"):
                policy_id = path.removeprefix("/api/policies/")
                policy = service.enrich_policies([get_policy(policy_id)])[0]
                follow = next((value for value in service.list_follows() if value["policy_id"] == policy_id), None)
                self._json(200, {"policy": policy, "follow": follow})
            elif path.startswith("/api/"):
                self._error(404, "not_found", "未找到这个接口。")
            else:
                self._static(path)
        except (PolicyValidationError, ServiceValidationError):
            self._error(404, "not_found", "未找到这项政策。")
        except RequestError as error:
            self._error(error.status, error.code, str(error))
        except self.server.account_errors as error:
            self._error(error.status, error.code, str(error))
        except Exception:
            self._error(500, "request_failed", "暂时无法读取本机数据，请稍后重试。")

    def do_HEAD(self):
        self.do_GET()

    def _body(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RequestError("unsupported_media_type", "请求需要使用 JSON。", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []) or len(lengths) != 1 or not lengths[0].isdigit():
            raise RequestError("invalid_request", "请求长度无效。")
        length = int(lengths[0])
        if length > MAX_REQUEST_BYTES:
            # Drain only small overruns so closing the connection does not erase the 413 response.
            if length <= MAX_REQUEST_BYTES * 2:
                try:
                    self.rfile.read(length)
                except (OSError, TimeoutError):
                    pass
            raise RequestError("request_too_large", "请求内容超过大小限制。", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            body = self.rfile.read(length)
        except (OSError, TimeoutError):
            raise RequestError("request_timeout", "请求接收超时。", HTTPStatus.REQUEST_TIMEOUT)
        if len(body) != length:
            raise RequestError("invalid_request", "请求内容不完整。")
        try:
            value = json.loads(body, object_pairs_hook=_unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise RequestError("invalid_json", "请求不是有效的 JSON。")
        if not isinstance(value, dict):
            raise RequestError("invalid_request", "请求内容需要是对象。")
        return value

    def _legacy_analyze(self, payload: dict):
        if set(payload) != {"city", "matter", "facts"} or not isinstance(payload["city"], str) or not isinstance(payload["matter"], str):
            raise RequestError("invalid_request", "请提供城市、事项和问答。")
        if payload["city"] != "天津市":
            raise RequestError("unsupported_city", "当前仅支持天津市。", HTTPStatus.UNPROCESSABLE_ENTITY)
        if payload["matter"] != "居住证办理":
            raise RequestError("invalid_request", "当前事项不受支持。")
        facts = _validate_facts(payload["facts"], legacy=True)
        return analyze_policy_request(city=payload["city"], matter=payload["matter"], facts=facts)

    def _write_route(self, path: str, payload: dict, *, session=None):
        service = self.server.tenants.service_for(session["user"]["id"]) if session else self.server.service
        if self.command == "POST" and path == "/api/analyze":
            return self._legacy_analyze(payload)
        if self.command == "POST" and path == "/api/discover":
            if set(payload) != {"profile"}:
                raise RequestError("invalid_request", "请提供问答选项。")
            return service.discover(_validate_facts(payload["profile"]))
        if self.command == "POST" and path == "/api/follows":
            if set(payload) != {"policy_id"} or not isinstance(payload["policy_id"], str):
                raise RequestError("invalid_request", "请选择要关注的政策。")
            return {"follow": service.follow(payload["policy_id"])}
        if self.command == "POST" and path == "/api/check-updates":
            if payload:
                raise RequestError("invalid_request", "检查更新不需要额外资料。")
            return service.check_updates()
        if self.command == "PATCH" and path.startswith("/api/follows/"):
            return {"follow": service.update_follow(path.removeprefix("/api/follows/"), payload)}
        if self.command == "DELETE" and path.startswith("/api/follows/"):
            if payload:
                raise RequestError("invalid_request", "取消关注不需要额外资料。")
            return {"deleted": service.unfollow(path.removeprefix("/api/follows/"))}
        if self.command == "PATCH" and path.startswith("/api/notifications/"):
            if set(payload) != {"read"}:
                raise RequestError("invalid_request", "提醒更新格式无效。")
            return {"notification": service.mark_notification(path.removeprefix("/api/notifications/"), payload["read"])}
        raise RequestError("not_found", "未找到这个接口。", HTTPStatus.NOT_FOUND)

    def _write(self):
        self.account_session = None
        if not self._guard():
            return
        try:
            path = self._path()
            auth = AccountHTTP(self)
            session = auth.require_session(write=True) if self.server.accounts and path not in PUBLIC_AUTH_WRITES else None
            payload = self._body()
            username = payload.get("username", "")
            username = username.casefold() if isinstance(username, str) and len(username) <= 32 else "invalid"
            actor = session["user"]["id"] if session else "anonymous"
            if not self.server.allow_request(path, actor=actor, client=self._client_ip(), username=username):
                raise RequestError("rate_limited", "操作太频繁，请稍等一分钟。", HTTPStatus.TOO_MANY_REQUESTS)
            if not auth.handle_write(path, payload, session):
                self._json(200, self._write_route(path, payload, session=session))
        except RequestError as error:
            self.close_connection = True
            self._error(error.status, error.code, str(error))
        except self.server.account_errors as error:
            self.close_connection = True
            self._error(error.status, error.code, str(error))
        except ServiceValidationError as error:
            status = 409 if error.code == "check_in_progress" else 404 if error.code == "not_found" else 400
            self._error(status, error.code, str(error))
        except PolicyValidationError:
            self._error(400, "invalid_request", "政策或问答选项无效。")
        except Exception:
            self._error(500, "analysis_failed", "暂时无法完成操作，请稍后重试。")

    do_POST = _write
    do_PATCH = _write
    do_DELETE = _write

    def _client_ip(self):
        address = self.client_address[0]
        forwarded = self.headers.get_all("X-Real-IP", [])
        if self.server.public_origin and ipaddress.ip_address(address).is_loopback and len(forwarded) == 1:
            try:
                return str(ipaddress.ip_address(forwarded[0]))
            except ValueError:
                pass
        return address

    def _static(self, path: str):
        try:
            decoded = unquote(path, errors="strict")
            relative = decoded.lstrip("/") or "index.html"
            parts = Path(relative).parts
            if any(character in relative for character in ("\x00", "\\", ":")) or any(part.startswith(".") or re.search(r"~\d+(?:\.|$)", part) for part in parts) or Path(relative).suffix.lower() not in STATIC_SUFFIXES:
                raise ValueError()
            target = (self.server.static_root / relative).resolve(strict=True)
            target.relative_to(self.server.static_root)
            if not target.is_file():
                raise ValueError()
        except (UnicodeError, ValueError, OSError, RuntimeError):
            self._error(404, "not_found", "未找到这个文件。")
            return
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if media.startswith("text/") or media in {"application/javascript", "image/svg+xml"}:
            media += "; charset=utf-8"
        self._send(200, target.read_bytes(), media, api=False)


def _public_origin(value, *, accounts):
    if value is None:
        return None
    parsed = urlsplit(value)
    if not accounts or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or any(char.isspace() for char in value):
        raise ValueError("public_origin must be an HTTPS origin in account mode.")
    hostname = parsed.hostname.encode("idna").decode().lower()
    hostname = "[" + hostname + "]" if ":" in hostname else hostname
    return "https://" + hostname + (":" + str(parsed.port) if parsed.port and parsed.port != 443 else "")


def create_server(*, port: int = 4173, static_root: Path | str | None = None, db_path: Path | str | None = None, start_monitor: bool = False, accounts: bool = False, public_origin: str | None = None, data_dir: Path | str | None = None) -> PolicyHTTPServer:
    base = Path(__file__).resolve().parent
    root = Path(static_root) if static_root is not None else base / "static"
    origin = _public_origin(public_origin, accounts=accounts)
    state = Path(db_path) if db_path is not None else Path(data_dir) / "policy.sqlite3" if data_dir is not None else root / ".state.sqlite3" if static_root is not None else base / "data" / "policy.sqlite3"
    if accounts:
        from policy_accounts import AccountStore
        from policy_tenants import TenantServices
        store = AccountStore(Path(data_dir) if data_dir is not None else state.parent)
        tenants = TenantServices(store, start_monitor=start_monitor)
        try:
            return PolicyHTTPServer(("127.0.0.1", port), root, None, accounts=store, tenants=tenants, public_origin=origin)
        except Exception:
            tenants.close()
            raise
    service = PolicyService(state)
    server = PolicyHTTPServer(("127.0.0.1", port), root, service)
    if start_monitor:
        service.start_monitor()
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Tianjin policy guide locally.")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--no-monitor", action="store_true")
    parser.add_argument("--accounts", action="store_true")
    parser.add_argument("--public-origin")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        server = create_server(port=args.port, start_monitor=not args.no_monitor, accounts=args.accounts, public_origin=args.public_origin, data_dir=args.data_dir)
    except ValueError as error:
        parser.error(str(error))
    print(f"天津权益雷达：http://127.0.0.1:{args.port}/", flush=True)
    print("政策监测仅在本机服务运行时执行。Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
