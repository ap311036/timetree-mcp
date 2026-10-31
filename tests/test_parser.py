import unittest

from timetree_mcp.parser import normalize_event, query_events


class NormalizeEventTests(unittest.TestCase):
    def test_normalizes_time_tree_event_attributes_and_label(self):
        raw = {
            "id": "event-1",
            "uuid": "uuid-1",
            "calendar_id": "calendar-1",
            "title": "Dentist",
            "all_day": False,
            "start_at": "2026-08-25T01:00:00.000Z",
            "end_at": "2026-08-25T02:00:00.000Z",
            "start_timezone": "Asia/Taipei",
            "end_timezone": "Asia/Taipei",
            "location": "Clinic",
            "note": "Bring insurance card",
            "label": {"id": "label-1", "name": "Health", "color": "#ff0000"},
        }

        event = normalize_event(raw, calendar_name="Personal")

        self.assertEqual(
            event,
            {
                "id": "event-1",
                "uuid": "uuid-1",
                "calendar_id": "calendar-1",
                "calendar_name": "Personal",
                "title": "Dentist",
                "all_day": False,
                "start": "2026-08-25T01:00:00.000Z",
                "end": "2026-08-25T02:00:00.000Z",
                "timezone": "Asia/Taipei",
                "location": "Clinic",
                "note": "Bring insurance card",
                "label": {"id": "label-1", "name": "Health", "color": "#ff0000"},
            },
        )

    def test_normalizes_epoch_milliseconds_to_iso_utc(self):
        event = normalize_event(
            {
                "id": "event-epoch",
                "calendar_id": "calendar-1",
                "title": "Epoch event",
                "start_at": 1792447200000,
                "end_at": 1792450800000,
            },
            calendar_name="Personal",
        )

        self.assertEqual(event["start"], "2026-10-19T22:00:00.000Z")
        self.assertEqual(event["end"], "2026-10-19T23:00:00.000Z")

    def test_normalizes_numeric_calendar_id_to_string(self):
        event = normalize_event(
            {
                "id": "event-number-calendar",
                "calendar_id": 57571629,
                "title": "Calendar id",
                "start_at": "2026-08-25T01:00:00Z",
                "end_at": "2026-08-25T02:00:00Z",
            }
        )

        self.assertEqual(event["calendar_id"], "57571629")

    def test_preserves_management_fields_for_memos_and_monitoring(self):
        event = normalize_event(
            {
                "id": "event-managed",
                "uuid": "uuid-managed",
                "calendar_id": "calendar-1",
                "title": "Memo",
                "category": 2,
                "label_id": 3,
                "start_at": "2026-08-25T00:00:00Z",
                "end_at": "2026-08-25T00:00:00Z",
                "attachment": {"checklist": [{"title": "Buy milk"}]},
                "attendees": [11],
                "alerts": [5],
                "created_at": 1787616000000,
                "updated_at": 1787619600000,
                "deactivated_at": None,
            }
        )

        self.assertEqual(event["category"], 2)
        self.assertEqual(event["label_id"], 3)
        self.assertEqual(event["attachment"]["checklist"][0]["title"], "Buy milk")
        self.assertEqual(event["attendees"], [11])
        self.assertEqual(event["created_at"], "2026-08-25T00:00:00.000Z")
        self.assertEqual(event["updated_at"], "2026-08-25T01:00:00.000Z")


class QueryEventsTests(unittest.TestCase):
    def test_returns_overlapping_events_sorted_and_filters_hidden_categories(self):
        events = [
            {
                "id": "late",
                "title": "Late",
                "start": "2026-08-26T01:00:00Z",
                "end": "2026-08-26T02:00:00Z",
                "calendar_id": "work",
            },
            {
                "id": "overlap",
                "title": "Overlapping",
                "start": "2026-08-25T23:30:00Z",
                "end": "2026-08-26T00:30:00Z",
                "calendar_id": "personal",
            },
            {
                "id": "memo",
                "title": "Private note",
                "category": "memo",
                "start": "2026-08-25T04:00:00Z",
                "end": "2026-08-25T05:00:00Z",
                "calendar_id": "personal",
            },
            {
                "id": "birthday",
                "title": "Birthday",
                "type": "birthday",
                "start": "2026-08-25T03:00:00Z",
                "end": "2026-08-25T04:00:00Z",
                "calendar_id": "personal",
            },
        ]

        result = query_events(
            events,
            start_date="2026-08-26",
            end_date="2026-08-27",
        )

        self.assertEqual([event["id"] for event in result], ["overlap", "late"])

    def test_filters_numeric_category_two_memos_from_event_queries(self):
        events = [
            {
                "id": "memo",
                "category": 2,
                "title": "Memo",
                "start": "2026-08-25T01:00:00Z",
                "end": "2026-08-25T01:00:00Z",
            }
        ]

        result = query_events(events, start_date="2026-08-25", end_date="2026-08-26")

        self.assertEqual(result, [])

    def test_filters_by_calendar_query_and_limit(self):
        events = [
            {
                "id": "1",
                "title": "Team meeting",
                "note": "Bring draft",
                "start": "2026-08-25T01:00:00Z",
                "end": "2026-08-25T02:00:00Z",
                "calendar_id": "work",
            },
            {
                "id": "2",
                "title": "Team lunch",
                "start": "2026-08-25T03:00:00Z",
                "end": "2026-08-25T04:00:00Z",
                "calendar_id": "personal",
            },
        ]

        result = query_events(
            events,
            start_date="2026-08-25",
            end_date="2026-08-26",
            calendar_id="work",
            query="DRAFT",
            limit=1,
        )

        self.assertEqual([event["id"] for event in result], ["1"])

    def test_filters_by_integer_calendar_id(self):
        events = [
            {
                "id": "1",
                "title": "Match",
                "start": "2026-08-25T01:00:00Z",
                "end": "2026-08-25T02:00:00Z",
                "calendar_id": "35207884",
            },
            {
                "id": "2",
                "title": "Other",
                "start": "2026-08-25T03:00:00Z",
                "end": "2026-08-25T04:00:00Z",
                "calendar_id": "35207796",
            },
        ]

        result = query_events(
            events,
            start_date="2026-08-25",
            end_date="2026-08-26",
            calendar_id=35207884,
        )

        self.assertEqual([event["id"] for event in result], ["1"])

    def test_rejects_invalid_date_range_and_limit(self):
        with self.assertRaises(ValueError):
            query_events([], start_date="2026-08-27", end_date="2026-08-26")

        with self.assertRaises(ValueError):
            query_events([], start_date="not-a-date", end_date="2026-08-26")

        with self.assertRaises(ValueError):
            query_events([], start_date="2026-08-25", end_date="2026-08-26", limit=0)


if __name__ == "__main__":
    unittest.main()
