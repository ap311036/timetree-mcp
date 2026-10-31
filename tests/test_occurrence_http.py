import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from timetree_mcp.http_reader import HttpResponse, TimeTreeHttpReader, UrllibTransport, _NoMutationRedirect
from timetree_mcp.server import TimeTreeMcpServer
from test_occurrence_editor import SimulatedCalendar


class CalendarTransport:
    def __init__(self):
        self.calendar = SimulatedCalendar()
        self.calls = []
        self.sync_override = self.create_override = None

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append((method, url, copy.deepcopy(body), headers))
        if method == "GET" and url.endswith("/calendars?since=0"):
            return HttpResponse(200, {}, {"calendars": [{"id": 42, "name": "Demo"}]})
        if method == "GET" and "/events/sync?" in url:
            payload = {"events": copy.deepcopy(self.calendar.events), "chunk": False, "since": 0}
            return HttpResponse(200, {}, self.sync_override if self.sync_override is not None else payload)
        if method == "POST":
            uuid = self.calendar.create("42", body)
            return HttpResponse(201, {}, self.create_override if self.create_override is not None
                                else {"event": {"uuid": uuid, "calendar_id": 42}})
        if method == "PUT":
            self.calendar.exclude("42", "series-demo", body["recurrences"])
            return HttpResponse(200, {}, {"event": self.calendar.events[0]})
        raise AssertionError("unexpected test request")


class OccurrenceHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / "journal" / "edits.sqlite3"
        self.transport = CalendarTransport()
        self.reader = TimeTreeHttpReader(transport=self.transport, base_url="https://example.test",
                                         occurrence_journal_path=self.path)
        self.reader._authenticated = True
        self.reader._csrf_token = "synthetic-csrf"
        self.server = TimeTreeMcpServer(self.reader)
        self.args = {"calendar_id": "42", "event_uuid": "series-demo",
                     "occurrence_start_at": "2030-01-14T09:00:00+08:00"}

    def call(self, name, arguments):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}})["result"]

    def request(self):
        read = self.call("timetree_get_occurrence_for_edit", self.args)
        self.assertFalse(read["isError"], read)
        return {**self.args, "new_start_at": "2030-01-14T11:00:00+08:00",
                "expected_revision": read["structuredContent"]["revision"],
                "operation_id": "http-move", "confirm": True}

    def test_mcp_to_http_contract_and_restart_replay(self):
        request = self.request()
        response = self.call("timetree_move_occurrence", request)
        self.assertFalse(response["isError"], response)
        writes = [call for call in self.transport.calls if call[0] in {"POST", "PUT"}]
        self.assertEqual([x[0] for x in writes], ["POST", "PUT"])
        self.assertTrue(writes[0][1].endswith("/api/v1/calendar/42/event"))
        self.assertEqual(writes[0][2]["parent_id"], "series-demo")
        self.assertNotIn("recurrences", writes[0][2])
        self.assertNotIn("confirm", writes[0][2])
        self.assertTrue(writes[1][1].endswith("/api/v1/calendar/42/event/series-demo"))
        self.assertEqual(set(writes[1][2]), {"recurrences", "silent"})
        self.assertEqual(writes[1][2]["recurrences"][-1], "EXDATE:20300114T010000Z")
        self.assertTrue(all(x[3]["X-CSRF-Token"] == "synthetic-csrf" for x in writes))
        offline = Mock()
        offline.request.side_effect = AssertionError("replay must be offline")
        restarted = TimeTreeHttpReader(transport=offline, base_url="https://example.test",
                                       occurrence_journal_path=self.path)
        self.assertEqual(restarted.read("move_occurrence", request), response["structuredContent"])
        self.assertTrue(restarted.read("occurrence_operation_status", {"operation_id": "http-move"})["completed"])
        offline.request.assert_not_called()

    def test_partial_or_malformed_sync_never_allows_writes(self):
        invalid = [
            {"events": [], "chunk": False, "sync_complete": False},
            {"events": [], "chunk": False, "truncated": True},
            {"events": []}, {"events": {}, "chunk": False},
            {"events": [None], "chunk": False},
            {"events": [], "chunk": True, "since": 0},
            {"events": [], "chunk": True, "since": 1.5},
            {"events": [{"id": "x", "calendar_id": "43"}], "chunk": False},
        ]
        for payload in invalid:
            self.transport.sync_override = payload
            response = self.call("timetree_get_occurrence_for_edit", self.args)
            self.assertTrue(response["isError"], payload)
        self.assertEqual(self.transport.calendar.posts, 0)

    def test_invalid_create_response_blocks_exclusion_with_structured_uncertainty(self):
        request = self.request()
        self.transport.create_override = {"event": {"id": "missing-uuid"}}
        response = self.call("timetree_move_occurrence", request)
        self.assertTrue(response["isError"])
        result = response["structuredContent"]
        self.assertEqual(result["phase"], "create_dispatched")
        self.assertFalse(result["retry_safe"])
        self.assertEqual((self.transport.calendar.posts, self.transport.calendar.puts), (1, 0))

    def test_confirm_missing_does_not_authenticate_or_initialize_journal(self):
        self.reader._authenticated = False
        response = self.call("timetree_move_occurrence", self.args)
        self.assertTrue(response["isError"])
        self.assertIn("confirm", response["structuredContent"]["error"])
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.path.exists())

    def test_unknown_operation_status_does_not_authenticate_or_create_journal(self):
        self.reader._authenticated = False
        response = self.call("timetree_occurrence_operation_status", {"operation_id": "unknown"})
        self.assertFalse(response["isError"])
        self.assertFalse(response["structuredContent"]["found"])
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.path.exists())

    def test_mutations_use_non_redirecting_transport(self):
        transport = UrllibTransport()
        response = Mock(status=200, headers={})
        response.read.return_value = b'{}'
        transport.opener = Mock()
        transport.mutation_opener = Mock()
        transport.mutation_opener.open.return_value = response
        transport.request("POST", "https://example.test/event", {"title": "Synthetic"})
        transport.opener.open.assert_not_called()
        transport.mutation_opener.open.assert_called_once()
        self.assertIsNone(_NoMutationRedirect().redirect_request(None, None, 302, "", {}, "https://example.test/other"))
