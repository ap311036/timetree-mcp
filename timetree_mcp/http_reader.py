"""Direct HTTPS TimeTree client with in-memory cookie and CSRF handling."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .credentials import MacOSKeychainCredentialStore
from .operations import calendar_id_value, to_epoch_millis
from .parser import normalize_event, query_events


BASE_URL = os.environ.get("TIMETREE_BASE_URL", "https://timetreeapp.com").rstrip("/")
API_BASE = f"{BASE_URL}/api/v1"
V2_API_BASE = f"{BASE_URL}/api/v2"
API_HEADERS = {
    "Content-Type": "application/json",
    "X-TimeTreeA": "web/2.1.0/en",
    "Referer": f"{BASE_URL}/",
}
CSRF_RE = re.compile(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', re.I)


@dataclass
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: Any


class TimeTreeHttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class UrllibTransport:
    """Small stdlib transport; CookieJar keeps session cookies in memory only."""

    def __init__(self, timeout: int = 60):
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener()
        self.opener.add_handler(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(self.cookie_jar))

    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> HttpResponse:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers = {"User-Agent": os.environ.get("TIMETREE_USER_AGENT", "timetree-mcp/0.2")}
        request_headers.update(headers or {})
        request = Request(url, data=data, headers=request_headers, method=method.upper())
        try:
            response = self.opener.open(request, timeout=timeout or self.timeout)
            return HttpResponse(response.status, dict(response.headers.items()), self._decode(response.read()))
        except HTTPError as exc:
            return HttpResponse(exc.code, dict(exc.headers.items()), self._decode(exc.read()))
        except URLError as exc:
            raise TimeTreeHttpError(f"TimeTree network error: {exc.reason}") from exc

    @staticmethod
    def _decode(data: bytes) -> Any:
        text = data.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def clear(self) -> None:
        self.cookie_jar.clear()


class TimeTreeHttpReader:
    """TimeTree reader/writer that never launches or depends on a browser."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        transport: Any | None = None,
        base_url: str | None = None,
        credential_store: Any | None = None,
    ):
        self.email = email
        self.password = password
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_base = f"{self.base_url}/api/v1"
        self.v2_api_base = f"{self.base_url}/api/v2"
        self.transport = transport or UrllibTransport()
        self.credential_store = credential_store or MacOSKeychainCredentialStore()
        self._authenticated = False
        self._csrf_token: str | None = None
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            email, password = self._credentials()
            return {
                "authenticated": self._authenticated,
                "credentials_configured": bool(email and password),
                "csrf_available": bool(self._csrf_token),
                "keychain": self.credential_store.status(),
                "transport": "direct_https",
            }

    def login(self) -> dict[str, Any]:
        with self._lock:
            email, password = self._credentials()
            if not email or not password:
                raise RuntimeError("Set TIMETREE_EMAIL and TIMETREE_PASSWORD, or run python3 -m timetree_mcp.credentials set")
            response = self._request_raw(
                "PUT",
                f"{self.api_base}/auth/email/signin",
                {"uid": email, "password": password, "uuid": uuid.uuid4().hex},
                csrf=False,
            )
            if not 200 <= response.status < 300:
                self._authenticated = False
                if response.status in (401, 403):
                    raise RuntimeError("TimeTree login failed: invalid credentials or account access forbidden")
                raise TimeTreeHttpError(f"TimeTree login failed with HTTP {response.status}", response.status)
            self._authenticated = True
            self._csrf_token = None
            self._refresh_csrf()
            return {"authenticated": True, "csrf_available": bool(self._csrf_token), "transport": "direct_https"}

    def logout(self) -> dict[str, Any]:
        with self._lock:
            clear = getattr(self.transport, "clear", None)
            if callable(clear):
                clear()
            self._authenticated = False
            self._csrf_token = None
            return {"authenticated": False, "transport": "direct_https"}

    def read(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if operation == "status":
                return self.status()
            if operation == "login":
                return self.login()
            if operation == "logout":
                return self.logout()
            if operation == "keychain_status":
                return self.credential_store.status()
            self._ensure_authenticated()
            if operation == "list_calendars":
                return {"authenticated": True, "calendars": self._get_calendars()}
            if operation == "list_events":
                return self._list_events(arguments)
            if operation == "sync_events":
                return self._sync_events_result(arguments.get("calendar_id"))
            if operation == "list_memos":
                return self._list_memos(arguments)
            if operation == "list_event_comments":
                return self._list_event_comments(arguments)
            if operation == "get_calendar_labels":
                return self._get_calendar_labels(arguments["calendar_id"])
            if operation == "update_calendar_labels":
                return self._update_calendar_labels(arguments)
            if operation == "get_calendar_members":
                return self._get_calendar_members(arguments)
            if operation == "get_calendar_virtual_members":
                return self._get_calendar_virtual_members(arguments)
            if operation == "create_event":
                return self._create_event(arguments)
            if operation == "update_event":
                return self._update_event(arguments)
            if operation == "delete_event":
                return self._delete_event(arguments)
            if operation == "create_memo":
                result = self._create_event(arguments)
                result["memo"] = result.pop("event", None)
                return result
            if operation == "update_memo":
                result = self._update_event(arguments)
                result["memo"] = result.pop("event", None)
                return result
            if operation == "delete_memo":
                result = self._delete_event(arguments)
                result["deleted_memo_uuid"] = arguments["event_uuid"]
                return result
            if operation == "add_event_comment":
                return self._add_event_comment(arguments)
            if operation == "update_event_comment":
                return self._update_event_comment(arguments)
            if operation == "delete_event_comment":
                return self._delete_event_comment(arguments)
            raise ValueError(f"Unsupported TimeTree operation: {operation}")

    def _credentials(self) -> tuple[str | None, str | None]:
        password = self.password or os.environ.get("TIMETREE_PASSWORD")
        if not password:
            password_file = os.environ.get("TIMETREE_PASSWORD_FILE")
            if password_file:
                try:
                    password = Path(password_file).read_text(encoding="utf-8").strip()
                except OSError as exc:
                    raise RuntimeError("Unable to read TIMETREE_PASSWORD_FILE") from exc
        keychain_email, keychain_password = self.credential_store.read()
        return (
            self.email or os.environ.get("TIMETREE_EMAIL") or keychain_email,
            password or keychain_password,
        )

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self.login()

    def _refresh_csrf(self) -> None:
        response = self._request_raw("GET", f"{self.base_url}/", csrf=False)
        self._csrf_token = self._header(response.headers, "x-csrf-token")
        if not self._csrf_token and isinstance(response.body, str):
            match = CSRF_RE.search(response.body)
            if match:
                self._csrf_token = match.group(1)

    def _request_raw(self, method: str, url: str, body: Any = None, csrf: bool = False) -> HttpResponse:
        headers = dict(API_HEADERS)
        if csrf:
            if not self._csrf_token:
                self._refresh_csrf()
            if not self._csrf_token:
                raise RuntimeError("TimeTree CSRF token unavailable; login again")
            headers["X-CSRF-Token"] = self._csrf_token
        response = self.transport.request(method, url, body, headers, 60)
        response_token = self._header(response.headers, "x-csrf-token")
        if response_token:
            self._csrf_token = response_token
        return response

    def _request_json(self, method: str, url: str, body: Any = None, csrf: bool = False) -> Any:
        response = self._request_raw(method, url, body, csrf)
        if not 200 <= response.status < 300:
            if response.status in (401, 403):
                self._authenticated = False
            raise TimeTreeHttpError(f"TimeTree {method} request failed with HTTP {response.status}", response.status)
        return response.body

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value
        return None

    @staticmethod
    def _body_dict(body: Any) -> dict[str, Any]:
        if isinstance(body, dict):
            return body
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _get_calendars(self) -> list[dict[str, Any]]:
        body = self._body_dict(self._request_json("GET", f"{self.api_base}/calendars?since=0"))
        calendars = body.get("calendars") or []
        result = []
        for calendar in calendars:
            attributes = calendar.get("attributes") if isinstance(calendar, dict) else None
            source = attributes if isinstance(attributes, dict) else calendar
            result.append({
                "id": str(calendar.get("id", source.get("id", ""))),
                "alias_code": source.get("alias_code"),
                "name": source.get("name", source.get("title", "")),
            })
        return [calendar for calendar in result if calendar["id"]]

    def _sync_raw_events(self, calendar_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        all_events: list[dict[str, Any]] = []
        since = 0
        for _ in range(25):
            response = self._request_raw("GET", f"{self.api_base}/calendar/{calendar_id}/events/sync?since={since}")
            body = self._body_dict(response.body)
            if not 200 <= response.status < 300:
                error = body.get("error") if isinstance(body, dict) else None
                if response.status == 400 and isinstance(error, dict) and error.get("code") == -425:
                    return [], {"calendar_id": str(calendar_id), "status": response.status, "reason": "TimeTree calendar sync unavailable"}
                raise TimeTreeHttpError(f"TimeTree GET request failed with HTTP {response.status}", response.status)
            events = body.get("events") or []
            all_events.extend(event for event in events if isinstance(event, dict))
            next_since = body.get("since")
            if not body.get("chunk") or not isinstance(next_since, (int, float)) or next_since <= since:
                break
            since = int(next_since)
        return all_events, None

    def _sync_events_result(self, calendar_id: Any = None) -> dict[str, Any]:
        calendars = self._get_calendars()
        requested = calendar_id_value(calendar_id) if calendar_id is not None else None
        selected = [calendar for calendar in calendars if requested is None or calendar["id"] == requested]
        if requested is not None and not selected:
            raise ValueError(f"Unknown TimeTree calendar_id: {requested}")
        names = {calendar["id"]: calendar["name"] for calendar in calendars}
        events: list[dict[str, Any]] = []
        skipped_calendars: list[dict[str, Any]] = []
        for calendar in selected:
            raw_events, issue = self._sync_raw_events(calendar["id"])
            if issue:
                skipped_calendars.append(issue)
                continue
            for raw in raw_events:
                calendar_event_id = raw.get("calendar_id", calendar["id"])
                events.append(normalize_event(raw, names.get(str(calendar_event_id))))
        result = {"authenticated": True, "calendars": calendars, "events": events}
        if skipped_calendars:
            result["skipped_calendars"] = skipped_calendars
        return result

    def _list_events(self, arguments: dict[str, Any]) -> dict[str, Any]:
        today = datetime.now().astimezone().date()
        start_date = arguments.get("start_date") or today.isoformat()
        end_date = arguments.get("end_date") or (today + timedelta(days=7)).isoformat()
        snapshot = self._sync_events_result(arguments.get("calendar_id"))
        filtered = query_events(snapshot["events"], start_date=start_date, end_date=end_date, calendar_id=arguments.get("calendar_id"), query=arguments.get("query"), limit=arguments.get("limit", 100))
        result = {"start_date": start_date, "end_date": end_date, "calendar_id": arguments.get("calendar_id"), "query": arguments.get("query"), "count": len(filtered), "events": filtered}
        if snapshot.get("skipped_calendars"):
            result["skipped_calendars"] = snapshot["skipped_calendars"]
        return result

    def _list_memos(self, arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._sync_events_result(arguments["calendar_id"])
        memos = [event for event in snapshot["events"] if event.get("category") == 2 and not event.get("deactivated_at")]
        if arguments.get("updated_after") is not None:
            cutoff = to_epoch_millis(arguments["updated_after"])
            memos = [event for event in memos if event.get("updated_at") and to_epoch_millis(event["updated_at"]) > cutoff]
        if arguments.get("limit") is not None:
            memos = memos[: arguments["limit"]]
        return {"calendar_id": str(arguments["calendar_id"]), "memos": memos, "count": len(memos)}

    def _list_event_comments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        calendar_id = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        body = self._body_dict(self._request_json("GET", f"{self.api_base}/calendar/{calendar_id}/event/{event_uuid}/activities?since=0"))
        activities = body.get("event_activities") or []
        comments = [self._format_comment(item, calendar_id, event_uuid) for item in activities if item.get("type") == 0 and not item.get("deactivated_at")]
        return {"calendar_id": calendar_id, "event_uuid": event_uuid, "comments": comments, "count": len(comments)}

    def _get_calendar_labels(self, calendar_id: Any) -> dict[str, Any]:
        cid = calendar_id_value(calendar_id)
        body = self._body_dict(self._request_json("GET", f"{self.api_base}/calendar/{cid}/labels"))
        labels = body.get("calendar_labels") or []
        return {"calendar_id": cid, "labels": labels, "count": len(labels)}

    def _update_calendar_labels(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        current = self._get_calendar_labels(cid)["labels"]
        updates = {int(label["id"]): label for label in arguments["labels"]}
        current_ids = {int(label.get("id")) for label in current}
        merged = []
        for label in current:
            update = updates.get(int(label.get("id")), {})
            merged.append({"id": int(label["id"]), "name": update.get("name", label.get("name", "")), "color": update.get("color", label.get("color", label.get("default_color")))})
        for label_id, update in updates.items():
            if label_id in current_ids:
                continue
            if "color" not in update:
                raise ValueError(f"new label {label_id} requires color")
            merged.append({"id": label_id, "name": update.get("name", ""), "color": update["color"]})
        body = self._body_dict(self._request_json("PUT", f"{self.api_base}/calendar/{cid}/labels", {"calendar_labels": merged}, csrf=True))
        labels = body.get("calendar_labels") or merged
        return {"success": True, "calendar_id": cid, "labels": labels, "count": len(labels)}

    def _get_calendar_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        body = self._body_dict(self._request_json("GET", f"{self.v2_api_base}/calendars/{cid}/users"))
        members = body.get("calendar_users") or []
        if not arguments.get("include_deactivated"):
            members = [member for member in members if not member.get("deactivated_at")]
        return {"calendar_id": cid, "members": members, "count": len(members)}

    def _get_calendar_virtual_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        body = self._body_dict(self._request_json("GET", f"{self.api_base}/calendars/{cid}/virtual_users"))
        members = body.get("calendar_virtual_users") or []
        if not arguments.get("include_deactivated"):
            members = [member for member in members if not member.get("deactivated_at")]
        return {"calendar_id": cid, "virtual_members": members, "count": len(members)}

    def _create_event(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        body = self._body_dict(self._request_json("POST", f"{self.api_base}/calendar/{cid}/event", arguments["payload"], csrf=True))
        raw = body.get("event") or body
        return {"success": True, "event": normalize_event(raw, None)}

    def _update_event(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        body = self._body_dict(self._request_json("PUT", f"{self.api_base}/calendar/{cid}/event/{event_uuid}", arguments["payload"], csrf=True))
        raw = body.get("event") or body
        return {"success": True, "event": normalize_event(raw, None)}

    def _delete_event(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        response = self._request_raw("DELETE", f"{self.api_base}/calendar/{cid}/event/{event_uuid}", csrf=True)
        if not 200 <= response.status < 300:
            raise TimeTreeHttpError(f"TimeTree DELETE request failed with HTTP {response.status}", response.status)
        return {"success": True, "deleted_event_uuid": event_uuid}

    def _add_event_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        body = self._body_dict(self._request_json("POST", f"{self.api_base}/calendar/{cid}/event/{event_uuid}/activity", {"id": uuid.uuid4().hex, "attachment": {"content": arguments["content"]}, "silent": arguments.get("silent", True)}, csrf=True))
        return {"success": True, "comment": self._format_comment(body.get("event_activity") or body, cid, event_uuid)}

    def _update_event_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        comment_id = arguments["comment_id"]
        body = self._body_dict(self._request_json("PUT", f"{self.api_base}/calendar/{cid}/event/{event_uuid}/activity/{comment_id}", {"attachment": {"content": arguments["content"]}}, csrf=True))
        return {"success": True, "comment": self._format_comment(body.get("event_activity") or body, cid, event_uuid)}

    def _delete_event_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cid = calendar_id_value(arguments["calendar_id"])
        event_uuid = arguments["event_uuid"]
        comment_id = arguments["comment_id"]
        response = self._request_raw("DELETE", f"{self.api_base}/calendar/{cid}/event/{event_uuid}/activity/{comment_id}", csrf=True)
        if not 200 <= response.status < 300:
            raise TimeTreeHttpError(f"TimeTree DELETE request failed with HTTP {response.status}", response.status)
        return {"success": True, "deleted_comment_id": comment_id}

    @staticmethod
    def _format_comment(comment: dict[str, Any], calendar_id: str, event_uuid: str) -> dict[str, Any]:
        attachment = comment.get("attachment") or {}
        return {"id": comment.get("id"), "event_id": comment.get("event_id", event_uuid), "calendar_id": comment.get("calendar_id", calendar_id), "content": attachment.get("content", ""), "author_id": comment.get("author_id"), "created_at": comment.get("created_at"), "updated_at": comment.get("updated_at")}
