"""Synthetic timezone and DST contracts; never contact a calendar service."""

import copy
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from timetree_mcp.occurrence_rules import (
    assert_occurrence, child_payload, excluded_rules, parse_occurrence_time,
    validate_parent, verify_child, verify_parent,
)
from test_occurrence_rules import child, fixture


def zoned(zone, start, end, **changes):
    return fixture(
        start_timezone=zone, end_timezone=zone,
        start_at=parse_occurrence_time(start), end_at=parse_occurrence_time(end),
        **changes,
    )


class OccurrenceTimezoneTests(unittest.TestCase):
    def test_numeric_offset_input_is_exact(self):
        for value in ("2030-01-07T09:00:00+09:00", "2030-01-07T09:00:00+05:30",
                      "2030-01-07T09:00:00-05:00", "2030-01-07T09:00:00+00:00"):
            self.assertIsInstance(parse_occurrence_time(value), int)
        for value in ("2030-01-07T09:00:00Z", "2030-01-07T09:00:00", "2030-01-07T09:00+09:00",
                      "2030-01-07T09:00:00.000+09:00", "2030-01-07T09:00:00-00:00",
                      "2030-01-07T09:00:00+24:00", "2030-01-07T09:00:00+05:60",
                      "2030-02-30T09:00:00+09:00", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_occurrence_time(value)

    def test_all_five_zones_preserve_payload_metadata_and_utc_exclusion(self):
        cases = (
            ("Asia/Taipei", "+08:00", "010000"),
            ("Asia/Tokyo", "+09:00", "000000"),
            ("Asia/Kolkata", "+05:30", "033000"),
            ("America/New_York", "-05:00", "140000"),
            ("Europe/London", "+00:00", "090000"),
        )
        for zone, offset, utc_time in cases:
            with self.subTest(zone=zone):
                parent = zoned(zone, f"2030-01-07T09:00:00{offset}", f"2030-01-07T10:00:00{offset}")
                before = copy.deepcopy(parent)
                original = parse_occurrence_time(f"2030-01-08T09:00:00{offset}", parent)
                moved = parse_occurrence_time(f"2030-01-08T11:00:00{offset}", parent)
                assert_occurrence(parent, original)
                payload = child_payload(parent, moved)
                self.assertEqual(payload["start_timezone"], zone)
                self.assertEqual(payload["end_timezone"], zone)
                self.assertEqual(payload["end_at"] - moved, 3_600_000)
                self.assertEqual(payload["attachment"], parent["attachment"])
                self.assertTrue(verify_child(parent, child(parent, payload), payload))
                rules = excluded_rules(parent, original)
                self.assertEqual(rules[-1], f"EXDATE:20300108T{utc_time}Z")
                self.assertTrue(verify_parent(parent, {**parent, "recurrences": rules}, rules))
                self.assertEqual(parent, before)

    def test_offset_must_describe_the_series_local_time(self):
        parent = zoned("Asia/Tokyo", "2030-01-07T09:00:00+09:00", "2030-01-07T10:00:00+09:00")
        for stamp in ("2030-01-08T09:00:00+08:00", "2030-01-08T00:00:00+00:00"):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, "occurrence_timezone_offset_mismatch"):
                parse_occurrence_time(stamp, parent)

    def test_raw_iso_offsets_and_overflow_also_fail_closed(self):
        for zone, start, end in (
            ("Asia/Kolkata", "2030-01-07T09:00:00+05:60", "2030-01-07T10:00:00+05:60"),
            ("Europe/London", "2030-01-07T09:00:00-00:00", "2030-01-07T10:00:00-00:00"),
            ("America/New_York", "9999-12-31T23:00:00-05:00", "9999-12-31T23:30:00-05:00"),
        ):
            parent = fixture(start_timezone=zone, end_timezone=zone, start_at=start, end_at=end)
            with self.subTest(start=start), self.assertRaises(ValueError):
                validate_parent(parent)

    def test_missing_unsupported_or_cross_timezones_fail_closed(self):
        parent = fixture()
        for change in ({"start_timezone": None}, {"end_timezone": ""},
                       {"start_timezone": "Asia/Tokyo"},
                       {"start_timezone": "Etc/UTC", "end_timezone": "Etc/UTC"},
                       {"start_timezone": "Invalid/Zone", "end_timezone": "Invalid/Zone"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_parent({**parent, **change})
        without_zone = dict(parent)
        del without_zone["start_timezone"]
        with self.assertRaises(ValueError):
            validate_parent(without_zone)

    def test_missing_timezone_database_has_explicit_error_for_every_zone(self):
        for zone in ("Asia/Taipei", "Asia/Tokyo", "Asia/Kolkata", "America/New_York", "Europe/London"):
            parent = {**fixture(), "start_timezone": zone, "end_timezone": zone}
            with self.subTest(zone=zone), patch("timetree_mcp.occurrence_rules.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing")):
                with self.assertRaisesRegex(ValueError, "^occurrence_timezone_data_unavailable$"):
                    validate_parent(parent)

    def test_local_1980_boundary_in_tokyo_and_pre_boundary_rejection(self):
        parent = zoned("Asia/Tokyo", "1980-01-01T00:00:00+09:00", "1980-01-01T01:00:00+09:00")
        validate_parent(parent)
        for stamp in ("1979-12-31T23:59:59+09:00", "1979-12-31T23:59:59-05:00"):
            with self.assertRaisesRegex(ValueError, "occurrence_date_out_of_range"):
                parse_occurrence_time(stamp)

    def test_safe_daily_and_weekly_recurrences_keep_wall_time_across_dst(self):
        cases = (
            ("America/New_York", "2030-03-09", "2030-03-11", "-05:00", "-04:00", "DAILY"),
            ("America/New_York", "2030-03-04", "2030-03-11", "-05:00", "-04:00", "WEEKLY"),
            ("Europe/London", "2030-03-30", "2030-04-01", "+00:00", "+01:00", "DAILY"),
            ("Europe/London", "2030-10-20", "2030-10-27", "+01:00", "+00:00", "WEEKLY"),
        )
        for zone, anchor, day, initial, later, freq in cases:
            with self.subTest(zone=zone, anchor=anchor):
                parent = zoned(zone, f"{anchor}T09:00:00{initial}", f"{anchor}T10:00:00{initial}",
                               recurrences=[f"RRULE:FREQ={freq};COUNT=3"])
                at = parse_occurrence_time(f"{day}T09:00:00{later}", parent)
                assert_occurrence(parent, at)
                self.assertEqual(child_payload(parent, at)["end_at"], at + 3_600_000)
                with self.assertRaises(ValueError):
                    parse_occurrence_time(f"{day}T09:00:00{initial}", parent)

    def test_gap_and_fold_inputs_rejected_even_with_explicit_offsets(self):
        cases = (
            ("America/New_York", "2030-03-10T02:30:00", ("-05:00", "-04:00")),
            ("America/New_York", "2030-11-03T01:30:00", ("-04:00", "-05:00")),
            ("Europe/London", "2030-03-31T01:30:00", ("+00:00", "+01:00")),
            ("Europe/London", "2030-10-27T01:30:00", ("+01:00", "+00:00")),
        )
        for zone, wall, offsets in cases:
            parent = {**fixture(), "start_timezone": zone, "end_timezone": zone}
            for offset in offsets:
                with self.subTest(zone=zone, wall=wall, offset=offset), self.assertRaisesRegex(ValueError, "occurrence_local_time_(ambiguous|nonexistent)"):
                    parse_occurrence_time(wall + offset, parent)

    def test_anchor_and_moved_intervals_may_not_cross_dst(self):
        cases = (
            ("America/New_York", "2030-03-10T01:30:00-05:00", "2030-03-10T03:30:00-04:00"),
            ("America/New_York", "2030-11-03T00:30:00-04:00", "2030-11-03T02:30:00-05:00"),
            ("Europe/London", "2030-03-31T00:30:00+00:00", "2030-03-31T02:30:00+01:00"),
            ("Europe/London", "2030-10-27T00:30:00+01:00", "2030-10-27T02:30:00+00:00"),
        )
        for zone, start, end in cases:
            with self.subTest(zone=zone, start=start), self.assertRaisesRegex(ValueError, "occurrence_interval_crosses_offset_transition"):
                validate_parent(zoned(zone, start, end))
        parent = zoned("America/New_York", "2030-03-09T01:30:00-05:00", "2030-03-09T02:30:00-05:00")
        at = parse_occurrence_time("2030-03-10T01:30:00-05:00", parent)
        with self.assertRaisesRegex(ValueError, "occurrence_interval_crosses_offset_transition"):
            child_payload(parent, at)
        with self.assertRaises(ValueError):
            assert_occurrence(parent, at)

    def test_ambiguous_endpoints_rejected_for_anchor_and_payload(self):
        with self.assertRaisesRegex(ValueError, "occurrence_local_time_ambiguous"):
            validate_parent(zoned("America/New_York", "2030-11-03T00:30:00-04:00", "2030-11-03T01:30:00-04:00"))
        parent = zoned("America/New_York", "2030-11-02T00:30:00-04:00", "2030-11-02T01:30:00-04:00")
        with self.assertRaisesRegex(ValueError, "occurrence_local_time_ambiguous"):
            child_payload(parent, parse_occurrence_time("2030-11-03T00:30:00-04:00", parent))

    def test_intervening_unsafe_generated_candidates_are_not_guessed_or_counted(self):
        cases = (
            ("America/New_York", "2030-03-09T02:30:00-05:00", "2030-03-09T03:30:00-05:00", "2030-03-11T02:30:00-04:00"),
            ("America/New_York", "2030-11-02T01:30:00-04:00", "2030-11-02T02:30:00-04:00", "2030-11-04T01:30:00-05:00"),
            ("Europe/London", "2030-03-30T01:30:00+00:00", "2030-03-30T02:30:00+00:00", "2030-04-01T01:30:00+01:00"),
        )
        for zone, start, end, target in cases:
            parent = zoned(zone, start, end, recurrences=["RRULE:FREQ=DAILY;COUNT=4"])
            with self.subTest(zone=zone, start=start), self.assertRaises(ValueError):
                assert_occurrence(parent, parse_occurrence_time(target, parent))

    def test_weekly_scan_only_checks_generated_days(self):
        parent = zoned("America/New_York", "2030-03-04T02:30:00-05:00", "2030-03-04T03:30:00-05:00",
                       recurrences=["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=2"])
        assert_occurrence(parent, parse_occurrence_time("2030-03-11T02:30:00-04:00", parent))
        with self.assertRaisesRegex(ValueError, "occurrence_local_time_nonexistent"):
            assert_occurrence({**parent, "recurrences": ["RRULE:FREQ=WEEKLY;BYDAY=MO,SU;COUNT=4"]},
                              parse_occurrence_time("2030-03-11T02:30:00-04:00", parent))

    def test_daily_interval_can_skip_dst_gap_without_changing_count(self):
        parent = zoned("America/New_York", "2030-03-09T02:30:00-05:00", "2030-03-09T03:30:00-05:00",
                       recurrences=["RRULE:FREQ=DAILY;INTERVAL=2;COUNT=2"])
        assert_occurrence(parent, parse_occurrence_time("2030-03-11T02:30:00-04:00", parent))
        with self.assertRaisesRegex(ValueError, "occurrence_missing_or_excluded"):
            assert_occurrence(parent, parse_occurrence_time("2030-03-13T02:30:00-04:00", parent))

    def test_intervening_cross_transition_interval_fails_even_if_excluded(self):
        parent = zoned("America/New_York", "2030-03-09T01:30:00-05:00", "2030-03-09T02:30:00-05:00",
                       recurrences=["RRULE:FREQ=DAILY;COUNT=4", "EXDATE:20300310T063000Z"])
        with self.assertRaisesRegex(ValueError, "occurrence_interval_crosses_offset_transition"):
            assert_occurrence(parent, parse_occurrence_time("2030-03-11T01:30:00-04:00", parent))

    def test_dst_candidate_validation_is_bounded_without_limiting_fixed_zones(self):
        parent = zoned("America/New_York", "2030-01-01T09:00:00-05:00", "2030-01-01T10:00:00-05:00",
                       recurrences=["RRULE:FREQ=DAILY"])
        with self.assertRaisesRegex(ValueError, "occurrence_timezone_validation_limit"):
            assert_occurrence(parent, parse_occurrence_time("2041-01-01T09:00:00-05:00", parent))
        parent = zoned("Asia/Tokyo", "2030-01-01T09:00:00+09:00", "2030-01-01T10:00:00+09:00",
                       recurrences=["RRULE:FREQ=DAILY"])
        assert_occurrence(parent, parse_occurrence_time("2041-01-01T09:00:00+09:00", parent))

    def test_dst_history_boundary_is_inclusive(self):
        parent = zoned("Europe/London", "2030-01-01T09:00:00+00:00", "2030-01-01T10:00:00+00:00",
                       recurrences=["RRULE:FREQ=DAILY"])
        for days in (3660, 3661):
            day = (datetime(2030, 1, 1) + timedelta(days=days)).strftime("%Y-%m-%d")
            at = parse_occurrence_time(f"{day}T09:00:00+00:00", parent)
            if days == 3660:
                assert_occurrence(parent, at)
            else:
                with self.assertRaisesRegex(ValueError, "occurrence_timezone_validation_limit"):
                    assert_occurrence(parent, at)

    def test_until_and_exdate_use_series_timezone(self):
        parent = zoned("Asia/Kolkata", "2030-01-07T09:00:00+05:30", "2030-01-07T10:00:00+05:30",
                       recurrences=["RRULE:FREQ=DAILY;UNTIL=20300109", "EXDATE;TZID=Asia/Kolkata:20300108T090000"])
        assert_occurrence(parent, parse_occurrence_time("2030-01-09T09:00:00+05:30", parent))
        for day in (8, 10):
            with self.assertRaises(ValueError):
                assert_occurrence(parent, parse_occurrence_time(f"2030-01-{day:02}T09:00:00+05:30", parent))
        for exclusion in ("EXDATE;TZID=Asia/Taipei:20300108T090000", "EXDATE;TZID=Asia/Kolkata:20300108T033000Z"):
            with self.assertRaises(ValueError):
                validate_parent({**parent, "recurrences": ["RRULE:FREQ=DAILY", exclusion]})

    def test_local_exdates_in_dst_gaps_or_folds_are_rejected(self):
        parent = zoned("America/New_York", "2030-01-07T09:00:00-05:00", "2030-01-07T10:00:00-05:00")
        for stamp in ("20300310T023000", "20301103T013000"):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_parent({**parent, "recurrences": ["RRULE:FREQ=DAILY", f"EXDATE;TZID=America/New_York:{stamp}"]})


if __name__ == "__main__":
    unittest.main()
