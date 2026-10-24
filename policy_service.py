from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from policy_catalog import get_catalog, get_policy, match_policies
from policy_sources import decompose_document, fetch_document, search_official, source_status


CHECK_INTERVAL_SECONDS = 6 * 60 * 60
CHINA = timezone(timedelta(hours=8))
FOLLOW_STATES = frozenset({"preparing", "submitted", "completed"})


class ServiceValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _changes(before: str, after: str) -> list[dict[str, str]]:
    previous = [line.strip() for line in before.splitlines() if line.strip()]
    current = [line.strip() for line in after.splitlines() if line.strip()]
    differences = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, previous, current).get_opcodes():
        if tag != "equal":
            differences.append({"before": "\n".join(previous[a:b])[:700], "after": "\n".join(current[c:d])[:700]})
    return differences[:6]


class PolicyService:
    def __init__(self, db_path: Path | str, *, clock: Callable[[], datetime] | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.check_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.monitor: threading.Thread | None = None
        self.search_cache: dict[tuple[str, str | None, bool], tuple[float, dict]] = {}
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS follows (
                    policy_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    checked_materials TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    policy_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL,
                    checked_at TEXT NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,
                    validity TEXT NOT NULL DEFAULT '未标明'
                );
                CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, payload TEXT NOT NULL,
                    read INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            if "validity" not in {row["name"] for row in db.execute("PRAGMA table_info(snapshots)")}:
                db.execute("ALTER TABLE snapshots ADD COLUMN validity TEXT NOT NULL DEFAULT '未标明'")

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _now(self) -> str:
        return self.clock().astimezone(CHINA).isoformat(timespec="seconds")

    def _get_setting(self, key: str, fallback=None):
        with self.lock, self._connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else fallback

    def _setting(self, key: str, value) -> None:
        with self.lock, self._connection() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))

    def list_follows(self) -> list[dict]:
        with self.lock, self._connection() as db:
            rows = db.execute("SELECT * FROM follows ORDER BY created_at DESC, policy_id").fetchall()
        return [{**dict(row), "checked_materials": json.loads(row["checked_materials"])} for row in rows]

    def follow(self, policy_id: str) -> dict:
        get_policy(policy_id)
        now = self._now()
        with self.lock, self._connection() as db:
            db.execute("INSERT OR IGNORE INTO follows VALUES (?, 'preparing', '[]', ?, ?)", (policy_id, now, now))
        self._deadline_reminders()
        return next(item for item in self.list_follows() if item["policy_id"] == policy_id)

    def update_follow(self, policy_id: str, updates: dict) -> dict:
        policy = get_policy(policy_id)
        if not isinstance(updates, dict) or not updates or not set(updates) <= {"status", "checked_materials"}:
            raise ServiceValidationError("invalid_request", "请提供申请状态或材料勾选记录。")
        existing = next((item for item in self.list_follows() if item["policy_id"] == policy_id), None)
        if existing is None:
            raise ServiceValidationError("not_found", "请先关注这项政策。")
        state = updates.get("status", existing["status"])
        checked = updates.get("checked_materials", existing["checked_materials"])
        allowed = {item["id"] for item in policy["materials"]}
        if not isinstance(state, str) or state not in FOLLOW_STATES:
            raise ServiceValidationError("invalid_request", "申请状态无效。")
        if not isinstance(checked, list) or any(not isinstance(value, str) or value not in allowed for value in checked) or len(set(checked)) != len(checked):
            raise ServiceValidationError("invalid_request", "材料勾选记录无效。")
        with self.lock, self._connection() as db:
            db.execute("UPDATE follows SET status = ?, checked_materials = ?, updated_at = ? WHERE policy_id = ?", (state, json.dumps(checked), self._now(), policy_id))
        return next(item for item in self.list_follows() if item["policy_id"] == policy_id)

    def unfollow(self, policy_id: str) -> bool:
        get_policy(policy_id)
        with self.lock, self._connection() as db:
            deleted = db.execute("DELETE FROM follows WHERE policy_id = ?", (policy_id,)).rowcount > 0
            db.execute("DELETE FROM notifications WHERE policy_id = ?", (policy_id,))
        return deleted

    def _add_notification(self, notification: dict) -> None:
        value = {**notification, "created_at": self._now(), "read": False}
        with self.lock, self._connection() as db:
            db.execute("INSERT OR IGNORE INTO notifications VALUES (?, ?, ?, 0)", (value["id"], value["policy_id"], json.dumps(value, ensure_ascii=False)))

    def _deadline_reminders(self) -> None:
        today = self.clock().astimezone(CHINA).date()
        for follow in self.list_follows():
            policy = get_policy(follow["policy_id"])
            deadline = policy.get("deadline")
            if follow["status"] == "completed" or not deadline or (policy.get("valid_until") and policy["valid_until"] < today.isoformat()):
                continue
            days = (datetime.strptime(deadline, "%Y-%m-%d").date() - today).days
            if 0 <= days <= 7:
                self._add_notification({
                    "id": _digest(f"deadline:{policy['id']}:{deadline}"), "policy_id": policy["id"],
                    "title": f"{policy['title']}：申请窗口临近截止",
                    "summary": f"官方通知要求在 {deadline} 前申请；请核对经办单位是否另有更早截止时间。",
                    "kind": "deadline", "source_url": policy["source_url"], "diff": [], "deadline": deadline,
                })

    def notifications(self) -> list[dict]:
        self._deadline_reminders()
        active = {item["policy_id"] for item in self.list_follows() if item["status"] != "completed"}
        today = self.clock().astimezone(CHINA).date().isoformat()
        with self.lock, self._connection() as db:
            rows = db.execute("SELECT payload, read FROM notifications ORDER BY rowid DESC").fetchall()
        result = [{**json.loads(row["payload"]), "read": bool(row["read"])} for row in rows]
        return [item for item in result if item["policy_id"] in active and (item["kind"] != "deadline" or item.get("deadline", today) >= today)]

    def mark_notification(self, notification_id: str, read: bool) -> dict:
        if read is not True:
            raise ServiceValidationError("invalid_request", "只支持将提醒标记为已读。")
        with self.lock, self._connection() as db:
            changed = db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)).rowcount
        if not changed:
            raise ServiceValidationError("not_found", "未找到这条提醒。")
        notice = next((value for value in self.notifications() if value["id"] == notification_id), None)
        if notice is None:
            raise ServiceValidationError("not_found", "这条提醒已经归档。")
        return notice

    def enrich_policies(self, policies: list[dict]) -> list[dict]:
        with self.lock, self._connection() as db:
            snapshots = {row["policy_id"]: dict(row) for row in db.execute("SELECT policy_id, checked_at, needs_review, validity FROM snapshots")}
        enriched = []
        for policy in policies:
            snapshot = snapshots.get(policy["id"])
            item = {**policy, "source_review": {"status": "needs_review" if snapshot and snapshot["needs_review"] else "baseline" if snapshot else "not_checked", "checked_at": snapshot["checked_at"] if snapshot else None, "validity": snapshot["validity"] if snapshot else "未标明", "rules_verified": False, "message": "正文监测只负责发现变化，申请条件仍需核对官方要求。"}}
            if snapshot and snapshot["needs_review"] and item.get("match", {}).get("status") == "possible":
                item = {**item, "match": {**item["match"], "status": "needs_info", "reasons": ["官方原文发生变化，已整理的申请条件需要重新复核。", *item["match"].get("reasons", [])]}}
            enriched.append(item)
        return enriched

    def bootstrap(self) -> dict:
        status = source_status()
        return {
            "city": "天津", "policies": self.enrich_policies(get_catalog()), "follows": self.list_follows(),
            "notifications": self.notifications(), "now": self._now(),
            "service": {"search_configured": bool(status.get("configured")), "monitor_running": bool(self.monitor and self.monitor.is_alive()), "last_checked_at": self._get_setting("last_checked_at"), "check_interval_seconds": CHECK_INTERVAL_SECONDS},
        }

    def discover(self, profile: dict) -> dict:
        policies = self.enrich_policies(match_policies(profile, today=self.clock().astimezone(CHINA).date()))
        # Search receives only public policy keywords, never the visitor's answers.
        query = "天津市 高校毕业生 求职补贴 灵活就业社会保险补贴 申请 官方政策"
        try:
            evidence = self._search(query)
        except Exception:
            evidence = {"status": "error", "query": query, "request_id": None, "results": [], "message": "元宝搜索暂时不可用，当前展示已整理的官方资料。"}
        self._record_search(evidence)
        return {"policies": policies, "search": evidence}

    def _search(self, query: str, freshness: str | None = None) -> dict:
        key = (query, freshness, bool(source_status().get("configured")))
        now = time.monotonic()
        with self.lock:
            cached = self.search_cache.get(key)
        if cached and now - cached[0] < 300:
            return {**cached[1], "cached": True}
        result = {**search_official(query, freshness=freshness), "cached": False, "checked_at": self._now()}
        with self.lock:
            self.search_cache = {item: value for item, value in self.search_cache.items() if now - value[0] < 300}
            self.search_cache[key] = (now, result)
        return result

    def _record_search(self, evidence: dict) -> None:
        metadata = {key: evidence.get(key) for key in ("status", "request_id", "query")}
        with self.lock:
            runs = self._get_setting("search_runs", [])
            self._setting("search_runs", [*runs[-19:], {**metadata, "checked_at": self._now()}])

    def _check_document(self, policy: dict) -> dict:
        try:
            document = fetch_document(policy["source_url"])
            if not document.get("body") or not document.get("fingerprint"):
                raise ValueError("Missing document content")
        except Exception:
            return {"policy_id": policy["id"], "status": "error", "message": "官方原文暂时无法读取，保留上次资料，稍后重试。"}
        with self.lock, self._connection() as db:
            previous = db.execute("SELECT * FROM snapshots WHERE policy_id = ?", (policy["id"],)).fetchone()
            changed = bool(previous and previous["fingerprint"] != document["fingerprint"])
            validity = document.get("validity", "未标明")
            invalid = validity in {"失效", "已废止", "废止", "部分失效"}
            reviewed_fingerprint = policy.get("reviewed_source_fingerprint")
            differs_from_review = bool(not previous and reviewed_fingerprint and reviewed_fingerprint != document["fingerprint"])
            needs_review = bool(changed or invalid or differs_from_review or (previous and previous["needs_review"]))
            db.execute("INSERT OR REPLACE INTO snapshots (policy_id, fingerprint, body, checked_at, needs_review, validity) VALUES (?, ?, ?, ?, ?, ?)", (policy["id"], document["fingerprint"], document["body"], self._now(), int(needs_review), validity))
        if changed:
            differences = _changes(previous["body"], document["body"])
            if previous["validity"] != validity:
                differences = [{"before": "官方标注有效性：" + previous["validity"], "after": "官方标注有效性：" + validity}, *differences]
            self._add_notification({
                "id": _digest(f"source:{policy['id']}:{document['fingerprint']}"), "policy_id": policy["id"],
                "title": f"{policy['title']}：官方原文有变化",
                "summary": "检测到正文变化，请复核下列差异；这并不自动证明申请规则已调整。",
                "kind": "source_changed", "source_url": policy["source_url"], "diff": differences,
            })
        elif differs_from_review:
            self._add_notification({"id": _digest(f"catalog-review:{policy['id']}:{document['fingerprint']}"), "policy_id": policy["id"], "title": f"{policy['title']}：官方原文需重新核对", "summary": "官方原文与资料整理时不同，申请条件需要重新复核；缺少原始正文，不展示未经验证的具体差异。", "kind": "review", "source_url": policy["source_url"], "diff": []})
        elif invalid:
            self._add_notification({"id": _digest(f"validity:{policy['id']}:{document['fingerprint']}"), "policy_id": policy["id"], "title": f"{policy['title']}：官方效力标注需复核", "summary": f"官方页面标注为“{validity}”，请先联系经办单位核对当前依据。", "kind": "review", "source_url": policy["source_url"], "diff": []})
        return {"policy_id": policy["id"], "status": "changed" if changed else "unchanged" if previous else "baseline", "checked_at": self._now(), "validity": validity, "needs_review": needs_review}

    def _check_search(self, policy: dict) -> dict:
        query = policy.get("queries", [policy["title"]])[0]
        try:
            evidence = self._search(query, freshness="m3")
        except Exception:
            return {"status": "error", "request_id": None}
        self._record_search(evidence)
        if evidence.get("status") != "ok":
            return evidence
        key = "seen_urls:" + policy["id"]
        seen = self._get_setting(key)
        pages = evidence.get("results", [])
        urls = [value["url"] for value in pages]
        if seen is not None:
            candidates = [page for page in pages if page["url"] not in seen and page["url"] != policy["source_url"]]
            for page in candidates[:2]:
                if page["url"] not in seen and page["url"] != policy["source_url"]:
                    try:
                        digest = decompose_document(fetch_document(page["url"]))
                    except Exception:
                        digest = {"reviewed": False, "summary": "官方正文暂时无法读取，请打开来源核对。", "audience": [], "requirements": [], "materials": [], "timing": [], "actions": []}
                    self._add_notification({
                        "id": _digest("candidate:" + policy["id"] + page["url"]), "policy_id": policy["id"],
                        "title": "发现相关官方资料，待核对", "summary": page["title"],
                        "kind": "review", "source_url": page["url"], "diff": [], "digest": digest,
                    })
        self._setting(key, sorted(set((seen or []) + urls))[-500:])
        return {"status": evidence.get("status"), "request_id": evidence.get("request_id")}

    def check_updates(self) -> dict:
        if not self.check_lock.acquire(blocking=False):
            raise ServiceValidationError("check_in_progress", "更新检查正在进行，请稍后查看。")
        try:
            policies = [get_policy(item["policy_id"]) for item in self.list_follows() if item["status"] != "completed"]
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(self._check_document, policies))
                searches = list(pool.map(self._check_search, policies)) if source_status().get("configured") else []
            checked_at = self._now()
            self._setting("last_checked_at", checked_at)
            return {"notifications": self.notifications(), "checked_at": checked_at, "results": results, "search_status": {"configured": bool(source_status().get("configured")), "runs": searches}}
        finally:
            self.check_lock.release()

    def start_monitor(self) -> None:
        if self.monitor and self.monitor.is_alive():
            return
        self.stop_event.clear()
        self.monitor = threading.Thread(target=self._monitor_loop, name="policy-monitor", daemon=True)
        self.monitor.start()

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.check_updates()
            except Exception:
                self._setting("monitor_error_at", self._now())
            self.stop_event.wait(CHECK_INTERVAL_SECONDS)

    def close(self) -> None:
        self.stop_event.set()
        if self.monitor and self.monitor.is_alive():
            self.monitor.join(timeout=1)
