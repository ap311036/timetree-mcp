"""Validation and payload construction for TimeTree mutating operations."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


def require_confirmation(arguments: dict[str, Any]) -> None:
    if arguments.get("confirm") is not True:
        raise ValueError("confirm must be true before a TimeTree write operation")


def calendar_id_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("calendar_id must be a string or integer")
    result = str(value).strip()
    if not result:
        raise ValueError("calendar_id must not be empty")
    return result


def _timezone(name: str | None) -> timezone | ZoneInfo:
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception as exc:  # noqa: BLE001 - surface a stable input error
        raise ValueError(f"Unknown timezone: {name}") from exc


def to_epoch_millis(value: Any, timezone_name: str = "UTC") -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("time must be an ISO-8601 string or Unix milliseconds")

    text = value.strip()
    try:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), time.min)
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone(timezone_name))
    return int(parsed.timestamp() * 1000)


def _attachment(arguments: dict[str, Any]) -> dict[str, Any] | None:
    attachment: dict[str, Any] = {}
    if "checklist" in arguments:
        attachment["checklist"] = arguments["checklist"]
    if "virtual_user_attendees" in arguments:
        attachment["virtual_user_attendees"] = arguments["virtual_user_attendees"]
    return attachment or None


def build_create_event_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    calendar_id_value(arguments.get("calendar_id"))
    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")

    start_timezone = arguments.get("start_timezone", "UTC")
    end_timezone = arguments.get("end_timezone", start_timezone)
    if "start_at" not in arguments or "end_at" not in arguments:
        raise ValueError("start_at and end_at are required")

    payload: dict[str, Any] = {
        "title": title,
        "all_day": bool(arguments.get("all_day", False)),
        "start_at": to_epoch_millis(arguments["start_at"], start_timezone),
        "start_timezone": start_timezone,
        "end_at": to_epoch_millis(arguments["end_at"], end_timezone),
        "end_timezone": end_timezone,
        "category": arguments.get("category", 1),
        "attendees": arguments.get("attendees", []),
        "recurrences": arguments.get("recurrences", []),
        "alerts": arguments.get("alerts", []),
        "file_uuids": arguments.get("file_uuids", []),
    }
    for key in ("label_id", "note", "location", "url"):
        if key in arguments:
            payload[key] = arguments[key]
    attachment = _attachment(arguments)
    if attachment is not None:
        payload["attachment"] = attachment
    return payload


def build_update_event_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    calendar_id_value(arguments.get("calendar_id"))
    event_uuid = arguments.get("event_uuid")
    if not isinstance(event_uuid, str) or not event_uuid.strip():
        raise ValueError("event_uuid must be a non-empty string")

    payload: dict[str, Any] = {}
    start_timezone = arguments.get("start_timezone", "UTC")
    end_timezone = arguments.get("end_timezone", start_timezone)
    for key in ("title", "all_day", "start_timezone", "end_timezone", "label_id", "category", "note", "location", "url", "attendees", "recurrences", "alerts", "file_uuids"):
        if key in arguments:
            payload[key] = arguments[key]
    if "start_at" in arguments:
        payload["start_at"] = to_epoch_millis(arguments["start_at"], start_timezone)
    if "end_at" in arguments:
        payload["end_at"] = to_epoch_millis(arguments["end_at"], end_timezone)
    attachment = _attachment(arguments)
    if attachment is not None:
        payload["attachment"] = attachment
    if not payload:
        raise ValueError("at least one event field is required to update")
    return payload


def build_memo_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    calendar_id_value(arguments.get("calendar_id"))
    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    memo_date = arguments.get("date") or datetime.now(timezone.utc).date().isoformat()
    start_at = to_epoch_millis(memo_date, "UTC")
    payload: dict[str, Any] = {
        "title": title,
        "all_day": True,
        "start_at": start_at,
        "start_timezone": "UTC",
        "end_at": start_at,
        "end_timezone": "UTC",
        "category": 2,
        "attendees": [],
        "recurrences": [],
        "alerts": [],
        "file_uuids": [],
    }
    for key in ("label_id", "note", "location", "url"):
        if key in arguments:
            payload[key] = arguments[key]
    attachment = _attachment(arguments)
    if attachment is not None:
        payload["attachment"] = attachment
    return payload


def build_memo_update_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = build_update_event_payload(arguments)
    payload["category"] = 2
    return payload
