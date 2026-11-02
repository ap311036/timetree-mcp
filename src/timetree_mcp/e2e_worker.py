"""Operator-only, read-only stdio worker with an explicit test-record boundary.

Credentials are entered on the operator's controlling terminal and stay in this
process. This module has no credential, cookie, report, or journal persistence.
The parent driver receives only public schemas and scoped, sanitized results.
"""

from __future__ import annotations

import getpass
import json
import math
import os
import re
import signal
import sys
import time
import warnings
from collections import Counter
from typing import Any

from .http_reader import HttpResponse, TimeTreeHttpReader
from . import http_transport as _transport_core
from .http_transport import CsrfCalendarRedirect, DirectHttpsTransport, ResponseBudget, TransportError
from .server import TimeTreeMcpServer, run_stdio

ORIGIN = _transport_core.DEFAULT_ORIGIN
CSRF_PATH = _transport_core.CSRF_PATH
CSRF_URL = ORIGIN + CSRF_PATH
CSRF_CALENDAR_URL = ORIGIN + _transport_core.CSRF_CALENDAR_PATH
MAX_RESPONSE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_INPUT_BYTES = 65_536
MAX_SESSION_SECONDS = 600
ROW_KEYS = frozenset({"event_uuid", "start_at", "end_at", "timezone", "start_local",
                      "end_local", "source", "series_uuid"})
_STARTUP_ERROR_EXITS = {"e2e_proxy_unsupported": 80, "e2e_proxy_state_unknown": 81,
                        "e2e_tty_required": 82, "e2e_credentials_required": 83}
class WorkerError(TransportError):
    """Fixed outward worker codes retain the production transport diagnosis."""

    def __init__(self, message, *, status=None, redirect_target=None):
        code = message.removeprefix("e2e_")
        super().__init__(code, status=status, redirect_target=redirect_target)
        self.args = (message,)


class _Cancelled(BaseException):
    pass


def _system_proxy_check():
    try:
        return _transport_core._system_proxy_check()
    except TransportError as error:
        raise WorkerError("e2e_" + error.code) from None


def require_direct_connection(*, environment=None, proxy_lookup=None, system_proxy_check=None):
    try:
        return _transport_core.require_direct_connection(
            environment=environment, proxy_lookup=proxy_lookup,
            system_proxy_check=system_proxy_check)
    except TransportError as error:
        raise WorkerError("e2e_" + error.code) from None


def prompt_credentials(*, tty_opener=open, prompt=getpass.getpass) -> tuple[str, str]:
    try:
        # getpass opens the controlling terminal for input itself. Its prompt
        # stream is output-only; macOS rejects buffered r+ on this device.
        with tty_opener("/dev/tty", "w", encoding="utf-8") as tty:
            if not tty.isatty():
                raise WorkerError("e2e_tty_required")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                email = prompt("TimeTree email (hidden): ", stream=tty).strip()
                password = prompt("TimeTree password (hidden): ", stream=tty)
            if not email or not password.strip() or "\x00" in email + password:
                raise WorkerError("e2e_credentials_required")
            return email, password
    except WorkerError:
        raise
    except (OSError, EOFError, getpass.GetPassWarning):
        raise WorkerError("e2e_tty_required") from None


def _strict_json(text):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError()

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number

    return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_constant,
                      parse_float=finite_float)


