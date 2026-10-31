"""Pure, fail-closed rules for moving one generated TimeTree occurrence.

Only timed events in the five explicitly supported IANA zones on or after
1980-01-01 local time are supported. ZoneInfo supplies offset rules; missing
timezone data and ambiguous local times fail closed. This module performs no
network or credential access; ZoneInfo may read the installed timezone database.
The child payload deliberately omits recurrences, as the web client does when
parent_id is present. Callers must journal and verify the two non-atomic writes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")
MIN_SUPPORTED_DATE = "1980-01-01"
MIN_SUPPORTED_MS = 315446400000  # A permissive UTC bound; local dates are checked separately.
SUPPORTED_TIMEZONES = frozenset(("Asia/Taipei", "Asia/Tokyo", "Asia/Kolkata", "America/New_York", "Europe/London"))
DST_TIMEZONES = frozenset(("America/New_York", "Europe/London"))
MAX_TIMEZONE_VALIDATION_DAYS = 3660
DAY_MS = 86400000
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_RULE_LINES = 100
MAX_RULE_LENGTH = 10000
MAX_RULE_NUMBER = 100000
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}")
TAIPEI_INPUT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00")
OCCURRENCE_INPUT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-](?:[01]\d|2[0-3]):[0-5]\d")
RAW_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)")
WEEKDAYS = {name: index for index, name in enumerate(("MO", "TU", "WE", "TH", "FR", "SA", "SU"))}
RAW_FIELDS = frozenset((
    "id", "primary_id", "uuid", "calendar_id", "category", "type", "author_id", "author_type",
    "title", "all_day", "start_at", "end_at", "start_timezone", "end_timezone", "label_id",
    "location", "location_lat", "location_lon", "url", "note", "description", "lunar",
    "attendees", "recurrences", "recurring_uuid", "alerts", "parent_id", "link_object_id",
    "link_object_id_string", "row_order", "attachment", "like_count", "files", "deactivated_at",
    "pinned_at", "updated_at", "created_at", "recur_start_at", "recur_end_at", "until_at",
))
ATTACHMENT_FIELDS = frozenset(("url", "lunar", "day_count", "checklist", "virtual_user_attendees", "ogp"))
FILE_FIELDS = frozenset(("uuid", "type", "original_file_name", "attached_at", "expire_at"))
EMPTY_ONLY_FIELDS = ("lunar", "link_object_id", "link_object_id_string", "row_order", "pinned_at", "deactivated_at")


def _fail(code: str = "unsupported_occurrence_shape") -> None:
    raise ValueError(code)


def _integer(value: Any) -> bool:
    return (type(value) is int and abs(value) <= MAX_SAFE_INTEGER) or (
        type(value) is float and math.isfinite(value) and value.is_integer() and abs(value) <= MAX_SAFE_INTEGER
    )


def _empty(value: Any) -> bool:
    return value is None or value is False or value == 0 or value == "" or value == [] or value == {}


def canonical(value: Any) -> str:
    """Return deterministic JSON, rejecting non-JSON values and nonfinite numbers."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                _fail("invalid_canonical_value")
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, bool, int, float):
            _fail("invalid_canonical_value")

    try:
        check(value)
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result.encode("utf-8")
        return result
    except (TypeError, ValueError, RecursionError):
        _fail("invalid_canonical_value")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _datetime_ms(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - epoch
    return delta.days * DAY_MS + delta.seconds * 1000 + delta.microseconds // 1000


def _ms_datetime(value: int, zone: tzinfo = timezone.utc) -> datetime:
    try:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)).astimezone(zone)
    except (OverflowError, ValueError):
        _fail("occurrence_date_out_of_range")


def _exact_ms(value: Any) -> int:
    if not _integer(value) or int(value) % 1000:
        _fail("invalid_occurrence_time")
    result = int(value)
    if result < MIN_SUPPORTED_MS:
        _fail("occurrence_date_out_of_range")
    _ms_datetime(result)
    return result


def parse_taipei(value: Any) -> int:
    """Parse the public occurrence input: exact seconds with an explicit +08:00."""
    if not isinstance(value, str) or not TAIPEI_INPUT.fullmatch(value):
        _fail("invalid_occurrence_time")
    return parse_occurrence_time(value)


