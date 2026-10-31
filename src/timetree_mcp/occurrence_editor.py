"""Conservative two-write occurrence moves with a durable dispatch journal.

Calendar snapshots stay in memory. A failed/ambiguous dispatch is never retried
or compensated automatically, even after a process restart.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from .occurrence_rules import (
    assert_occurrence, canonical, child_payload, corroborated_children, digest,
    excluded_rules, parse_occurrence_time, validate_parent, verify_child, verify_parent,
)
from .operations import require_confirmation

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
CONCURRENCY_WARNING = (
    "No upstream compare-and-swap: another client's recurrence edit between the last read "
    "and exclusion may be overwritten without detection. Avoid concurrent series edits. "
    "Never retry an uncertain move with a new operation ID or roll back automatically."
)


def validate_arguments(arguments: dict[str, Any], *, write: bool = False) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("invalid_occurrence_arguments")
    if write:
        require_confirmation(arguments)
    required = {"calendar_id", "event_uuid", "occurrence_start_at"}
    if write:
        required |= {"new_start_at", "expected_revision", "operation_id", "confirm"}
    if set(arguments) != required:
        raise ValueError("invalid_occurrence_arguments")
    cid = arguments["calendar_id"]
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9]{1,40}", cid):
        raise ValueError("invalid_occurrence_calendar_id")
    _identifier(arguments["event_uuid"])
    parse_occurrence_time(arguments["occurrence_start_at"])
    if write:
        parse_occurrence_time(arguments["new_start_at"])
        _identifier(arguments["operation_id"])
        revision = arguments["expected_revision"]
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise ValueError("invalid_occurrence_revision")


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid_occurrence_identifier")
    return value


def _event_id(event: dict[str, Any]) -> str:
    return _identifier(event.get("uuid") or event.get("id"))


def _iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


class OccurrenceEditor:
    def __init__(self, snapshot: Callable, create: Callable, exclude: Callable,
                 journal: Any, *, namespace: str):
        self.snapshot, self.create, self.exclude = snapshot, create, exclude
        self.journal, self.namespace = journal, namespace

    def _key(self, operation_id: str) -> str:
        return digest([self.namespace, _identifier(operation_id)])

    def _events(self, calendar_id: str) -> list[dict[str, Any]]:
        events = self.snapshot(calendar_id)
        if not isinstance(events, list) or len(events) > 20000:
            raise ValueError("invalid_occurrence_snapshot")
        if any(not isinstance(e, dict) or str(e.get("calendar_id")) != calendar_id for e in events):
            raise ValueError("invalid_occurrence_snapshot")
        ids = [_event_id(e) for e in events]
        if len(set(ids)) != len(ids):
            raise ValueError("event_missing_or_ambiguous")
        ordered = sorted(events, key=_event_id)
        if len(canonical(ordered).encode("utf-8")) > 8_000_000:
            raise ValueError("occurrence_snapshot_capacity_exceeded")
        return ordered

    def _context(self, arguments: dict[str, Any]):
        events = self._events(arguments["calendar_id"])
        matches = [e for e in events if _event_id(e) == arguments["event_uuid"]]
        if len(matches) != 1:
            raise ValueError("event_missing_or_ambiguous")
        parent = matches[0]
        validate_parent(parent)
        assert_occurrence(parent, parse_occurrence_time(arguments["occurrence_start_at"], parent))
        return parent, events, digest(events)

    def preflight(self, arguments: dict[str, Any]) -> dict[str, Any]:
        validate_arguments(arguments)
        parent, events, revision = self._context(arguments)
        at = parse_occurrence_time(arguments["occurrence_start_at"], parent)
        payload = child_payload(parent, at)
        editable = corroborated_children(parent, events)
        return {
            "ok": True, "editable": editable, "revision": revision,
            "edit_scope": "single_generated_occurrence",
            "calendar_id": arguments["calendar_id"], "event_uuid": parent["uuid"],
            "title": parent["title"], "occurrence_start_at": arguments["occurrence_start_at"],
            "timezone": parent["start_timezone"],
            "end_at": _iso(payload["end_at"]),
            "duration_ms": payload["end_at"] - payload["start_at"],
            **({} if editable else {"edit_blocker": "occurrence_child_link_contract_unverified"}),
            "concurrency": "two_non_atomic_writes_with_verification",
            "compare_and_swap": False,
            "warning": CONCURRENCY_WARNING,
        }

    @staticmethod
    def _same_except(before, after, skipped):
        return canonical([e for e in before if _event_id(e) not in skipped]) == canonical(
            [e for e in after if _event_id(e) not in skipped])

    def move(self, arguments: dict[str, Any]) -> dict[str, Any]:
        validate_arguments(arguments, write=True)
        key = self._key(arguments["operation_id"])
        request_hash = digest([self.namespace, arguments])
        previous = self.journal.find(key)
        if previous:
            if previous["request_hash"] != request_hash:
                raise ValueError("occurrence_operation_id_conflict")
            return previous["result"] or self._uncertain(arguments, previous["phase"], previous["data"])

        parent, events, revision = self._context(arguments)
        if not corroborated_children(parent, events):
            raise ValueError("occurrence_child_link_contract_unverified")
        if revision != arguments["expected_revision"]:
            raise ValueError("event_changed_read_again")
        original = parse_occurrence_time(arguments["occurrence_start_at"], parent)
        new_start = parse_occurrence_time(arguments["new_start_at"], parent)
        if original == new_start:
            raise ValueError("occurrence_time_unchanged")
        payload, rules = child_payload(parent, new_start), excluded_rules(parent, original)
        data = {"calendar_id": arguments["calendar_id"], "event_uuid": arguments["event_uuid"],
                "snapshot_revision": revision, "payload_hash": digest(payload)}
        target = digest([self.namespace, arguments["calendar_id"], arguments["event_uuid"]])
        if not self.journal.claim(key, request_hash, target, data):
            return {"ok": False, "code": "occurrence_series_locked_do_not_retry",
                    "operation_id": arguments["operation_id"], "retry_safe": False}

        phase = "prepared"

        def transition(next_phase):
            nonlocal phase
            if not self.journal.transition(key, request_hash, phase, next_phase, data):
                raise RuntimeError("occurrence_journal_transition_failed")
            phase = next_phase

        try:
            if self._context(arguments)[2] != revision:
                raise ValueError("stale_before_create")
            transition("create_dispatched")
            child_id = self.create(arguments["calendar_id"], payload)
            data["created_child_uuid"] = _identifier(child_id)
            if child_id in {_event_id(e) for e in events}:
                raise ValueError("invalid_created_identity")
            transition("child_created")
            _, after_create, _ = self._context(arguments)
            children = [e for e in after_create if _event_id(e) == child_id]
            if (len(children) != 1 or not verify_child(parent, children[0], payload)
                    or not self._same_except(events, after_create, {child_id})):
                raise ValueError("child_verification_failed")
            verified_child = children[0]
            data["verified_child_hash"] = digest(verified_child)
            transition("child_verified")
            if canonical(self._context(arguments)[1]) != canonical(after_create):
                raise ValueError("changed_before_exclusion")
            transition("exclude_dispatched")
            self.exclude(arguments["calendar_id"], arguments["event_uuid"], rules)
            final = self._events(arguments["calendar_id"])
            parents = [e for e in final if _event_id(e) == arguments["event_uuid"]]
            children = [e for e in final if _event_id(e) == child_id]
            if (len(parents) != 1 or len(children) != 1
                    or not verify_parent(parent, parents[0], rules)
                    or canonical(children[0]) != canonical(verified_child)
                    or not self._same_except(events, final, {arguments["event_uuid"], child_id})):
                raise ValueError("final_verification_failed")
            result = {"ok": True, "operation_id": arguments["operation_id"],
                      "calendar_id": arguments["calendar_id"], "event_uuid": child_id,
                      "series_uuid": arguments["event_uuid"],
                      "original_occurrence_start_at": arguments["occurrence_start_at"],
                      "start_at": _iso(payload["start_at"]), "end_at": _iso(payload["end_at"]),
                      "timezone": parent["start_timezone"],
                      "edit_scope": "single_generated_occurrence",
                      "concurrency": "two_non_atomic_writes_with_verification",
                      "compare_and_swap": False, "warning": CONCURRENCY_WARNING}
            if not self.journal.finish(key, request_hash, phase, result, data):
                raise RuntimeError("occurrence_journal_finish_failed")
            return result
        except Exception:  # All post-claim failures retain the series lock.
            result = self._uncertain(arguments, phase, data)
            try:
                self.journal.finish(key, request_hash, phase, result, data)
            except Exception:
                pass  # The last durable dispatch phase remains blocked.
            return result

    @staticmethod
    def _uncertain(arguments, phase, data):
        return {"ok": False, "code": "occurrence_outcome_uncertain_do_not_retry",
                "operation_id": arguments["operation_id"], "phase": phase,
                "retry_safe": False, "calendar_write_may_have_occurred": phase != "prepared",
                **({"created_child_uuid": data["created_child_uuid"]}
                   if data.get("created_child_uuid") else {})}

    def operation_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"operation_id"}:
            raise ValueError("invalid_occurrence_status_arguments")
        row = self.journal.find(self._key(arguments["operation_id"]))
        if row is None:
            return {"found": False, "operation_id": arguments["operation_id"]}
        return {"found": True, "operation_id": arguments["operation_id"],
                "phase": row["phase"], "completed": row["completed"],
                "result": row["result"] or self._uncertain(arguments, row["phase"], row["data"])}
