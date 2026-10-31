"""Reproduce the offline README demo; never connect to TimeTree or Keychain."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from timetree_mcp.server import TimeTreeMcpServer


class DemoReader:
    """Synthetic responses in place of the authenticated HTTPS transport."""

    def __init__(self):
        self.writes = 0
        self.events = [{
            "id": "demo-lunch", "calendar_id": "demo-family",
            "calendar_name": "Demo family", "title": "Family lunch",
            "start": "2026-11-07T12:00:00+08:00",
            "end": "2026-11-07T13:00:00+08:00",
        }]

    def read(self, operation, arguments):
        if operation == "list_calendars":
            return {"calendars": [{"id": "demo-family", "name": "Demo family"}]}
        if operation in {"list_events", "sync_events"}:
            return {"events": deepcopy(self.events), "sync_complete": True}
        if operation == "create_event":
            self.writes += 1
            return {"demo_only": True, "success": True,
                    "event": {"id": "demo-created", "title": arguments["payload"]["title"]}}
        raise AssertionError(f"Unexpected demo operation: {operation}")


def show(label, value):
    print(label)
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def main():
    reader = DemoReader()
    server = TimeTreeMcpServer(reader)
    notifications = []
    server.set_notification_sink(notifications.append)

    def call(name, arguments):
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}})
        result = response["result"]
        show(name, {"isError": result["isError"], **result["structuredContent"]})
        return result

    print("OFFLINE DEMO: synthetic calendar; no credentials, network, or real writes.")
    try:
        call("timetree_list_calendars", {})
        call("timetree_list_events", {"calendar_id": "demo-family"})
        event = {"calendar_id": "demo-family", "title": "Saturday picnic",
                 "start_at": "2026-11-07T14:00:00+08:00",
                 "end_at": "2026-11-07T15:00:00+08:00"}
        rejected = call("timetree_create_event", event)
        assert rejected["isError"] and reader.writes == 0
        accepted = call("timetree_create_event", {**event, "confirm": True})
        assert not accepted["isError"] and reader.writes == 1

        # Two immediate polls keep the example deterministic; no background wait.
        baseline = server.monitor.poll_once("demo-family")
        assert baseline["primed"] and not notifications
        show("Monitor baseline", baseline)
        reader.events[0]["title"] = "Family lunch at the park"
        change = server.monitor.poll_once("demo-family")
        assert change["updated"] == 1 and len(notifications) == 1
        show("Monitor change", change)
        show("Notification changes (timestamp omitted)", notifications[0]["changes"])
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
