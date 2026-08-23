"""stdio MCP server for read and explicitly-confirmed write TimeTree operations."""

from __future__ import annotations

import json
import re
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any, Protocol

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetree_mcp.monitor import MonitorManager
from timetree_mcp.operations import (
    build_create_event_payload,
    build_memo_payload,
    build_memo_update_payload,
    build_update_event_payload,
    calendar_id_value,
    require_confirmation,
)


class BrowserReader(Protocol):
    def read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Read or mutate one operation through the authenticated TimeTree browser session."""


SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.2.1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

READ_TOOLS = {
    "timetree_status",
    "timetree_list_calendars",
    "timetree_list_events",
    "timetree_list_memos",
    "timetree_list_event_comments",
    "timetree_get_calendar_labels",
    "timetree_get_calendar_members",
    "timetree_get_calendar_virtual_members",
    "timetree_monitor_status",
    "timetree_keychain_status",
}

WRITE_TOOLS = {
    "timetree_create_event",
    "timetree_update_event",
    "timetree_delete_event",
    "timetree_create_memo",
    "timetree_update_memo",
    "timetree_delete_memo",
    "timetree_add_event_comment",
    "timetree_update_event_comment",
    "timetree_delete_event_comment",
    "timetree_update_calendar_labels",
}

TIME_SCHEMA = {
    "oneOf": [
        {"type": "string", "description": "ISO-8601 datetime, preferably with timezone offset."},
        {"type": "number", "description": "Unix timestamp in milliseconds."},
    ]
}
ID_SCHEMA = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
CHECKLIST_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"checked": {"type": "boolean"}, "title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    },
}
VIRTUAL_ATTENDEES_SCHEMA = {
    "type": "array",
    "items": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
}
CONFIRM_SCHEMA = {
    "type": "boolean",
    "const": True,
    "description": "Must be true to authorize this real TimeTree write.",
}


def _object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _event_common_properties() -> dict[str, Any]:
    return {
        "title": {"type": "string"},
        "all_day": {"type": "boolean"},
        "start_at": TIME_SCHEMA,
        "start_timezone": {"type": "string"},
        "end_at": TIME_SCHEMA,
        "end_timezone": {"type": "string"},
        "label_id": {"type": "integer", "minimum": 1, "maximum": 10},
        "category": {"type": "integer"},
        "note": {"type": "string"},
        "location": {"type": "string"},
        "url": {"type": "string"},
        "attendees": {"type": "array", "items": ID_SCHEMA},
        "recurrences": {"type": "array"},
        "alerts": {"type": "array"},
        "file_uuids": {"type": "array", "items": {"type": "string"}},
        "checklist": CHECKLIST_SCHEMA,
        "virtual_user_attendees": VIRTUAL_ATTENDEES_SCHEMA,
    }


def _memo_common_properties() -> dict[str, Any]:
    return {
        "title": {"type": "string"},
        "note": {"type": "string"},
        "label_id": {"type": "integer", "minimum": 1, "maximum": 10},
        "location": {"type": "string"},
        "url": {"type": "string"},
        "checklist": CHECKLIST_SCHEMA,
        "virtual_user_attendees": VIRTUAL_ATTENDEES_SCHEMA,
    }


class TimeTreeMcpServer:
    def __init__(self, browser: BrowserReader, monitor: MonitorManager | None = None):
        self.browser = browser
        self.monitor = monitor or MonitorManager(browser)

    def set_notification_sink(self, sink: Any) -> None:
        self.monitor.set_notification_sink(sink)

    def shutdown(self) -> None:
        self.monitor.shutdown()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if isinstance(method, str) and method.startswith("notifications/"):
            return None
        if not isinstance(method, str):
            return self._error(request_id, -32600, "Invalid Request")

        if method == "initialize":
            return self._initialize(request_id, message.get("params") or {})
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": self.tool_definitions()})
        if method == "tools/call":
            return self._call_tool(request_id, message.get("params") or {})
        return self._error(request_id, -32601, f"Method not found: {method}")

    def _initialize(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return self._result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}, "logging": {}},
                "serverInfo": {"name": "timetree-mcp", "version": SERVER_VERSION},
                "instructions": (
                    "TimeTree read/write access through the direct HTTPS session. "
                    "Write tools require confirm=true. Monitor notifications use notifications/message."
                ),
            },
        )

    @staticmethod
    def tool_definitions() -> list[dict[str, Any]]:
        event_create = _event_common_properties()
        event_create.update({"calendar_id": ID_SCHEMA, "confirm": CONFIRM_SCHEMA})
        event_update = _event_common_properties()
        event_update.update({"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}, "confirm": CONFIRM_SCHEMA})
        event_delete = {"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}, "confirm": CONFIRM_SCHEMA}
        memo_create = _memo_common_properties()
        memo_create.update({"calendar_id": ID_SCHEMA, "date": {"type": "string", "pattern": DATE_RE.pattern}, "confirm": CONFIRM_SCHEMA})
        memo_update = _memo_common_properties()
        memo_update.update({"calendar_id": ID_SCHEMA, "memo_uuid": {"type": "string"}, "confirm": CONFIRM_SCHEMA})
        memo_delete = {"calendar_id": ID_SCHEMA, "memo_uuid": {"type": "string"}, "confirm": CONFIRM_SCHEMA}

        return [
            {"name": "timetree_list_calendars", "description": "Read the calendars available in the authenticated TimeTree account.", "inputSchema": _object_schema({})},
            {"name": "timetree_list_events", "description": "Read TimeTree events overlapping a date range. This tool is read-only.", "inputSchema": _object_schema({"start_date": {"type": "string", "description": "Inclusive YYYY-MM-DD date."}, "end_date": {"type": "string", "description": "Exclusive YYYY-MM-DD date."}, "calendar_id": ID_SCHEMA, "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}})},
            {"name": "timetree_status", "description": "Read the current direct HTTPS TimeTree authentication status.", "inputSchema": _object_schema({})},
            {"name": "timetree_login", "description": "Login to TimeTree over direct HTTPS using TIMETREE_EMAIL and TIMETREE_PASSWORD from the MCP environment; no browser is used.", "inputSchema": _object_schema({})},
            {"name": "timetree_logout", "description": "Clear the in-memory TimeTree session cookie and CSRF token.", "inputSchema": _object_schema({})},
            {"name": "timetree_keychain_status", "description": "Report whether the macOS Keychain contains TimeTree email/password items; never returns their values.", "inputSchema": _object_schema({})},
            {"name": "timetree_create_event", "description": "Create a TimeTree event. Requires confirm=true and writes to the real calendar.", "inputSchema": _object_schema(event_create, ["calendar_id", "title", "start_at", "end_at", "confirm"])},
            {"name": "timetree_update_event", "description": "Update selected fields of a TimeTree event. Requires confirm=true.", "inputSchema": _object_schema(event_update, ["calendar_id", "event_uuid", "confirm"])},
            {"name": "timetree_delete_event", "description": "Delete a TimeTree event. This cannot be undone and requires confirm=true.", "inputSchema": _object_schema(event_delete, ["calendar_id", "event_uuid", "confirm"])},
            {"name": "timetree_list_memos", "description": "Read category-2 TimeTree memos from a calendar.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "updated_after": TIME_SCHEMA, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, ["calendar_id"])},
            {"name": "timetree_create_memo", "description": "Create a TimeTree memo. Requires confirm=true.", "inputSchema": _object_schema(memo_create, ["calendar_id", "title", "confirm"])},
            {"name": "timetree_update_memo", "description": "Update a TimeTree memo. Requires confirm=true.", "inputSchema": _object_schema(memo_update, ["calendar_id", "memo_uuid", "confirm"])},
            {"name": "timetree_delete_memo", "description": "Delete a TimeTree memo. This cannot be undone and requires confirm=true.", "inputSchema": _object_schema(memo_delete, ["calendar_id", "memo_uuid", "confirm"])},
            {"name": "timetree_list_event_comments", "description": "Read comments attached to a TimeTree event.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}}, ["calendar_id", "event_uuid"])},
            {"name": "timetree_add_event_comment", "description": "Add a comment to a TimeTree event. Requires confirm=true.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}, "content": {"type": "string"}, "silent": {"type": "boolean", "default": True}, "confirm": CONFIRM_SCHEMA}, ["calendar_id", "event_uuid", "content", "confirm"])},
            {"name": "timetree_update_event_comment", "description": "Update a TimeTree event comment. Requires confirm=true.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}, "comment_id": {"type": "string"}, "content": {"type": "string"}, "confirm": CONFIRM_SCHEMA}, ["calendar_id", "event_uuid", "comment_id", "content", "confirm"])},
            {"name": "timetree_delete_event_comment", "description": "Delete a TimeTree event comment. Requires confirm=true.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "event_uuid": {"type": "string"}, "comment_id": {"type": "string"}, "confirm": CONFIRM_SCHEMA}, ["calendar_id", "event_uuid", "comment_id", "confirm"])},
            {"name": "timetree_get_calendar_labels", "description": "Read all labels for a TimeTree calendar.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA}, ["calendar_id"])},
            {"name": "timetree_update_calendar_labels", "description": "Merge-update TimeTree label names/colors. Requires confirm=true; omitted labels are preserved.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "labels": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"id": {"type": "integer", "minimum": 1, "maximum": 10}, "name": {"type": "string"}, "color": {"type": "integer"}}, "required": ["id"], "additionalProperties": False}}, "confirm": CONFIRM_SCHEMA}, ["calendar_id", "labels", "confirm"])},
            {"name": "timetree_get_calendar_members", "description": "Read active members of a TimeTree calendar, optionally including deactivated members.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "include_deactivated": {"type": "boolean", "default": False}}, ["calendar_id"])},
            {"name": "timetree_get_calendar_virtual_members", "description": "Read virtual members of a TimeTree calendar, optionally including deactivated members.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "include_deactivated": {"type": "boolean", "default": False}}, ["calendar_id"])},
            {"name": "timetree_start_monitor", "description": "Start background polling for calendar changes; changes are sent as MCP notifications/message.", "inputSchema": _object_schema({"calendar_id": ID_SCHEMA, "interval_seconds": {"type": "integer", "minimum": 30, "maximum": 3600, "default": 300}})},
            {"name": "timetree_stop_monitor", "description": "Stop the background TimeTree change monitor.", "inputSchema": _object_schema({})},
            {"name": "timetree_monitor_status", "description": "Read background monitor state and last error/change information.", "inputSchema": _object_schema({})},
        ]

    def _call_tool(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return self._error(request_id, -32602, "tools/call requires a name and object arguments")

        operation_by_tool = {
            "timetree_status": "status", "timetree_login": "login", "timetree_logout": "logout", "timetree_keychain_status": "keychain_status", "timetree_list_calendars": "list_calendars", "timetree_list_events": "list_events",
            "timetree_create_event": "create_event", "timetree_update_event": "update_event", "timetree_delete_event": "delete_event",
            "timetree_list_memos": "list_memos", "timetree_create_memo": "create_memo", "timetree_update_memo": "update_memo", "timetree_delete_memo": "delete_memo",
            "timetree_list_event_comments": "list_event_comments", "timetree_add_event_comment": "add_event_comment", "timetree_update_event_comment": "update_event_comment", "timetree_delete_event_comment": "delete_event_comment",
            "timetree_get_calendar_labels": "get_calendar_labels", "timetree_update_calendar_labels": "update_calendar_labels", "timetree_get_calendar_members": "get_calendar_members", "timetree_get_calendar_virtual_members": "get_calendar_virtual_members",
        }

        if name == "timetree_monitor_status":
            return self._tool_success(request_id, self.monitor.status())
        if name == "timetree_stop_monitor":
            try:
                self._validate_arguments(name, arguments)
                return self._tool_success(request_id, self.monitor.stop())
            except Exception as exc:  # noqa: BLE001
                return self._tool_error(request_id, str(exc))
        if name == "timetree_start_monitor":
            try:
                self._validate_arguments(name, arguments)
                return self._tool_success(request_id, self.monitor.start(arguments.get("calendar_id"), arguments.get("interval_seconds", 300)))
            except Exception as exc:  # noqa: BLE001
                return self._tool_error(request_id, str(exc))

        operation = operation_by_tool.get(name)
        if operation is None:
            return self._tool_error(request_id, f"Unknown TimeTree tool: {name}")

        try:
            self._validate_arguments(name, arguments)
            prepared = self._prepare_arguments(name, arguments)
            payload = self.browser.read(operation, prepared)
            return self._tool_success(request_id, payload)
        except Exception as exc:  # noqa: BLE001 - tool errors must reach the MCP client
            return self._tool_error(request_id, str(exc))

    @staticmethod
    def _allowed_arguments(name: str) -> set[str]:
        base = {
            "calendar_id", "confirm", "title", "all_day", "start_at", "start_timezone", "end_at", "end_timezone",
            "label_id", "category", "note", "location", "url", "attendees", "recurrences", "alerts", "file_uuids",
            "checklist", "virtual_user_attendees", "event_uuid", "memo_uuid", "comment_id", "content", "silent",
            "date", "updated_after", "limit", "query", "start_date", "end_date", "labels", "include_deactivated", "interval_seconds",
        }
        specific = {
            "timetree_status": set(), "timetree_login": set(), "timetree_logout": set(), "timetree_keychain_status": set(), "timetree_list_calendars": set(), "timetree_list_events": {"start_date", "end_date", "calendar_id", "query", "limit"},
            "timetree_create_event": base - {"event_uuid", "memo_uuid", "comment_id", "date", "updated_after", "limit", "query", "start_date", "end_date", "labels", "include_deactivated", "interval_seconds"},
            "timetree_update_event": base - {"memo_uuid", "comment_id", "date", "updated_after", "limit", "query", "start_date", "end_date", "labels", "include_deactivated", "interval_seconds"},
            "timetree_delete_event": {"calendar_id", "event_uuid", "confirm"}, "timetree_list_memos": {"calendar_id", "updated_after", "limit"},
            "timetree_create_memo": {"calendar_id", "title", "note", "label_id", "location", "url", "date", "checklist", "virtual_user_attendees", "confirm"},
            "timetree_update_memo": {"calendar_id", "memo_uuid", "title", "note", "label_id", "location", "url", "checklist", "virtual_user_attendees", "confirm"}, "timetree_delete_memo": {"calendar_id", "memo_uuid", "confirm"},
            "timetree_list_event_comments": {"calendar_id", "event_uuid"}, "timetree_add_event_comment": {"calendar_id", "event_uuid", "content", "silent", "confirm"},
            "timetree_update_event_comment": {"calendar_id", "event_uuid", "comment_id", "content", "confirm"}, "timetree_delete_event_comment": {"calendar_id", "event_uuid", "comment_id", "confirm"},
            "timetree_get_calendar_labels": {"calendar_id"}, "timetree_update_calendar_labels": {"calendar_id", "labels", "confirm"},
            "timetree_get_calendar_members": {"calendar_id", "include_deactivated"}, "timetree_get_calendar_virtual_members": {"calendar_id", "include_deactivated"},
            "timetree_start_monitor": {"calendar_id", "interval_seconds"}, "timetree_stop_monitor": set(), "timetree_monitor_status": set(),
        }
        if name not in specific:
            raise ValueError(f"Unknown TimeTree tool: {name}")
        return specific[name]

    @classmethod
    def _validate_arguments(cls, name: str, arguments: dict[str, Any]) -> None:
        allowed = cls._allowed_arguments(name)
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise ValueError(f"Unsupported argument(s): {', '.join(unknown)}")
        if name in WRITE_TOOLS:
            require_confirmation(arguments)
        if "calendar_id" in arguments:
            calendar_id_value(arguments["calendar_id"])
        for key in ("start_date", "end_date", "date"):
            if key in arguments:
                value = arguments[key]
                if not isinstance(value, str) or not DATE_RE.fullmatch(value):
                    raise ValueError(f"{key} must be YYYY-MM-DD")
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(f"{key} must be a valid date") from exc
        if "limit" in arguments:
            value = arguments["limit"]
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 500:
                raise ValueError("limit must be an integer from 1 to 500")
        if "interval_seconds" in arguments:
            value = arguments["interval_seconds"]
            if not isinstance(value, int) or isinstance(value, bool) or not 30 <= value <= 3600:
                raise ValueError("interval_seconds must be an integer from 30 to 3600")
        for key in ("event_uuid", "memo_uuid", "comment_id", "content", "title"):
            if key in arguments and not isinstance(arguments[key], str):
                raise ValueError(f"{key} must be a string")
        if "labels" in arguments:
            if not isinstance(arguments["labels"], list) or not arguments["labels"]:
                raise ValueError("labels must be a non-empty list")
            for label in arguments["labels"]:
                if not isinstance(label, dict) or not isinstance(label.get("id"), int) or not 1 <= label["id"] <= 10:
                    raise ValueError("each label must have an integer id from 1 to 10")

    @staticmethod
    def _prepare_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "timetree_create_event":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "payload": build_create_event_payload(arguments)}
        if name == "timetree_update_event":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "event_uuid": arguments["event_uuid"], "payload": build_update_event_payload(arguments)}
        if name == "timetree_delete_event":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "event_uuid": arguments["event_uuid"]}
        if name == "timetree_create_memo":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "payload": build_memo_payload(arguments)}
        if name == "timetree_update_memo":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "event_uuid": arguments["memo_uuid"], "payload": build_memo_update_payload(arguments)}
        if name == "timetree_delete_memo":
            return {"calendar_id": calendar_id_value(arguments["calendar_id"]), "event_uuid": arguments["memo_uuid"]}
        if name in {"timetree_add_event_comment", "timetree_update_event_comment", "timetree_delete_event_comment", "timetree_get_calendar_labels", "timetree_update_calendar_labels", "timetree_get_calendar_members", "timetree_get_calendar_virtual_members"}:
            prepared = dict(arguments)
            prepared["calendar_id"] = calendar_id_value(arguments["calendar_id"])
            prepared.pop("confirm", None)
            return prepared
        return arguments

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_success(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": text}], "structuredContent": payload, "isError": False}}

    @staticmethod
    def _tool_error(request_id: Any, message: str) -> dict[str, Any]:
        payload = {"error": message}
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], "structuredContent": payload, "isError": True}}


def run_stdio(server: TimeTreeMcpServer) -> None:
    """Serve newline-delimited JSON-RPC messages and async monitor notifications."""

    output_lock = threading.Lock()

    def write_message(message: dict[str, Any]) -> None:
        with output_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def notify(data: dict[str, Any]) -> None:
        write_message({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "logger": "timetree-monitor", "data": data}})

    server.set_notification_sink(notify)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                response = server.handle(message)
            except json.JSONDecodeError as exc:
                response = TimeTreeMcpServer._error(None, -32700, f"Parse error: {exc.msg}")
            except Exception as exc:  # noqa: BLE001
                response = TimeTreeMcpServer._error(None, -32603, f"Internal error: {exc}")
            if response is not None:
                write_message(response)
    finally:
        server.shutdown()


def main() -> None:
    from timetree_mcp.http_reader import TimeTreeHttpReader

    run_stdio(TimeTreeMcpServer(TimeTreeHttpReader()))


if __name__ == "__main__":
    main()