def _parent_zone(parent: Any) -> ZoneInfo:
    if not isinstance(parent, dict):
        _fail()
    name = parent.get("start_timezone")
    if not isinstance(name, str) or name not in SUPPORTED_TIMEZONES:
        _fail("unsupported_occurrence_timezone")
    if parent.get("end_timezone") != name:
        _fail("occurrence_timezone_mismatch")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        _fail("occurrence_timezone_data_unavailable")


def _unique_local(wall: datetime, zone: ZoneInfo) -> datetime:
    """Resolve a wall time only when both fold candidates identify one instant."""
    if wall.date() < datetime(1980, 1, 1).date():
        _fail("occurrence_date_out_of_range")
    instants: dict[int, datetime] = {}
    try:
        for fold in (0, 1):
            candidate = wall.replace(tzinfo=zone, fold=fold)
            utc = candidate.astimezone(timezone.utc)
            if utc.astimezone(zone).replace(tzinfo=None) == wall:
                instants[_datetime_ms(utc)] = candidate
    except (OverflowError, ValueError):
        _fail("occurrence_date_out_of_range")
    if not instants:
        _fail("occurrence_local_time_nonexistent")
    if len(instants) != 1:
        _fail("occurrence_local_time_ambiguous")
    return next(iter(instants.values()))


def _safe_instant(at_ms: int, zone: ZoneInfo) -> datetime:
    local = _ms_datetime(_exact_ms(at_ms), zone)
    _unique_local(local.replace(tzinfo=None), zone)
    return local


def _safe_interval(start_ms: int, duration: int, zone: ZoneInfo) -> None:
    start = _safe_instant(start_ms, zone)
    end = _safe_instant(start_ms + duration, zone)
    # Supported DST zones have at most one transition in an interval <=24h.
    # Rejecting offset changes preserves both the wall and elapsed duration.
    if start.utcoffset() != end.utcoffset():
        _fail("occurrence_interval_crosses_offset_transition")


def parse_occurrence_time(value: Any, parent: Any = None) -> int:
    """Parse explicit numeric offsets and, with a parent, verify its local zone.

    Lexical validation requires no timezone database, allowing durable operation
    replay without network or zone lookup. New dispatches must validate with the
    parent before building any write payload.
    """
    if not isinstance(value, str) or not OCCURRENCE_INPUT.fullmatch(value) or value.endswith("-00:00"):
        _fail("invalid_occurrence_time")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.date() < datetime(1980, 1, 1).date():
            _fail("occurrence_date_out_of_range")
        result = _exact_ms(_datetime_ms(parsed))
    except (OverflowError, ValueError) as exc:
        if str(exc) == "occurrence_date_out_of_range":
            raise
        _fail("invalid_occurrence_time")
    if parent is not None:
        local = _unique_local(parsed.replace(tzinfo=None), _parent_zone(parent))
        if local.utcoffset() != parsed.utcoffset():
            _fail("occurrence_timezone_offset_mismatch")
    return result


def _raw_instant(value: Any) -> int:
    # The existing raw reader accepts epoch seconds as well as milliseconds.
    if _integer(value):
        result = int(value) * 1000 if abs(value) < 100000000000 else int(value)
    elif isinstance(value, str) and re.fullmatch(r"\d{10,}", value):
        if len(value) > 16:
            _fail("invalid_occurrence_time")
        return _raw_instant(int(value))
    elif isinstance(value, str) and RAW_ISO.fullmatch(value):
        if value.endswith("-00:00"):
            _fail("invalid_occurrence_time")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            result = _datetime_ms(parsed)
        except ValueError:
            _fail("invalid_occurrence_time")
        except OverflowError:
            _fail("occurrence_date_out_of_range")
        if parsed.microsecond:
            _fail("invalid_occurrence_time")
    else:
        _fail("invalid_occurrence_time")
    return _exact_ms(result)


def _object(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in fields and not _empty(item) for key, item in value.items()):
        _fail()
    return value


