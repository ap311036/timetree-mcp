"""Direct HTTPS TimeTree client with in-memory cookie and CSRF handling."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .credentials import MacOSKeychainCredentialStore
from .http_transport import (
    CSRF_PATH, CSRF_CALENDAR_PATH, CSRF_REDIRECT_TARGETS, LOGIN_DIAGNOSTIC_REASONS,
    CsrfCalendarRedirect, DirectHttpsTransport, HttpResponse, TimeTreeHttpError, TransportError,
    failure_reason, http_failure_reason, valid_header_value,
)
from .operations import calendar_id_value, to_epoch_millis
from .occurrence_editor import OccurrenceEditor
from .occurrence_journal import OccurrenceJournal, default_journal_path
from .parser import normalize_event, query_events
from .recurrence_query import expand_occurrences, validate_query_arguments


BASE_URL = os.environ.get("TIMETREE_BASE_URL", "https://timetreeapp.com").rstrip("/")
API_BASE = f"{BASE_URL}/api/v1"
V2_API_BASE = f"{BASE_URL}/api/v2"
API_HEADERS = {
    "Content-Type": "application/json",
    "X-TimeTreeA": "web/2.1.0/en",
    "Referer": f"{BASE_URL}/",
}
CSRF_RE = re.compile(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)', re.I)
MAX_SYNC_EVENTS = 20_000
MAX_SYNC_BYTES = 8_000_000
MAX_CALENDAR_RECORDS = 512
# Compatibility name only: there is one production HTTP implementation.
UrllibTransport = DirectHttpsTransport


@dataclass(frozen=True, repr=False)
class _CredentialConfiguration:
    source: str
    email: str | None = None
    password: str | None = None
    password_file: str | None = None


@dataclass
class _SyncBudget:
    """Accepted raw event payload across one request, not an HTTP download limit."""
    event_count: int = 0
    serialized_bytes: int = 2  # JSON array brackets.
    exhausted: bool = False


class TimeTreeHttpReader:
    """TimeTree reader/writer that never launches or depends on a browser."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        transport: Any | None = None,
        base_url: str | None = None,
        credential_store: Any | None = None,
        occurrence_journal_path: Path | None = None,
    ):
        self.email = email
        self.password = password
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_base = f"{self.base_url}/api/v1"
        self.v2_api_base = f"{self.base_url}/api/v2"
        self.transport = transport or DirectHttpsTransport(origin=self.base_url)
        self.credential_store = credential_store or MacOSKeychainCredentialStore()
        self._authenticated = False
        self._csrf_token: str | None = None
        self.login_diagnostic: dict[str, str] | None = None
        self._login_phase = "sign_in"
        self._lock = threading.RLock()
        self._occurrence_journal_path = occurrence_journal_path
        self._occurrence_editor_instance: OccurrenceEditor | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            configuration = self._credential_configuration()
            keychain: dict[str, bool] = {"checked": False}
            if configuration.source == "keychain":
                keychain = {"checked": True, **self.credential_store.status()}
                configured = bool(keychain.get("email_configured") and keychain.get("password_configured"))
            elif configuration.password_file is not None:
                # Presence is not authentication or validation of the file contents.
                try:
                    info = Path(configuration.password_file).stat()
                    configured = (stat.S_ISREG(info.st_mode) and info.st_size > 0
                                  and os.access(configuration.password_file, os.R_OK))
                except OSError:
                    configured = False
            else:
                configured = True
            return {
                "authenticated": self._authenticated,
                "credentials_configured": configured,
                "credential_source": configuration.source,
                "csrf_available": bool(self._csrf_token),
                "keychain": keychain,
                "transport": "direct_https",
            }

    def login(self) -> dict[str, Any]:
        with self._lock:
            self.login_diagnostic = None
            self._login_phase = "sign_in"
            resolving_credentials = False
            try:
                self._reset_session()
                resolving_credentials = True
                email, password = self._credentials()
                if not email or not password:
                    raise RuntimeError("Set TIMETREE_EMAIL and TIMETREE_PASSWORD, or run timetree-credentials set")
                resolving_credentials = False
                response = self._request_raw(
                    "PUT", f"{self.api_base}/auth/email/signin",
                    {"uid": email, "password": password, "uuid": uuid.uuid4().hex}, csrf=False,
                )
                self._require_auth_success(response)
                if not isinstance(response.body, dict) or response.body.get("error") is not None:
                    raise TransportError("invalid_response")
                self._refresh_csrf()
                self._authenticated = True
                return {"authenticated": True, "csrf_available": True, "transport": "direct_https"}
            except BaseException as error:
                reason = failure_reason(error)
                if reason not in LOGIN_DIAGNOSTIC_REASONS or (reason == "csrf_missing" and self._login_phase != "csrf_fetch"):
                    reason = "internal_error"
                self.login_diagnostic = {"phase": self._login_phase, "reason": reason}
                if self._login_phase == "csrf_fetch" and reason == "http_redirect_rejected":
                    target = getattr(error, "redirect_target", None)
                    if type(target) is str and target in CSRF_REDIRECT_TARGETS:
                        self.login_diagnostic["redirect_target"] = target
                try:
                    self._reset_session()
                except BaseException:
                    pass  # Cleanup must never replace the primary failure/cancellation.
                if not isinstance(error, Exception):
                    raise
                if resolving_credentials and self._known_credential_error(error):
                    raise
                if isinstance(error, TransportError):
                    raise
                status = error.status if isinstance(error, TimeTreeHttpError) else None
                raise TransportError(reason, status=status) from None

    def _reset_session(self) -> None:
        self._authenticated = False
        self._csrf_token = None
        clear = getattr(self.transport, "clear", None)
        if callable(clear):
            clear()

    def _invalidate_session(self) -> None:
        try:
            self._reset_session()
        except BaseException:
            pass  # Preserve the failed request's original error or cancellation.
        finally:
            self._authenticated = False
            self._csrf_token = None

    @staticmethod
    def _known_credential_error(error: Exception) -> bool:
        messages = {
            "Set TIMETREE_EMAIL and TIMETREE_PASSWORD, or run timetree-credentials set",
            "Incomplete or empty TimeTree credentials: provide a complete constructor pair, "
            "or TIMETREE_EMAIL with TIMETREE_PASSWORD or TIMETREE_PASSWORD_FILE",
            "Unable to read TIMETREE_PASSWORD_FILE", "macOS Keychain read failed",
        }
        return (type(error) is RuntimeError and len(error.args) == 1
                and type(error.args[0]) is str and error.args[0] in messages)

    @staticmethod
    def _require_auth_success(response: HttpResponse) -> None:
        if type(response.status) is not int:
            raise TransportError("invalid_response")
        if not 200 <= response.status < 300:
            raise TransportError(http_failure_reason(response.status), status=response.status)

    def logout(self) -> dict[str, Any]:
        with self._lock:
            try:
                self._reset_session()
            except Exception as error:
                raise TransportError(failure_reason(error)) from None
            finally:
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
            if operation == "list_occurrences":
                query = validate_query_arguments(arguments)
                events = self._occurrence_snapshot(query["calendar_id"])
                return expand_occurrences(events, **query)
            # A recorded result/status can be read without credentials or network.
            if operation in {"get_occurrence_for_edit", "move_occurrence", "occurrence_operation_status"}:
                editor = self._occurrence_editor()
                if operation == "get_occurrence_for_edit":
                    return editor.preflight(arguments)
                if operation == "move_occurrence":
                    return editor.move(arguments)
                return editor.operation_status(arguments)
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

    @staticmethod
    def _require_credential(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise RuntimeError(
                "Incomplete or empty TimeTree credentials: provide a complete constructor pair, "
                "or TIMETREE_EMAIL with TIMETREE_PASSWORD or TIMETREE_PASSWORD_FILE"
            )
        return value

    def _credential_configuration(self) -> _CredentialConfiguration:
        """Choose one complete source without opening a password file or Keychain."""
        if self.email is not None or self.password is not None:
            return _CredentialConfiguration(
                "constructor", self._require_credential(self.email).strip(),
                self._require_credential(self.password),
            )
        environment = os.environ
        if any(name in environment for name in ("TIMETREE_EMAIL", "TIMETREE_PASSWORD", "TIMETREE_PASSWORD_FILE")):
            email = self._require_credential(environment.get("TIMETREE_EMAIL")).strip()
            if "TIMETREE_PASSWORD" in environment:
                return _CredentialConfiguration(
                    "environment", email, self._require_credential(environment["TIMETREE_PASSWORD"]),
                )
            return _CredentialConfiguration(
                "password_file", email,
                password_file=self._require_credential(environment.get("TIMETREE_PASSWORD_FILE")),
            )
        return _CredentialConfiguration("keychain")

    def _credentials(self) -> tuple[str | None, str | None]:
        configuration = self._credential_configuration()
        if configuration.source == "keychain":
            return self.credential_store.read()
        password = configuration.password
        if configuration.password_file is not None:
            try:
                password = Path(configuration.password_file).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError) as exc:
                raise RuntimeError("Unable to read TIMETREE_PASSWORD_FILE") from exc
            password = self._require_credential(password)
        return configuration.email, password

    def _ensure_authenticated(self) -> None:
        if not self._authenticated:
            self.login()

    def _refresh_csrf(self) -> None:
        self._login_phase = "csrf_fetch"
        self._csrf_token = None
        try:
            response = self._request_raw("GET", f"{self.base_url}{CSRF_PATH}", csrf=False)
            if type(response) is CsrfCalendarRedirect:
                response = self._request_raw("GET", f"{self.base_url}{CSRF_CALENDAR_PATH}", csrf=False)
            self._require_auth_success(response)
            token = self._header(response.headers, "x-csrf-token")
            if token is None or isinstance(token, str) and not token.strip():
                if isinstance(response.body, str):
                    match = CSRF_RE.search(response.body)
                    if match:
                        token = match.group(1)
            if token is None or isinstance(token, str) and not token.strip():
                raise TransportError("csrf_missing")
            if not self._valid_csrf_token(token):
                raise TransportError("invalid_response")
            self._csrf_token = token
        except BaseException:
            self._csrf_token = None
            raise

    @staticmethod
    def _valid_csrf_token(token: Any) -> bool:
        return valid_header_value(token) and bool(token.strip())

    def _request_raw(self, method: str, url: str, body: Any = None,
                     csrf: bool = False) -> HttpResponse | CsrfCalendarRedirect:
        try:
            headers = {**API_HEADERS, "Referer": f"{self.base_url}/"}
            if csrf:
                if not self._csrf_token:
                    self._refresh_csrf()
                if not self._csrf_token:
                    raise RuntimeError("TimeTree CSRF token unavailable; login again")
                headers["X-CSRF-Token"] = self._csrf_token
            response = self.transport.request(method, url, body, headers, 60)
            if type(response) is CsrfCalendarRedirect:
                if method == "GET" and url == f"{self.base_url}{CSRF_PATH}" and body is None and not csrf:
                    return response
                raise TransportError("http_redirect_rejected")
            if type(response.status) is int and response.status in (401, 403):
                self._invalidate_session()
                return response
            response_token = self._header(response.headers, "x-csrf-token")
            if type(response.status) is int and 200 <= response.status < 300 and self._valid_csrf_token(response_token):
                self._csrf_token = response_token
            return response
        except BaseException:
            self._invalidate_session()
            raise

    def _request_json(self, method: str, url: str, body: Any = None, csrf: bool = False) -> Any:
        response = self._request_raw(method, url, body, csrf)
        if not 200 <= response.status < 300:
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
        result = []
        for source in self._calendar_records(body):
            deactivated_at = source.get("deactivated_at")
            if deactivated_at not in (None, 0):
                continue
            result.append({
                "id": self._response_id(source["id"]),
                "alias_code": source.get("alias_code"),
                "name": source.get("name", source.get("title", "")),
            })
        return result

    @staticmethod
    def _response_id(value: Any) -> str:
        if (type(value) not in (str, int) or not str(value)
                or str(value).strip() != str(value) or len(str(value)) > 512
                or any(ord(c) < 32 or ord(c) == 127 or c in "/\\?#%" for c in str(value))):
            raise ValueError("invalid_sync_identity")
        return str(value)

    @classmethod
    def _response_fields(cls, item: Any) -> dict[str, Any]:
        if not isinstance(item, dict) or not isinstance(item.get("attributes", {}), dict):
            raise ValueError("invalid_sync_record")
        attributes = item.get("attributes", {})
        for key in ("id", "uuid", "calendar_id"):
            if key in attributes and key in item and not (attributes[key] is None and item[key] is None):
                if cls._response_id(attributes[key]) != cls._response_id(item[key]):
                    raise ValueError("conflicting_sync_identity")
        return {**attributes, **{key: value for key, value in item.items() if key != "attributes"}}

    @classmethod
    def _calendar_records(cls, body: dict[str, Any]) -> list[dict[str, Any]]:
        cls._require_complete_metadata(body)
        if (not isinstance(body.get("calendars"), list)
                or ("chunk" in body and (type(body["chunk"]) not in (bool, int) or body["chunk"] != 0))):
            raise ValueError("invalid_calendar_response")
        records = body["calendars"]
        if len(records) > MAX_CALENDAR_RECORDS:
            raise ValueError("calendar_index_capacity_exceeded")
        result, seen = [], set()
        for item in records:
            fields = cls._response_fields(item)
            identity = cls._response_id(fields.get("id"))
            if identity in seen:
                raise ValueError("duplicate_calendar_identity")
            seen.add(identity)
            deactivated = fields.get("deactivated_at")
            if deactivated is not None and (type(deactivated) not in (int, float) or not math.isfinite(deactivated)):
                raise ValueError("Invalid TimeTree calendar deactivated_at")
            result.append(fields)
        try:
            size = len(json.dumps(records, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid_calendar_response") from exc
        if size > MAX_SYNC_BYTES:
            raise ValueError("calendar_index_capacity_exceeded")
        return result

    @classmethod
    def _event_identity_keys(cls, event: Any, calendar_id: str) -> set[tuple[str, str]]:
        fields = cls._response_fields(event)
        keys = {(key, cls._response_id(fields[key])) for key in ("uuid", "id")
                if fields.get(key) is not None and fields[key] != ""}
        if not keys:
            raise ValueError("missing_event_identity")
        calendar_refs = []
        if fields.get("calendar_id") is not None:
            calendar_refs.append(fields["calendar_id"])
        if fields.get("calendar") is not None:
            if not isinstance(fields["calendar"], dict):
                raise ValueError("invalid_event_calendar")
            calendar_refs.append(fields["calendar"].get("id"))
        relationships = fields.get("relationships")
        if isinstance(relationships, dict) and "calendar" in relationships:
            relation = relationships["calendar"]
            if not isinstance(relation, dict) or not isinstance(relation.get("data"), dict):
                raise ValueError("invalid_event_calendar")
            calendar_refs.append(relation["data"].get("id"))
        if any(cls._response_id(value) != calendar_id for value in calendar_refs):
            raise ValueError("cross_calendar_sync_event")
        return keys

    def _sync_raw_events(self, calendar_id: str, *, strict: bool = False,
                         budget: _SyncBudget | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        all_events: list[dict[str, Any]] = []
        calendar_bytes = 2
        seen_event_keys: set[tuple[str, str]] = set()
        since = 0
        requested_cursors = set()
        for page in range(1, 26):
            issue = {"calendar_id": str(calendar_id), "pages": page}

            def incomplete(reason: str):
                if strict:
                    raise ValueError("occurrence_snapshot_incomplete")
                return all_events, {**issue, "reason": reason}

            if budget is not None and (budget.exhausted or budget.event_count >= MAX_SYNC_EVENTS
                                       or budget.serialized_bytes >= MAX_SYNC_BYTES):
                budget.exhausted = True
                return incomplete("request_capacity_exceeded")
            requested_cursors.add(since)
            response = self._request_raw("GET", f"{self.api_base}/calendar/{calendar_id}/events/sync?since={since}")
            body = self._body_dict(response.body)
            if not 200 <= response.status < 300:
                error = body.get("error") if isinstance(body, dict) else None
                if response.status == 400 and isinstance(error, dict) and error.get("code") == -425:
                    return all_events, {"calendar_id": str(calendar_id), "status": response.status, "error_code": -425, "reason": "TimeTree calendar sync unavailable", "pages": page}
                raise TimeTreeHttpError(f"TimeTree GET request failed with HTTP {response.status}", response.status)
            try:
                self._require_complete_metadata(body)
            except ValueError:
                return incomplete("incomplete_sync_metadata")
            if (not isinstance(body.get("events"), list)
                    or type(body.get("chunk")) not in (bool, int)
                    or body["chunk"] not in (False, True, 0, 1)):
                return incomplete("invalid_sync_response")
            events = body["events"]
            if budget is not None and budget.event_count + len(events) > MAX_SYNC_EVENTS:
                budget.exhausted = True
                return incomplete("request_capacity_exceeded")
            if len(all_events) + len(events) > MAX_SYNC_EVENTS:
                return incomplete("snapshot_capacity_exceeded")
            page_keys: set[tuple[str, str]] = set()
            try:
                for event in events:
                    keys = self._event_identity_keys(event, str(calendar_id))
                    if keys & seen_event_keys or keys & page_keys:
                        raise ValueError("duplicate_event_identity")
                    page_keys.update(keys)
                page_bytes = len(json.dumps(events, allow_nan=False).encode("utf-8")) - 2
            except (TypeError, ValueError, OverflowError):
                return incomplete("invalid_sync_event")
            next_calendar_bytes = calendar_bytes + page_bytes + (2 if all_events and events else 0)
            if budget is not None:
                next_request_bytes = budget.serialized_bytes + page_bytes + (2 if budget.event_count and events else 0)
                if next_request_bytes > MAX_SYNC_BYTES:
                    budget.exhausted = True
                    return incomplete("request_capacity_exceeded")
            if next_calendar_bytes > MAX_SYNC_BYTES:
                return incomplete("snapshot_capacity_exceeded")
            all_events.extend(events)
            calendar_bytes = next_calendar_bytes
            seen_event_keys.update(page_keys)
            if budget is not None:
                budget.event_count += len(events)
                budget.serialized_bytes = next_request_bytes
            next_since = body.get("since")
            if not body.get("chunk"):
                return all_events, None
            # TimeTree can return a decreasing cursor; treat it as an opaque token.
            if isinstance(next_since, bool) or not isinstance(next_since, (int, float)) or not math.isfinite(next_since) or next_since != int(next_since):
                return all_events, {**issue, "reason": "invalid_cursor"}
            since = int(next_since)
            if since in requested_cursors:
                return all_events, {**issue, "reason": "repeated_cursor"}
        return all_events, {"calendar_id": str(calendar_id), "pages": 25, "reason": "page_limit"}

    @staticmethod
    def _require_complete_metadata(body: dict[str, Any]) -> None:
        if (not isinstance(body, dict) or body.get("sync_complete", True) is not True
                or ("error" in body and body["error"] is not None)
                or any(key in body and (type(body[key]) not in (bool, int) or body[key] != 0)
                       for key in ("truncated", "has_more", "incomplete"))
                or any(key in body and (not isinstance(body[key], list) or body[key])
                       for key in ("skipped_calendars", "incomplete_calendars", "skipped_unreadable_events"))):
            raise ValueError("occurrence_snapshot_incomplete")

    def _occurrence_editor(self) -> OccurrenceEditor:
        if self._occurrence_editor_instance is None:
            path = self._occurrence_journal_path or default_journal_path()
            self._occurrence_editor_instance = OccurrenceEditor(
                self._occurrence_snapshot, self._occurrence_create, self._occurrence_exclude,
                OccurrenceJournal(path), namespace=self.base_url,
            )
        return self._occurrence_editor_instance

    def _occurrence_snapshot(self, calendar_id: str) -> list[dict[str, Any]]:
        self._ensure_authenticated()
        body = self._body_dict(self._request_json("GET", f"{self.api_base}/calendars?since=0"))
        matches = []
        for calendar in self._calendar_records(body):
            if str(calendar.get("id")) == calendar_id:
                matches.append(calendar)
        if len(matches) != 1:
            raise ValueError("unknown_or_ambiguous_occurrence_calendar")
        deactivated = matches[0].get("deactivated_at")
        if deactivated is not None and (type(deactivated) not in (int, float) or deactivated != 0):
            raise ValueError("inactive_or_invalid_occurrence_calendar")
        events, issue = self._sync_raw_events(calendar_id, strict=True)
        if issue:
            raise ValueError("occurrence_snapshot_incomplete")
        raw_events = []
        for item in events:
            attributes = item.get("attributes", {})
            if not isinstance(attributes, dict):
                raise ValueError("invalid_occurrence_event")
            event = {**attributes, **item}
            event.pop("attributes", None)
            if event.get("calendar_id") is not None and str(event["calendar_id"]) != calendar_id:
                raise ValueError("cross_calendar_occurrence_event")
            event["calendar_id"] = calendar_id
            raw_events.append(event)
        return raw_events

    def _occurrence_create(self, calendar_id: str, payload: dict[str, Any]) -> str:
        body = self._request_json("POST", f"{self.api_base}/calendar/{calendar_id}/event", payload, csrf=True)
        event = body.get("event") if isinstance(body, dict) and not body.get("error") else None
        if (not isinstance(event, dict) or not isinstance(event.get("uuid"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", event["uuid"])
                or (event.get("calendar_id") is not None and str(event["calendar_id"]) != calendar_id)):
            raise ValueError("occurrence_create_response_unverified")
        return event["uuid"]

    def _occurrence_exclude(self, calendar_id: str, event_uuid: str, rules: list[str]) -> None:
        self._request_json("PUT", f"{self.api_base}/calendar/{calendar_id}/event/{event_uuid}",
                           {"recurrences": rules, "silent": True}, csrf=True)

    @classmethod
    def _normalized_sync_event(cls, raw: dict[str, Any], calendar_id: str,
                               calendar_name: str | None) -> dict[str, Any]:
        fields = cls._response_fields(raw)
        if "all_day" in fields and type(fields["all_day"]) is not bool:
            raise ValueError("invalid_event_normalization")
        fields["calendar_id"] = calendar_id
        event = normalize_event(fields, calendar_name)
        bounds = []
        for key in ("start", "end"):
            value = event[key]
            if not isinstance(value, str):
                raise ValueError("invalid_event_normalization")
            if event["all_day"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                bounds.append(date.fromisoformat(value))
            else:
                instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError("invalid_event_normalization")
                bounds.append(instant)
        if type(bounds[0]) is not type(bounds[1]) or bounds[1] < bounds[0]:
            raise ValueError("invalid_event_normalization")
        return event

    def _sync_events_result(self, calendar_id: Any = None) -> dict[str, Any]:
        calendars = self._get_calendars()
        requested = calendar_id_value(calendar_id) if calendar_id is not None else None
        selected = [calendar for calendar in calendars if requested is None or calendar["id"] == requested]
        if requested is not None and not selected:
            raise ValueError(f"Unknown TimeTree calendar_id: {requested}")
        names = {calendar["id"]: calendar["name"] for calendar in calendars}
        events: list[dict[str, Any]] = []
        skipped_calendars: list[dict[str, Any]] = []
        incomplete_calendars: list[dict[str, Any]] = []
        budget = _SyncBudget()
        for index, calendar in enumerate(selected):
            if budget.exhausted or budget.event_count >= MAX_SYNC_EVENTS or budget.serialized_bytes >= MAX_SYNC_BYTES:
                incomplete_calendars.extend({"calendar_id": item["id"], "reason": "request_capacity_exceeded",
                                             "not_attempted": True} for item in selected[index:])
                break
            raw_events, issue = self._sync_raw_events(calendar["id"], budget=budget)
            if issue:
                if not raw_events and issue.get("status") == 400:
                    skipped_calendars.append(issue)
                else:
                    incomplete_calendars.append(issue)
            for raw in raw_events:
                try:
                    events.append(self._normalized_sync_event(raw, calendar["id"], names.get(calendar["id"])))
                except (ValueError, TypeError, OverflowError):
                    if not any(item["calendar_id"] == calendar["id"] for item in incomplete_calendars):
                        incomplete_calendars.append({"calendar_id": calendar["id"], "reason": "invalid_event_normalization"})
        result = {"authenticated": True, "calendars": calendars, "events": events, "sync_complete": not (skipped_calendars or incomplete_calendars)}
        if incomplete_calendars:
            result["incomplete_calendars"] = incomplete_calendars
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
        result["sync_complete"] = snapshot["sync_complete"]
        for key in ("skipped_calendars", "incomplete_calendars"):
            if snapshot.get(key):
                result[key] = snapshot[key]
        return result

    def _list_memos(self, arguments: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._sync_events_result(arguments["calendar_id"])
        memos = [event for event in snapshot["events"] if event.get("category") == 2 and not event.get("deactivated_at")]
        if arguments.get("updated_after") is not None:
            cutoff = to_epoch_millis(arguments["updated_after"])
            memos = [event for event in memos if event.get("updated_at") and to_epoch_millis(event["updated_at"]) > cutoff]
        if arguments.get("limit") is not None:
            memos = memos[: arguments["limit"]]
        result = {"calendar_id": str(arguments["calendar_id"]), "memos": memos,
                  "count": len(memos), "sync_complete": snapshot["sync_complete"]}
        for key in ("skipped_calendars", "incomplete_calendars"):
            if snapshot.get(key):
                result[key] = snapshot[key]
        return result

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