class ScopedTransport:
    """Scope and run budgets around the actual production HTTP transport."""

    def __init__(self, plan, *, proxy_check=None, connection_factory=None,
                 max_response_bytes=MAX_RESPONSE_BYTES):
        self.calendar_id = plan["calendar_id"]
        self.delegate = DirectHttpsTransport(
            origin=ORIGIN, timeout=20, proxy_check=proxy_check,
            connection_factory=connection_factory,
            max_response_bytes=min(max_response_bytes, MAX_RESPONSE_BYTES))
        self._budget = ResponseBudget(MAX_TOTAL_BYTES)
        self._remaining_requests = 3 + 27 * len(plan["checks"])
        self._deadline = time.monotonic() + MAX_SESSION_SECONDS
        self._login_attempted = False
        self._signin_completed = False
        self._csrf_signin_requests = 0
        self._csrf_calendar_pending = False
        self._csrf_calendar_used = False
        self._closed = False
        self.csrf_redirect_target = None
        try:
            self.delegate.preflight()  # Shared policy, before the hidden prompt.
        except TransportError as error:
            raise WorkerError("e2e_" + error.code) from None

    @property
    def cookie_jar(self):
        return self.delegate.cookie_jar

    @cookie_jar.setter
    def cookie_jar(self, value):
        self.delegate.cookie_jar = value

    def clear(self):
        # Production login resets a session before beginning. This must not
        # reset scope/run budgets or permanently close the one-run guard.
        self._signin_completed = False
        self._csrf_calendar_pending = False
        self.delegate.clear()

    def close(self):
        try:
            self.clear()
        finally:
            self._closed = True

    def _path(self, method, url, body):
        if self._closed or type(method) is not str or type(url) is not str:
            raise WorkerError("e2e_request_denied")
        if method == "PUT" and url == ORIGIN + "/api/v1/auth/email/signin":
            if (self._login_attempted or not isinstance(body, dict)
                    or set(body) != {"uid", "password", "uuid"}
                    or any(not isinstance(value, str) or not value or "\x00" in value for value in body.values())
                    or not re.fullmatch(r"[a-f0-9]{32}", body["uuid"])):
                raise WorkerError("e2e_request_denied")
            self._login_attempted = True
        elif method == "GET" and body is None:
            if url == CSRF_CALENDAR_URL:
                if not self._csrf_calendar_pending or self._csrf_calendar_used:
                    raise WorkerError("e2e_request_denied")
                # Consume before budgets, delegate calls or any possible failure.
                self._csrf_calendar_pending = False
                self._csrf_calendar_used = True
                return url[len(ORIGIN):]
            if url == CSRF_URL:
                self._csrf_signin_requests += 1
            allowed = url in {CSRF_URL, ORIGIN + "/api/v1/calendars?since=0"}
            sync = re.fullmatch(re.escape(ORIGIN + f"/api/v1/calendar/{self.calendar_id}/events/sync?since=")
                                + r"-?[0-9]{1,24}", url)
            if not allowed and sync is None:
                raise WorkerError("e2e_request_denied")
        else:
            raise WorkerError("e2e_request_denied")
        return url[len(ORIGIN):]

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.csrf_redirect_target = None
        if method != "GET" or url != CSRF_CALENDAR_URL or body is not None:
            self._csrf_calendar_pending = False
        self._path(method, url, body)
        remaining = self._deadline - time.monotonic()
        if self._remaining_requests <= 0 or self._budget.remaining_bytes <= 0 or remaining <= 0:
            raise WorkerError("e2e_session_limit")
        self._remaining_requests -= 1
        try:
            response = self.delegate.request(method, url, body, headers,
                min(20, remaining), budget=self._budget)
            if type(response) is CsrfCalendarRedirect:
                if (method == "GET" and url == CSRF_URL and body is None
                        and self._signin_completed and self._csrf_signin_requests == 1
                        and not self._csrf_calendar_used):
                    self._csrf_calendar_pending = True
                    return response
                self.clear()
                raise WorkerError("e2e_http_redirect_rejected", redirect_target="calendars")
            if not 200 <= response.status < 300:
                self.clear()
                raise WorkerError("e2e_" + _transport_core.http_failure_reason(response.status),
                                  status=response.status)
            if method == "PUT" and url == ORIGIN + _transport_core.AUTH_PATH:
                self._signin_completed = True
            return response
        except BaseException as error:
            self._csrf_calendar_pending = False
            self._signin_completed = False
            if isinstance(error, TransportError):
                self.csrf_redirect_target = error.redirect_target
                raise WorkerError("e2e_" + error.code, status=error.status,
                                  redirect_target=error.redirect_target) from None
            raise


class _NoCredentialStore:
    def read(self):
        raise WorkerError("e2e_credential_store_denied")

    status = read
    write = read
    clear = read


