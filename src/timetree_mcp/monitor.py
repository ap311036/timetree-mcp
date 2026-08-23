"""Background polling and MCP notification support for TimeTree changes."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


class MonitorReader(Protocol):
    def read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Read a complete, normalized event snapshot."""


class MonitorManager:
    def __init__(
        self,
        reader: MonitorReader,
        notification_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.reader = reader
        self.notification_sink = notification_sink
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshots: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {}
        self._calendar_id: str | None = None
        self._interval_seconds = 300
        self._last_poll: str | None = None
        self._last_change: str | None = None
        self._last_error: str | None = None
        self._notifications_sent = 0

    def set_notification_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        with self._lock:
            self.notification_sink = sink

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._thread is not None and self._thread.is_alive(),
                "calendar_id": self._calendar_id,
                "interval_seconds": self._interval_seconds,
                "last_poll": self._last_poll,
                "last_change": self._last_change,
                "notifications_sent": self._notifications_sent,
                "last_error": self._last_error,
            }

    def start(self, calendar_id: str | None = None, interval_seconds: int = 300) -> dict[str, Any]:
        if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool):
            raise ValueError("interval_seconds must be an integer")
        if not 30 <= interval_seconds <= 3600:
            raise ValueError("interval_seconds must be between 30 and 3600")
        normalized_calendar_id = str(calendar_id) if calendar_id is not None else None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("TimeTree monitor is already active; stop it before starting another")
            self._calendar_id = normalized_calendar_id
            self._interval_seconds = interval_seconds
            self._last_error = None
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="timetree-monitor", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        with self._lock:
            self._thread = None
        return self.status()

    def shutdown(self) -> None:
        self.stop()

    def poll_once(self, calendar_id: str | None = None) -> dict[str, Any]:
        scope = str(calendar_id) if calendar_id is not None else "__all__"
        arguments = {"calendar_id": str(calendar_id)} if calendar_id is not None else {}
        payload = self.reader.read("sync_events", arguments)
        events = payload.get("events") or []
        current = self._snapshot(events)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            previous = self._snapshots.get(scope)
            self._snapshots[scope] = current
            self._last_poll = now
            self._last_error = None

        if previous is None:
            return {"primed": True, "event_count": len(current), "added": 0, "updated": 0, "removed": 0}

        changes: list[dict[str, Any]] = []
        added_ids = sorted(set(current) - set(previous))
        removed_ids = sorted(set(previous) - set(current))
        shared_ids = sorted(set(current) & set(previous))
        for event_id in added_ids:
            changes.append(self._change("added", current[event_id][1]))
        for event_id in shared_ids:
            if current[event_id][0] != previous[event_id][0]:
                changes.append(self._change("updated", current[event_id][1]))
        for event_id in removed_ids:
            changes.append(self._change("removed", previous[event_id][1]))

        added = len(added_ids)
        updated = sum(1 for event_id in shared_ids if current[event_id][0] != previous[event_id][0])
        removed = len(removed_ids)
        if changes:
            notification = {
                "type": "timetree_change",
                "calendar_id": calendar_id,
                "changes": changes,
                "detected_at": now,
            }
            sink = self.notification_sink
            if sink is not None:
                sink(notification)
            with self._lock:
                self._last_change = now
                self._notifications_sent += 1

        return {
            "primed": False,
            "event_count": len(current),
            "added": added,
            "updated": updated,
            "removed": removed,
            "changes": changes,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once(self._calendar_id)
            except Exception as exc:  # noqa: BLE001 - monitoring must survive transient errors
                with self._lock:
                    self._last_error = str(exc)
                    self._last_poll = datetime.now(timezone.utc).isoformat()
            self._stop_event.wait(self._interval_seconds)

    @staticmethod
    def _snapshot(events: list[dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
        snapshot: dict[str, tuple[str, dict[str, Any]]] = {}
        for event in events:
            event_id = str(event.get("uuid") or event.get("id") or "")
            if not event_id:
                continue
            serialized = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            snapshot[event_id] = (fingerprint, event)
        return snapshot

    @staticmethod
    def _change(kind: str, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": kind,
            "event_id": str(event.get("uuid") or event.get("id") or ""),
            "calendar_id": event.get("calendar_id"),
            "calendar_name": event.get("calendar_name"),
            "title": event.get("title", ""),
            "start": event.get("start"),
            "end": event.get("end"),
        }
