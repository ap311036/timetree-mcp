"""Bounded, read-only projections of a conservative recurrence subset.

This module does not authorize writes. In particular, its MONTHLY/YEARLY rules
do not expand the separate occurrence editor's DAILY/WEEKLY write contract.
An unsupported series contributes diagnostics, never partially guessed rows.
"""

from __future__ import annotations

import heapq
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .occurrence_rules import (
    DAY_MS, DST_TIMEZONES, IDENTIFIER, MAX_RULE_LENGTH, MAX_RULE_LINES,
    MAX_TIMEZONE_VALIDATION_DAYS, _datetime_ms, _empty, _exact_ms,
    _ms_datetime, _parent_zone, _parse_rules, _raw_instant, _safe_instant,
    _safe_interval, _unique_local, canonical, parse_occurrence_time,
)

MAX_QUERY_DAYS = 93
MAX_CANDIDATES = 100000
MAX_SNAPSHOT_EVENTS = 20000
MAX_SNAPSHOT_BYTES = 8_000_000
MAX_TITLE_BYTES = 4096


def _fail(code: str) -> None:
    raise ValueError(code)


def _validate_range(calendar_id, start_ms, end_ms, limit, query) -> None:
    if not isinstance(calendar_id, str) or not re.fullmatch(r"[0-9]{1,40}", calendar_id):
        _fail("invalid_occurrence_calendar_id")
    _exact_ms(start_ms)
    _exact_ms(end_ms)
    if not 0 < end_ms - start_ms <= MAX_QUERY_DAYS * DAY_MS:
        _fail("invalid_occurrence_query_range")
    if type(limit) is not int or not 1 <= limit <= 500:
        _fail("invalid_occurrence_query_limit")
    if query is not None and (not isinstance(query, str) or len(query) > 500):
        _fail("invalid_occurrence_query_filter")


def validate_query_arguments(arguments: Any) -> dict[str, Any]:
    """Validate public arguments without credentials, snapshots or timezone data."""
    required = {"calendar_id", "start_at", "end_at"}
    if (not isinstance(arguments, dict) or not required <= set(arguments)
            or set(arguments) - required - {"query", "limit"}):
        _fail("invalid_occurrence_query_arguments")
    if "query" in arguments and not isinstance(arguments["query"], str):
        _fail("invalid_occurrence_query_filter")
    result = {"calendar_id": arguments["calendar_id"],
              "start_ms": parse_occurrence_time(arguments["start_at"]),
              "end_ms": parse_occurrence_time(arguments["end_at"]),
              "limit": arguments.get("limit", 100), "query": arguments.get("query")}
    _validate_range(**result)
    return result


class _Budget:
    def __init__(self):
        self.remaining = MAX_CANDIDATES

    def use(self):
        if self.remaining <= 0:
            _fail("occurrence_query_capacity_exceeded")
        self.remaining -= 1