def _attachment(value: Any) -> dict[str, Any]:
    source = _object(value, ATTACHMENT_FIELDS)
    result: dict[str, Any] = {}
    if not _empty(source.get("lunar")) or not _empty(source.get("day_count")):
        _fail()
    if source.get("url") is not None:
        if not isinstance(source["url"], str):
            _fail()
        result["url"] = source["url"]
    if source.get("checklist") is not None:
        if not isinstance(source["checklist"], list):
            _fail()
        result["checklist"] = []
        for item in source["checklist"]:
            item = _object(item, frozenset(("title", "checked")))
            if not isinstance(item.get("title"), str) or type(item.get("checked")) is not bool:
                _fail()
            result["checklist"].append({"title": item["title"], "checked": item["checked"]})
    if source.get("virtual_user_attendees") is not None:
        values = source["virtual_user_attendees"]
        if not isinstance(values, list) or any(not isinstance(item, str) and not _integer(item) for item in values):
            _fail()
        result["virtual_user_attendees"] = deepcopy(values)
    if not _empty(source.get("ogp")):
        ogp = _object(source["ogp"], frozenset(("crawl_target_url", "title", "url", "description", "image", "favicon")))
        if any(not isinstance(item, str) for item in ogp.values()):
            _fail()
        result["ogp"] = deepcopy(ogp)
    return result


def _file_uuids(value: Any) -> list[str]:
    if not isinstance(value, list):
        _fail()
    result = []
    for item in value:
        item = _object(item, FILE_FIELDS)
        if not isinstance(item.get("uuid"), str) or not IDENTIFIER.fullmatch(item["uuid"]):
            _fail()
        result.append(item["uuid"])
    if len(set(result)) != len(result):
        _fail()
    return result


def _coordinate(value: Any) -> float:
    if type(value) is bool or value == "" or not isinstance(value, (str, int, float)):
        _fail()
    try:
        result = float(value)
    except (ValueError, OverflowError):
        _fail()
    if not math.isfinite(result):
        _fail()
    return result


def _calendar_id(value: Any) -> str:
    if type(value) not in (str, int) or not re.fullmatch(r"\d+", str(value)):
        _fail()
    return str(value)


def _build_payload(parent: Any, new_start_ms: Any) -> dict[str, Any]:
    if not isinstance(parent, dict):
        _fail()
    canonical(parent)
    _object(parent, RAW_FIELDS)
    if not isinstance(parent.get("id"), str) or not IDENTIFIER.fullmatch(parent["id"]) or parent["id"] != parent.get("uuid"):
        _fail()
    _calendar_id(parent.get("calendar_id"))
    if parent.get("all_day") is not False or type(parent.get("category")) is not int or parent["category"] != 1:
        _fail()
    if parent.get("type") is not None and (type(parent["type"]) is not int or parent["type"] != 0):
        _fail()
    if any(not _empty(parent.get(key)) for key in (*EMPTY_ONLY_FIELDS, "parent_id")):
        _fail()
    if not _empty(parent.get("recurring_uuid")) and parent["recurring_uuid"] != parent["uuid"]:
        _fail()
    if not isinstance(parent.get("title"), str) or not parent["title"].strip():
        _fail()
    zone = _parent_zone(parent)
    start = _raw_instant(parent.get("start_at"))
    duration = _raw_instant(parent.get("end_at")) - start
    new_start_ms = _exact_ms(new_start_ms)
    if not 0 < duration <= DAY_MS or duration % 1000:
        _fail("unsupported_occurrence_duration")
    _safe_interval(start, duration, zone)
    _safe_interval(new_start_ms, duration, zone)
    for key in ("attendees", "alerts"):
        if not isinstance(parent.get(key), list) or any(not _integer(item) for item in parent[key]):
            _fail()
    if not _integer(parent.get("label_id")):
        _fail()
    payload = {
        "title": parent["title"], "all_day": False, "start_at": new_start_ms,
        "end_at": new_start_ms + duration, "start_timezone": zone.key, "end_timezone": zone.key,
        "label_id": parent["label_id"], "category": 1, "attendees": list(parent["attendees"]),
        "alerts": list(parent["alerts"]), "parent_id": parent["id"], "silent": True,
        "attachment": _attachment(parent.get("attachment")),
    }
    for key in ("note", "location"):
        if parent.get(key) is not None:
            if not isinstance(parent[key], str):
                _fail()
            payload[key] = parent[key]
    if parent.get("description") is not None and parent["description"] != parent.get("note"):
        _fail()
    if parent.get("url") is not None:
        if not isinstance(parent["url"], str) or (parent["url"] and parent["url"] != payload["attachment"].get("url")):
            _fail()
    for key in ("location_lat", "location_lon"):
        if parent.get(key) is not None:
            payload[key] = _coordinate(parent[key])
    files = _file_uuids(parent.get("files"))
    if files:
        payload["file_uuids"] = files
    return payload


