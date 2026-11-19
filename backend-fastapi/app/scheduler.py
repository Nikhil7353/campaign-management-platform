from collections.abc import Callable
from datetime import datetime, timezone
import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from .db import SessionLocal
from .services import MAX_SYNC_INTERVAL_SECONDS, MIN_SYNC_INTERVAL_SECONDS, get_sync_settings, run_inbox_sync

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class InboxSyncScheduler:
    """Background daemon scheduler for periodic IMAP inbox synchronization."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        sync_runner: Callable[[Session], dict[str, Any]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._sync_runner = sync_runner or (lambda db: run_inbox_sync(db, limit=30, trigger="auto"))
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wakeup_event.clear()
            self._thread = threading.Thread(target=self._run, name="inbox-auto-sync", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            self._wakeup_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def notify(self) -> None:
        self._wakeup_event.set()

    @staticmethod
    def compute_delay(state: dict[str, Any], now_dt: datetime | None = None) -> float:
        interval = int(state.get("interval_seconds") or MIN_SYNC_INTERVAL_SECONDS)
        interval = max(MIN_SYNC_INTERVAL_SECONDS, min(MAX_SYNC_INTERVAL_SECONDS, interval))
        last_dt = _parse_iso(state.get("last_sync_at"))
        if last_dt is None:
            return 0.0
        current = now_dt or datetime.now(timezone.utc)
        elapsed = (current - last_dt).total_seconds()
        return max(0.0, interval - elapsed)

    def _load_state(self) -> dict[str, Any]:
        with self._session_factory() as db:
            return get_sync_settings(db)

    def _execute_once(self) -> float:
        with self._session_factory() as db:
            before = get_sync_settings(db).get("last_sync_at")
            try:
                self._sync_runner(db)
            except RuntimeError as exc:
                logger.info("Scheduled inbox sync skipped: %s", exc)
                return 5.0
            except Exception as exc:
                logger.warning("Scheduled inbox sync failed: %s", exc)
            after = get_sync_settings(db).get("last_sync_at")
            if after == before:
                return float(MIN_SYNC_INTERVAL_SECONDS)
            return 0.0

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._wakeup_event.clear()
            try:
                state = self._load_state()
            except Exception as exc:
                logger.warning("Failed to load inbox sync settings: %s", exc)
                self._wakeup_event.wait(timeout=MIN_SYNC_INTERVAL_SECONDS)
                continue

            if not state.get("enabled"):
                self._wakeup_event.wait()
                continue

            delay = self.compute_delay(state)
            if delay > 0:
                woken = self._wakeup_event.wait(timeout=delay)
                if woken:
                    continue

            if self._stop_event.is_set():
                break

            cooldown = self._execute_once()
            if cooldown > 0 and not self._stop_event.is_set():
                self._wakeup_event.wait(timeout=cooldown)
