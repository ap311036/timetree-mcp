import json
from contextlib import closing
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from timetree_mcp.occurrence_journal import (
    OccurrenceJournal,
    OccurrenceJournalError,
    default_journal_path,
)


def competing_claim(arguments):
    path, operation_id = arguments
    return OccurrenceJournal(Path(path)).claim(operation_id, "hash", "same-series", {})


class OccurrenceJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "private" / "occurrence-edits.sqlite3"
        self.journal = OccurrenceJournal(self.path)

    def claim(self):
        self.assertTrue(self.journal.claim("operation", "hash", "series", {"snapshot_hash": "abc"}))

    def advance(self):
        phases = ["prepared", "create_dispatched", "child_created", "child_verified", "exclude_dispatched"]
        for expected, following in zip(phases, phases[1:]):
            self.assertTrue(self.journal.transition("operation", "hash", expected, following, {"child_id": "child"}))

    def test_missing_find_is_read_only_and_lazy(self):
        self.assertFalse(self.path.parent.exists())
        self.assertIsNone(self.journal.find("operation"))
        self.assertFalse(self.path.parent.exists())

    def test_claim_can_be_replayed_after_reopen_without_overwriting(self):
        self.claim()
        reopened = OccurrenceJournal(self.path)
        self.assertEqual(reopened.find("operation"), {
            "request_hash": "hash", "series_target": "series", "phase": "prepared",
            "data": {"snapshot_hash": "abc"}, "result": None, "completed": False,
        })
        self.assertFalse(reopened.claim("operation", "other-hash", "other-series", {}))
        self.assertEqual(reopened.find("operation")["request_hash"], "hash")

    def test_series_lock_is_atomic_across_processes(self):
        context = multiprocessing.get_context("spawn")
        with context.Pool(2) as pool:
            outcomes = pool.map(competing_claim, [(str(self.path), "one"), (str(self.path), "two")])
        self.assertEqual(sorted(outcomes), [False, True])
        rows = [self.journal.find("one"), self.journal.find("two")]
        self.assertEqual(sum(row is not None for row in rows), 1)

    def test_success_releases_series_lock_but_preserves_operation_result(self):
        self.claim()
        self.advance()
        result = {"ok": True, "child_id": "child"}
        self.assertTrue(self.journal.finish("operation", "hash", "exclude_dispatched", result, {}))
        reopened = OccurrenceJournal(self.path)
        row = reopened.find("operation")
        self.assertEqual(row["phase"], "verified_complete")
        self.assertTrue(row["completed"])
        self.assertEqual(row["result"], result)
        self.assertTrue(reopened.claim("next-operation", "next-hash", "series", {}))
        self.assertFalse(reopened.claim("operation", "hash", "series", {}))
        self.assertFalse(reopened.finish("operation", "hash", "exclude_dispatched", {"ok": False}, {}))
        self.assertEqual(reopened.find("operation")["result"], result)

    def test_uncertain_result_keeps_durable_series_lock(self):
        self.claim()
        result = {"ok": False, "error": "uncertain"}
        self.assertTrue(self.journal.finish("operation", "hash", "prepared", result, {}))
        reopened = OccurrenceJournal(self.path)
        self.assertEqual(reopened.find("operation")["phase"], "uncertain_blocked")
        self.assertFalse(reopened.find("operation")["completed"])
        self.assertFalse(reopened.claim("retry", "hash", "series", {}))
        self.assertTrue(reopened.claim("unrelated", "hash", "other-series", {}))
        self.assertFalse(reopened.finish("operation", "hash", "prepared", {"ok": False}, {}))
        self.assertFalse(reopened.transition("operation", "hash", "prepared", "create_dispatched", {}))
        self.assertEqual(reopened.find("operation")["result"], result)

    def test_phase_and_request_hash_are_compare_and_swap_guards(self):
        self.claim()
        self.assertFalse(self.journal.transition("operation", "wrong", "prepared", "create_dispatched", {}))
        self.assertFalse(self.journal.transition("operation", "hash", "child_created", "child_verified", {}))
        self.assertFalse(self.journal.finish("operation", "wrong", "prepared", {"ok": False}, {}))
        self.assertTrue(self.journal.transition("operation", "hash", "prepared", "create_dispatched", {}))
        self.assertFalse(self.journal.transition("operation", "hash", "prepared", "create_dispatched", {}))
        for expected, next_phase in [("prepared", "child_created"), ("unknown", "prepared"), ([], "prepared")]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.journal.transition("operation", "hash", expected, next_phase, {})
        with self.assertRaises(ValueError):
            self.journal.finish("operation", "hash", "create_dispatched", {"ok": True}, {})

    def test_input_validation_rejects_invalid_values_before_creating_files(self):
        for value in [None, 12, "", "x" * 513, "a\x00b", "\ud800"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.journal.find(value)
        cyclic = {}; cyclic["nested"] = cyclic
        for data in [[], None, {1: "value"}, {"a": float("nan")}, cyclic, {"value": "x" * 350000}]:
            with self.subTest(data_type=type(data).__name__), self.assertRaises(ValueError):
                self.journal.claim("operation", "hash", "series", data)
        for result in [{}, {"ok": 1}, {"ok": None}, [], {"ok": False, "detail": "x" * 350000}]:
            with self.subTest(result_type=type(result).__name__), self.assertRaises(ValueError):
                self.journal.finish("operation", "hash", "prepared", result, {})
        self.assertFalse(self.path.parent.exists())

    def test_capacity_is_measured_in_utf8_bytes(self):
        data = {"hash": "a" * 349989}
        self.assertEqual(len(json.dumps(data, separators=(",", ":")).encode()), 350000)
        self.assertTrue(self.journal.claim("operation", "hash", "series", data))
        with self.assertRaises(ValueError):
            self.journal.claim("other", "hash", "series", {"hash": "界" * 116664})

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    def test_private_permissions_and_delete_journal_mode(self):
        self.claim()
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.path.chmod(0o644)
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics")
    def test_insecure_directory_is_refused_without_chmod(self):
        self.path.parent.mkdir(mode=0o755)
        self.path.parent.chmod(0o755)
        with self.assertRaises(OccurrenceJournalError):
            self.claim()
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o755)
        self.assertFalse(self.path.exists())

    def test_symlink_file_directory_and_sidecar_are_refused(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        self.path.parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")
        with self.assertRaises(OccurrenceJournalError):
            self.claim()
        self.path.parent.unlink()
        self.path.parent.mkdir(mode=0o700)
        self.path.symlink_to(target / "absent")
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")
        self.path.unlink()
        self.claim()
        Path(str(self.path) + "-journal").symlink_to(target / "absent")
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")

    def test_file_replaced_with_symlink_during_open_is_not_written(self):
        self.claim()
        other_path = self.root / "other" / "journal.sqlite3"
        other = OccurrenceJournal(other_path)
        self.assertTrue(other.claim("sentinel", "hash", "other-series", {}))
        real_connect = sqlite3.connect

        def replace_at_open(*args, **kwargs):
            self.path.unlink()
            self.path.symlink_to(other_path)
            return real_connect(*args, **kwargs)

        with patch("timetree_mcp.occurrence_journal.sqlite3.connect", side_effect=replace_at_open):
            with self.assertRaises(OccurrenceJournalError):
                self.journal.claim("unexpected", "hash", "new-series", {})
        self.assertIsNone(other.find("unexpected"))
        self.assertIsNotNone(other.find("sentinel"))

    def test_corrupt_file_and_sqlite_failures_are_sanitized(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text("private-calendar-content")
        self.path.chmod(0o600)
        with self.assertRaises(OccurrenceJournalError) as failure:
            self.journal.find("operation")
        self.assertNotIn(str(self.path), str(failure.exception))
        self.assertNotIn("private-calendar", str(failure.exception))
        self.assertTrue(failure.exception.__suppress_context__)
        self.path.unlink()
        with patch("timetree_mcp.occurrence_journal.sqlite3.connect", side_effect=sqlite3.OperationalError("private-path")):
            with self.assertRaises(OccurrenceJournalError) as failure:
                self.claim()
        self.assertNotIn("private-path", str(failure.exception))

    def test_regular_file_replacement_during_open_is_not_written(self):
        self.claim()
        other_path = self.root / "other" / "journal.sqlite3"
        other = OccurrenceJournal(other_path)
        self.assertTrue(other.claim("sentinel", "hash", "other-series", {}))
        real_connect = sqlite3.connect

        def replace_at_open(*args, **kwargs):
            self.path.rename(self.path.with_suffix(".saved"))
            other_path.rename(self.path)
            return real_connect(*args, **kwargs)

        with patch("timetree_mcp.occurrence_journal.sqlite3.connect", side_effect=replace_at_open):
            with self.assertRaises(OccurrenceJournalError):
                self.journal.claim("unexpected", "hash", "new-series", {})
        self.assertIsNone(self.journal.find("unexpected"))
        self.assertIsNotNone(self.journal.find("sentinel"))

    def test_transaction_failure_rolls_back_and_does_not_release_lock(self):
        self.claim()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON occurrence_edits "
                               "BEGIN SELECT RAISE(ABORT, 'private-data'); END")
            connection.commit()
        with self.assertRaises(OccurrenceJournalError) as failure:
            self.journal.transition("operation", "hash", "prepared", "create_dispatched", {})
        self.assertNotIn("private-data", str(failure.exception))
        self.assertEqual(self.journal.find("operation")["phase"], "prepared")
        self.assertFalse(self.journal.claim("another", "hash", "series", {}))

    def test_invalid_persisted_json_and_terminal_state_fail_closed(self):
        self.claim()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE occurrence_edits SET data_json = '[]'")
            connection.commit()
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE occurrence_edits SET data_json = '{}', completed = 1")
            connection.commit()
        with self.assertRaises(OccurrenceJournalError):
            self.journal.find("operation")

    def test_invalid_paths_fail_closed(self):
        for path in ["string", Path("relative/file.db"), self.root / ".." / "file.db"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                OccurrenceJournal(path)

    def test_default_path_environment_precedence_and_validation(self):
        with patch.dict(os.environ, {"TIMETREE_STATE_DIR": str(self.root / "override"), "XDG_STATE_HOME": str(self.root / "state")}, clear=True):
            self.assertEqual(default_journal_path(), self.root / "override" / "occurrence-edits.sqlite3")
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")}, clear=True):
            self.assertEqual(default_journal_path(), self.root / "state" / "timetree-mcp" / "occurrence-edits.sqlite3")
        for name in ["TIMETREE_STATE_DIR", "XDG_STATE_HOME"]:
            with patch.dict(os.environ, {name: "relative"}, clear=True), self.assertRaises(ValueError):
                default_journal_path()
        with patch.dict(os.environ, {}, clear=True), patch("pathlib.Path.home", return_value=self.root):
            self.assertEqual(default_journal_path(), self.root / ".local/state/timetree-mcp/occurrence-edits.sqlite3")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=True), patch("timetree_mcp.occurrence_journal.sys.platform", "win32"):
            self.assertEqual(default_journal_path(), self.root / "timetree-mcp/occurrence-edits.sqlite3")


if __name__ == "__main__":
    unittest.main()
