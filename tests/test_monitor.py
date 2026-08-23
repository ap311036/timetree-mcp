import unittest

from timetree_mcp.monitor import MonitorManager


def event(event_id, title):
    return {
        "id": event_id,
        "uuid": event_id,
        "calendar_id": "calendar-1",
        "calendar_name": "Personal",
        "title": title,
        "start": "2026-08-25T01:00:00.000Z",
        "end": "2026-08-25T02:00:00.000Z",
        "updated_at": title,
    }


class FakeReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def read(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation != "sync_events":
            raise AssertionError(operation)
        return {"events": self.snapshot}


class MonitorTests(unittest.TestCase):
    def test_first_poll_primes_and_next_poll_notifies_added_and_updated_events(self):
        reader = FakeReader([event("1", "Initial")])
        notifications = []
        manager = MonitorManager(reader, notification_sink=notifications.append)

        first = manager.poll_once(calendar_id="calendar-1")
        self.assertTrue(first["primed"])
        self.assertEqual(notifications, [])

        reader.snapshot = [event("1", "Updated"), event("2", "Added")]
        second = manager.poll_once(calendar_id="calendar-1")

        self.assertEqual(second["added"], 1)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(
            {(change["kind"], change["event_id"]) for change in notifications[0]["changes"]},
            {("added", "2"), ("updated", "1")},
        )

    def test_next_poll_notifies_removed_events(self):
        reader = FakeReader([event("1", "Initial"), event("2", "Removed")])
        notifications = []
        manager = MonitorManager(reader, notification_sink=notifications.append)
        manager.poll_once(calendar_id="calendar-1")

        reader.snapshot = [event("1", "Initial")]
        result = manager.poll_once(calendar_id="calendar-1")

        self.assertEqual(result["removed"], 1)
        self.assertEqual(notifications[0]["changes"][0]["kind"], "removed")
        self.assertEqual(notifications[0]["changes"][0]["event_id"], "2")

    def test_monitor_status_starts_inactive(self):
        manager = MonitorManager(FakeReader([]))

        status = manager.status()

        self.assertFalse(status["active"])
        self.assertIsNone(status["calendar_id"])
        self.assertEqual(status["notifications_sent"], 0)


if __name__ == "__main__":
    unittest.main()
