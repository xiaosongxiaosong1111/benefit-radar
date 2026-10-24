from __future__ import annotations

import logging
import re
import threading

from policy_service import CHECK_INTERVAL_SECONDS, PolicyService, ServiceValidationError
from policy_sources import search_official, source_status


logger = logging.getLogger(__name__)


class TenantServices:
    """Keep account state and provider credentials out of the local demonstration service."""

    def __init__(self, accounts, *, start_monitor: bool = False):
        self.accounts = accounts
        self.root = accounts.root.resolve()
        self.users_root = self.root / "users"
        self.users_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.users_root.resolve().parent != self.root:
            raise ValueError("Account storage must remain inside its configured directory.")
        self.users_root.chmod(0o700)
        self.lock = threading.RLock()
        self.services: dict[str, PolicyService] = {}
        self.stop_event = threading.Event()
        self.closed = False
        self.monitor: threading.Thread | None = None
        if start_monitor:
            self.monitor = threading.Thread(target=self._monitor_loop, name="account-policy-monitor", daemon=True)
            self.monitor.start()

    def service_for(self, user_id: str) -> PolicyService:
        if not isinstance(user_id, str) or re.fullmatch(r"[0-9a-f]{32}", user_id) is None or user_id not in self.accounts.list_user_ids():
            raise ServiceValidationError("not_found", "未找到这个账户。")
        with self.lock:
            if self.closed:
                raise RuntimeError("Account services have stopped.")
            if user_id not in self.services:
                directory = self.users_root / user_id
                directory.mkdir(mode=0o700, exist_ok=True)
                if directory.resolve().parent != self.users_root:
                    raise ValueError("Account storage must remain inside its configured directory.")
                directory.chmod(0o700)
                service = PolicyService(
                    directory / "policy.sqlite3", persist_profile=True,
                    searcher=lambda query, freshness=None: search_official(query, freshness=freshness, api_key=self.accounts.get_api_key(user_id)),
                    status_provider=lambda: self._status(user_id),
                )
                service.db_path.chmod(0o600)
                self.services = {**self.services, user_id: service}
            return self.services[user_id]

    def _status(self, user_id: str) -> dict:
        metadata = self.accounts.api_metadata(user_id)
        return {**source_status(api_key=self.accounts.get_api_key(user_id)), "revision": metadata["revision"]}

    def invalidate(self, user_id: str) -> None:
        with self.lock:
            service = self.services.get(user_id)
        if service is not None:
            with service.lock:
                service.search_cache = {}

    @property
    def monitor_running(self) -> bool:
        return bool(self.monitor and self.monitor.is_alive())

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            for user_id in self.accounts.list_user_ids():
                if self.stop_event.is_set():
                    return
                service = None
                try:
                    service = self.service_for(user_id)
                    service.check_updates()
                except Exception:
                    logger.warning("Policy monitoring failed for account %s; other accounts will continue.", user_id)
                    if service is not None:
                        try:
                            service._setting("monitor_error_at", service._now())
                        except Exception:
                            logger.warning("Could not save monitoring status for account %s.", user_id)
            self.stop_event.wait(CHECK_INTERVAL_SECONDS)

    def close(self) -> None:
        self.stop_event.set()
        if self.monitor and self.monitor.is_alive():
            self.monitor.join(timeout=1)
        with self.lock:
            self.closed = True
            services = list(self.services.values())
        for service in services:
            service.close()
