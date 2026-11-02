import io
import json
import unittest
from unittest.mock import patch

from timetree_mcp.server import run_stdio
from timetree_mcp.monitor import MonitorManager


class CapturingServer:
    def set_notification_sink(self, sink):
        self.sink = sink

    def shutdown(self):
        pass


class RecordingOutput:
    def __init__(self, failure=None):
        self.failure = failure
        self.writes = []
        self.flushes = 0

    def write(self, text):
        self.writes.append(text)
        if self.failure == "write":
            raise OSError("synthetic private transport detail")
        if self.failure == "interrupt_write":
            raise KeyboardInterrupt()
        if self.failure == "short":
            return len(text) - 1
        if self.failure == "none":
            return None
        return len(text)

    def flush(self):
        self.flushes += 1
        if self.failure == "flush":
            raise OSError("synthetic private transport detail")
        if self.failure == "interrupt_flush":
            raise KeyboardInterrupt()


class StdioDeliveryTests(unittest.TestCase):
    def test_interrupted_write_or_flush_poison_transport_and_preserve_interrupt(self):
        for failure in ("interrupt_write", "interrupt_flush"):
            with self.subTest(failure=failure):
                output, server = RecordingOutput(failure), CapturingServer()
                with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", output):
                    run_stdio(server)
                    with self.assertRaises(KeyboardInterrupt):
                        server.sink({"type": "timetree_change", "changes": []})
                    output.failure = None
                    with self.assertRaisesRegex(RuntimeError, "stdio_transport_unavailable"):
                        server.sink({"type": "timetree_change", "changes": []})
                self.assertEqual(len(output.writes), 1)

    def test_success_requires_one_complete_json_line_and_flush(self):
        output, server = RecordingOutput(), CapturingServer()
        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", output):
            run_stdio(server)
            server.sink({"type": "timetree_change", "changes": []})
        self.assertEqual(len(output.writes), 1)
        self.assertTrue(output.writes[0].endswith("\n"))
        self.assertEqual(json.loads(output.writes[0])["method"], "notifications/message")
        self.assertEqual(output.flushes, 1)

    def test_serialization_failure_is_known_unsent_and_does_not_poison_transport(self):
        output, server = RecordingOutput(), CapturingServer()
        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", output):
            run_stdio(server)
            with self.assertRaisesRegex(RuntimeError, "stdio_message_not_serializable"):
                server.sink({"unsupported": object()})
            self.assertEqual(output.writes, [])
            self.assertEqual(output.flushes, 0)
            server.sink({"type": "timetree_change", "changes": []})
        self.assertEqual(len(output.writes), 1)

    def test_flush_uncertainty_retains_monitor_batch_and_prevents_a_second_send(self):
        class Reader:
            title = "Before"

            def read(self, operation, arguments):
                return {"sync_complete": True, "calendars": [{"id": "42"}],
                        "events": [{"id": "synthetic", "uuid": "synthetic", "calendar_id": "42",
                                    "title": self.title, "start": "2030-01-01T01:00:00+00:00",
                                    "end": "2030-01-01T01:30:00+00:00"}]}

        reader, output, server = Reader(), RecordingOutput("flush"), CapturingServer()
        manager = MonitorManager(reader)
        manager.poll_once("42")
        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", output):
            run_stdio(server)
            manager.set_notification_sink(server.sink)
            reader.title = "After"
            result = manager.poll_once("42")
            self.assertTrue(result["check_complete"])
            status = manager.status()
            self.assertEqual(status["pending_unknown_count"], 1)
            self.assertEqual(status["transport_writes_succeeded"], 0)
            batch = status["pending_batches"][0]["batch_id"]
            output.failure = None
            self.assertEqual(manager.poll_once("42")["changes"], [])
            self.assertEqual(manager.status()["pending_batches"][0]["batch_id"], batch)
        self.assertEqual(len(output.writes), 1)

    def test_possible_partial_write_or_flush_failure_is_unknown_and_no_later_write_occurs(self):
        for failure in ("write", "short", "none", "flush"):
            with self.subTest(failure=failure):
                output, server = RecordingOutput(failure), CapturingServer()
                with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", output):
                    run_stdio(server)
                    with self.assertRaisesRegex(RuntimeError, "stdio_delivery_uncertain"):
                        server.sink({"type": "timetree_change", "changes": []})
                    output.failure = None
                    with self.assertRaisesRegex(RuntimeError, "stdio_transport_unavailable"):
                        server.sink({"type": "timetree_change", "changes": []})
                self.assertEqual(len(output.writes), 1)
                self.assertEqual(output.flushes, 1 if failure == "flush" else 0)


if __name__ == "__main__":
    unittest.main()