class ScopedReader(TimeTreeHttpReader):
    def __init__(self, plan, email, password, transport):
        super().__init__(email=email, password=password, transport=transport,
                         base_url=ORIGIN, credential_store=_NoCredentialStore())
        self.plan = plan
        self._login_attempted = False
        self._closed = False
        self.login_diagnostic = None
        self._login_phase = "sign_in"

    def _occurrence_editor(self):
        raise WorkerError("e2e_journal_denied")

    def login(self):
        if self._login_attempted or self._closed:
            raise WorkerError("e2e_login_denied")
        self._login_attempted = True
        try:
            result = super().login()
            return {"authenticated": result["authenticated"],
                    "csrf_available": result["csrf_available"]}
        except BaseException as error:
            try:
                self.close()
            except BaseException:
                pass
            if isinstance(error, Exception):
                raise WorkerError("e2e_operation_failed") from None
            raise

    def _ensure_authenticated(self):
        if self._closed or not self._authenticated:
            raise WorkerError("e2e_login_required")

    def status(self):
        return {"authenticated": bool(self._authenticated), "csrf_available": bool(self._csrf_token),
                "credentials_configured": bool(self.email and self.password),
                "credential_source": "constructor", "keychain": {"checked": False}}

    def logout(self):
        self.close()
        return {"authenticated": False}

    def close(self):
        try:
            super().logout()
        except Exception:
            raise WorkerError("e2e_cleanup_failed") from None
        finally:
            try:
                close = getattr(self.transport, "close", None)
                if callable(close):
                    close()
            finally:
                self.email = self.password = self._csrf_token = None
                self._authenticated = False
                self._closed = True

    def _calendar_records(self, body):
        records = super()._calendar_records(body)
        for raw in body["calendars"]:
            if any(key in raw and raw[key] != value for key, value in raw.get("attributes", {}).items()):
                raise WorkerError("e2e_calendar_mismatch")
        matches = [row for row in records if str(row.get("id")) == self.plan["calendar_id"]]
        if (len(matches) != 1
                or matches[0].get("name", matches[0].get("title")) != self.plan["calendar_name"]):
            raise WorkerError("e2e_calendar_mismatch")
        return records

    def _sync_raw_events(self, calendar_id, **kwargs):
        records, issue = super()._sync_raw_events(calendar_id, **kwargs)
        for row in records:
            attrs = row.get("attributes", {})
            if any(key in row and row[key] != value for key, value in attrs.items()):
                raise WorkerError("e2e_source_conflict")
        return records, issue

    def _occurrence_snapshot(self, calendar_id):
        if calendar_id != self.plan["calendar_id"]:
            raise WorkerError("e2e_request_denied")
        # Full pagination, completeness and identity validation happen first.
        records = super()._occurrence_snapshot(calendar_id)
        approved = {row["source_uuid"]: row["title"] for row in self.plan["records"]}
        selected = {}
        for row in records:
            identifier = row.get("uuid")
            links = (row.get("parent_id"), row.get("recurring_uuid"))
            if any(link is not None and not isinstance(link, str) for link in links):
                raise WorkerError("e2e_source_scope_mismatch")
            if identifier not in approved:
                if any(isinstance(link, str) and link in approved for link in links):
                    raise WorkerError("e2e_source_scope_mismatch")
                continue
            deactivated = row.get("deactivated_at")
            if (row.get("id") != identifier or row.get("title") != approved[identifier]
                    or type(row.get("category")) is not int or row["category"] != 1
                    or (deactivated is not None and (type(deactivated) not in (int, float) or deactivated != 0))
                    or identifier in selected
                    or any(link not in (None, "", identifier) and link not in approved for link in links)):
                raise WorkerError("e2e_source_scope_mismatch")
            selected[identifier] = row
        if set(selected) != set(approved):
            raise WorkerError("e2e_source_scope_mismatch")
        # Only this subset reaches the public recurrence provider.
        return list(selected.values())

    def read(self, operation, arguments):
        if operation not in {"login", "status", "logout", "list_occurrences"}:
            raise WorkerError("e2e_request_denied")
        try:
            result = super().read(operation, arguments)
            if operation != "list_occurrences":
                return result
            identifiers = {row["source_uuid"] for row in self.plan["records"]}
            rows = result["occurrences"]
            if any(row.get("event_uuid") not in identifiers
                   or row.get("series_uuid") not in identifiers | {None} for row in rows):
                raise WorkerError("e2e_result_scope_mismatch")
            return {"occurrences": [{key: row[key] for key in ROW_KEYS} for row in rows],
                    "count": result["count"], "complete": result["complete"],
                    "coverage_complete": result["coverage_complete"], "has_more": result["has_more"],
                    "scope": "plan_records"}
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise WorkerError("e2e_operation_failed") from None


