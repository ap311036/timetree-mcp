"""Bounded process-local polling. Callback success means transport write, not receipt.

Baselines and pending notifications survive stop/start, but not process exit.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

MAX_SNAPSHOT_EVENTS = 20_000
MAX_SNAPSHOT_BYTES = 8_000_000
MAX_SNAPSHOT_NODES = 200_000
MAX_JSON_DEPTH = 32
MAX_CALENDARS = 512
MAX_BASELINE_SCOPES = 16
MAX_BASELINE_BYTES = 8_000_000
MAX_PENDING_BATCHES = 100
MAX_PENDING_BYTES = 8_000_000
STOP_TIMEOUT_SECONDS = 2


class NotificationNotSent(RuntimeError):
    """The callback guarantees that no transport write was attempted."""


class NotificationDeliveryUncertain(RuntimeError):
    """A transport write may have occurred; this batch must not be retried."""


class _MonitorFailure(ValueError):
    """A fixed, non-sensitive error code safe to expose through MCP."""


class MonitorReader(Protocol):
    def read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return events, calendars, and an explicit sync_complete=true marker."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _MonitorFailure("monitor_snapshot_identity_invalid")
    result = str(value)
    if not result or result != result.strip() or len(result) > 256:
        raise _MonitorFailure("monitor_snapshot_identity_invalid")
    return result


class MonitorManager:
    def __init__(self, reader: MonitorReader,
                 notification_sink: Callable[[dict[str, Any]], None] | None = None):
        self.reader = reader
        self.notification_sink = notification_sink
        self._lock = threading.RLock()
        # Non-reentrant: callback recursion and concurrent polls fail immediately.
        self._poll_gate = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshots: dict[Any, dict[Any, tuple[str, dict[str, Any]]]] = {}
        self._scope_calendars: dict[Any, frozenset[str]] = {}
        self._snapshot_bytes: dict[Any, int] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._pending_bytes = 0
        self._calendar_id: str | None = None
        self._interval_seconds = 300
        self._last_poll: str | None = None
        self._last_success: str | None = None
        self._last_change: str | None = None
        self._last_error: str | None = None
        self._last_check_complete: bool | None = None
        self._baseline_advanced = False
        self._notifications_sent = 0

    def set_notification_sink(self, sink: Callable[[dict[str, Any]], None] | None) -> None:
        with self._lock:
            self.notification_sink = sink

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            batches = [{"batch_id": key, "calendar_id": value["notification"]["calendar_id"],
                        "state": value["state"]} for key, value in self._pending.items()]
            counts = {state: sum(item["state"] == state for item in batches)
                      for state in ("not_sent", "unknown", "in_flight")}
            return {
                "active": active, "stopping": active and self._stop_event.is_set(),
                "poll_in_flight": self._poll_gate.locked(), "calendar_id": self._calendar_id,
                "interval_seconds": self._interval_seconds,
                "last_poll": self._last_poll, "last_success": self._last_success,
                "last_change": self._last_change, "notifications_sent": self._notifications_sent,
                "transport_writes_succeeded": self._notifications_sent,
                "delivery_semantics": "transport_write_only", "last_error": self._last_error,
                "last_check_complete": self._last_check_complete,
                "baseline_advanced": self._baseline_advanced,
                "baseline_initialized": bool(self._snapshots),
                "baseline_scope_count": len(self._snapshots), "state_storage": "memory_only",
                "pending_count": len(batches), "pending_not_sent_count": counts["not_sent"],
                "pending_unknown_count": counts["unknown"],
                "pending_in_flight_count": counts["in_flight"],
                "delivery_blocked": bool(counts["unknown"]), "pending_batches": batches,
            }

    def start(self, calendar_id: str | None = None, interval_seconds: int = 300) -> dict[str, Any]:
        if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool):
            raise ValueError("interval_seconds must be an integer")
        if not 30 <= interval_seconds <= 3600:
            raise ValueError("interval_seconds must be between 30 and 3600")
        calendar_id = _identity(calendar_id) if calendar_id is not None else None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("TimeTree monitor is already active; stop it before starting another")
            if self._poll_gate.locked():
                raise ValueError("monitor_poll_busy")
            self._calendar_id = calendar_id
            self._interval_seconds = interval_seconds
            # Each run has its own token; a timed-out run cannot be revived.
            self._stop_event = threading.Event()
            self._thread = threading.Thread(target=self._run,
                                            args=(self._stop_event, calendar_id, interval_seconds),
                                            name="timetree-monitor", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            self._stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STOP_TIMEOUT_SECONDS)
        with self._lock:
            if self._thread is thread and thread is not None and not thread.is_alive():
                self._thread = None
        return self.status()

    def shutdown(self) -> None:
        self.stop()

    def poll_once(self, calendar_id: str | None = None, *, _stop: threading.Event | None = None) -> dict[str, Any]:
        if not self._poll_gate.acquire(blocking=False):
            raise ValueError("monitor_poll_busy")
        try:
            with self._lock:
                self._last_poll = _now()
                self._last_check_complete = False
                self._baseline_advanced = False
            calendar_id = _identity(calendar_id) if calendar_id is not None else None
            arguments = {"calendar_id": calendar_id} if calendar_id is not None else {}
            try:
                payload = self.reader.read("sync_events", arguments)
            except Exception:
                raise _MonitorFailure("monitor_read_failed") from None
            current, calendars, size = self._snapshot(payload, calendar_id)
            with self._lock:
                previous = self._snapshots.get(calendar_id)
                if previous is not None and self._scope_calendars[calendar_id] != calendars:
                    raise _MonitorFailure("monitor_calendar_scope_changed")
                if (previous is None and len(self._snapshots) >= MAX_BASELINE_SCOPES
                        or sum(self._snapshot_bytes.values()) - self._snapshot_bytes.get(calendar_id, 0)
                        + size > MAX_BASELINE_BYTES):
                    raise _MonitorFailure("monitor_baseline_capacity_exceeded")
            changes, added, updated, removed = self._changes(previous, current)
            now = _now()
            notification = None
            notification_bytes = 0
            if changes:
                notification = {"type": "timetree_change", "batch_id": uuid.uuid4().hex,
                                "calendar_id": calendar_id, "changes": changes, "detected_at": now}
                notification_json = json.dumps(notification, ensure_ascii=False)
                notification_bytes = len(notification_json.encode("utf-8"))
                if notification_bytes > MAX_PENDING_BYTES:
                    raise _MonitorFailure("monitor_pending_capacity_exceeded")
                # Neither the reader, returned poll result, nor callback owns the
                # retained queue payload or its nested objects.
                notification = json.loads(notification_json)
            self._check_stopped(_stop)
            # A full queue can recover known-unsent batches after all source gates
            # pass. Partial/invalid reads never initiate notification retries.
            attempted: set[str] = set()
            self._dispatch(attempted, _stop)
            with self._lock:
                self._check_stopped(_stop)
                if notification is not None:
                    if (len(self._pending) >= MAX_PENDING_BATCHES
                            or self._pending_bytes + notification_bytes > MAX_PENDING_BYTES):
                        raise _MonitorFailure("monitor_pending_capacity_exceeded")
                    # Capacity, queue, and baseline commit under the same lock.
                    self._pending[notification["batch_id"]] = {
                        "notification": notification, "state": "not_sent", "bytes": notification_bytes,
                    }
                    self._pending_bytes += notification_bytes
                    self._last_change = now
                self._snapshots[calendar_id] = current
                self._scope_calendars[calendar_id] = calendars
                self._snapshot_bytes[calendar_id] = size
                self._last_success = now
                self._last_check_complete = True
                self._baseline_advanced = True
                self._last_error = None
            self._dispatch(attempted, _stop)
            return {"primed": previous is None, "event_count": len(current),
                    "added": added, "updated": updated, "removed": removed, "changes": changes,
                    "check_complete": True, "baseline_advanced": True,
                    "batch_id": notification["batch_id"] if notification else None,
                    "pending_count": self.status()["pending_count"]}
        except _MonitorFailure as error:
            with self._lock:
                self._last_error = str(error)
            raise
        except Exception:
            with self._lock:
                self._last_error = "monitor_snapshot_invalid"
            raise _MonitorFailure("monitor_snapshot_invalid") from None
        finally:
            self._poll_gate.release()

    @staticmethod
    def _check_stopped(stop: threading.Event | None) -> None:
        if stop is not None and stop.is_set():
            raise _MonitorFailure("monitor_poll_stopped")

    def _dispatch(self, attempted: set[str], stop: threading.Event | None) -> None:
        while True:
            with self._lock:
                if not self._pending or stop is not None and stop.is_set():
                    return
                batch_id = next(iter(self._pending))
                batch = self._pending[batch_id]
                if batch["state"] == "unknown":
                    self._last_error = "monitor_notification_delivery_unknown"
                    return
                if batch_id in attempted:
                    self._last_error = "monitor_notification_not_sent"
                    return
                sink = self.notification_sink
                if sink is None:
                    self._last_error = "monitor_notification_sink_unavailable"
                    return
                outgoing = copy.deepcopy(batch["notification"])
                attempted.add(batch_id)
                batch["state"] = "in_flight"
            try:
                sink(outgoing)
            except NotificationNotSent:
                with self._lock:
                    batch["state"] = "not_sent"
                    self._last_error = "monitor_notification_not_sent"
                return
            except BaseException as error:
                # Interrupts also leave a potentially-written batch held.
                with self._lock:
                    batch["state"] = "unknown"
                    self._last_error = "monitor_notification_delivery_unknown"
                if not isinstance(error, Exception):
                    raise
                return
            with self._lock:
                self._pending_bytes -= batch["bytes"]
                del self._pending[batch_id]
                self._notifications_sent += 1
                self._last_error = None

    def _run(self, stop: threading.Event, calendar_id: str | None, interval_seconds: int) -> None:
        try:
            while not stop.is_set():
                try:
                    self.poll_once(calendar_id, _stop=stop)
                except Exception:  # Safe error codes are recorded by poll_once.
                    pass
                stop.wait(interval_seconds)
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    @staticmethod
    def _snapshot(payload: Any, calendar_id: str | None):
        if (not isinstance(payload, dict) or payload.get("sync_complete") is not True
                or payload.get("authenticated", True) is not True
                or any(payload.get(key) for key in ("error", "truncated", "has_more", "incomplete",
                       "skipped_calendars", "incomplete_calendars", "skipped_unreadable_events"))):
            raise _MonitorFailure("monitor_snapshot_incomplete")
        events, calendars = payload.get("events"), payload.get("calendars")
        if not isinstance(events, list) or not isinstance(calendars, list):
            raise _MonitorFailure("monitor_snapshot_incomplete")
        if len(events) > MAX_SNAPSHOT_EVENTS or len(calendars) > MAX_CALENDARS:
            raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
        calendar_ids = set()
        for calendar in calendars:
            if not isinstance(calendar, dict):
                raise _MonitorFailure("monitor_snapshot_identity_invalid")
            identifier = _identity(calendar.get("id"))
            if identifier in calendar_ids:
                raise _MonitorFailure("monitor_snapshot_duplicate_identity")
            calendar_ids.add(identifier)
        if calendar_id is not None:
            if calendar_id not in calendar_ids:
                raise _MonitorFailure("monitor_calendar_scope_changed")
            calendar_ids = {calendar_id}
        if payload.get("calendar_id") is not None and _identity(payload["calendar_id"]) != calendar_id:
            raise _MonitorFailure("monitor_calendar_scope_changed")
        snapshot, aliases = {}, set()
        size = nodes = text_bytes = 0
        for event in events:
            if not isinstance(event, dict):
                raise _MonitorFailure("monitor_snapshot_identity_invalid")
            if (not isinstance(event.get("start"), str) or not event["start"]
                    or not isinstance(event.get("end"), str) or not event["end"]
                    or any(key in event and not isinstance(event[key], str)
                           for key in ("title", "calendar_name"))):
                raise _MonitorFailure("monitor_snapshot_invalid")
            cid = _identity(event.get("calendar_id"))
            if cid not in calendar_ids:
                raise _MonitorFailure("monitor_calendar_scope_changed")
            event_id = _identity(event.get("uuid") if event.get("uuid") is not None else event.get("id"))
            for key in ("uuid", "id"):
                if event.get(key) is not None:
                    alias = (cid, key, _identity(event[key]))
                    if alias in aliases:
                        raise _MonitorFailure("monitor_snapshot_duplicate_identity")
                    aliases.add(alias)
            # Bound nested input before serialization and immutable copying.
            stack = [(event, 0)]
            while stack:
                value, depth = stack.pop()
                nodes += 1
                if nodes > MAX_SNAPSHOT_NODES or depth > MAX_JSON_DEPTH:
                    raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
                if isinstance(value, str):
                    if len(value) > MAX_SNAPSHOT_BYTES:
                        raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
                    text_bytes += len(value.encode("utf-8"))
                    if text_bytes > MAX_SNAPSHOT_BYTES:
                        raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
                elif isinstance(value, dict):
                    if (2 * len(value) > MAX_SNAPSHOT_NODES - nodes - len(stack)
                            or any(not isinstance(key, str) for key in value)):
                        raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
                    stack.extend((item, depth + 1) for pair in value.items() for item in pair)
                elif isinstance(value, list):
                    if len(value) > MAX_SNAPSHOT_NODES - nodes - len(stack):
                        raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
                    stack.extend((item, depth + 1) for item in value)
                elif value is not None and not isinstance(value, (bool, int, float)):
                    raise _MonitorFailure("monitor_snapshot_invalid")
                elif isinstance(value, float) and not math.isfinite(value):
                    raise _MonitorFailure("monitor_snapshot_invalid")
            serialized = json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False)
            encoded = serialized.encode("utf-8")
            size += len(encoded)
            if size > MAX_SNAPSHOT_BYTES:
                raise _MonitorFailure("monitor_snapshot_capacity_exceeded")
            identity = (cid, event_id)
            if identity in snapshot:
                raise _MonitorFailure("monitor_snapshot_duplicate_identity")
            snapshot[identity] = (hashlib.sha256(encoded).hexdigest(), json.loads(serialized))
        return snapshot, frozenset(calendar_ids), size

    @classmethod
    def _changes(cls, previous, current):
        if previous is None:
            return [], 0, 0, 0
        added = sorted(current.keys() - previous.keys())
        removed = sorted(previous.keys() - current.keys())
        updated = sorted(key for key in current.keys() & previous.keys() if current[key][0] != previous[key][0])
        changes = [cls._change("added", current[key][1]) for key in added]
        changes += [cls._change("updated", current[key][1]) for key in updated]
        changes += [cls._change("removed", previous[key][1]) for key in removed]
        return changes, len(added), len(updated), len(removed)

    @staticmethod
    def _change(kind: str, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": kind,
            "event_id": str(event.get("uuid") if event.get("uuid") is not None else event.get("id")),
            "calendar_id": event.get("calendar_id"), "calendar_name": event.get("calendar_name"),
            "title": event.get("title", ""), "start": event.get("start"), "end": event.get("end"),
        }