def _basic_date(value: str, *, utc: bool, zone: ZoneInfo | None = None) -> int:
    pattern = r"\d{8}T\d{6}Z" if utc else r"\d{8}T\d{6}"
    if not re.fullmatch(pattern, value):
        _fail("unsupported_recurrence_date")
    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ" if utc else "%Y%m%dT%H%M%S")
    except ValueError:
        _fail("invalid_recurrence_date")
    if utc:
        resolved = parsed.replace(tzinfo=timezone.utc)
    elif zone is not None:
        resolved = _unique_local(parsed, zone)
    else:
        _fail("unsupported_occurrence_timezone")
    return _exact_ms(_datetime_ms(resolved))


def _parse_rules(parent: dict[str, Any]) -> tuple[dict[str, Any], set[int]]:
    zone = _parent_zone(parent)
    lines = parent.get("recurrences")
    if not isinstance(lines, list) or not lines or len(lines) > MAX_RULE_LINES:
        _fail("unsupported_recurrence_shape")
    if any(not isinstance(line, str) or len(line) > MAX_RULE_LENGTH or "\n" in line or "\r" in line for line in lines):
        _fail("unsupported_recurrence_shape")
    rules: list[dict[str, str]] = []
    exclusions: set[int] = set()
    for line in lines:
        if line.startswith("RRULE:"):
            fields: dict[str, str] = {}
            for part in line[6:].split(";"):
                pair = part.split("=")
                if len(pair) != 2 or not pair[1] or pair[0] in fields:
                    _fail("unsupported_recurrence_rule")
                fields[pair[0]] = pair[1]
            rules.append(fields)
        else:
            match = re.fullmatch(r"EXDATE(?:;TZID=([^:]+))?:(.+)", line)
            if not match:
                _fail("unsupported_recurrence_property")
            if match[1] is not None and match[1] != zone.key:
                _fail("recurrence_date_timezone_mismatch")
            for item in match[2].split(","):
                if match[1] is not None and item.endswith("Z"):
                    _fail("recurrence_date_timezone_mismatch")
                exclusions.add(_basic_date(item, utc=item.endswith("Z"), zone=zone))
    if len(rules) != 1:
        _fail("unsupported_recurrence_generator")
    fields = rules[0]
    frequency = fields.get("FREQ")
    if frequency not in ("DAILY", "WEEKLY"):
        _fail("unsupported_recurrence_frequency")
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL"}
    if frequency == "WEEKLY":
        allowed.update(("BYDAY", "WKST"))
    if set(fields) - allowed:
        _fail("unsupported_recurrence_rule")
    if "COUNT" in fields and "UNTIL" in fields:
        _fail("recurrence_count_until_conflict")
    parsed: dict[str, Any] = {"frequency": frequency, "interval": 1, "count": None, "until": None}
    for name in ("INTERVAL", "COUNT"):
        if name in fields:
            if not re.fullmatch(r"\d{1,6}", fields[name]) or not 1 <= int(fields[name]) <= MAX_RULE_NUMBER:
                _fail("invalid_recurrence_count_or_interval")
            parsed[name.lower()] = int(fields[name])
    if "UNTIL" in fields:
        until = fields["UNTIL"]
        if re.fullmatch(r"\d{8}", until):
            parsed["until"] = _basic_date(until + "T235959", utc=False, zone=zone)
        else:
            parsed["until"] = _basic_date(until, utc=True)
    if frequency == "WEEKLY":
        days = fields.get("BYDAY", "").split(",") if "BYDAY" in fields else None
        if days is not None and (len(set(days)) != len(days) or any(day not in WEEKDAYS for day in days)):
            _fail("unsupported_recurrence_rule")
        week_start = fields.get("WKST", "MO")
        if week_start not in WEEKDAYS:
            _fail("unsupported_recurrence_rule")
        parsed["week_start"] = WEEKDAYS[week_start]
        parsed["weekdays"] = [WEEKDAYS[day] for day in days] if days else None
    return parsed, exclusions