def _query_key(arguments):
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RestrictedServer:
    def __init__(self, reader, plan):
        self.reader = reader
        self.server = TimeTreeMcpServer(reader)
        self.queries = Counter(_query_key(check["arguments"]) for check in plan["checks"])
        self.failed = False

    def set_notification_sink(self, sink):
        # Monitor methods are inaccessible; never forward unsolicited output.
        self.server.set_notification_sink(lambda _: None)

    def shutdown(self):
        try:
            self.server.shutdown()
        finally:
            self.reader.close()

    def handle(self, message):
        request_id = None
        try:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise WorkerError("e2e_request_denied")
            method = message.get("method")
            if method == "notifications/initialized" and "id" not in message:
                if set(message) - {"jsonrpc", "method", "params"} or message.get("params", {}) != {}:
                    raise WorkerError("e2e_request_denied")
                return None
            candidate_id = message.get("id")
            if type(candidate_id) is not int or not 0 <= candidate_id <= 1024:
                raise WorkerError("e2e_request_denied")
            request_id = candidate_id
            if set(message) - {"jsonrpc", "id", "method", "params"}:
                raise WorkerError("e2e_request_denied")
            if method not in {"initialize", "ping", "tools/list", "tools/call"}:
                raise WorkerError("e2e_request_denied")
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise WorkerError("e2e_request_denied")
            if method in {"ping", "tools/list"} and params:
                raise WorkerError("e2e_request_denied")
            if method == "tools/call":
                if set(params) != {"name", "arguments"} or not isinstance(params["arguments"], dict):
                    raise WorkerError("e2e_request_denied")
                name, arguments = params["name"], params["arguments"]
                if name not in {"timetree_login", "timetree_status", "timetree_logout", "timetree_list_occurrences"}:
                    raise WorkerError("e2e_request_denied")
                if self.failed and name != "timetree_logout":
                    raise WorkerError("e2e_request_denied")
                if name == "timetree_list_occurrences":
                    key = _query_key(arguments)
                    if self.queries[key] <= 0 or not self.reader._authenticated or self.reader._closed:
                        raise WorkerError("e2e_request_denied")
                    self.queries[key] -= 1
                elif arguments or (name == "timetree_login" and self.reader._login_attempted):
                    raise WorkerError("e2e_request_denied")
            response = self.server.handle(message)
            if isinstance(response, dict) and ("error" in response or response.get("result", {}).get("isError")):
                self.failed = True
                result = TimeTreeMcpServer._tool_error(request_id, "e2e_operation_failed")
                if method == "tools/call" and params["name"] == "timetree_login":
                    from .e2e_runner import safe_login_diagnostic
                    diagnostic = safe_login_diagnostic(self.reader.login_diagnostic)
                    if diagnostic is not None:
                        result["result"]["structuredContent"] = {"login_diagnostic": diagnostic}
                try:
                    self.reader.close()
                except Exception:
                    pass
                return result
            return response
        except Exception:
            return TimeTreeMcpServer._error(request_id, -32600, "e2e_request_denied")


class _BoundedInput:
    def __init__(self, stream):
        self.stream = stream
        self.remaining = 128

    def __iter__(self):
        return self

    def __next__(self):
        line = self.stream.readline(MAX_INPUT_BYTES + 1)
        if not line:
            raise StopIteration
        self.remaining -= 1
        if self.remaining < 0 or len(line.encode("utf-8")) > MAX_INPUT_BYTES or not line.endswith("\n"):
            raise WorkerError("e2e_input_rejected")
        try:
            obj = _strict_json(line)
            pending = [(obj, 0)]
            while pending:
                value, depth = pending.pop()
                if depth > 12:
                    raise ValueError()
                if isinstance(value, dict):
                    pending.extend((item, depth + 1) for item in value.values())
                elif isinstance(value, list):
                    pending.extend((item, depth + 1) for item in value)
        except Exception:
            raise WorkerError("e2e_input_rejected") from None
        return line


def run_worker(plan, *, credential_prompt=prompt_credentials, transport_factory=ScopedTransport):
    """Run the actual public dispatcher over stdio; injectable fakes are Python-only."""
    from .e2e_runner import validate_plan

    reader = transport = credentials = None
    original_input = sys.stdin
    old_handlers = {}
    cancelling = cleaning_up = False
    stdio_started = False

    def cancel(signum, frame):
        nonlocal cancelling
        if not cancelling and not cleaning_up:
            cancelling = True
            raise _Cancelled()

    try:
        plan = validate_plan(plan)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            old_handlers[signum] = signal.signal(signum, cancel)
        signal.alarm(MAX_SESSION_SECONDS)
        transport = transport_factory(plan)
        credentials = credential_prompt()
        if (not isinstance(credentials, tuple) or len(credentials) != 2
                or any(not isinstance(value, str) or not value.strip() or "\x00" in value for value in credentials)):
            raise WorkerError("e2e_credentials_required")
        reader = ScopedReader(plan, credentials[0], credentials[1], transport)
        credentials = None
        server = RestrictedServer(reader, plan)
        sys.stdin = _BoundedInput(original_input)
        stdio_started = True
        run_stdio(server)
        return 0
    except (Exception, _Cancelled, KeyboardInterrupt) as error:
        try:
            sys.stderr.write("e2e_worker_failed\n")
        except Exception:
            pass
        if stdio_started:
            return 1
        if isinstance(error, (_Cancelled, KeyboardInterrupt)):
            return 84
        if isinstance(error, WorkerError) and len(error.args) == 1 and isinstance(error.args[0], str):
            return _STARTUP_ERROR_EXITS.get(error.args[0], 85)
        return 85
    finally:
        cleaning_up = True
        signal.alarm(0)
        sys.stdin = original_input
        try:
            if reader is not None:
                reader.close()
            elif transport is not None:
                transport.clear()
        except Exception:
            pass
        credentials = None
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
