import unittest

from timetree_mcp.operations import (
    build_create_event_payload,
    build_memo_payload,
    build_update_event_payload,
    require_confirmation,
)


class WritePayloadTests(unittest.TestCase):
    def test_create_event_converts_iso_times_and_builds_attachment(self):
        payload = build_create_event_payload(
            {
                "calendar_id": "57571629",
                "title": "Test event",
                "start_at": "2026-08-25T01:00:00Z",
                "end_at": "2026-08-25T02:00:00Z",
                "confirm": True,
                "checklist": [{"title": "Bring card"}],
                "virtual_user_attendees": ["pet"],
            }
        )

        self.assertEqual(payload["start_at"], 1787619600000)
        self.assertEqual(payload["end_at"], 1787623200000)
        self.assertEqual(payload["category"], 1)
        self.assertEqual(payload["attendees"], [])
        self.assertEqual(
            payload["attachment"],
            {"checklist": [{"title": "Bring card"}], "virtual_user_attendees": ["pet"]},
        )
        self.assertNotIn("confirm", payload)
        self.assertNotIn("calendar_id", payload)

    def test_update_event_preserves_empty_values_and_only_sends_changes(self):
        payload = build_update_event_payload(
            {
                "calendar_id": "57571629",
                "event_uuid": "event-1",
                "confirm": True,
                "note": "",
                "start_at": "2026-08-25T01:00:00Z",
                "checklist": [],
            }
        )

        self.assertEqual(
            payload,
            {
                "note": "",
                "start_at": 1787619600000,
                "attachment": {"checklist": []},
            },
        )

    def test_memo_payload_is_an_all_day_category_two_event(self):
        payload = build_memo_payload(
            {
                "calendar_id": "57571629",
                "title": "Shopping list",
                "date": "2026-08-25",
                "confirm": True,
            }
        )

        self.assertEqual(payload["category"], 2)
        self.assertTrue(payload["all_day"])
        self.assertEqual(payload["start_at"], 1787616000000)
        self.assertEqual(payload["end_at"], 1787616000000)
        self.assertEqual(payload["start_timezone"], "UTC")

    def test_mutating_operations_require_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "confirm"):
            require_confirmation({})
        with self.assertRaisesRegex(ValueError, "confirm"):
            require_confirmation({"confirm": False})

        self.assertIsNone(require_confirmation({"confirm": True}))


if __name__ == "__main__":
    unittest.main()
