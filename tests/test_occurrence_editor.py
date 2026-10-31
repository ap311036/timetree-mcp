import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from timetree_mcp.occurrence_editor import OccurrenceEditor
from timetree_mcp.occurrence_journal import OccurrenceJournal


def millis(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def series():
    return {
        "id": "series-demo", "uuid": "series-demo", "calendar_id": "42",
        "primary_id": 100, "category": 1, "type": 0, "title": "Weekly planning",
        "all_day": False, "start_at": millis("2030-01-07T09:00:00+08:00"),
        "end_at": millis("2030-01-07T10:00:00+08:00"),
        "start_timezone": "Asia/Taipei", "end_timezone": "Asia/Taipei",
        "label_id": 2, "note": "Synthetic fixture", "location": "Room A",
        "attendees": [10], "alerts": [30], "files": [],
        "attachment": {"checklist": [{"title": "Agenda", "checked": False}]},
        "recurrences": ["RRULE:FREQ=WEEKLY", "EXDATE:20300107T010000Z"],
        "recurring_uuid": "series-demo", "parent_id": None, "updated_at": 1,
    }


class SimulatedCalendar:
    def __init__(self):
        parent = series()
        sibling = {**copy.deepcopy(parent), "id": "prior-child", "uuid": "prior-child",
                   "parent_id": parent["id"], "recurrences": [], "primary_id": 101}
        self.events = [parent, sibling]
        self.posts = self.puts = self.reads = 0
        self.on_create = self.on_exclude = self.on_read = None

    def snapshot(self, calendar_id):
        if calendar_id != "42":
            raise ValueError("unknown_calendar")
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return copy.deepcopy(self.events)

    def create(self, calendar_id, payload):
        self.posts += 1
        child = copy.deepcopy(payload)
        child.pop("silent")
        child.pop("file_uuids", None)
        child.update(id="new-child", uuid="new-child", calendar_id=calendar_id,
                     primary_id=102, recurrences=[], recurring_uuid="series-demo",
                     files=[], updated_at=2)
        self.events.append(child)
        if self.on_create:
            self.on_create(child)
        return child["uuid"]

    def exclude(self, calendar_id, event_uuid, rules):
        self.puts += 1
        assert (calendar_id, event_uuid) == ("42", "series-demo")
        self.events[0]["recurrences"] = copy.deepcopy(rules)
        self.events[0]["updated_at"] = 3
        if self.on_exclude:
            self.on_exclude()


class OccurrenceEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "journal" / "edits.sqlite3"
        self.calendar = SimulatedCalendar()
        self.journal = OccurrenceJournal(self.path)
        self.editor = self.make_editor()
        self.args = {"calendar_id": "42", "event_uuid": "series-demo",
                     "occurrence_start_at": "2030-01-14T09:00:00+08:00"}

    def make_editor(self):
        return OccurrenceEditor(self.calendar.snapshot, self.calendar.create,
                                self.calendar.exclude, self.journal,
                                namespace="https://example.test")

    def request(self):
        preflight = self.editor.preflight(self.args)
        self.assertTrue(preflight["editable"])
        return {**self.args, "new_start_at": "2030-01-14T11:00:00+08:00",
                "expected_revision": preflight["revision"],
                "operation_id": "move-demo-1", "confirm": True}

    def test_two_writes_preserve_metadata_and_replay_after_restart_without_network(self):
        request = self.request()
        before = copy.deepcopy(self.calendar.events)
        result = self.editor.move(request)
        self.assertTrue(result["ok"])
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 1))
        self.assertEqual(self.calendar.events[0]["recurrences"],
                         before[0]["recurrences"] + ["EXDATE:20300114T010000Z"])
        self.assertEqual(self.calendar.events[1], before[1])
        child = self.calendar.events[-1]
        self.assertEqual(child["end_at"] - child["start_at"], 3600000)
        for key in ("title", "note", "location", "attachment", "attendees", "alerts"):
            self.assertEqual(child[key], before[0][key])
        self.journal = OccurrenceJournal(self.path)
        self.editor = self.make_editor()
        self.calendar.on_read = lambda _: self.fail("replay must not read upstream")
        self.assertEqual(self.editor.move(request), result)
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 1))

    def test_missing_confirmation_rejected_before_journal_or_network(self):
        for confirm in (None, False, 1, "true"):
            with self.subTest(confirm=confirm):
                request = {**self.args, "new_start_at": "2030-01-14T11:00:00+08:00",
                           "expected_revision": "a" * 64, "operation_id": "op", "confirm": confirm}
                with self.assertRaisesRegex(ValueError, "confirm"):
                    self.editor.move(request)
        self.assertEqual(self.calendar.reads, 0)
        self.assertFalse(self.path.exists())

    def test_same_operation_id_changed_intent_rejected(self):
        request = self.request()
        self.editor.move(request)
        with self.assertRaisesRegex(ValueError, "operation_id_conflict"):
            self.editor.move({**request, "new_start_at": "2030-01-14T12:00:00+08:00"})
        self.assertEqual(self.calendar.posts, 1)

    def test_post_response_loss_locks_series_and_never_retries(self):
        request = self.request()
        self.calendar.on_create = lambda _: (_ for _ in ()).throw(TimeoutError("private detail"))
        result = self.editor.move(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "create_dispatched")
        self.assertNotIn("private detail", str(result))
        self.assertEqual(self.editor.move(request), result)
        next_args = {**self.args, "occurrence_start_at": "2030-01-21T09:00:00+08:00"}
        revision = self.editor.preflight(next_args)["revision"]
        locked = self.editor.move({**request, **next_args, "expected_revision": revision,
                                   "operation_id": "different-operation"})
        self.assertEqual(locked["code"], "occurrence_series_locked_do_not_retry")
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 0))

    def test_put_response_loss_is_uncertain_even_when_applied(self):
        request = self.request()
        self.calendar.on_exclude = lambda: (_ for _ in ()).throw(TimeoutError())
        result = self.editor.move(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "exclude_dispatched")
        self.assertEqual(result["created_child_uuid"], "new-child")
        self.assertEqual(self.editor.move(request), result)
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 1))

    def test_journal_failure_before_dispatch_prevents_all_calendar_writes(self):
        request = self.request()
        with patch.object(self.journal, "transition", side_effect=OSError("unavailable")):
            result = self.editor.move(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "prepared")
        self.assertFalse(result["calendar_write_may_have_occurred"])
        self.assertEqual((self.calendar.posts, self.calendar.puts), (0, 0))

    def test_journal_failure_after_child_creation_prevents_parent_write(self):
        request = self.request()
        transition = self.journal.transition
        def fail_child_record(op, fingerprint, before, after, data):
            if after == "child_created":
                raise OSError("storage unavailable")
            return transition(op, fingerprint, before, after, data)
        with patch.object(self.journal, "transition", side_effect=fail_child_record):
            result = self.editor.move(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["created_child_uuid"], "new-child")
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 0))
        self.journal = OccurrenceJournal(self.path)
        self.assertEqual(self.make_editor().move(request), result)

    def test_process_interruption_leaves_durable_dispatch_record_without_retry(self):
        request = self.request()
        self.calendar.on_create = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.editor.move(request)
        self.journal = OccurrenceJournal(self.path)
        self.editor = self.make_editor()
        self.calendar.on_read = lambda _: self.fail("uncertain replay must be offline")
        result = self.editor.move(request)
        self.assertFalse(result["ok"])
        self.assertEqual(result["phase"], "create_dispatched")
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 0))

    def test_stale_revision_and_bad_occurrence_never_write(self):
        request = self.request()
        self.calendar.events[1]["title"] = "Changed elsewhere"
        with self.assertRaisesRegex(ValueError, "event_changed"):
            self.editor.move(request)
        with self.assertRaises(ValueError):
            self.editor.preflight({**self.args, "occurrence_start_at": "2030-01-15T09:00:00+08:00"})
        self.assertEqual((self.calendar.posts, self.calendar.puts), (0, 0))

    def test_detected_snapshot_change_stops_dispatch(self):
        for read_number, expected_phase, posts in [(3, "prepared", 0), (5, "child_verified", 1)]:
            with self.subTest(read_number=read_number):
                # Each iteration gets independent storage and snapshots.
                self.calendar = SimulatedCalendar()
                self.journal = OccurrenceJournal(self.path.with_name(f"race-{read_number}.sqlite3"))
                self.editor = self.make_editor()
                request = self.request()
                def race(n):
                    if n == read_number:
                        self.calendar.events[1]["note"] = "Concurrent edit"
                self.calendar.on_read = race
                result = self.editor.move(request)
                self.assertFalse(result["ok"])
                self.assertEqual(result["phase"], expected_phase)
                self.assertEqual((self.calendar.posts, self.calendar.puts), (posts, 0))

    def test_wrong_child_or_collateral_parent_change_blocks_second_write(self):
        mutations = [lambda child: child.update(parent_id=100),
                     lambda child: child.update(note="Lost note"),
                     lambda child: child.update(recurrences=["RRULE:FREQ=DAILY"])]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.calendar = SimulatedCalendar()
                self.journal = OccurrenceJournal(self.path.with_name(f"bad-{index}.sqlite3"))
                self.editor = self.make_editor()
                request = self.request()
                self.calendar.on_create = mutate
                result = self.editor.move(request)
                self.assertFalse(result["ok"])
                self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 0))

    def test_failed_final_verification_never_reports_success(self):
        request = self.request()
        self.calendar.on_exclude = lambda: self.calendar.events[1].update(note="Changed")
        self.assertFalse(self.editor.move(request)["ok"])
        self.assertEqual((self.calendar.posts, self.calendar.puts), (1, 1))

    def test_unobservable_race_is_explicitly_outside_the_concurrency_guarantee(self):
        request = self.request()
        exclude = self.editor.exclude
        def concurrent_edit_then_exclusion(calendar_id, event_uuid, rules):
            # The unofficial API has no verified conditional update. A change
            # in this gap cannot be detected from the resulting snapshot.
            self.calendar.events[0]["recurrences"].append("EXDATE:20300121T010000Z")
            exclude(calendar_id, event_uuid, rules)
        self.editor.exclude = concurrent_edit_then_exclusion
        result = self.editor.move(request)
        self.assertTrue(result["ok"])
        self.assertFalse(result["compare_and_swap"])
        self.assertIn("overwritten without detection", result["warning"])

    def test_uncorroborated_series_and_unchanged_time_are_rejected(self):
        request = self.request()
        with self.assertRaisesRegex(ValueError, "occurrence_time_unchanged"):
            self.editor.move({**request, "new_start_at": self.args["occurrence_start_at"]})
        self.assertFalse(self.path.exists())
        self.calendar.events.pop()
        read = self.editor.preflight(self.args)
        self.assertFalse(read["editable"])
        with self.assertRaisesRegex(ValueError, "child_link_contract_unverified"):
            self.editor.move({**request, "expected_revision": read["revision"]})
        self.assertEqual((self.calendar.posts, self.calendar.puts), (0, 0))

    def test_status_has_no_calendar_text_and_never_reads_upstream(self):
        request = self.request()
        self.editor.move(request)
        self.calendar.on_read = lambda _: self.fail("status must be local")
        result = self.editor.operation_status({"operation_id": request["operation_id"]})
        self.assertTrue(result["completed"])
        self.assertNotIn("Weekly planning", str(result))
        self.assertNotIn("Synthetic fixture", str(result))
        self.assertFalse(self.editor.operation_status({"operation_id": "unknown"})["found"])
