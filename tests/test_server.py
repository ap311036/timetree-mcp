import json
import unittest

from timetree_mcp.server import TimeTreeMcpServer


class FakeBrowser:
    def __init__(self):
        self.calls = []

    def read(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation == "status":
            return {"authenticated": True, "url": "https://timetreeapp.com/calendar"}
        if operation == "login":
            return {"authenticated": True, "transport": "direct_https"}
        if operation == "logout":
            return {"authenticated": False, "transport": "direct_https"}
        if operation == "keychain_status":
            return {"available": True, "email_configured": True, "password_configured": True}
        if operation == "list_calendars":
            return {"calendars": [{"id": "personal", "name": "Personal"}]}
        if operation == "list_events":
            return {"events": [{"id": "event-1", "title": "Lunch"}]}
        if operation == "create_event":
            return {"success": True, "event": {"id": "created"}}
        if operation == "get_calendar_labels":
            return {"calendar_labels": []}
        raise AssertionError(f"unexpected operation: {operation}")


class ServerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.browser = FakeBrowser()
        self.server = TimeTreeMcpServer(self.browser)

    def test_initialize_negotiates_supported_protocol_and_tools_capability(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )

        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(response["result"]["capabilities"], {"tools": {}, "logging": {}})

    def test_tools_list_exposes_all_requested_tools_in_deterministic_order(self):
        response = self.server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )

        tools = response["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(names[:3], [
            "timetree_list_calendars",
            "timetree_list_events",
            "timetree_status",
        ])
        for expected in (
            "timetree_login",
            "timetree_logout",
            "timetree_keychain_status",
            "timetree_create_event",
            "timetree_update_event",
            "timetree_delete_event",
            "timetree_list_memos",
            "timetree_create_memo",
            "timetree_update_memo",
            "timetree_delete_memo",
            "timetree_list_event_comments",
            "timetree_add_event_comment",
            "timetree_update_event_comment",
            "timetree_delete_event_comment",
            "timetree_get_calendar_labels",
            "timetree_update_calendar_labels",
            "timetree_get_calendar_members",
            "timetree_get_calendar_virtual_members",
            "timetree_start_monitor",
            "timetree_stop_monitor",
            "timetree_monitor_status",
        ):
            self.assertIn(expected, names)

    def test_status_tool_returns_structured_json_and_calls_browser(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "timetree_status", "arguments": {}},
            }
        )

        self.assertEqual(self.browser.calls, [("status", {})])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["authenticated"], True)
        self.assertEqual(response["result"]["structuredContent"], payload)

    def test_login_and_logout_tools_use_direct_transport_operations(self):
        login = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "timetree_login", "arguments": {}},
            }
        )
        logout = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "timetree_logout", "arguments": {}},
            }
        )

        self.assertEqual(self.browser.calls[:2], [("login", {}), ("logout", {})])
        self.assertFalse(login["result"]["isError"])
        self.assertFalse(logout["result"]["isError"])

    def test_keychain_status_never_returns_credential_values(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "timetree_keychain_status", "arguments": {}},
            }
        )

        self.assertEqual(self.browser.calls[:1], [("keychain_status", {})])
        self.assertEqual(response["result"]["structuredContent"]["password_configured"], True)
        self.assertNotIn("password", response["result"]["structuredContent"])

    def test_list_events_passes_filters_to_browser(self):
        arguments = {
            "start_date": "2026-08-25",
            "end_date": "2026-08-26",
            "calendar_id": "personal",
            "query": "lunch",
            "limit": 10,
        }

        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "timetree_list_events", "arguments": arguments},
            }
        )

        self.assertEqual(self.browser.calls, [("list_events", arguments)])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["events"][0]["title"], "Lunch")

    def test_mutating_tool_requires_explicit_confirmation_without_calling_browser(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "timetree_create_event",
                    "arguments": {
                        "calendar_id": "personal",
                        "title": "No write",
                        "start_at": "2026-08-25T01:00:00Z",
                        "end_at": "2026-08-25T02:00:00Z",
                        "confirm": False,
                    },
                },
            }
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("confirm", response["result"]["structuredContent"]["error"])
        self.assertEqual(self.browser.calls, [])

    def test_confirmed_create_event_builds_payload_and_calls_browser(self):
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "timetree_create_event",
                    "arguments": {
                        "calendar_id": "personal",
                        "title": "Created",
                        "start_at": "2026-08-25T01:00:00Z",
                        "end_at": "2026-08-25T02:00:00Z",
                        "confirm": True,
                    },
                },
            }
        )

        operation, arguments = self.browser.calls[0]
        self.assertEqual(operation, "create_event")
        self.assertEqual(arguments["calendar_id"], "personal")
        self.assertEqual(arguments["payload"]["start_at"], 1787619600000)
        self.assertNotIn("confirm", arguments["payload"])
        self.assertFalse(response["result"]["isError"])

    def test_read_metadata_and_monitor_status_do_not_require_browser_writes(self):
        labels = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "timetree_get_calendar_labels",
                    "arguments": {"calendar_id": "personal"},
                },
            }
        )
        status = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "timetree_monitor_status", "arguments": {}},
            }
        )

        self.assertEqual(self.browser.calls, [("get_calendar_labels", {"calendar_id": "personal"})])
        self.assertFalse(status["result"]["structuredContent"]["active"])
        self.assertFalse(labels["result"]["isError"])

    def test_notifications_have_no_response_and_unknown_method_is_json_rpc_error(self):
        self.assertIsNone(
            self.server.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

        response = self.server.handle(
            {"jsonrpc": "2.0", "id": 5, "method": "unknown/method", "params": {}}
        )
        self.assertEqual(response["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
