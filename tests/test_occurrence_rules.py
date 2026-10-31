"""Synthetic contracts for the deliberately narrow single-occurrence adapter."""

import copy
import hashlib
import unittest

from timetree_mcp.occurrence_rules import (
    assert_occurrence,
    canonical,
    child_payload,
    corroborated_children,
    digest,
    excluded_rules,
    parse_taipei,
    validate_parent,
    verify_child,
    verify_parent,
)


def fixture(**changes):
    event = {
        "id": "series-demo", "uuid": "series-demo", "calendar_id": 7001,
        "title": "Example meeting", "category": 1, "type": 0, "all_day": False,
        "start_at": parse_taipei("2030-01-07T09:00:00+08:00"),
        "end_at": parse_taipei("2030-01-07T10:00:00+08:00"),
        "start_timezone": "Asia/Taipei", "end_timezone": "Asia/Taipei",
        "label_id": 2, "attendees": [3, 8], "alerts": [10],
        "recurrences": ["RRULE:FREQ=DAILY;COUNT=5"],
        "note": "Bring a notebook", "location": "Example room",
        "location_lat": "25.0", "location_lon": "121.5",
        "attachment": {
            "url": "https://example.com/agenda",
            "checklist": [{"title": "Notebook", "checked": False}],
            "virtual_user_attendees": ["guest-demo"],
            "ogp": {"title": "Example agenda", "url": "https://example.com/agenda"},
        },
        "files": [{"uuid": "file-demo", "type": "other", "original_file_name": "agenda.txt"}],
        "updated_at": 1,
    }
    event.update(changes)
    return event


def child(parent, payload):
    result = copy.deepcopy(payload)
    result.pop("silent")
    result["files"] = [{"uuid": value} for value in result.pop("file_uuids", [])]
    result.update({"id": "child-demo", "uuid": "child-demo", "calendar_id": parent["calendar_id"],
                   "recurring_uuid": parent["uuid"], "recurrences": []})
    return result


