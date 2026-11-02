import threading
import unittest
from unittest.mock import patch

from timetree_mcp import monitor
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
        self.metadata = {"sync_complete": True, "calendars": [{"id": "calendar-1"}]}

    def read(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation != "sync_events":
            raise AssertionError(operation)
        return {"events": self.snapshot, **self.metadata}


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

    def test_incomplete_snapshots_never_replace_baseline_or_send(self):
        for metadata in ({"sync_complete": False}, {"sync_complete": 1},
                         {"incomplete_calendars": [{}]}, {"skipped_calendars": [{}]},
                         {"skipped_unreadable_events": 1}, {"has_more": True},
                         {"error": "private detail"}, {"authenticated": False}):
            with self.subTest(metadata=metadata):
                reader = FakeReader([event("1", "Initial")])
                sent = []
                manager = MonitorManager(reader, sent.append)
                manager.poll_once()
                reader.snapshot = []
                reader.metadata.update(metadata)
                with self.assertRaisesRegex(ValueError, "monitor_snapshot_incomplete"):
                    manager.poll_once()
                self.assertFalse(manager.status()["last_check_complete"])
                self.assertFalse(manager.status()["baseline_advanced"])
                reader.snapshot = [event("1", "Initial")]
                reader.metadata = FakeReader([]).metadata
                self.assertEqual(manager.poll_once()["changes"], [])
                self.assertEqual(sent, [])

    def test_missing_complete_marker_events_or_calendars_rejected(self):
        for payload in ({"events": []}, {"sync_complete": True, "calendars": []},
                        {"sync_complete": True, "events": []},
                        {"sync_complete": True, "calendars": [], "events": None}):
            with self.subTest(payload=payload):
                reader = FakeReader([])
                reader.read = lambda *args: payload
                manager = MonitorManager(reader)
                with self.assertRaises(ValueError):
                    manager.poll_once()
                self.assertFalse(manager.status()["baseline_initialized"])

    def test_invalid_identity_and_wrong_calendar_rejected_before_baseline(self):
        bad_events = [[event("1", "a"), event("1", "b")],
                      [{**event("1", "a"), "id": None, "uuid": None}],
                      [{**event("1", "a"), "calendar_id": "other"}],
                      [{**event("1", "a"), "calendar_id": None}], [None]]
        for events in bad_events:
            with self.subTest(events=events):
                manager = MonitorManager(FakeReader(events))
                with self.assertRaises(ValueError):
                    manager.poll_once("calendar-1")
                self.assertFalse(manager.status()["baseline_initialized"])

    def test_all_calendar_membership_change_does_not_cancel_old_events(self):
        reader = FakeReader([event("1", "Initial")])
        sent = []
        manager = MonitorManager(reader, sent.append)
        manager.poll_once()
        reader.snapshot = []
        reader.metadata["calendars"] = []
        with self.assertRaisesRegex(ValueError, "monitor_calendar_scope_changed"):
            manager.poll_once()
        self.assertEqual(sent, [])

    def test_reader_mutation_cannot_change_saved_baseline(self):
        reader = FakeReader([event("1", "Initial")])
        manager = MonitorManager(reader, lambda notification: None)
        manager.poll_once()
        reader.snapshot[0]["title"] = "Changed in place"
        self.assertEqual(manager.poll_once()["updated"], 1)

    def test_no_sink_keeps_stable_pending_then_retries_once(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        status = manager.status()
        self.assertEqual(status["notifications_sent"], 0)
        self.assertEqual(status["pending_not_sent_count"], 1)
        batch_id = status["pending_batches"][0]["batch_id"]
        sent = []
        manager.set_notification_sink(sent.append)
        manager.poll_once()
        manager.poll_once()
        self.assertEqual([item["batch_id"] for item in sent], [batch_id])
        self.assertEqual(manager.status()["pending_count"], 0)
        self.assertEqual(manager.status()["transport_writes_succeeded"], 1)

    def test_known_not_sent_can_retry_without_recreating_batch(self):
        reader = FakeReader([])
        attempts = []
        def fail_before_write(notification):
            attempts.append(notification["batch_id"])
            raise monitor.NotificationNotSent("private serialization details")
        manager = MonitorManager(reader, fail_before_write)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        manager.poll_once()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(manager.status()["pending_not_sent_count"], 1)
        self.assertEqual(manager.status()["last_error"], "monitor_notification_not_sent")
        self.assertNotIn("private", str(manager.status()))

    def test_unknown_outcome_is_barrier_even_after_sink_change_and_stop(self):
        reader = FakeReader([])
        attempts = []
        def uncertain(notification):
            attempts.append(notification["batch_id"])
            notification["changes"].clear()
            raise RuntimeError("private transport details")
        manager = MonitorManager(reader, uncertain)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        manager.stop()
        sent = []
        manager.set_notification_sink(sent.append)
        reader.snapshot.append(event("2", "Later"))
        manager.poll_once()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sent, [])
        status = manager.status()
        self.assertEqual(status["pending_unknown_count"], 1)
        self.assertEqual(status["pending_not_sent_count"], 1)
        self.assertEqual(status["pending_count"], 2)
        self.assertNotIn("private", str(status))

    def test_callback_mutation_does_not_corrupt_retry_payload(self):
        reader = FakeReader([])
        def mutate(notification):
            notification["changes"].clear()
            raise monitor.NotificationNotSent()
        manager = MonitorManager(reader, mutate)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        sent = []
        manager.set_notification_sink(sent.append)
        manager.poll_once()
        self.assertEqual(sent[0]["changes"][0]["title"], "Added")

    def test_pending_capacity_preserves_baseline_and_can_recover_not_sent(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        manager.poll_once()
        with patch.object(monitor, "MAX_PENDING_BATCHES", 1):
            reader.snapshot = [event("1", "Added")]
            manager.poll_once()
            reader.snapshot.append(event("2", "Later"))
            with self.assertRaisesRegex(ValueError, "monitor_pending_capacity_exceeded"):
                manager.poll_once()
            self.assertEqual(manager.status()["pending_count"], 1)
            sent = []
            manager.set_notification_sink(sent.append)
            result = manager.poll_once()
            self.assertEqual(result["added"], 1)
            self.assertEqual(len(sent), 2)
            self.assertEqual(manager.status()["pending_count"], 0)

    def test_snapshot_work_limits_preserve_last_success(self):
        reader = FakeReader([event("1", "Initial")])
        manager = MonitorManager(reader)
        manager.poll_once()
        last_success = manager.status()["last_success"]
        with patch.object(monitor, "MAX_SNAPSHOT_EVENTS", 0):
            with self.assertRaisesRegex(ValueError, "monitor_snapshot_capacity_exceeded"):
                manager.poll_once()
        self.assertEqual(manager.status()["last_success"], last_success)

    def test_reader_exception_is_sanitized(self):
        reader = FakeReader([])
        def fail(*args):
            raise RuntimeError("private credential or event detail")
        reader.read = fail
        manager = MonitorManager(reader)
        with self.assertRaisesRegex(ValueError, "^monitor_read_failed$"):
            manager.poll_once()
        self.assertNotIn("private", str(manager.status()))

    def test_returned_changes_cannot_mutate_queued_notification(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        result = manager.poll_once()
        result["changes"][0]["title"] = "Caller mutation"
        result["changes"].clear()
        sent = []
        manager.set_notification_sink(sent.append)
        manager.poll_once()
        self.assertEqual(sent[0]["changes"][0]["title"], "Added")

    def test_partial_read_does_not_dispatch_existing_pending(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        sent = []
        manager.set_notification_sink(sent.append)
        reader.metadata["sync_complete"] = False
        with self.assertRaises(ValueError):
            manager.poll_once()
        self.assertEqual(manager.status()["pending_count"], 1)
        self.assertEqual(sent, [])

    def test_unknown_full_queue_keeps_old_baseline_and_all_batches(self):
        reader = FakeReader([])
        def uncertain(notification):
            raise monitor.NotificationDeliveryUncertain()
        manager = MonitorManager(reader, uncertain)
        manager.poll_once()
        with patch.object(monitor, "MAX_PENDING_BATCHES", 1):
            reader.snapshot = [event("1", "Added")]
            manager.poll_once()
            before = manager.status()
            reader.snapshot.append(event("2", "Later"))
            with self.assertRaisesRegex(ValueError, "monitor_pending_capacity_exceeded"):
                manager.poll_once()
            after = manager.status()
            self.assertEqual(after["last_success"], before["last_success"])
            self.assertEqual(after["pending_batches"], before["pending_batches"])
            self.assertFalse(after["baseline_advanced"])

    def test_queue_bytes_and_baseline_bytes_limits_preserve_state(self):
        for name, code in (("MAX_PENDING_BYTES", "monitor_pending_capacity_exceeded"),
                           ("MAX_BASELINE_BYTES", "monitor_baseline_capacity_exceeded"),
                           ("MAX_SNAPSHOT_BYTES", "monitor_snapshot_capacity_exceeded"),
                           ("MAX_SNAPSHOT_NODES", "monitor_snapshot_capacity_exceeded")):
            with self.subTest(name=name):
                reader = FakeReader([])
                manager = MonitorManager(reader)
                manager.poll_once()
                before = manager.status()["last_success"]
                reader.snapshot = [event("1", "Added")]
                with patch.object(monitor, name, 1):
                    with self.assertRaisesRegex(ValueError, code):
                        manager.poll_once()
                self.assertEqual(manager.status()["last_success"], before)
                self.assertEqual(manager.status()["pending_count"], 0)

    def test_deep_or_cyclic_event_rejected_with_bounded_work(self):
        for cyclic in (False, True):
            item = event("1", "Initial")
            if cyclic:
                item["attachment"] = item
            else:
                nested = []
                item["attachment"] = nested
                for _ in range(40):
                    inner = []
                    nested.append(inner)
                    nested = inner
            manager = MonitorManager(FakeReader([item]))
            with self.assertRaisesRegex(ValueError, "monitor_snapshot_capacity_exceeded"):
                manager.poll_once()

    def test_calendar_scopes_and_same_uuid_in_distinct_calendars_are_independent(self):
        reader = FakeReader([event("1", "First")])
        reader.metadata["calendars"].append({"id": "__all__"})
        reader.snapshot.append({**event("1", "Second"), "calendar_id": "__all__"})
        manager = MonitorManager(reader)
        self.assertEqual(manager.poll_once()["event_count"], 2)
        reader.snapshot = reader.snapshot[1:]
        self.assertTrue(manager.poll_once("__all__")["primed"])
        self.assertEqual(manager.status()["baseline_scope_count"], 2)

    def test_pending_survives_scope_switch_and_unknown_blocks_other_scope(self):
        reader = FakeReader([])
        reader.metadata["calendars"].append({"id": "calendar-2"})
        def uncertain(notification):
            raise RuntimeError("unknown")
        manager = MonitorManager(reader, uncertain)
        manager.poll_once("calendar-1")
        reader.snapshot = [event("1", "Added")]
        manager.poll_once("calendar-1")
        old_batch = manager.status()["pending_batches"][0]
        reader.snapshot = []
        manager.poll_once("calendar-2")
        sent = []
        manager.set_notification_sink(sent.append)
        reader.snapshot = [{**event("2", "Second"), "calendar_id": "calendar-2"}]
        manager.poll_once("calendar-2")
        self.assertEqual(manager.status()["pending_batches"][0], old_batch)
        self.assertEqual(manager.status()["pending_count"], 2)
        self.assertEqual(sent, [])

    def test_concurrent_poll_is_busy_without_second_read(self):
        entered, release = threading.Event(), threading.Event()
        reader = FakeReader([])
        original_read = reader.read
        errors = []
        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test cleanup did not release reader")
            return original_read(*args)
        reader.read = blocked
        manager = MonitorManager(reader)
        def poll():
            try:
                manager.poll_once()
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=poll)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, "monitor_poll_busy"):
                manager.poll_once()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(reader.calls), 1)

    def test_duplicate_numeric_identity_is_rejected_even_with_distinct_uuids(self):
        records = [{**event("a", "First"), "id": 1}, {**event("b", "Second"), "id": 1}]
        manager = MonitorManager(FakeReader(records))
        with self.assertRaisesRegex(ValueError, "monitor_snapshot_duplicate_identity"):
            manager.poll_once()

    def test_scope_capacity_does_not_discard_existing_baseline(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        with patch.object(monitor, "MAX_BASELINE_SCOPES", 1):
            manager.poll_once()
            with self.assertRaisesRegex(ValueError, "monitor_baseline_capacity_exceeded"):
                manager.poll_once("calendar-1")
            self.assertEqual(manager.status()["baseline_scope_count"], 1)
            self.assertFalse(manager.poll_once()["primed"])

    def test_sink_can_stop_its_background_thread_without_deadlock(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        manager.poll_once()
        called = threading.Event()
        statuses = []
        def sink(notification):
            statuses.append(manager.stop())
            called.set()
        manager.set_notification_sink(sink)
        reader.snapshot = [event("1", "Added")]
        try:
            manager.start(interval_seconds=30)
            self.assertTrue(called.wait(2))
        finally:
            manager.stop()
        self.assertTrue(statuses[0]["stopping"])
        self.assertEqual(statuses[0]["pending_in_flight_count"], 1)
        self.assertEqual(manager.status()["notifications_sent"], 1)
        self.assertFalse(manager.status()["active"])

    def test_unknown_batch_survives_actual_background_restart(self):
        reader = FakeReader([])
        calls = []
        def uncertain(notification):
            calls.append(notification["batch_id"])
            raise RuntimeError("unknown")
        manager = MonitorManager(reader, uncertain)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        batch = manager.status()["pending_batches"][0]
        read_started = threading.Event()
        read_original = reader.read
        def read(*args):
            read_started.set()
            return read_original(*args)
        reader.read = read
        try:
            manager.start(interval_seconds=30)
            self.assertTrue(read_started.wait(2))
        finally:
            manager.stop()
        self.assertEqual(manager.status()["pending_batches"], [batch])
        self.assertEqual(calls, [batch["batch_id"]])

    def test_reentrant_poll_is_busy_and_cannot_change_baseline(self):
        reader = FakeReader([])
        manager = MonitorManager(reader)
        busy = []
        def sink(notification):
            self.assertEqual(manager.status()["pending_in_flight_count"], 1)
            with self.assertRaisesRegex(ValueError, "monitor_poll_busy"):
                manager.poll_once()
            busy.append(True)
        manager.set_notification_sink(sink)
        manager.poll_once()
        reader.snapshot = [event("1", "Added")]
        manager.poll_once()
        self.assertEqual(busy, [True])
        self.assertEqual(manager.status()["pending_count"], 0)

    def test_stop_timeout_retains_old_thread_and_prevents_restart(self):
        entered, release = threading.Event(), threading.Event()
        reader = FakeReader([])
        original_read = reader.read
        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("test cleanup did not release reader")
            return original_read(*args)
        reader.read = blocked
        manager = MonitorManager(reader)
        try:
            manager.start(interval_seconds=30)
            self.assertTrue(entered.wait(2))
            with patch.object(monitor, "STOP_TIMEOUT_SECONDS", 0.01):
                status = manager.stop()
            self.assertTrue(status["active"])
            self.assertTrue(status["stopping"])
            with self.assertRaisesRegex(ValueError, "already active"):
                manager.start(interval_seconds=30)
        finally:
            release.set()
            manager.stop()
        self.assertFalse(manager.status()["active"])
        self.assertFalse(manager.status()["baseline_initialized"])


if __name__ == "__main__":
    unittest.main()
