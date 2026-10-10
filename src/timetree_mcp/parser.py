"""Pure data normalization and filtering for TimeTree responses."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any


def _attributes(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes")
    if isinstance(attributes, dict):
        merged = dict(attributes)
        merged.update({key: value for key, value in raw.items() if key != "attributes"})
        return merged
    return raw


def _relationship_value(raw: dict[str, Any], name: str) -> Any:
    relationships = raw.get("relationships")
    if not isinstance(relationships, dict):
        return None
    relationship = relationships.get(name)
    if not isinstance(relationship, dict):
        return None
    data = relationship.get("data")
    if isinstance(data, dict):
        return data
    return None


def _label(raw: dict[str, Any]) -> dict[str, Any] | None:
    value = raw.get("label")
    if not isinstance(value, dict):
        value = _relationship_value(raw, "label")
    if not isinstance(value, dict):
        return None

    source = value.get("attributes") if isinstance(value.get("attributes"), dict) else value
    result: dict[str, Any] = {}
    for output_key, input_key in (("id", "id"), ("name", "name"), ("color", "color")):
        if source.get(input_key) is not None:
            result[output_key] = source[input_key]
    return result or None


def _epoch_datetime(value: int | float) -> datetime:
    seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _canonical_time(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _epoch_datetime(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, str) and value.isdigit() and len(value) >= 10:
        return _epoch_datetime(int(value)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return value


def normalize_event(raw: dict[str, Any], calendar_name: str | None = None) -> dict[str, Any]:
    """Convert a flat or JSON:API-like TimeTree event into a stable read shape."""

    fields = _attributes(raw)
    calendar = fields.get("calendar")
    calendar_id = fields.get("calendar_id")
    if calendar_id is None and isinstance(calendar, dict):
        calendar_id = calendar.get("id")
    if calendar_id is None:
        relationship = _relationship_value(raw, "calendar")
        calendar_id = relationship.get("id") if relationship else None

    start = _canonical_time(fields.get("start_at", fields.get("start")))
    end = _canonical_time(fields.get("end_at", fields.get("end")))
    if start is None:
        raise ValueError("TimeTree event is missing start_at")
    if end is None:
        end = start

    timezone_name = fields.get("start_timezone") or fields.get("end_timezone")
    result: dict[str, Any] = {
        "id": fields.get("id"),
        "uuid": fields.get("uuid"),
        "calendar_id": str(calendar_id) if calendar_id is not None else None,
        "calendar_name": calendar_name or fields.get("calendar_name"),
        "title": fields.get("title", ""),
        "all_day": bool(fields.get("all_day", False)),
        "start": start,
        "end": end,
        "timezone": timezone_name,
        "location": fields.get("location"),
        "note": fields.get("note", fields.get("description")),
        "label": _label(raw),
    }

    for key in ("category", "type", "url", "recurrences", "recurrence"):
        if fields.get(key) is not None:
            result[key] = fields[key]
    for key in ("label_id", "attendees", "alerts", "attachment", "deactivated_at"):
        if fields.get(key) is not None:
            result[key] = fields[key]
    for key in ("created_at", "updated_at"):
        if fields.get(key) is not None:
            result[key] = _canonical_time(fields[key])

    return {key: value for key, value in result.items() if value is not None}


def _parse_instant(value: Any) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _epoch_datetime(value)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid event datetime: {value!r}")
    if value.isdigit() and len(value) >= 10:
        return _epoch_datetime(int(value))
    if len(value) == 10:
        parsed = datetime.combine(date.fromisoformat(value), time.min)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_date(value: str, field_name: str) -> datetime:
    try:
        return datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc


def _event_text(event: dict[str, Any]) -> str:
    label = event.get("label")
    label_name = label.get("name", "") if isinstance(label, dict) else ""
    return " ".join(
        str(event.get(key, ""))
        for key in ("title", "note", "location", "calendar_name", "calendar_id")
    ) + " " + label_name


def query_events(
    events: list[dict[str, Any]],
    *,
    start_date: str,
    end_date: str,
    calendar_id: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return visible events overlapping the half-open date interval."""

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer from 1 to 500")
    start_bound = _parse_date(start_date, "start_date")
    end_bound = _parse_date(end_date, "end_date")
    if start_bound >= end_bound:
        raise ValueError("end_date must be after start_date")

    needle = query.casefold().strip() if query else None
    selected: list[tuple[datetime, str, dict[str, Any]]] = []
    for event in events:
        category = event.get("category")
        if category == 2 or str(category).casefold() in {"2", "memo"}:
            continue
        if str(event.get("type", "")).casefold() == "birthday":
            continue
        if calendar_id is not None and event.get("calendar_id") != str(calendar_id):
            continue
        if needle and needle not in _event_text(event).casefold():
            continue

        event_start = _parse_instant(event.get("start"))
        event_end = _parse_instant(event.get("end", event.get("start")))
        if event_end < event_start:
            event_end = event_start
        overlaps = event_start < end_bound and event_end > start_bound
        if not overlaps and event_start == event_end:
            overlaps = start_bound <= event_start < end_bound
        if overlaps:
            selected.append((event_start, str(event.get("id", "")), event))

    selected.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in selected[:limit]]
