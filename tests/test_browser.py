import unittest
from unittest.mock import patch

from timetree_mcp.browser import EgoBrowserReader, _script_for, parse_result_line, run_ego_browser_script


class FakeRunner:
    def __init__(self, output):
        self.output = output
        self.script = None

    def __call__(self, script):
        self.script = script
        return self.output


class BrowserReaderTests(unittest.TestCase):
    def test_run_script_accepts_cli_log_written_to_stderr(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": "",
                "stderr": '__TIMETREE_MCP_RESULT__{"authenticated":true}\n',
            },
        )()

        with patch("timetree_mcp.browser.subprocess.run", return_value=completed):
            output = run_ego_browser_script("cliLog('probe')")

        self.assertEqual(parse_result_line(output), {"authenticated": True})

    def test_parse_result_line_reads_only_the_marked_json_payload(self):
        payload = parse_result_line(
            "diagnostic line\n__TIMETREE_MCP_RESULT__{\"authenticated\":true}\n"
        )

        self.assertEqual(payload, {"authenticated": True})

    def test_status_uses_ego_browser_task_space_without_credentials(self):
        runner = FakeRunner(
            "__TIMETREE_MCP_RESULT__{\"authenticated\":true,\"url\":\"https://timetreeapp.com/calendar\"}\n"
        )
        reader = EgoBrowserReader(runner=runner)

        result = reader.read("status", {})

        self.assertEqual(result["authenticated"], True)
        self.assertIn("useOrCreateTaskSpace", runner.script)
        self.assertNotIn("TIMETREE_PASSWORD", runner.script)
        self.assertNotIn("TIMETREE_EMAIL", runner.script)

    def test_list_events_normalizes_and_filters_browser_payload(self):
        runner = FakeRunner(
            "__TIMETREE_MCP_RESULT__"
            + '{"authenticated":true,"calendars":[{"id":"personal","name":"Personal"}],'
            + '"events":[{"id":"1","calendar_id":"personal","title":"Lunch",'
            + '"start_at":"2026-08-25T03:00:00Z","end_at":"2026-08-25T04:00:00Z"},'
            + '{"id":"2","calendar_id":"personal","title":"Later",'
            + '"start_at":"2026-08-27T03:00:00Z","end_at":"2026-08-27T04:00:00Z"}]}\n'
        )
        reader = EgoBrowserReader(runner=runner)

        result = reader.read(
            "list_events",
            {"start_date": "2026-08-25", "end_date": "2026-08-26"},
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["calendar_name"], "Personal")
        self.assertEqual(result["events"][0]["title"], "Lunch")

    def test_unauthenticated_payload_is_a_clear_error(self):
        reader = EgoBrowserReader(
            runner=FakeRunner(
                '__TIMETREE_MCP_RESULT__{"authenticated":false,"reason":"login required"}\n'
            )
        )

        with self.assertRaisesRegex(RuntimeError, "login"):
            reader.read("list_calendars", {})

    def test_list_memos_filters_category_two_events(self):
        reader = EgoBrowserReader(
            runner=FakeRunner(
                '__TIMETREE_MCP_RESULT__{"authenticated":true,"calendars":[{"id":"personal","name":"Personal"}],'
                '"events":[{"id":"memo","calendar_id":"personal","category":2,"title":"Buy milk",'
                '"start_at":"2026-08-25T00:00:00Z","end_at":"2026-08-25T00:00:00Z"},'
                '{"id":"event","calendar_id":"personal","category":1,"title":"Meeting",'
                '"start_at":"2026-08-25T01:00:00Z","end_at":"2026-08-25T02:00:00Z"}]}\n'
            )
        )

        result = reader.read("list_memos", {"calendar_id": "personal"})

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["memos"][0]["title"], "Buy milk")

    def test_list_event_comments_filters_non_comment_activities(self):
        reader = EgoBrowserReader(
            runner=FakeRunner(
                '__TIMETREE_MCP_RESULT__{"authenticated":true,"event_activities":['
                '{"id":"comment","type":0,"attachment":{"content":"hello"}},'
                '{"id":"system","type":1,"attachment":{"content":"edited"}},'
                '{"id":"deleted","type":0,"deactivated_at":1,"attachment":{"content":"gone"}}]}\n'
            )
        )

        result = reader.read(
            "list_event_comments",
            {"calendar_id": "personal", "event_uuid": "event-1"},
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["comments"][0]["content"], "hello")

    def test_write_script_contains_csrf_header_and_known_endpoint_shapes(self):
        script = _script_for(
            "create_event",
            {"calendar_id": "personal", "payload": {"title": "Test"}},
        )

        self.assertIn("X-CSRF-Token", script)
        self.assertIn("/api/v1/calendar/", script)
        self.assertIn("/event", script)
        self.assertIn("method: \"POST\"", script)


if __name__ == "__main__":
    unittest.main()
