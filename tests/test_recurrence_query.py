"""Read-only recurrence projections over synthetic calendar snapshots."""

import copy
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from timetree_mcp.occurrence_rules import parse_occurrence_time as instant
from timetree_mcp.recurrence_query import expand_occurrences, validate_query_arguments


def event(identifier="series", *, start="2030-01-07T09:00:00+08:00",
          end="2030-01-07T10:00:00+08:00", zone="Asia/Taipei", rules=None, **changes):
    result = {"id": identifier, "uuid": identifier, "calendar_id": "42", "category": 1,
              "type": 0, "title": "Planning", "all_day": False,
              "start_at": instant(start), "end_at": instant(end),
              "start_timezone": zone, "end_timezone": zone,
              "parent_id": None, "recurring_uuid": identifier,
              "recurrences": ["RRULE:FREQ=DAILY;COUNT=5"] if rules is None else rules}
    result.update(changes)
    return result


def query(events, start="2030-01-01T00:00:00+08:00", end="2030-02-01T00:00:00+08:00", **options):
    return expand_occurrences(events, "42", instant(start), instant(end), **options)


class RecurrenceQueryTests(unittest.TestCase):
    def test_public_arguments_validate_before_any_snapshot(self):
        args = {"calendar_id": "42", "start_at": "2030-01-01T00:00:00+08:00",
                "end_at": "2030-02-01T00:00:00+08:00"}
        parsed = validate_query_arguments(args)
        self.assertEqual(parsed, {"calendar_id": "42", "start_ms": instant(args["start_at"]),
                                  "end_ms": instant(args["end_at"]), "limit": 100, "query": None})
        for change in ({"calendar_id": 42}, {"calendar_id": "-1"}, {"limit": True}, {"limit": 501},
                       {"query": 1}, {"query": None}, {"query": "x" * 501}, {"extra": 1},
                       {"start_at": "2030-01-01T00:00:00Z"}, {"end_at": args["start_at"]},
                       {"end_at": "2030-05-01T00:00:00+08:00"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_query_arguments({**args, **change})

    def test_daily_overlap_and_exclusive_window_boundaries(self):
        source = event(rules=["RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20300108T010000Z"])
        result = query([source], "2030-01-07T09:30:00+08:00", "2030-01-09T09:00:00+08:00")
        self.assertTrue(result["complete"])
        self.assertEqual(result["count"], 1)
        row = result["occurrences"][0]
        self.assertEqual((row["source"], row["event_uuid"], row["series_uuid"]), ("generated", "series", "series"))
        self.assertEqual(row["start_local"], "2030-01-07T09:00:00+08:00")
        self.assertEqual(row["start_at"], "2030-01-07T01:00:00+00:00")
        self.assertEqual(query([source], "2030-01-07T10:00:00+08:00", "2030-01-08T00:00:00+08:00")["count"], 0)

    def test_weekly_interval_byday_and_partial_first_week(self):
        source = event(start="2030-01-09T09:00:00+08:00", end="2030-01-09T10:00:00+08:00",
                       rules=["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;WKST=MO;COUNT=3"])
        result = query([source])
        self.assertEqual([row["start_local"][:10] for row in result["occurrences"]],
                         ["2030-01-09", "2030-01-21", "2030-01-23"])

    def test_monthly_invalid_days_do_not_consume_count(self):
        source = event(start="2030-01-31T09:00:00+08:00", end="2030-01-31T10:00:00+08:00",
                       rules=["RRULE:FREQ=MONTHLY;COUNT=3;BYMONTHDAY=31"])
        first = query([source], "2030-01-01T00:00:00+08:00", "2030-04-01T00:00:00+08:00")
        self.assertEqual([row["start_local"][:10] for row in first["occurrences"]], ["2030-01-31", "2030-03-31"])
        later = query([source], "2030-05-01T00:00:00+08:00", "2030-08-01T00:00:00+08:00")
        self.assertEqual([row["start_local"][:10] for row in later["occurrences"]], ["2030-05-31"])
        self.assertTrue(later["complete"])

    def test_monthly_interval_advances_months_before_skipping_invalid_dates(self):
        source = event(start="2030-01-30T09:00:00+08:00", end="2030-01-30T10:00:00+08:00",
                       rules=["RRULE:FREQ=MONTHLY;INTERVAL=2;COUNT=3"])
        result = query([source], "2030-03-01T00:00:00+08:00", "2030-06-01T00:00:00+08:00")
        self.assertEqual([row["start_local"][:10] for row in result["occurrences"]], ["2030-03-30", "2030-05-30"])

    def test_yearly_leap_dates_and_explicit_month_day(self):
        for rule in ("RRULE:FREQ=YEARLY;COUNT=2", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29;COUNT=2"):
            source = event(start="2024-02-29T09:00:00+08:00", end="2024-02-29T10:00:00+08:00", rules=[rule])
            self.assertEqual(query([source], "2028-02-01T00:00:00+08:00", "2028-03-01T00:00:00+08:00")["count"], 1)
            self.assertEqual(query([source], "2032-02-01T00:00:00+08:00", "2032-03-01T00:00:00+08:00")["count"], 0)

    def test_unsupported_or_unsynchronized_patterns_report_incomplete(self):
        invalid = ["RRULE:FREQ=HOURLY", "RRULE:FREQ=MONTHLY;BYDAY=1MO",
                   "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1", "RRULE:FREQ=MONTHLY;BYMONTHDAY=7,8",
                   "RRULE:FREQ=MONTHLY;BYMONTHDAY=8", "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=7",
                   "RRULE:FREQ=YEARLY;BYMONTH=1", "RRULE:FREQ=WEEKLY;BYDAY=TU",
                   "RRULE:FREQ=MONTHLY;COUNT=3;UNTIL=20310101", "RRULE:FREQ=YEARLY;INTERVAL=0"]
        for rule in invalid:
            with self.subTest(rule=rule):
                result = query([event(rules=[rule])])
                self.assertFalse(result["complete"])
                self.assertEqual(result["occurrences"], [])
                self.assertEqual(result["unsupported"][0]["event_uuid"], "series")

    def test_until_and_exdate_apply_without_extending_count(self):
        source = event(start="2030-01-31T09:00:00+08:00", end="2030-01-31T10:00:00+08:00",
                       rules=["RRULE:FREQ=MONTHLY;COUNT=2", "EXDATE;TZID=Asia/Taipei:20300331T090000"])
        self.assertEqual(query([source], "2030-05-01T00:00:00+08:00", "2030-06-01T00:00:00+08:00")["count"], 0)
        until = event(rules=["RRULE:FREQ=DAILY;UNTIL=20300108"])
        self.assertEqual(query([until])["count"], 2)

    def test_detached_child_and_excluded_parent_are_returned_once(self):
        parent = event(rules=["RRULE:FREQ=DAILY;COUNT=2", "EXDATE:20300108T010000Z"])
        child = event("child", start="2030-01-08T11:00:00+08:00", end="2030-01-08T12:00:00+08:00",
                      rules=[], parent_id="series", recurring_uuid="series")
        single = event("standalone", rules=[])
        result = query([child, single, parent])
        self.assertTrue(result["complete"])
        self.assertEqual(result["count"], 3)
        self.assertEqual([row["source"] for row in result["occurrences"]], ["generated", "single", "detached"])
        self.assertEqual(len({row["occurrence_id"] for row in result["occurrences"]}), 3)

    def test_conflicting_or_orphan_child_links_never_become_standalone(self):
        parent = event()
        for changes in ({"parent_id": "series", "recurring_uuid": "wrong"},
                        {"parent_id": None, "recurring_uuid": "series"},
                        {"parent_id": "missing", "recurring_uuid": "missing"}):
            child = event("child", rules=[], **changes)
            result = query([parent, child])
            self.assertFalse(result["complete"])
            self.assertNotIn("child", {row["event_uuid"] for row in result["occurrences"]})
            if "series" in changes.values():
                self.assertNotIn("series", {row["event_uuid"] for row in result["occurrences"]})

    def test_partial_child_move_without_exclusion_suppresses_the_whole_group(self):
        for child_id in ("a-child", "z-child"):
            parent = event()
            child = event(child_id, rules=[], parent_id="series", recurring_uuid="series")
            result = query([parent, child])
            self.assertFalse(result["complete"])
            self.assertEqual(result["occurrences"], [])
            self.assertEqual({item["event_uuid"] for item in result["unsupported"]}, {"series", child_id})

    def test_unexcluded_parent_and_linked_child_at_same_instant_are_not_guessed(self):
        parent = event(rules=["RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20300108T010000Z"])
        child = event("child", rules=[], parent_id="series", recurring_uuid="series")
        result = query([parent, child])
        self.assertFalse(result["complete"])
        self.assertEqual(result["occurrences"], [])

    def test_parent_expansion_failure_invalidates_children_independent_of_id_order(self):
        for child_id in ("a-child", "z-child"):
            parent = event(rules=["RRULE:FREQ=MONTHLY;BYDAY=1MO", "EXDATE:20300108T010000Z"])
            child = event(child_id, rules=[], parent_id="series", recurring_uuid="series")
            self.assertEqual(query([parent, child])["occurrences"], [])

    def test_insufficient_exclusions_for_children_are_incomplete(self):
        parent = event(rules=["RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20300108T010000Z"])
        children = [event(identifier, rules=[], parent_id="series", recurring_uuid="series") for identifier in ("child-a", "child-b")]
        result = query([parent, *children])
        self.assertFalse(result["complete"])
        self.assertEqual(result["occurrences"], [])

    def test_unsupported_records_do_not_hide_valid_results_or_claim_complete(self):
        result = query([event("all-day", all_day=True), event("cross", end_timezone="Asia/Tokyo"),
                        event("good", rules=[]), event("memo", category=2), event("inactive", deactivated_at=1)])
        self.assertEqual([row["event_uuid"] for row in result["occurrences"]], ["good"])
        self.assertFalse(result["complete"])
        self.assertEqual({row["event_uuid"] for row in result["unsupported"]}, {"all-day", "cross"})

    def test_literal_filter_and_limit_are_applied_after_chronological_sort(self):
        later = event("later", start="2030-01-09T09:00:00+08:00", end="2030-01-09T10:00:00+08:00", rules=[], title="Plan [x]")
        early = event("early", rules=[], title="Plan [x]")
        ignored = event("ignore", rules=[], title="Plan x")
        result = query([later, ignored, early], query="[x]", limit=1)
        self.assertEqual([row["event_uuid"] for row in result["occurrences"]], ["early"])
        self.assertTrue(result["has_more"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["coverage_complete"])

    def test_read_projection_does_not_require_write_only_metadata(self):
        source = event(extra_field={"safe_to_ignore_for_read": True})
        before = copy.deepcopy(source)
        result = query([source])
        self.assertTrue(result["complete"])
        self.assertEqual(result["count"], 5)
        self.assertEqual(source, before)

    def test_date_semantics_metadata_and_until_before_anchor_fail_closed(self):
        for changes in ({"attachment": {"lunar": {"month": 1, "day": 1}}},
                        {"attachment": {"day_count": {"kind": 1}}},
                        {"recurrences": ["RRULE:FREQ=MONTHLY;UNTIL=20290101"]}):
            result = query([event(**changes)])
            self.assertFalse(result["complete"])
            self.assertEqual(result["occurrences"], [])

    def test_monthly_query_support_does_not_expand_write_contract(self):
        from timetree_mcp.occurrence_rules import validate_parent
        from test_occurrence_rules import fixture
        with self.assertRaisesRegex(ValueError, "unsupported_recurrence_frequency"):
            validate_parent(fixture(recurrences=["RRULE:FREQ=MONTHLY"]))

    def test_dst_safe_wall_clock_series_changes_utc_offset(self):
        source = event(start="2030-03-09T09:00:00-05:00", end="2030-03-09T10:00:00-05:00",
                       zone="America/New_York", rules=["RRULE:FREQ=DAILY;COUNT=3"])
        result = query([source], "2030-03-09T00:00:00-05:00", "2030-03-12T00:00:00-04:00")
        self.assertTrue(result["complete"])
        self.assertEqual([row["start_at"][11:16] for row in result["occurrences"]], ["14:00", "13:00", "13:00"])

    def test_dst_unsafe_history_discards_entire_series_even_if_earlier_rows_were_valid(self):
        source = event(start="2030-03-09T02:30:00-05:00", end="2030-03-09T03:30:00-05:00",
                       zone="America/New_York", rules=["RRULE:FREQ=DAILY;COUNT=3"])
        result = query([source], "2030-03-09T00:00:00-05:00", "2030-03-12T00:00:00-04:00")
        self.assertFalse(result["complete"])
        self.assertEqual(result["occurrences"], [])
        self.assertEqual(result["unsupported"][0]["code"], "occurrence_local_time_nonexistent")

    def test_fall_back_query_cutoff_cannot_hide_an_earlier_ambiguous_occurrence(self):
        cases = (("America/New_York", "2030-11-02", "2030-11-03", "-04:00", "-05:00"),
                 ("Europe/London", "2030-10-26", "2030-10-27", "+01:00", "+00:00"))
        for zone, anchor, day, before, after in cases:
            with self.subTest(zone=zone):
                source = event(start=f"{anchor}T01:30:00{before}", end=f"{anchor}T02:30:00{before}",
                               zone=zone, rules=["RRULE:FREQ=DAILY;COUNT=3"])
                result = query([source], f"{day}T00:00:00{before}", f"{day}T01:00:00{after}")
                self.assertFalse(result["complete"])
                self.assertEqual(result["occurrences"], [])
                self.assertEqual(result["unsupported"][0]["code"], "occurrence_local_time_ambiguous")

    def test_fall_back_until_cutoff_cannot_hide_an_earlier_ambiguous_occurrence(self):
        source = event(start="2030-11-02T01:30:00-04:00", end="2030-11-02T02:30:00-04:00",
                       zone="America/New_York", rules=["RRULE:FREQ=DAILY;UNTIL=20301103T060000Z"])
        result = query([source], "2030-11-03T00:00:00-04:00", "2030-11-04T00:00:00-05:00")
        self.assertFalse(result["complete"])
        self.assertEqual(result["unsupported"][0]["code"], "occurrence_local_time_ambiguous")

    def test_dst_history_and_global_work_budget_are_bounded(self):
        distant = event(start="2000-01-01T09:00:00-05:00", end="2000-01-01T10:00:00-05:00",
                        zone="America/New_York", rules=["RRULE:FREQ=YEARLY"])
        result = query([distant])
        self.assertEqual(result["unsupported"][0]["code"], "occurrence_timezone_validation_limit")
        with patch("timetree_mcp.recurrence_query.MAX_CANDIDATES", 3):
            result = query([event("a"), event("b")])
        self.assertFalse(result["complete"])
        self.assertEqual(result["occurrences"], [])
        self.assertTrue(all(item["code"] == "occurrence_query_capacity_exceeded" for item in result["unsupported"]))

    def test_missing_timezone_database_returns_safe_diagnostics(self):
        with patch("timetree_mcp.occurrence_rules.ZoneInfo") as constructor:
            constructor.no_cache.side_effect = ZoneInfoNotFoundError("private path")
            result = query([event()])
        self.assertFalse(result["complete"])
        self.assertEqual(result["unsupported"], [{"event_uuid": "series", "code": "occurrence_timezone_data_unavailable"}])
        self.assertNotIn("private path", str(result))

    def test_malformed_snapshots_fail_instead_of_reporting_empty_calendar(self):
        for events in (None, [None], [event(), event()], [event(calendar_id="43")], [event(id="wrong")]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                query(events)


if __name__ == "__main__":
    unittest.main()
