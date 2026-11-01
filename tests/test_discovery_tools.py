"""MCP/HTTP contracts for read-only occurrence discovery and local diagnostics."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from timetree_mcp.http_reader import TimeTreeHttpReader
from timetree_mcp.server import TimeTreeMcpServer
from test_occurrence_editor import millis
from test_occurrence_http import CalendarTransport


class DiscoveryToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name).resolve() / "journal" / "edits.sqlite3"
        self.transport = CalendarTransport()
        self.credentials = Mock()
        self.credentials.read.side_effect = AssertionError("unexpected credential access")
        self.reader = TimeTreeHttpReader(transport=self.transport, base_url="https://example.test",
                                         credential_store=self.credentials, occurrence_journal_path=self.journal)
        self.reader._authenticated = True
        self.server = TimeTreeMcpServer(self.reader)
        self.args = {"calendar_id": "42", "start_at": "2030-01-14T00:00:00+08:00",
                     "end_at": "2030-01-22T00:00:00+08:00"}

    def call(self, name, arguments):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}})["result"]

    def test_query_finds_later_occurrences_with_only_reads_and_no_journal(self):
        response = self.call("timetree_list_occurrences", self.args)
        self.assertFalse(response["isError"], response)
        result = response["structuredContent"]
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["complete"])
        self.assertEqual([row["start_local"][:10] for row in result["occurrences"]], ["2030-01-14", "2030-01-21"])
        self.assertTrue(all(row["source"] == "generated" for row in result["occurrences"]))
        self.assertTrue(self.transport.calls)
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))
        self.credentials.read.assert_not_called()
        self.assertFalse(self.journal.exists())

    def test_monthly_query_does_not_enable_monthly_writes(self):
        parent = self.transport.calendar.events[0]
        parent.update(start_at=millis("2030-01-31T09:00:00+08:00"),
                      end_at=millis("2030-01-31T10:00:00+08:00"),
                      recurrences=["RRULE:FREQ=MONTHLY;COUNT=3"],
                      future_metadata={"keep": "read-only does not copy metadata"})
        self.transport.calendar.events = [parent]
        response = self.call("timetree_list_occurrences", {**self.args,
            "start_at": "2030-03-01T00:00:00+08:00", "end_at": "2030-04-01T00:00:00+08:00"})
        self.assertFalse(response["isError"], response)
        result = response["structuredContent"]
        self.assertTrue(result["complete"])
        self.assertEqual([row["start_local"][:10] for row in result["occurrences"]], ["2030-03-31"])
        parent.pop("future_metadata")
        preflight = self.call("timetree_get_occurrence_for_edit", {"calendar_id": "42", "event_uuid": "series-demo",
            "occurrence_start_at": "2030-03-31T09:00:00+08:00"})
        self.assertTrue(preflight["isError"])
        self.assertEqual(preflight["structuredContent"]["error"], "unsupported_recurrence_frequency")
        self.assertEqual((self.transport.calendar.posts, self.transport.calendar.puts), (0, 0))
        self.assertFalse(self.journal.exists())

    def test_limit_and_unsupported_series_cannot_claim_complete_coverage(self):
        result = self.call("timetree_list_occurrences", {**self.args, "limit": 1})["structuredContent"]
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["has_more"])
        self.assertFalse(result["complete"])
        self.transport.calendar.events[0]["recurrences"] = ["RRULE:FREQ=HOURLY"]
        response = self.call("timetree_list_occurrences", self.args)
        self.assertFalse(response["isError"], response)
        self.assertFalse(response["structuredContent"]["complete"])
        self.assertTrue(response["structuredContent"]["unsupported"])

    def test_invalid_query_never_authenticates_or_reads(self):
        self.reader._authenticated = False
        for changes in ({"calendar_id": 42}, {"calendar_id": "../other"}, {"start_at": "2030-01-14"},
                        {"start_at": "2030-01-14T00:00:00Z"}, {"end_at": self.args["start_at"]},
                        {"end_at": "2031-01-14T00:00:00+08:00"}, {"query": []}, {"query": "x" * 501},
                        {"limit": True}, {"limit": 501}, {"confirm": True}):
            with self.subTest(changes=changes):
                response = self.call("timetree_list_occurrences", {**self.args, **changes})
                self.assertTrue(response["isError"], response)
        self.assertTrue(self.call("timetree_list_occurrences", {})["isError"])
        self.assertEqual(self.transport.calls, [])
        self.credentials.read.assert_not_called()

    def test_partial_calendar_snapshot_is_not_expanded(self):
        self.transport.sync_override = {"events": [], "chunk": False, "sync_complete": False}
        response = self.call("timetree_list_occurrences", self.args)
        self.assertTrue(response["isError"])
        self.assertNotIn("occurrences", response["structuredContent"])
        self.assertTrue(all(call[0] == "GET" for call in self.transport.calls))
        self.assertFalse(self.journal.exists())

    def test_timezone_diagnostic_runs_without_reader_or_credentials(self):
        self.reader._authenticated = False
        with patch.object(self.reader, "read", side_effect=AssertionError("diagnostics must be local")):
            response = self.call("timetree_timezone_status", {"timezone": "Asia/Kolkata"})
        self.assertFalse(response["isError"], response)
        self.assertIn("Asia/Kolkata", str(response["structuredContent"]))
        self.assertEqual(self.transport.calls, [])
        self.credentials.read.assert_not_called()
        self.assertFalse(self.journal.exists())

    def test_bad_timezone_diagnostic_arguments_never_reach_reader(self):
        for arguments in ({"timezone": "../private"}, {"timezone": 42}, {"timezone": None}, {"extra": True}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call("timetree_timezone_status", arguments)["isError"])
        self.assertEqual(self.transport.calls, [])
        self.credentials.read.assert_not_called()
