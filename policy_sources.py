"""Read official policy bodies and call Yuanbao SearchPro without retaining secrets."""
from __future__ import annotations

import hashlib
import html as html_lib
import ipaddress
import json
import os
import re
import socket
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_URL = "https://api.wsa.cloud.tencent.com/SearchPro"
API_KEY_ENV = "TENCENTCLOUD_WSA_APIKEY"
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_SEARCH_BYTES = 4 * 1024 * 1024
FRESHNESS = re.compile(r"^(?:d(?:[1-9]|[12]\d|30)?|m(?:[1-9]|1[0-2])?|y[1-5]?)$")


class SourceError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _checked_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        valid = (
            parsed.scheme == "https"
            and (host == "tj.gov.cn" or host.endswith(".tj.gov.cn"))
            and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and len(url) < 4096
            and not any(unicodedata.category(c).startswith("C") for c in url)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise SourceError("仅支持天津政府网站的 HTTPS 官方来源。")
    return url


def _ensure_public_host(url: str) -> None:
    try:
        addresses = socket.getaddrinfo(urlsplit(url).hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("non-public host")
    except (OSError, ValueError) as error:
        raise SourceError("官方来源地址暂时无法安全访问。") from error


def _clean(value: object, limit: int = 3000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t").split())[:limit]


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.stack: list[str] = []
        self.capture: int | None = None
        self.skip: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta":
            self.meta[(values.get("name") or "").lower()] = values.get("content") or ""
        if tag in {"meta", "link", "img", "input", "hr", "br", "source", "wbr"}:
            if self.capture is not None and tag in {"br", "hr"}:
                self.parts.append("\n")
            return
        self.stack.append(tag)
        classes = (values.get("class") or "").lower()
        if self.capture is None and ("trs_editor" in classes or "trs_ueditor" in classes or tag == "article"):
            self.capture = len(self.stack)
        if self.capture is not None and tag in {"script", "style", "nav", "footer"}:
            self.skip = tag
        if self.capture is not None and tag in {"p", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        if self.capture is not None and tag in {"p", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if self.skip == tag:
            self.skip = None
        if self.capture is not None and index < self.capture:
            self.capture = None
        self.stack = self.stack[:index]

    def handle_data(self, data):
        if self.capture is not None and self.skip is None:
            self.parts.append(data)


def parse_document(html: str, url: str) -> dict:
    _checked_url(url)
    parser = DocumentParser()
    parser.feed(html)
    body = "\n".join(line for part in "".join(parser.parts).splitlines() if (line := _clean(part, 10000)))
    if len(body) < 20:
        raise SourceError("未识别到官方政策正文，请打开原文核对。")
    title = _clean(parser.meta.get("articletitle"), 500)
    metadata_html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    metadata_text = re.sub(r"\s+", "", html_lib.unescape(re.sub(r"<[^>]*>", "", metadata_html)))
    validity_match = re.search(r"有效性[：:]?(已废止|部分失效|失效|废止|有效)", metadata_text)
    validity = validity_match.group(1) if validity_match else "未标明"
    fingerprint = hashlib.sha256(re.sub(r"\s+", "", title + body + validity).encode()).hexdigest()
    return {
        "title": title, "body": body, "published_at": _clean(parser.meta.get("pubdate"), 32),
        "source_url": url, "fingerprint": fingerprint,
        "validity": validity,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_document(url: str, *, opener: Callable | None = None) -> dict:
    _checked_url(url)
    _ensure_public_host(url)
    request = Request(url, headers={"User-Agent": "TianjinPolicyGuide/1.0", "Accept": "text/html"})
    try:
        open_url = opener or build_opener(NoRedirect()).open
        with open_url(request, timeout=12) as response:
            body = response.read(MAX_DOCUMENT_BYTES + 1)
            media = response.headers.get("Content-Type", "")
        if len(body) > MAX_DOCUMENT_BYTES or "text/html" not in media.lower():
            raise SourceError("官方来源格式或大小不符合预期。")
        charset = re.search(r"charset=([\w-]+)", media, re.I)
        encoding = charset.group(1) if charset else "utf-8"
        return parse_document(body.decode(encoding), url)
    except SourceError:
        raise
    except Exception as error:
        raise SourceError("官方来源暂时无法访问，已保留上次记录。") from error


def decompose_document(document: dict) -> dict:
    """Extract candidate evidence; reviewed catalog rules remain a separate source."""
    _checked_url(document.get("source_url"))
    body = document.get("body")
    if not isinstance(body, str) or not body.strip():
        raise SourceError("缺少可拆解的官方正文。")
    patterns = {
        "audience": r"对象|范围|适用|毕业生|申请人",
        "requirements": r"条件|应当|符合|不得|需要|须",
        "materials": r"材料|复印件|申请表|证明|凭证",
        "timing": r"截止|日前|期限|有效期|施行|实施|废止|\d{4}年|\d+月\d+日",
        "actions": r"申请|办理|提交|登录|登记|申领",
    }
    excerpts = [line.strip() for line in body.splitlines() if 3 <= len(line.strip()) <= 1500]
    return {
        "reviewed": False,
        "summary": "以下为官方原文摘录；适用条件与当前效力仍需核对。",
        **{key: [line[:500] for line in excerpts if re.search(pattern, line)][:3]
           for key, pattern in patterns.items()},
    }


def source_status(api_key: str | None = None) -> dict:
    key = (os.environ.get(API_KEY_ENV, "") if api_key is None else api_key).strip()
    configured = 8 <= len(key) <= 512 and not any(c.isspace() or unicodedata.category(c).startswith("C") for c in key)
    return {"configured": configured, "provider": "元宝搜索 SearchPro", "message": "联网检索已配置" if configured else "官方资料预览 · 联网检索未启用"}


def _open_search(request, timeout):
    return build_opener(NoRedirect()).open(request, timeout=timeout)


def _search_results(pages: list) -> list[dict]:
    results = []
    for raw in pages[:50]:
        try:
            page = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(page, dict):
                continue
            url = _checked_url(page.get("url"))
        except (ValueError, TypeError):
            continue
        results.append({"title": _clean(page.get("title"), 500), "url": url,
                        "date": _clean(page.get("date"), 50), "passage": _clean(page.get("passage"))})
    return results


def search_official(query: str, freshness: str | None = None, *, api_key: str | None = None) -> dict:
    result = {"status": "not_configured", "query": query, "request_id": None, "results": [],
              "message": "联网检索未启用；当前展示已整理的官方资料。"}
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > 1500:
        raise SourceError("搜索词为空或过长。")
    if freshness is not None and not FRESHNESS.fullmatch(freshness):
        raise SourceError("搜索时间范围不正确。")
    key = os.environ.get(API_KEY_ENV, "") if api_key is None else api_key
    if not source_status(api_key=key)["configured"]:
        return result
    payload = {"Query": query.strip(), "Site": "tj.gov.cn"}
    if freshness:
        payload["Freshness"] = freshness
    request = Request(API_URL, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST", headers={
        "Authorization": "Bearer " + key.strip(), "Content-Type": "application/json; charset=UTF-8",
    })
    try:
        with _open_search(request, timeout=12) as response:
            raw = response.read(MAX_SEARCH_BYTES + 1)
        if len(raw) > MAX_SEARCH_BYTES:
            raise SourceError("search response too large")
        data = json.loads(raw)["Response"]
        if data.get("Error") or not isinstance(data.get("Pages"), list) or (data.get("Msg") and not data["Pages"]):
            raise SourceError("search response unavailable")
        return {**result, "status": "ok", "request_id": _clean(data.get("RequestId"), 200) or None,
                "results": _search_results(data["Pages"]), "message": "已检索天津官方公开来源；结果需结合原文核对。"}
    except Exception:
        return {**result, "status": "error", "message": "元宝搜索暂时不可用，已保留官方资料清单，请稍后重试。"}