class OccurrenceRuleTests(unittest.TestCase):
    def test_taipei_input_is_exact_and_bounded(self):
        self.assertIsInstance(parse_taipei("2030-01-07T09:00:00+08:00"), int)
        for value in [True, 1, None, "2030-02-30T09:00:00+08:00", "2030-01-07T09:00+08:00",
                      "2030-01-07T09:00:00Z", "2030-01-07T09:00:00.000+08:00",
                      "1979-12-31T09:00:00+08:00", "2030-01-07T09:00:00+08:00\n"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_taipei(value)

    def test_canonical_json_and_digest_are_stable_and_reject_non_json(self):
        value = {"z": [2, 1], "a": {"b": "測試", "a": False}}
        text = canonical(value)
        self.assertEqual(text, canonical({"a": {"a": False, "b": "測試"}, "z": [2, 1]}))
        self.assertEqual(digest(value), hashlib.sha256(text.encode("utf-8")).hexdigest())
        for bad in [float("nan"), {"x": float("inf")}, {1: "integer key"}, {"x": {1, 2}}]:
            with self.subTest(value=bad), self.assertRaises(ValueError):
                canonical(bad)
        with self.assertRaisesRegex(ValueError, "^invalid_canonical_value$"):
            canonical("\ud800")

    def test_daily_interval_count_and_exact_time(self):
        parent = fixture(recurrences=["RRULE:FREQ=DAILY;INTERVAL=2;COUNT=3"])
        for day in (7, 9, 11):
            assert_occurrence(parent, parse_taipei(f"2030-01-{day:02}T09:00:00+08:00"))
        for stamp in ("2030-01-06T09:00:00+08:00", "2030-01-08T09:00:00+08:00",
                      "2030-01-13T09:00:00+08:00", "2030-01-09T09:00:01+08:00"):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, "occurrence_missing"):
                assert_occurrence(parent, parse_taipei(stamp))

    def test_weekly_count_respects_partial_first_week_and_week_start(self):
        parent = fixture(
            start_at=parse_taipei("2030-01-09T09:00:00+08:00"),
            end_at=parse_taipei("2030-01-09T10:00:00+08:00"),
            recurrences=["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=3;WKST=MO"],
        )
        for day in (9, 21, 23):
            assert_occurrence(parent, parse_taipei(f"2030-01-{day:02}T09:00:00+08:00"))
        for stamp in ("2030-01-14T09:00:00+08:00", "2030-02-04T09:00:00+08:00"):
            with self.assertRaisesRegex(ValueError, "occurrence_missing"):
                assert_occurrence(parent, parse_taipei(stamp))
        sunday = fixture(
            start_at=parse_taipei("2030-01-06T09:00:00+08:00"),
            end_at=parse_taipei("2030-01-06T10:00:00+08:00"),
            recurrences=["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SU,MO;WKST=SU"],
        )
        assert_occurrence(sunday, parse_taipei("2030-01-07T09:00:00+08:00"))
        with self.assertRaises(ValueError):
            assert_occurrence(sunday, parse_taipei("2030-01-14T09:00:00+08:00"))

    def test_weekly_without_byday_uses_anchor_weekday(self):
        parent = fixture(recurrences=["RRULE:FREQ=WEEKLY;COUNT=2"])
        assert_occurrence(parent, parse_taipei("2030-01-14T09:00:00+08:00"))
        with self.assertRaises(ValueError):
            assert_occurrence(parent, parse_taipei("2030-01-15T09:00:00+08:00"))

    def test_until_is_inclusive_in_utc_or_local_calendar_date(self):
        for until in ("20300109", "20300109T010000Z"):
            parent = fixture(recurrences=[f"RRULE:FREQ=DAILY;UNTIL={until}"])
            assert_occurrence(parent, parse_taipei("2030-01-09T09:00:00+08:00"))
            with self.assertRaises(ValueError):
                assert_occurrence(parent, parse_taipei("2030-01-10T09:00:00+08:00"))

    def test_exdate_forms_exclude_without_extending_count(self):
        for exclusion in ("EXDATE:20300108T010000Z", "EXDATE:20300108T090000",
                          "EXDATE;TZID=Asia/Taipei:20300108T090000"):
            parent = fixture(recurrences=["RRULE:FREQ=DAILY;COUNT=2", exclusion])
            assert_occurrence(parent, parse_taipei("2030-01-07T09:00:00+08:00"))
            for day in (8, 9):
                with self.assertRaises(ValueError):
                    assert_occurrence(parent, parse_taipei(f"2030-01-{day:02}T09:00:00+08:00"))

    def test_recurrence_shape_fails_closed(self):
        invalid = [
            [], ["RRULE:FREQ=MONTHLY"], ["RRULE:FREQ=DAILY;BYDAY=MO"],
            ["RRULE:FREQ=WEEKLY;BYDAY=1MO"], ["RRULE:FREQ=DAILY;COUNT=2;UNTIL=20300109"],
            ["RRULE:FREQ=DAILY;COUNT=0"], ["RRULE:FREQ=DAILY;INTERVAL=100001"],
            ["RRULE:FREQ=DAILY;COUNT=2;COUNT=3"], ["RRULE:FREQ=DAILY;BYSETPOS=1"],
            ["RRULE:FREQ=WEEKLY;WKST=XX"], ["RRULE:FREQ=WEEKLY;BYDAY=MO,MO"],
            ["RRULE:FREQ=DAILY", "RRULE:FREQ=WEEKLY"], ["RRULE:FREQ=DAILY", "RDATE:20300108T010000Z"],
            ["RRULE:FREQ=DAILY", "EXDATE;TZID=UTC:20300108T010000"],
            ["RRULE:FREQ=DAILY", "EXDATE:20300230T010000Z"],
            ["RRULE:FREQ=DAILY;UNTIL=20300230"], ["RRULE:FREQ=DAILY;UNTIL=20300109T090000"],
            ["RRULE:FREQ=DAILY\n"], ["RRULE:FREQ=DAILY"] + ["EXDATE:20300108T010000Z"] * 100,
            ["RRULE:FREQ=DAILY;COUNT=" + "9" * 5000],
        ]
        for lines in invalid:
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                validate_parent(fixture(recurrences=lines))

    def test_parent_shape_rejects_lossy_or_ambiguous_metadata(self):
        invalid = [
            {"id": "different"}, {"calendar_id": True}, {"all_day": 0}, {"category": True},
            {"type": False}, {"start_timezone": "UTC"}, {"parent_id": "other"},
            {"recurring_uuid": "other"}, {"deactivated_at": 2}, {"unknown": "meaningful"},
            {"lunar": True}, {"attachment": {"day_count": {"kind": 1}}},
            {"attachment": {"unknown": "meaningful"}}, {"attendees": [True]},
            {"alerts": [float("inf")]}, {"label_id": True}, {"files": None},
            {"files": [{"uuid": "f"}, {"uuid": "f"}]}, {"files": [{"uuid": "f", "extra": 1}]},
            {"description": "Would be lost"}, {"location_lat": ""},
            {"attachment": {"checklist": [{"title": "x", "checked": 1}]}},
            {"attachment": {"ogp": {"title": 4}}}, {"recurrence": {"frequency": "daily"}},
            {"url": 123}, {"url": "https://example.com/a-different-url"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_parent(fixture(**changes))

    def test_duration_and_new_time_are_whole_seconds_and_at_most_one_day(self):
        parent = fixture()
        for duration in (0, -1000, 86401000, 1001):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                validate_parent(fixture(end_at=parent["start_at"] + duration))
        for value in (True, float("nan"), parent["start_at"] + 1):
            with self.assertRaises(ValueError):
                child_payload(parent, value)
        with self.assertRaisesRegex(ValueError, "^invalid_occurrence_time$"):
            validate_parent(fixture(start_at="9" * 5000))

    def test_payload_preserves_supported_metadata_without_mutating_source(self):
        parent = fixture()
        before = copy.deepcopy(parent)
        at = parse_taipei("2030-01-10T11:00:00+08:00")
        payload = child_payload(parent, at)
        self.assertEqual(payload["end_at"], at + 3600000)
        self.assertEqual(payload["parent_id"], parent["id"])
        self.assertTrue(payload["silent"])
        self.assertNotIn("recurrences", payload)
        self.assertNotIn("recurring_uuid", payload)
        self.assertEqual(payload["file_uuids"], ["file-demo"])
        self.assertEqual(payload["attachment"], parent["attachment"])
        self.assertEqual(payload["location_lat"], 25.0)
        payload["attachment"]["checklist"][0]["checked"] = True
        payload["attendees"].append(999)
        self.assertEqual(parent, before)

    def test_raw_iso_and_seconds_are_supported_with_whole_second_precision(self):
        parent = fixture(start_at="2030-01-07T01:00:00Z")
        # The end is supplied as epoch seconds to match the supported raw-time reader.
        parent["end_at"] = parse_taipei("2030-01-07T10:00:00+08:00") // 1000
        validate_parent(parent)
        assert_occurrence(parent, parse_taipei("2030-01-08T09:00:00+08:00"))

    def test_exclusion_is_one_utc_line_and_preserves_original_rules(self):
        parent = fixture(recurrences=["RRULE:FREQ=DAILY;COUNT=5", "EXDATE:20300108T010000Z"])
        before = copy.deepcopy(parent)
        rules = excluded_rules(parent, parse_taipei("2030-01-09T09:00:00+08:00"))
        self.assertEqual(rules, parent["recurrences"] + ["EXDATE:20300109T010000Z"])
        self.assertEqual(parent, before)
        with self.assertRaises(ValueError):
            excluded_rules(parent, parse_taipei("2030-01-08T09:00:00+08:00"))
        at_limit = fixture(recurrences=["RRULE:FREQ=DAILY;COUNT=5"] + ["EXDATE:20300108T010000Z"] * 99)
        with self.assertRaisesRegex(ValueError, "^recurrence_rule_limit$"):
            excluded_rules(at_limit, parse_taipei("2030-01-09T09:00:00+08:00"))

    def test_verify_child_checks_identity_content_and_preserved_fields(self):
        parent = fixture()
        payload = child_payload(parent, parse_taipei("2030-01-08T11:00:00+08:00"))
        result = child(parent, payload)
        self.assertTrue(verify_child(parent, result, payload))
        for changes in ({"id": "wrong"}, {"uuid": parent["uuid"]}, {"calendar_id": 7002},
                        {"parent_id": "wrong"}, {"recurring_uuid": "wrong"}, {"recurrences": ["RRULE:FREQ=DAILY"]},
                        {"title": "Changed"}, {"files": []}, {"attachment": {}}, {"deactivated_at": 1}):
            with self.subTest(changes=changes):
                self.assertFalse(verify_child(parent, {**result, **changes}, payload))
        self.assertFalse(verify_child(parent, result, {**payload, "extra": "injected"}))
        for value in (True, False, 2):
            self.assertFalse(verify_child(parent, {**result, "type": value}, payload))
        empty_note = fixture(note="")
        empty_payload = child_payload(empty_note, payload["start_at"])
        empty_result = child(empty_note, empty_payload)
        self.assertFalse(verify_child(empty_note, {**empty_result, "note": False}, empty_payload))

    def test_verify_parent_allows_only_rules_and_updated_at_to_change(self):
        before = fixture()
        rules = excluded_rules(before, parse_taipei("2030-01-09T09:00:00+08:00"))
        after = {**copy.deepcopy(before), "recurrences": rules, "updated_at": 2}
        self.assertTrue(verify_parent(before, after, rules))
        self.assertFalse(verify_parent(before, {**after, "note": "Concurrent change"}, rules))
        self.assertFalse(verify_parent(before, after, ["RRULE:FREQ=DAILY"]))
        self.assertFalse(verify_parent(before, None, rules))

    def test_link_corroboration_needs_existing_child_and_rejects_either_conflicting_link(self):
        parent = fixture()
        payload = child_payload(parent, parse_taipei("2030-01-08T11:00:00+08:00"))
        linked = child(parent, payload)
        self.assertFalse(corroborated_children(parent, [parent]))
        self.assertTrue(corroborated_children(parent, [copy.deepcopy(parent), linked]))
        for changes in ({"parent_id": "wrong"}, {"recurring_uuid": "wrong"}, {"id": "wrong"},
                        {"recurrences": ["RRULE:FREQ=DAILY"]}, {"calendar_id": 7002}):
            with self.subTest(changes=changes):
                self.assertFalse(corroborated_children(parent, [parent, linked, {**linked, **changes}]))


if __name__ == "__main__":
    unittest.main()
