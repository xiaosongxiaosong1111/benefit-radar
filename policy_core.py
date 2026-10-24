from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class PolicyValidationError(ValueError):
    """A policy or an answer is outside the supported public-data contract."""


def is_official_tianjin_source(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        return False
    if "\\" in value:
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        return (url.scheme == "https" and bool(host)
                and (host == "tj.gov.cn" or host.endswith(".tj.gov.cn"))
                and url.username is None and url.password is None
                and url.port in (None, 443))
    except ValueError:
        return False


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    pairs = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path,
                       urlencode(sorted(pairs)), ""))


def _normalize(value: object, *, field: str = "") -> object:
    if isinstance(value, str):
        clean = unicodedata.normalize("NFKC", value).strip()
        if field.endswith("url"):
            return _canonical_url(clean)
        clean = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", clean)
        return " ".join(clean.split())
    if isinstance(value, Mapping):
        return {str(key): _normalize(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise PolicyValidationError("政策内容必须是可序列化的公开数据。")


def _policy_content(policy: Mapping) -> dict:
    if not isinstance(policy, Mapping):
        raise PolicyValidationError("政策内容格式不正确。")
    return {str(key): _normalize(value, field=str(key)) for key, value in policy.items()
            if key not in {"source_checked_at", "checked_at", "fetched_at", "match"}}


def policy_fingerprint(policy: Mapping) -> str:
    content = json.dumps(_policy_content(policy), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def detect_policy_change(previous: Mapping, current: Mapping) -> dict:
    old, new = _policy_content(previous), _policy_content(current)
    changed_fields = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    return {"changed": bool(changed_fields), "changed_fields": changed_fields,
            "previous_fingerprint": policy_fingerprint(previous),
            "current_fingerprint": policy_fingerprint(current)}


def decompose_policy(policy: Mapping) -> dict:
    if not isinstance(policy, Mapping) or not is_official_tianjin_source(policy.get("source_url")):
        raise PolicyValidationError("政策拆解需要天津官方 HTTPS 来源。")
    actions = policy.get("actions")
    if not isinstance(policy.get("title"), str) or not policy["title"].strip():
        raise PolicyValidationError("政策缺少标题。")
    if (not isinstance(actions, (list, tuple)) or not actions
            or any(not isinstance(item, str) or not item.strip() for item in actions)):
        raise PolicyValidationError("政策缺少有依据的办理动作。")
    fields = ("title", "document_number", "issuer", "published_at", "effective_at",
              "applies_to", "what_changed", "actions")
    return {**{key: deepcopy(policy.get(key, [] if key in {"applies_to", "what_changed", "actions"} else ""))
               for key in fields},
            "source": {"url": policy["source_url"], "official": True}}


def build_checklist(requirements: Sequence, facts: Mapping) -> dict:
    if not isinstance(requirements, (list, tuple)) or not isinstance(facts, Mapping):
        raise PolicyValidationError("清单和回答格式不正确。")
    if any(not isinstance(item, Mapping) or not all(isinstance(item.get(key), str) and item[key]
           for key in ("id", "label", "fact")) for item in requirements):
        raise PolicyValidationError("清单条目缺少必要信息。")
    allowed = {item["fact"] for item in requirements}
    if not set(facts) <= allowed or any(value is not None and type(value) is not bool for value in facts.values()):
        raise PolicyValidationError("只接受当前清单的问题及是、否、不确定回答。")
    items = [{**deepcopy(item), "state": "confirm" if facts.get(item["fact"]) is None
              else "ready" if facts[item["fact"]] is item.get("expected", True) else "missing"}
             for item in requirements]
    counts = {state: sum(item["state"] == state for item in items)
              for state in ("ready", "missing", "confirm")}
    status = "not_ready" if counts["missing"] else "needs_confirmation" if counts["confirm"] else "ready"
    return {"status": status, "items": items, "counts": counts}


def _parsed_date(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def build_in_app_reminders(changes: Sequence, *, followed_matters: set[str], today: date | None = None) -> list[dict]:
    current_day = today or date.today()
    seen: set[str] = set()
    reminders: list[dict] = []
    for change in changes:
        if not isinstance(change, Mapping) or change.get("matter") not in followed_matters:
            continue
        if not is_official_tianjin_source(change.get("source_url")):
            continue
        published = _parsed_date(change.get("published_at"))
        expires = _parsed_date(change.get("expires_at"))
        if not published or published > current_day or (expires and expires < current_day):
            continue
        if change.get("expires_at") and expires is None:
            continue
        fingerprint = change.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        reminders.append({"id": fingerprint, "channel": "in_app", "read": False,
                          **{key: deepcopy(change.get(key, "")) for key in
                             ("matter", "title", "summary", "published_at", "source_url")}})
    return reminders


def get_catalog() -> list[dict]:
    from policy_catalog import get_catalog as catalog
    return catalog()


def get_policy(policy_id: str) -> dict:
    from policy_catalog import get_policy as policy
    return policy(policy_id)


def match_policies(profile: dict, today: date | None = None) -> list[dict]:
    from policy_catalog import match_policies as match
    return match(profile, today=today)