def _snapshot(events: Any, calendar_id: str) -> dict[str, dict[str, Any]]:
    if not isinstance(events, list) or len(events) > MAX_SNAPSHOT_EVENTS:
        _fail("invalid_occurrence_query_snapshot")
    if len(canonical(events).encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        _fail("occurrence_query_capacity_exceeded")
    result = {}
    for event in events:
        if not isinstance(event, dict) or str(event.get("calendar_id")) != calendar_id:
            _fail("invalid_occurrence_query_snapshot")
        identifier = event.get("uuid")
        if (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
                or event.get("id") != identifier or identifier in result):
            _fail("event_missing_or_ambiguous")
        result[identifier] = event
    return result


def _query_rule(event: dict[str, Any], anchor: datetime):
    """Reuse the strict common syntax while keeping new frequencies read-only."""
    lines = event.get("recurrences")
    if (not isinstance(lines, list) or not 1 <= len(lines) <= MAX_RULE_LINES
            or any(not isinstance(line, str) or len(line) > MAX_RULE_LENGTH for line in lines)):
        _fail("unsupported_recurrence_shape")
    generators = [line for line in lines if line.startswith("RRULE:")]
    if len(generators) != 1:
        _fail("unsupported_recurrence_generator")
    fields = {}
    for item in generators[0][6:].split(";"):
        pair = item.split("=")
        if len(pair) != 2 or not pair[1] or pair[0] in fields:
            _fail("unsupported_recurrence_rule")
        fields[pair[0]] = pair[1]
    frequency = fields.get("FREQ")
    if frequency in {"DAILY", "WEEKLY"}:
        rule, exclusions = _parse_rules(event)
        if frequency == "WEEKLY" and rule["weekdays"] and anchor.weekday() not in rule["weekdays"]:
            _fail("recurrence_anchor_mismatch")
        if rule["until"] is not None and rule["until"] < _datetime_ms(anchor):
            _fail("recurrence_anchor_mismatch")
        return rule, exclusions
    if frequency not in {"MONTHLY", "YEARLY"}:
        _fail("unsupported_recurrence_frequency")
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYMONTHDAY"}
    if frequency == "YEARLY":
        allowed.add("BYMONTH")
        if ("BYMONTH" in fields) != ("BYMONTHDAY" in fields):
            _fail("unsupported_recurrence_rule")
    if set(fields) - allowed:
        _fail("unsupported_recurrence_rule")
    month, day = anchor.month, anchor.day
    for name, maximum in (("BYMONTH", 12), ("BYMONTHDAY", 31)):
        if name not in fields:
            continue
        if not re.fullmatch(r"\d{1,2}", fields[name]) or not 1 <= int(fields[name]) <= maximum:
            _fail("unsupported_recurrence_rule")
        value = int(fields[name])
        if value != (month if name == "BYMONTH" else day):
            _fail("recurrence_anchor_mismatch")
    common = "RRULE:FREQ=DAILY" + "".join(f";{name}={fields[name]}" for name in ("INTERVAL", "COUNT", "UNTIL") if name in fields)
    validated = {**event, "recurrences": [common if line.startswith("RRULE:") else line for line in lines]}
    rule, exclusions = _parse_rules(validated)
    if rule["until"] is not None and rule["until"] < _datetime_ms(anchor):
        _fail("recurrence_anchor_mismatch")
    rule.update(frequency=frequency, month=month, monthday=day)
    return rule, exclusions


def _wall_candidates(rule, anchor: datetime, last: datetime, budget: _Budget):
    """Yield calendar dates in order; invalid month days do not become instances."""
    frequency, interval = rule["frequency"], rule["interval"]
    naive = anchor.replace(tzinfo=None)
    period = 0
    while True:
        budget.use()
        try:
            if frequency == "DAILY":
                candidates = [naive + timedelta(days=period * interval)]
            elif frequency == "WEEKLY":
                week_start = rule["week_start"]
                first = naive - timedelta(days=(naive.weekday() - week_start) % 7)
                base = first + timedelta(weeks=period * interval)
                days = rule["weekdays"] or [naive.weekday()]
                candidates = [base + timedelta(days=offset) for offset in sorted((day - week_start) % 7 for day in days)]
            else:
                if frequency == "MONTHLY":
                    absolute = naive.year * 12 + naive.month - 1 + period * interval
                    year, zero_month = divmod(absolute, 12)
                    month = zero_month + 1
                else:
                    year, month = naive.year + period * interval, rule["month"]
                if year > 9999 or (year, month) > (last.year, last.month):
                    return
                try:
                    candidates = [naive.replace(year=year, month=month, day=rule["monthday"])]
                except ValueError:
                    candidates = []  # RFC 5545 invalid dates do not consume COUNT.
        except OverflowError:
            return
        for wall in candidates:
            if wall < naive:
                continue
            if wall > last:
                return
            budget.use()
            yield wall
        period += 1


def _shape(event):
    if type(event.get("category")) is not int or event["category"] != 1 or event.get("all_day") is not False:
        _fail("unsupported_occurrence_query_shape")
    if event.get("type") is not None and (type(event["type"]) is not int or event["type"] != 0):
        _fail("unsupported_occurrence_query_shape")
    if not _empty(event.get("lunar")):
        _fail("unsupported_occurrence_query_shape")
    attachment = event.get("attachment")
    if attachment is not None:
        if not isinstance(attachment, dict) or any(not _empty(attachment.get(key)) for key in ("lunar", "day_count")):
            _fail("unsupported_occurrence_query_shape")
    title = event.get("title")
    if not isinstance(title, str) or not title or len(title.encode("utf-8")) > MAX_TITLE_BYTES:
        _fail("unsupported_occurrence_query_title")
    zone = _parent_zone(event)
    start, end = _raw_instant(event.get("start_at")), _raw_instant(event.get("end_at"))
    duration = end - start
    if not 0 < duration <= DAY_MS:
        _fail("unsupported_occurrence_duration")
    _safe_interval(start, duration, zone)
    return zone, start, duration


def _row(event, at_ms, duration, zone, source, parent_id):
    return {"occurrence_id": f"{event['uuid']}:{at_ms}", "event_uuid": event["uuid"],
            "series_uuid": parent_id, "source": source, "title": event["title"],
            "timezone": zone.key, "start_at": _ms_datetime(at_ms).isoformat(),
            "end_at": _ms_datetime(at_ms + duration).isoformat(),
            "start_local": _ms_datetime(at_ms, zone).isoformat(),
            "end_local": _ms_datetime(at_ms + duration, zone).isoformat(), "_start_ms": at_ms}


def _series_rows(event, start_ms, end_ms, limit, budget, child_starts):
    zone, anchor_ms, duration = _shape(event)
    anchor = _safe_instant(anchor_ms, zone)
    rule, exclusions = _query_rule(event, anchor)
    # A fall-back cutoff can have an earlier wall clock than an occurrence
    # already inside the UTC window. Bound by the whole local cutoff date,
    # then compare instants only after resolving each candidate safely.
    last = _ms_datetime(end_ms, zone).replace(tzinfo=None, hour=23, minute=59, second=59)
    if rule["until"] is not None:
        last = min(last, _ms_datetime(rule["until"], zone).replace(tzinfo=None, hour=23, minute=59, second=59))
    candidates = iter(_wall_candidates(rule, anchor, last, budget))
    ordinal, count, rows = 0, 0, []
    while rule["count"] is None or ordinal < rule["count"]:
        wall = next(candidates, None)
        if wall is None:
            break
        candidate = _unique_local(wall, zone)
        at_ms = _datetime_ms(candidate)
        if at_ms >= end_ms or (rule["until"] is not None and at_ms > rule["until"]):
            break
        if zone.key in DST_TIMEZONES and (wall.date() - anchor.date()).days > MAX_TIMEZONE_VALIDATION_DAYS:
            _fail("occurrence_timezone_validation_limit")
        _safe_interval(at_ms, duration, zone)
        ordinal += 1
        if at_ms not in exclusions and at_ms in child_starts:
            _fail("occurrence_child_exclusion_unverified")
        if at_ms in exclusions or at_ms + duration <= start_ms:
            continue
        count += 1
        if len(rows) <= limit:
            rows.append(_row(event, at_ms, duration, zone, "generated", event["uuid"]))
    return rows, count


def _single_rows(event, parent_id, start_ms, end_ms, budget):
    budget.use()
    zone, at_ms, duration = _shape(event)
    if event.get("recurrences") not in (None, []):
        _fail("unsupported_recurrence_shape")
    if at_ms < end_ms and at_ms + duration > start_ms:
        return [_row(event, at_ms, duration, zone, "detached" if parent_id else "single", parent_id)], 1
    return [], 0


def _error_code(error):
    code = str(error)
    return code if re.fullmatch(r"[a-z][a-z0-9_]{1,80}", code) else "unsupported_occurrence_query_shape"


def expand_occurrences(events, calendar_id, start_ms, end_ms, limit=100, query=None):
    """Project a complete raw snapshot; diagnostics and truncation deny completeness.

    Rows overlap [start_ms, end_ms). Unsupported records are not silently dropped;
    callers must inspect complete, coverage_complete, has_more and unsupported.
    Unknown metadata is not copied or used to authorize any calendar mutation.
    """
    _validate_range(calendar_id, start_ms, end_ms, limit, query)
    snapshot = _snapshot(events, calendar_id)
    active, issues = {}, {}
    for identifier, event in snapshot.items():
        deactivated = event.get("deactivated_at")
        if not _empty(deactivated):
            if type(deactivated) not in (int, float) or deactivated < 0:
                issues[identifier] = "invalid_occurrence_deactivation"
            continue
        if type(event.get("category")) is int and event["category"] == 2:
            continue
        active[identifier] = event
    parents, linked = {}, {}
    for identifier, event in active.items():
        parent, recurring = event.get("parent_id"), event.get("recurring_uuid")
        has_parent = not _empty(parent)
        has_other_recurring = not _empty(recurring) and recurring != identifier
        if has_parent or has_other_recurring:
            refs = [ref for ref in (parent, recurring) if isinstance(ref, str) and ref in active and ref != identifier]
            valid = (isinstance(parent, str) and parent == recurring and parent != identifier
                     and parent in active and isinstance(active[parent].get("recurrences"), list)
                     and bool(active[parent]["recurrences"]) and _empty(event.get("recurrences"))
                     and _empty(active[parent].get("parent_id"))
                     and (_empty(active[parent].get("recurring_uuid")) or active[parent]["recurring_uuid"] == parent))
            if valid:
                linked[identifier] = parent
            else:
                issues[identifier] = "occurrence_child_link_contract_unverified"
                for ref in refs:
                    issues[ref] = "occurrence_child_link_contract_unverified"
        elif event.get("recurrences"):
            parents[identifier] = event
    children = {}
    for identifier, parent in linked.items():
        children.setdefault(parent, []).append(identifier)
    budget, rows, total = _Budget(), [], 0

    def merge(existing, added):
        return heapq.nsmallest(limit + 1, existing + added, key=lambda row: (row["_start_ms"], row["event_uuid"]))

    def matches(event):
        return query is None or query.casefold() in event["title"].casefold()

    for identifier, event in sorted(parents.items()):
        group = [identifier, *children.get(identifier, [])]
        if any(member in issues for member in group):
            for member in group:
                issues.setdefault(member, "occurrence_child_link_contract_unverified")
            continue
        try:
            child_starts = set()
            if len(group) > 1:
                zone, anchor_ms, _ = _shape(event)
                _, exclusions = _query_rule(event, _safe_instant(anchor_ms, zone))
                if len(exclusions) < len(group) - 1:
                    _fail("occurrence_child_exclusion_unverified")
                for member in group[1:]:
                    child_zone, child_at, _ = _shape(active[member])
                    if child_zone.key != zone.key:
                        _fail("occurrence_child_link_contract_unverified")
                    child_starts.add(child_at)
            found, count = _series_rows(event, start_ms, end_ms, limit, budget, child_starts)
            if not matches(event):
                found, count = [], 0
            for member in group[1:]:
                child_rows, child_count = _single_rows(active[member], identifier, start_ms, end_ms, budget)
                if matches(active[member]):
                    found = merge(found, child_rows)
                    count += child_count
            rows = merge(rows, found)
            total += count
        except (ValueError, OverflowError) as error:
            issues[identifier] = _error_code(error)
            for member in group[1:]:
                issues[member] = "occurrence_parent_projection_unverified"
    for identifier, event in sorted(active.items()):
        if identifier in parents or identifier in linked or identifier in issues:
            continue
        try:
            found, count = _single_rows(event, None, start_ms, end_ms, budget)
            if matches(event):
                rows = merge(rows, found)
                total += count
        except (ValueError, OverflowError) as error:
            issues[identifier] = _error_code(error)
    has_more = total > limit
    for row in rows:
        row.pop("_start_ms")
    return {"calendar_id": calendar_id, "start_at": _ms_datetime(start_ms).isoformat(),
            "end_at": _ms_datetime(end_ms).isoformat(), "occurrences": rows[:limit],
            "count": min(total, limit), "complete": not issues and not has_more,
            "coverage_complete": not issues, "has_more": has_more,
            "unsupported": [{"event_uuid": identifier, "code": code} for identifier, code in sorted(issues.items())]}