def validate_parent(parent: Any) -> None:
    if not isinstance(parent, dict):
        _fail()
    _build_payload(parent, _raw_instant(parent.get("start_at")))
    _parse_rules(parent)


def _ordinal(rule: dict[str, Any], start: datetime, at: datetime) -> int | None:
    """Return the generated local date's ordinal before exclusions, if present."""
    if rule["frequency"] == "DAILY":
        days = (at.date() - start.date()).days
        if days % rule["interval"]:
            return None
        ordinal = days // rule["interval"] + 1
    else:
        week_start = rule["week_start"]
        first_offset = (start.weekday() - week_start) % 7
        first_week = start.date() - timedelta(days=first_offset)
        days = (at.date() - first_week).days
        week_number, day_offset = divmod(days, 7)
        weekdays = rule["weekdays"] or [start.weekday()]
        offsets = sorted((day - week_start) % 7 for day in weekdays)
        if week_number % rule["interval"] or day_offset not in offsets:
            return None
        if week_number == 0:
            ordinal = sum(first_offset <= day <= day_offset for day in offsets)
        else:
            first_count = sum(day >= first_offset for day in offsets)
            full_weeks = week_number // rule["interval"] - 1
            ordinal = first_count + full_weeks * len(offsets) + sum(day <= day_offset for day in offsets)
    return ordinal


def _assert_safe_history(rule: dict[str, Any], start: datetime, at: datetime,
                         duration: int, zone: ZoneInfo) -> None:
    if zone.key not in DST_TIMEZONES:
        return
    days = (at.date() - start.date()).days
    if days > MAX_TIMEZONE_VALIDATION_DAYS:
        _fail("occurrence_timezone_validation_limit")
    # Never guess how TimeTree counts or normalizes a gap/fold recurrence.
    # Check only generated dates, including excluded dates, so COUNT cannot
    # silently shift. The explicit bound keeps work independent of long rules.
    for offset in range(days + 1):
        wall = datetime.combine(start.date() + timedelta(days=offset), start.time())
        if _ordinal(rule, start, wall) is not None:
            candidate = _unique_local(wall, zone)
            _safe_interval(_datetime_ms(candidate), duration, zone)


def assert_occurrence(parent: dict[str, Any], at_ms: Any) -> None:
    """Validate membership and bounded DST history without guessing normalization."""
    validate_parent(parent)
    at_ms = _exact_ms(at_ms)
    start_ms = _raw_instant(parent["start_at"])
    rule, exclusions = _parse_rules(parent)
    if at_ms < start_ms or at_ms in exclusions or (rule["until"] is not None and at_ms > rule["until"]):
        _fail("occurrence_missing_or_excluded")
    zone = _parent_zone(parent)
    start = _safe_instant(start_ms, zone)
    at = _safe_instant(at_ms, zone)
    if start.time() != at.time():
        _fail("occurrence_missing_or_excluded")
    ordinal = _ordinal(rule, start, at)
    if ordinal is None:
        _fail("occurrence_missing_or_excluded")
    if rule["count"] is not None and ordinal > rule["count"]:
        _fail("occurrence_missing_or_excluded")
    duration = _raw_instant(parent["end_at"]) - start_ms
    _safe_interval(at_ms, duration, zone)
    _assert_safe_history(rule, start, at, duration, zone)


def child_payload(parent: dict[str, Any], new_start_ms: Any) -> dict[str, Any]:
    validate_parent(parent)
    return _build_payload(parent, new_start_ms)


def excluded_rules(parent: dict[str, Any], at_ms: Any) -> list[str]:
    assert_occurrence(parent, at_ms)
    if len(parent["recurrences"]) >= MAX_RULE_LINES:
        _fail("recurrence_rule_limit")
    stamp = _ms_datetime(int(at_ms), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return [*parent["recurrences"], f"EXDATE:{stamp}"]


def _child_content(event: dict[str, Any], *, payload: bool = False) -> dict[str, Any]:
    result = {key: event.get(key) for key in ("title", "all_day", "start_timezone", "end_timezone", "label_id", "category", "attendees", "alerts")}
    result.update({
        "start_at": _raw_instant(event.get("start_at")), "end_at": _raw_instant(event.get("end_at")),
        "note": "" if event.get("note") is None else event["note"],
        "location": "" if event.get("location") is None else event["location"],
        "attachment": _attachment(event.get("attachment")),
        "file_uuids": event.get("file_uuids", []) if payload else _file_uuids(event.get("files")),
    })
    for key in ("location_lat", "location_lon"):
        result[key] = None if event.get(key) is None else _coordinate(event[key])
    return result


def verify_child(parent: dict[str, Any], child: Any, payload: Any) -> bool:
    try:
        if not isinstance(child, dict) or not isinstance(payload, dict):
            return False
        if canonical(payload) != canonical(child_payload(parent, payload.get("start_at"))):
            return False
        canonical(child)
        _object(child, RAW_FIELDS)
        if not isinstance(child.get("uuid"), str) or not IDENTIFIER.fullmatch(child["uuid"]):
            return False
        if child.get("id") != child["uuid"] or child["uuid"] == parent["uuid"]:
            return False
        if _calendar_id(child.get("calendar_id")) != _calendar_id(parent["calendar_id"]):
            return False
        if child.get("parent_id") != parent["id"] or child.get("recurring_uuid") != parent["uuid"] or child.get("recurrences") != []:
            return False
        if child.get("type") is not None and (type(child["type"]) is not int or child["type"] != 0):
            return False
        if any(not _empty(child.get(key)) for key in EMPTY_ONLY_FIELDS):
            return False
        if child.get("description") is not None and child["description"] != child.get("note"):
            return False
        if child.get("url") is not None:
            if not isinstance(child["url"], str) or (child["url"] and child["url"] != _attachment(child.get("attachment")).get("url")):
                return False
        return canonical(_child_content(child)) == canonical(_child_content(payload, payload=True))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def verify_parent(before: dict[str, Any], after: Any, rules: Any) -> bool:
    try:
        if not isinstance(after, dict) or not isinstance(rules, list):
            return False
        validate_parent(before)
        validate_parent(after)
        if len(rules) != len(before["recurrences"]) + 1 or rules[:-1] != before["recurrences"]:
            return False
        if not isinstance(rules[-1], str) or not rules[-1].startswith("EXDATE:"):
            return False
        added = _basic_date(rules[-1][7:], utc=True)
        if excluded_rules(before, added) != rules:
            return False
        rest = lambda event: {key: value for key, value in event.items() if key not in ("updated_at", "recurrences")}
        return canonical(after["recurrences"]) == canonical(rules) and canonical(rest(before)) == canonical(rest(after))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def corroborated_children(parent: dict[str, Any], events: Any) -> bool:
    """Require at least one linked child, with no conflicting link in the snapshot."""
    try:
        validate_parent(parent)
        if not isinstance(events, list):
            return False
        confirmed = 0
        seen_parent = False
        for event in events:
            if not isinstance(event, dict):
                return False
            if event.get("id") == parent["id"] and event.get("uuid") == parent["uuid"]:
                if seen_parent or canonical(event) != canonical(parent):
                    return False
                seen_parent = True
                continue
            if event.get("parent_id") != parent["id"] and event.get("recurring_uuid") != parent["uuid"]:
                continue
            if (event.get("parent_id") != parent["id"] or event.get("recurring_uuid") != parent["uuid"]
                    or not isinstance(event.get("id"), str) or not IDENTIFIER.fullmatch(event["id"])
                    or event["id"] != event.get("uuid") or event.get("recurrences") != []
                    or _calendar_id(event.get("calendar_id")) != _calendar_id(parent["calendar_id"])):
                return False
            confirmed += 1
        return confirmed > 0
    except (ValueError, TypeError, KeyError):
        return False
