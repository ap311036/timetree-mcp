"""Offline boundaries for the operator-only E2E worker; no real credentials."""

import io
import http.client
import json
import signal
import ssl
import subprocess
import unittest
import warnings
from email.message import Message
from unittest.mock import patch

from timetree_mcp.e2e_worker import (
    ORIGIN, RestrictedServer, ScopedReader, ScopedTransport, WorkerError,
    _BoundedInput, _Cancelled, _system_proxy_check, prompt_credentials,
    require_direct_connection, run_worker,
)
from timetree_mcp.http_reader import HttpResponse
from timetree_mcp.http_transport import DirectHttpsTransport, ResponseBudget
from timetree_mcp.occurrence_rules import parse_occurrence_time
from timetree_mcp.server import run_stdio


def plan():
    return {"version": 1, "calendar_id": "42", "calendar_name": "EWS",
            "records": [{"source_uuid": "test-series", "title": "TIMETREE-MCP-TEST daily"}],
            "checks": [{"label": "daily", "arguments": {
                "calendar_id": "42", "start_at": "2030-01-01T00:00:00+08:00",
                "end_at": "2030-02-01T00:00:00+08:00", "limit": 100}, "expected": []}]}


def event(identifier="test-series", **changes):
    row = {"id": identifier, "uuid": identifier, "calendar_id": "42", "category": 1,
           "type": 0, "title": "TIMETREE-MCP-TEST daily", "all_day": False,
           "start_at": parse_occurrence_time("2030-01-07T09:00:00+08:00"),
           "end_at": parse_occurrence_time("2030-01-07T10:00:00+08:00"),
           "start_timezone": "Asia/Taipei", "end_timezone": "Asia/Taipei",
           "parent_id": None, "recurring_uuid": identifier,
           "recurrences": ["RRULE:FREQ=DAILY;COUNT=2"]}
    row.update(changes)
    return row


class FakeTransport:
    def __init__(self, events=None, calendar_name="EWS"):
        self.events = [event()] if events is None else events
        self.calendar_name = calendar_name
        self.requests = []
        self.cleared = 0

    def clear(self):
        self.cleared += 1

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.requests.append((method, url))
        if url == ORIGIN + "/api/v1/auth/email/signin":
            return HttpResponse(200, {}, {})
        if url == ORIGIN + "/signin":
            return HttpResponse(200, {"x-csrf-token": "synthetic-token"}, "")
        if url == ORIGIN + "/api/v1/calendars?since=0":
            return HttpResponse(200, {}, {"calendars": [
                {"id": "42", "name": self.calendar_name},
                {"id": "99", "name": "PRIVATE CANARY CALENDAR"}]})
        if url == ORIGIN + "/api/v1/calendar/42/events/sync?since=0":
            return HttpResponse(200, {}, {"events": self.events, "chunk": 0})
        raise AssertionError("unexpected synthetic request")


def rpc(name, args=None, request_id=2):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": {} if args is None else args}}


class FakeResponse:
    def __init__(self, payload=b"{}", status=200, headers=None):
        self.payload, self.status = payload, status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.read_sizes = []

    def read(self, count):
        self.read_sizes.append(count)
        return self.payload[:count]

    def info(self):
        return self.headers


class FakeConnection:
    def __init__(self, response):
        self.response, self.calls, self.closed = response, [], False

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class WorkerTransportTests(unittest.TestCase):
    def transport(self, response=None, **kwargs):
        connection = FakeConnection(response or FakeResponse())
        factory = lambda *a, **kw: connection
        transport = ScopedTransport(plan(), proxy_check=lambda: None,
                                    connection_factory=factory, **kwargs)
        return transport, connection

    def test_guard_delegates_to_production_core_and_clear_cannot_reset_login_limit(self):
        transport, connection = self.transport()
        self.assertIs(type(transport.delegate), DirectHttpsTransport)
        body = {"uid": "synthetic@example.invalid", "password": "synthetic", "uuid": "0" * 32}
        with patch.object(transport.delegate, "request", wraps=transport.delegate.request) as shared:
            transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
            remaining = transport._budget.remaining_bytes
            requests_left = transport._remaining_requests
            transport.clear()
            self.assertEqual(transport._budget.remaining_bytes, remaining)
            self.assertEqual(transport._remaining_requests, requests_left)
            with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
            self.assertEqual(shared.call_count, 1)
            self.assertEqual(len(connection.calls), 1)
            transport.close()
            with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                transport.request("GET", ORIGIN + "/signin")
            self.assertEqual(shared.call_count, 1)

    def bootstrap_transport(self):
        transport, connection = self.transport(FakeResponse(headers={"Set-Cookie": "session=SYNTHETIC; Secure; Path=/"}))
        body = {"uid": "synthetic@example.invalid", "password": "synthetic", "uuid": "0" * 32}
        transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
        connection.response = FakeResponse(b"PRIVATE REDIRECT BODY", status=302, headers={
            "Location": "/calendars", "Set-Cookie": "session=BAD; Secure; Path=/", "X-CSRF-Token": "BAD"})
        return transport, connection

    def test_calendar_bootstrap_requires_successful_signin_marker_and_is_single_use(self):
        from timetree_mcp.http_transport import CsrfCalendarRedirect
        transport, connection = self.bootstrap_transport()
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")
        redirect = connection.response
        result = transport.request("GET", ORIGIN + "/signin")
        self.assertIs(type(result), CsrfCalendarRedirect)
        self.assertEqual(redirect.read_sizes, [])
        self.assertEqual([cookie.value for cookie in transport.cookie_jar], ["SYNTHETIC"])
        connection.response = FakeResponse(b'<meta name="csrf-token" content="SYNTHETIC">')
        response = transport.request("GET", ORIGIN + "/calendars")
        self.assertEqual(response.status, 200)
        self.assertIsNone(connection.calls[-1][1]["body"])
        self.assertEqual(len(connection.calls), 3)
        self.assertEqual(transport._remaining_requests, 27 * len(plan()["checks"]))
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")
        self.assertEqual(len(connection.calls), 3)

    def test_bootstrap_permit_is_revoked_by_clear_close_or_intervening_request(self):
        for action in ("clear", "close", "intervening"):
            with self.subTest(action=action):
                transport, connection = self.bootstrap_transport()
                transport.request("GET", ORIGIN + "/signin")
                if action == "intervening":
                    connection.response = FakeResponse()
                    transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
                else:
                    getattr(transport, action)()
                calls = len(connection.calls)
                with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                    transport.request("GET", ORIGIN + "/calendars")
                self.assertEqual(len(connection.calls), calls)

    def test_bootstrap_permit_cannot_be_regranted_or_used_after_failed_destination(self):
        transport, connection = self.bootstrap_transport()
        transport.request("GET", ORIGIN + "/signin")
        connection.response = FakeResponse(status=302, headers={"Location": "/calendars"})
        with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
            transport.request("GET", ORIGIN + "/calendars")
        self.assertEqual(list(transport.cookie_jar), [])
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")
        with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
            transport.request("GET", ORIGIN + "/signin")
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")
        self.assertEqual(sum(call[0][0] == "PUT" for call in connection.calls), 1)

    def test_plain_302_or_marker_without_successful_signin_cannot_grant_calendar(self):
        transport, connection = self.bootstrap_transport()
        with patch.object(transport.delegate, "request", return_value=HttpResponse(302, {"Location": "/calendars"}, "")):
            with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
                transport.request("GET", ORIGIN + "/signin")
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")
        transport, connection = self.transport(FakeResponse(status=302, headers={"Location": "/calendars"}))
        with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
            transport.request("GET", ORIGIN + "/signin")
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/calendars")

    def test_transport_failure_or_cancellation_revokes_signin_stage_before_bootstrap(self):
        class Cancelled(BaseException):
            pass
        for failure in (OSError("synthetic failure"), Cancelled()):
            with self.subTest(kind=type(failure).__name__):
                transport, connection = self.bootstrap_transport()
                def fail():
                    raise failure
                connection.getresponse = fail
                with self.assertRaises(BaseException):
                    transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
                connection.getresponse = lambda: connection.response
                with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
                    transport.request("GET", ORIGIN + "/signin")
                with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                    transport.request("GET", ORIGIN + "/calendars")
                self.assertEqual(sum(call[0][0] == "PUT" for call in connection.calls), 1)

    def test_aggregate_raw_budget_is_applied_inside_core_before_read_and_not_reset_by_clear(self):
        response = FakeResponse(b"synthetic-over-budget")
        transport, connection = self.transport(response)
        transport._budget = ResponseBudget(5)
        with self.assertRaisesRegex(WorkerError, "^e2e_response_limit$"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(response.read_sizes, [6])
        self.assertLessEqual(transport._budget.remaining_bytes, 0)
        transport.clear()
        with self.assertRaisesRegex(WorkerError, "^e2e_session_limit$"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(len(connection.calls), 1)

    def test_denies_writes_and_every_noncanonical_origin_before_connection(self):
        cases = [("POST", ORIGIN + "/api/v1/calendar/42/event"),
                 ("GET", ORIGIN + "/"),
                 ("GET", ORIGIN + "/signin/"),
                 ("GET", ORIGIN + "/signin?x=1"),
                 ("GET", ORIGIN + "/signin#private"),
                 ("GET", ORIGIN + "//signin"),
                 ("GET", ORIGIN + "/SIGNIN"),
                 ("GET", ORIGIN + "/%73ignin"),
                 ("POST", ORIGIN + "/signin"),
                 ("HEAD", ORIGIN + "/signin"),
                 ("GET", "http://timetreeapp.com/"),
                 ("GET", "https://timetreeapp.com:443/"),
                 ("GET", "https://user@timetreeapp.com/"),
                 ("GET", "https://other.invalid/"),
                 ("GET", ORIGIN + "/api/v1/calendar/99/events/sync?since=0"),
                 ("GET", ORIGIN + "/api/v1/calendar/42/events/sync?since=0&x=1"),
                 ("GET", ORIGIN + "/#private"),
                 ("GET", ORIGIN + "/api/v1/calendars?since=1")]
        for method, url in cases:
            with self.subTest(url=url):
                transport, conn = self.transport()
                with self.assertRaisesRegex(WorkerError, "e2e_request_denied"):
                    transport.request(method, url)
                self.assertEqual(conn.calls, [])

    def test_redirects_and_network_errors_are_fixed_and_never_followed(self):
        transport, conn = self.transport(FakeResponse(status=302, headers={"Location": "https://PRIVATE.invalid"}))
        with self.assertRaisesRegex(WorkerError, "e2e_http_redirect_rejected"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(len(conn.calls), 1)
        self.assertTrue(conn.closed)
        transport, conn = self.transport()
        conn.getresponse = lambda: (_ for _ in ()).throw(OSError("PRIVATE ERROR SECRET"))
        with self.assertRaisesRegex(WorkerError, "^e2e_network_failed$"):
            transport.request("GET", ORIGIN + "/signin")

    def test_http_login_failures_classify_status_without_reading_error_body(self):
        cases = [(401, "http_401"), (403, "http_403"), (429, "http_429"),
                 (404, "http_4xx"), (500, "http_5xx"), (302, "http_redirect_rejected"),
                 (304, "http_redirect_rejected"), (100, "http_rejected"), (600, "invalid_response")]
        for status, reason in cases:
            with self.subTest(status=status):
                response = FakeResponse(b"PRIVATE PASSWORD SESSION BODY", status=status)
                transport, conn = self.transport(response)
                with self.assertRaisesRegex(WorkerError, "^e2e_" + reason + "$"):
                    transport.request("GET", ORIGIN + "/signin")
                self.assertEqual(response.read_sizes, [])
                self.assertEqual(len(conn.calls), 1)

    def test_exact_signin_redirect_target_is_metadata_only_and_never_followed(self):
        cases = [(ORIGIN + path, kind) for path, kind in (
            ("/intl/en/", "public_landing"), ("/intl/en", "public_landing"),
            ("/signin", "signin"), ("/calendars", "calendars"))]
        cases += [("/intl/en/", "public_landing"), ("/intl/en", "public_landing"),
                  ("/signin", "signin"), ("/calendars", "calendars")]
        for location, kind in cases:
            with self.subTest(kind=kind):
                response = FakeResponse(b"PRIVATE BODY", status=302,
                                        headers={"Location": location, "Set-Cookie": "private_session=CANARY"})
                transport, conn = self.transport(response)
                with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
                    transport.request("GET", ORIGIN + "/signin")
                self.assertEqual(transport.csrf_redirect_target, kind)
                self.assertEqual(len(conn.calls), 1)
                self.assertEqual(response.read_sizes, [])
                self.assertEqual(list(transport.cookie_jar), [])

    def test_nonliteral_missing_duplicate_or_unsafe_locations_are_not_exposed(self):
        cases = [[], ["/signin", "/signin"], [None], [42], ["x" * 65537],
                 ["/signin\nPRIVATE"], [" /signin"], ["/signin "], ["/signin/"],
                 ["/signin?private=CANARY"], ["/signin#PRIVATE"], ["//timetreeapp.com/signin"],
                 ["http://timetreeapp.com/signin"], ["https://timetreeapp.com:443/signin"],
                 ["https://PRIVATE@timetreeapp.com/signin"], ["https://timetreeapp.com.PRIVATE/signin"],
                 ["https://TIMETREEAPP.com/signin"], ["/intl%2fen/"], ["/PRIVATE"]]
        for locations in cases:
            with self.subTest(count=len(locations)):
                response = FakeResponse(status=302)
                for location in locations:
                    response.headers["Location"] = location
                transport, conn = self.transport(response)
                reason = ("invalid_response" if locations in ([None], [42]) else
                          "response_limit" if locations == ["x" * 65537] else "http_redirect_rejected")
                with self.assertRaisesRegex(WorkerError, "^e2e_" + reason + "$"):
                    transport.request("GET", ORIGIN + "/signin")
                self.assertEqual(transport.csrf_redirect_target,
                                 "other_or_missing" if reason == "http_redirect_rejected" else None)
                self.assertEqual(response.read_sizes, [])
                self.assertEqual(len(conn.calls), 1)

    def test_redirect_metadata_resets_and_is_absent_outside_exact_get_signin(self):
        transport, conn = self.transport(FakeResponse(status=302, headers={"Location": "/signin"}))
        with self.assertRaises(WorkerError):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(transport.csrf_redirect_target, "signin")
        with self.assertRaises(WorkerError):
            transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
        self.assertIsNone(transport.csrf_redirect_target)
        transport.csrf_redirect_target = "signin"
        with self.assertRaises(WorkerError):
            transport.request("GET", ORIGIN + "/")
        self.assertIsNone(transport.csrf_redirect_target)
        body = {"uid": "synthetic@example.invalid", "password": "SYNTHETIC", "uuid": "0" * 32}
        with self.assertRaises(WorkerError):
            transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
        self.assertIsNone(transport.csrf_redirect_target)

    def test_network_and_internal_errors_classify_by_type_never_exception_text(self):
        cases = [(ssl.SSLError("PRIVATE TLS DETAILS"), "tls_failed"),
                 (TimeoutError("PRIVATE TIMEOUT"), "network_timeout"),
                 (OSError("PRIVATE NETWORK"), "network_failed"),
                 (RuntimeError("e2e_http_401 PRIVATE"), "internal_error"),
                 (http.client.IncompleteRead(b"PRIVATE PARTIAL BODY"), "invalid_response")]
        for failure, reason in cases:
            with self.subTest(reason=reason):
                transport, conn = self.transport()
                def fail():
                    raise failure
                conn.getresponse = fail
                with self.assertRaisesRegex(WorkerError, "^e2e_" + reason + "$"):
                    transport.request("GET", ORIGIN + "/signin")

    def test_invalid_encoding_json_and_response_shape_are_not_network_errors(self):
        cases = [(b"\xff", {}), (b"PRIVATE NOT JSON", {}), (b"[]", {}),
                 (b'{"error":"PRIVATE"}', {}), (b'{"a":1,"a":2}', {}),
                 (b'{"a":NaN}', {}), (b"{}", {"Content-Encoding": "gzip"})]
        for payload, headers in cases:
            with self.subTest(payload=payload):
                transport, conn = self.transport(FakeResponse(payload, headers=headers))
                body = {"uid": "synthetic@example.invalid", "password": "SYNTHETIC", "uuid": "0" * 32}
                with self.assertRaisesRegex(WorkerError, "^e2e_invalid_response$"):
                    transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)

    def test_primary_http_failure_survives_connection_cleanup_failure(self):
        transport, conn = self.transport(FakeResponse(status=401))
        def fail_close():
            raise OSError("PRIVATE CLEANUP DETAIL")
        conn.close = fail_close
        with self.assertRaisesRegex(WorkerError, "^e2e_http_401$"):
            transport.request("GET", ORIGIN + "/signin")

    def test_primary_http_failure_survives_cookie_cleanup_error_and_discards_session(self):
        transport, conn = self.transport(FakeResponse(status=401))
        class BrokenCookieJar:
            def add_cookie_header(self, request):
                pass
            def clear(self):
                raise OSError("PRIVATE COOKIE CLEANUP")
        transport.cookie_jar = BrokenCookieJar()
        def fail_close():
            raise OSError("PRIVATE CONNECTION CLEANUP")
        conn.close = fail_close
        with self.assertRaisesRegex(WorkerError, "^e2e_http_401$"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(list(transport.cookie_jar), [])

    def test_raw_response_bound_applies_before_decode(self):
        response = FakeResponse(b"x" * 20)
        transport, conn = self.transport(response, max_response_bytes=10)
        with self.assertRaisesRegex(WorkerError, "e2e_response_limit"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(response.read_sizes, [11])
        self.assertTrue(conn.closed)

    def test_login_once_and_nonlogin_body_denied(self):
        transport, conn = self.transport()
        body = {"uid": "synthetic@example.invalid", "password": "SYNTHETIC", "uuid": "0" * 32}
        transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
        with self.assertRaises(WorkerError):
            transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
        with self.assertRaises(WorkerError):
            transport.request("GET", ORIGIN + "/signin", {"password": "SYNTHETIC"})

    def test_proxy_detection_rejects_before_prompt_without_printing_values(self):
        with self.assertRaisesRegex(WorkerError, "^e2e_proxy_unsupported$"):
            require_direct_connection(environment={"HTTPS_PROXY": "PRIVATE PROXY"},
                                      proxy_lookup=lambda: self.fail("must reject env first"),
                                      system_proxy_check=lambda: None)
        with self.assertRaisesRegex(WorkerError, "^e2e_proxy_unsupported$"):
            require_direct_connection(environment={}, proxy_lookup=lambda: {"https": "PRIVATE PROXY"},
                                      system_proxy_check=lambda: None)

    def test_native_bypass_metadata_still_runs_system_proxy_check(self):
        checked = []
        require_direct_connection(environment={}, proxy_lookup=lambda: {"no": "*.local,169.254/16"},
                                  system_proxy_check=lambda: checked.append(True))
        self.assertEqual(checked, [True])
        def pac_enabled():
            raise WorkerError("e2e_proxy_unsupported")
        with self.assertRaisesRegex(WorkerError, "^e2e_proxy_unsupported$"):
            require_direct_connection(environment={}, proxy_lookup=lambda: {"no": "*.local"},
                                      system_proxy_check=pac_enabled)

    def test_native_bypass_exception_never_allows_active_unknown_or_malformed_proxy_settings(self):
        values = [{"no": "*.local", "https": "PRIVATE PROXY"}, {"No": "*.local"},
                  {"unknown": "PRIVATE"}, {"no": None}, {"no": []}, {"no": 1},
                  {"no": ""}, {"no": "PRIVATE\nINJECTED"}, {"no": "x" * 4097}]
        for proxies in values:
            with self.subTest(proxies=proxies), self.assertRaises(WorkerError):
                require_direct_connection(environment={}, proxy_lookup=lambda: proxies,
                                          system_proxy_check=lambda: self.fail("must reject invalid native settings"))
        with self.assertRaisesRegex(WorkerError, "^e2e_proxy_unsupported$"):
            require_direct_connection(environment={"no_proxy": "*.local"},
                                      proxy_lookup=lambda: self.fail("environment proxy must remain denied"),
                                      system_proxy_check=lambda: None)

    def test_native_proxy_pac_and_ambiguous_results_fail_closed(self):
        for output in ("<dictionary> {\n ProxyAutoConfigEnable : 1\n}",
                       "<dictionary> {\n ProxyAutoDiscoveryEnable : 1\n}",
                       "<dictionary> {\n HTTPSEnable : PRIVATE\n}",
                       "PRIVATE malformed", "<dictionary> {"):
            with self.subTest(output=output), patch("sys.platform", "darwin"), \
                    patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")), \
                    self.assertRaises(WorkerError):
                _system_proxy_check()

    def test_cookie_session_memory_and_fixed_headers(self):
        response = FakeResponse(headers={"Set-Cookie": "synthetic_session=canary; Secure; HttpOnly; Path=/"})
        transport, conn = self.transport(response)
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/signin", headers={"Referer": "https://PRIVATE.invalid"})
        self.assertEqual(conn.calls, [])
        transport.request("GET", ORIGIN + "/signin")
        transport.request("GET", ORIGIN + "/signin")
        headers = conn.calls[1][1]["headers"]
        self.assertEqual(headers["Referer"], ORIGIN + "/")
        self.assertIn("synthetic_session=canary", headers["Cookie"])
        transport.clear()
        self.assertEqual(list(transport.cookie_jar), [])

    def test_total_request_budget_stops_before_next_network_operation(self):
        transport, conn = self.transport()
        transport._remaining_requests = 1
        transport.request("GET", ORIGIN + "/signin")
        with self.assertRaisesRegex(WorkerError, "e2e_session_limit"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(len(conn.calls), 1)

    def test_deadline_and_close_failure_never_expose_provider_details(self):
        transport, conn = self.transport()
        transport._deadline = 0
        with self.assertRaisesRegex(WorkerError, "^e2e_session_limit$"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(conn.calls, [])
        transport, conn = self.transport()
        def bad_close():
            raise OSError("PRIVATE socket cleanup token")
        conn.close = bad_close
        with self.assertRaisesRegex(WorkerError, "^e2e_network_failed$"):
            transport.request("GET", ORIGIN + "/signin")


class WorkerScopeTests(unittest.TestCase):
    def make_server(self, **options):
        transport = FakeTransport(**options)
        reader = ScopedReader(plan(), "synthetic@example.invalid", "SYNTHETIC PASSWORD", transport)
        return RestrictedServer(reader, plan()), reader, transport

    def login(self, server):
        result = server.handle(rpc("timetree_login"))
        self.assertFalse(result["result"]["isError"])

    def test_login_uses_one_api_put_then_one_fixed_signin_get(self):
        server, reader, transport = self.make_server()
        self.login(server)
        self.assertEqual(transport.requests, [
            ("PUT", ORIGIN + "/api/v1/auth/email/signin"),
            ("GET", ORIGIN + "/signin")])

    def test_fixed_signin_get_reuses_public_header_and_meta_token_extraction(self):
        for use_header in (False, True):
            with self.subTest(use_header=use_header):
                server, reader, transport = self.make_server()
                original = transport.request
                def request(method, url, body=None, headers=None, timeout=None):
                    if url == ORIGIN + "/signin":
                        self.assertEqual(method, "GET")
                        self.assertIsNone(body)
                        transport.requests.append((method, url))
                        response_headers = {"X-CsRf-ToKeN": "SYNTHETIC-HEADER"} if use_header else {}
                        return HttpResponse(200, response_headers,
                                            '<meta name="csrf-token" content="SYNTHETIC-META">')
                    return original(method, url, body, headers, timeout)
                transport.request = request
                self.login(server)
                self.assertEqual(reader._csrf_token, "SYNTHETIC-HEADER" if use_header else "SYNTHETIC-META")
                self.assertEqual(len(transport.requests), 2)

    def test_public_server_query_filters_before_projection_and_sanitizes_output(self):
        unrelated = event("PRIVATE-UUID", title="PRIVATE CANARY TITLE", recurrences=["PRIVATE INVALID RULE"])
        server, reader, transport = self.make_server(events=[event(note="PRIVATE NOTE"), unrelated])
        self.login(server)
        result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
        text = json.dumps(result)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("title", text)
        payload = result["result"]["structuredContent"]
        self.assertEqual(payload["count"], 2)
        self.assertTrue(payload["complete"])
        self.assertEqual(set(payload["occurrences"][0]), {
            "event_uuid", "series_uuid", "source", "timezone", "start_at", "end_at", "start_local", "end_local"})

    def test_missing_renamed_calendar_or_unplanned_links_fail_without_details(self):
        cases = [{"events": []}, {"events": [event(title="PRIVATE RENAMED")]},
                 {"calendar_name": "PRIVATE CALENDAR"},
                 {"events": [event(), event("PRIVATE-CHILD", parent_id="test-series", recurring_uuid="test-series", recurrences=[])]},
                 {"events": [event(parent_id="PRIVATE-PARENT", recurring_uuid="PRIVATE-PARENT")]},
                 {"events": [event(), event()]}]
        for options in cases:
            with self.subTest(options=list(options)):
                server, reader, transport = self.make_server(**options)
                self.login(server)
                result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
                self.assertTrue(result["result"]["isError"])
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_selected_deactivated_and_ambiguous_records_cannot_pass_empty_expectation(self):
        for value in (1, -1, True, False, "0"):
            with self.subTest(value=value):
                server, reader, transport = self.make_server(events=[event(deactivated_at=value)])
                self.login(server)
                result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
                self.assertTrue(result["result"]["isError"])

    def test_selected_memo_or_invalid_category_cannot_pass_empty_expectation(self):
        for value in (2, True, "1", None):
            with self.subTest(value=value):
                server, reader, transport = self.make_server(events=[event(category=value)])
                self.login(server)
                result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
                self.assertTrue(result["result"]["isError"])

    def test_conflicting_nested_data_and_ambiguous_relations_fail_closed(self):
        for records in ([event(attributes={"title": "PRIVATE nested title"})],
                        [event(), event("unrelated", parent_id=["test-series"])]):
            server, reader, transport = self.make_server(events=records)
            self.login(server)
            result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
            self.assertTrue(result["result"]["isError"])
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_conflicting_nested_calendar_name_cannot_satisfy_scope(self):
        server, reader, transport = self.make_server()
        original = transport.request
        def request(method, url, *args, **kwargs):
            result = original(method, url, *args, **kwargs)
            if url.endswith("/calendars?since=0"):
                result.body["calendars"][0]["attributes"] = {"name": "PRIVATE OTHER CALENDAR"}
            return result
        transport.request = request
        self.login(server)
        result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
        self.assertTrue(result["result"]["isError"])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_tool_and_query_allowlist_blocks_every_mutation_and_arbitrary_query(self):
        server, reader, transport = self.make_server()
        for name in ("timetree_move_occurrence", "timetree_create_event", "timetree_keychain_status",
                     "timetree_occurrence_operation_status", "timetree_start_monitor", "timetree_list_calendars"):
            result = server.handle(rpc(name))
            self.assertIn("error", result)
        result = server.handle(rpc("timetree_list_occurrences", {**plan()["checks"][0]["arguments"], "calendar_id": "99"}))
        self.assertIn("error", result)
        self.assertEqual(transport.requests, [])

    def test_guidance_methods_remain_outside_restricted_live_worker(self):
        for method in ("prompts/list", "prompts/get", "resources/list", "resources/read", "resources/templates/list"):
            with self.subTest(method=method):
                server, reader, transport = self.make_server()
                self.addCleanup(server.shutdown)
                response = server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}})
                self.assertEqual(response["error"], {"code": -32600, "message": "e2e_request_denied"})
                self.assertEqual(transport.requests, [])

    def test_requires_explicit_single_login_and_logout_drops_credentials(self):
        server, reader, transport = self.make_server()
        result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
        self.assertIn("error", result)
        self.assertEqual(transport.requests, [])
        self.login(server)
        repeated = server.handle(rpc("timetree_login"))
        self.assertIn("error", repeated)
        server.shutdown()
        self.assertIsNone(reader.email)
        self.assertIsNone(reader.password)
        self.assertGreater(transport.cleared, 0)

    def test_errors_sanitized_before_actual_stdio_and_no_unused_secret_reads(self):
        server, reader, transport = self.make_server()
        def fail(*a, **kw):
            raise RuntimeError("PRIVATE CANARY PASSWORD")
        transport.request = fail
        messages = [rpc("timetree_login"), rpc("timetree_status")]
        with patch("sys.stdin", io.StringIO("".join(json.dumps(m) + "\n" for m in messages))), \
                patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("subprocess.run", side_effect=AssertionError("no process/Keychain")):
            run_stdio(server)
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertNotIn("SYNTHETIC PASSWORD", output.getvalue())
        self.assertIn("e2e_operation_failed", output.getvalue())

    def test_failed_login_carries_only_fixed_phase_reason_and_survives_cleanup(self):
        cases = [("sign_in", "http_401"), ("csrf_fetch", "http_403"),
                 ("csrf_fetch", "network_timeout"), ("csrf_fetch", "tls_failed")]
        for phase, reason in cases:
            with self.subTest(phase=phase, reason=reason):
                server, reader, transport = self.make_server()
                original = transport.request
                def request(method, url, *args, **kwargs):
                    target = ORIGIN + ("/api/v1/auth/email/signin" if phase == "sign_in" else "/signin")
                    if url == target:
                        raise WorkerError("e2e_" + reason)
                    return original(method, url, *args, **kwargs)
                transport.request = request
                initial_clear = transport.clear
                clear_calls = []
                def bad_clear():
                    clear_calls.append(True)
                    if len(clear_calls) == 1:
                        return initial_clear()
                    raise OSError("PRIVATE CLEAR PASSWORD")
                transport.clear = bad_clear
                result = server.handle(rpc("timetree_login"))
                self.assertTrue(result["result"]["isError"])
                self.assertEqual(result["result"]["structuredContent"], {
                    "login_diagnostic": {"phase": phase, "reason": reason}})
                self.assertEqual(reader.login_diagnostic, {"phase": phase, "reason": reason})
                self.assertIsNone(reader.password)
                self.assertNotIn("PRIVATE", json.dumps(result))
                reader.login_diagnostic["reason"] = "PRIVATE AFTER COPY"
                self.assertNotIn("PRIVATE", json.dumps(result))
                repeated = server.handle(rpc("timetree_login"))
                self.assertNotIn("login_diagnostic", json.dumps(repeated))

    def test_missing_csrf_and_unclassified_login_error_have_safe_diagnostics(self):
        for missing in (True, False):
            server, reader, transport = self.make_server()
            original = transport.request
            def request(method, url, *args, **kwargs):
                if not missing:
                    raise RuntimeError("e2e_http_401 PRIVATE PASSWORD")
                if url == ORIGIN + "/signin":
                    return HttpResponse(200, {}, "PRIVATE PAGE WITHOUT TOKEN")
                return original(method, url, *args, **kwargs)
            transport.request = request
            result = server.handle(rpc("timetree_login"))
            self.assertTrue(result["result"]["isError"])
            self.assertEqual(result["result"]["structuredContent"], {"login_diagnostic": {
                "phase": "csrf_fetch" if missing else "sign_in",
                "reason": "csrf_missing" if missing else "internal_error"}})
            self.assertNotIn("PRIVATE", json.dumps(result))

    def test_redirect_target_is_optional_only_for_csrf_redirect_and_survives_cleanup(self):
        cases = [("csrf_fetch", "http_redirect_rejected", value, True)
                 for value in ("public_landing", "signin", "calendars", "other_or_missing")]
        cases += [("sign_in", "http_redirect_rejected", "signin", False),
                  ("csrf_fetch", "http_403", "signin", False),
                  ("csrf_fetch", "http_redirect_rejected", "PRIVATE RAW LOCATION", False),
                  ("csrf_fetch", "http_redirect_rejected", None, False),
                  ("csrf_fetch", "http_redirect_rejected", ["signin"], False)]
        for phase, reason, target, expected in cases:
            with self.subTest(phase=phase, reason=reason, expected=expected):
                server, reader, transport = self.make_server()
                original = transport.request
                def request(method, url, *args, **kwargs):
                    if url == ORIGIN + ("/api/v1/auth/email/signin" if phase == "sign_in" else "/signin"):
                        transport.csrf_redirect_target = target
                        raise WorkerError("e2e_" + reason, redirect_target=target)
                    return original(method, url, *args, **kwargs)
                def clear():
                    transport.csrf_redirect_target = None
                transport.request, transport.clear = request, clear
                result = server.handle(rpc("timetree_login"))
                diagnostic = {"phase": phase, "reason": reason}
                if expected:
                    diagnostic["redirect_target"] = target
                self.assertEqual(result["result"]["structuredContent"], {"login_diagnostic": diagnostic})
                self.assertEqual(reader.login_diagnostic, diagnostic)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_invalid_diagnostic_is_omitted_and_nonlogin_error_never_reuses_it(self):
        invalid = [{"phase": "sign_in", "reason": "csrf_missing"},
                   {"phase": "sign_in", "reason": "PRIVATE"},
                   {"phase": "sign_in", "reason": "http_401", "private": "PRIVATE"},
                   {"phase": None, "reason": "http_401"}]
        for value in invalid:
            server, reader, transport = self.make_server()
            def fail():
                reader.login_diagnostic = value
                raise RuntimeError("PRIVATE")
            reader.login = fail
            result = server.handle(rpc("timetree_login"))
            self.assertTrue(result["result"]["isError"])
            self.assertNotIn("login_diagnostic", json.dumps(result))
            self.assertNotIn("PRIVATE", json.dumps(result))
        server, reader, transport = self.make_server()
        self.login(server)
        reader.login_diagnostic = {"phase": "sign_in", "reason": "http_401"}
        def fail_request(*a, **kw):
            raise RuntimeError("PRIVATE")
        transport.request = fail_request
        result = server.handle(rpc("timetree_list_occurrences", plan()["checks"][0]["arguments"]))
        self.assertNotIn("login_diagnostic", json.dumps(result))

    def test_actual_stdio_login_diagnostic_contains_no_provider_body_or_exception(self):
        server, reader, transport = self.make_server()
        def fail(*a, **kw):
            raise RuntimeError("PRIVATE PASSWORD SESSION")
        transport.request = fail
        with patch("sys.stdin", io.StringIO(json.dumps(rpc("timetree_login")) + "\n")), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            run_stdio(server)
        result = json.loads(output.getvalue())
        self.assertEqual(result["result"]["structuredContent"], {
            "login_diagnostic": {"phase": "sign_in", "reason": "internal_error"}})
        self.assertNotIn("PRIVATE", output.getvalue())


class WorkerPromptTests(unittest.TestCase):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    def test_both_prompts_use_tty_and_fallback_is_error(self):
        tty = self.Tty()
        replies = iter(["synthetic@example.invalid", " SYNTHETIC password "])
        self.assertEqual(prompt_credentials(tty_opener=lambda *a, **kw: tty,
                         prompt=lambda *a, **kw: next(replies)),
                         ("synthetic@example.invalid", " SYNTHETIC password "))
        import getpass
        def fallback(*a, **kw):
            warnings.warn("PRIVATE warning", getpass.GetPassWarning)
            return "must never return"
        with self.assertRaisesRegex(WorkerError, "^e2e_tty_required$"):
            prompt_credentials(tty_opener=lambda *a, **kw: self.Tty(), prompt=fallback)

    def test_prompt_stream_is_output_only_for_nonseekable_macos_controlling_tty(self):
        opened, prompts = [], []
        def opener(path, mode, **kwargs):
            opened.append((path, mode))
            if mode != "w":
                raise io.UnsupportedOperation("File or stream is not seekable")
            return self.Tty()
        answers = iter(["synthetic@example.invalid", "SYNTHETIC"])
        def prompt(message, *, stream):
            prompts.append(stream.isatty())
            return next(answers)
        self.assertEqual(prompt_credentials(tty_opener=opener, prompt=prompt),
                         ("synthetic@example.invalid", "SYNTHETIC"))
        self.assertEqual(opened, [("/dev/tty", "w")])
        self.assertEqual(prompts, [True, True])

    def test_missing_tty_and_eof_fail_closed(self):
        def missing(*a, **kw):
            raise OSError("PRIVATE PATH")
        with self.assertRaisesRegex(WorkerError, "^e2e_tty_required$"):
            prompt_credentials(tty_opener=missing)
        with self.assertRaisesRegex(WorkerError, "^e2e_tty_required$"):
            prompt_credentials(tty_opener=lambda *a, **kw: self.Tty(),
                               prompt=lambda *a, **kw: (_ for _ in ()).throw(EOFError()))


class WorkerLifecycleTests(unittest.TestCase):
    def test_input_rejects_duplicate_keys_nonfinite_deep_or_unterminated_frames(self):
        for text in ('{"jsonrpc":"2.0","id":1,"id":2}\n', '{"id":NaN}\n',
                     '{"id":Infinity}\n', '[' * 14 + '0' + ']' * 14 + '\n',
                     '{"id":1}', 'x' * 65537 + '\n'):
            with self.subTest(text=text[:40]), self.assertRaisesRegex(WorkerError, "e2e_input_rejected"):
                next(_BoundedInput(io.StringIO(text)))

    def run_fake(self, transport, prompt):
        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("sys.stderr", new_callable=io.StringIO) as error, \
                patch("signal.signal"), patch("signal.alarm"):
            code = run_worker(plan(), credential_prompt=prompt, transport_factory=lambda _: transport)
        return code, output.getvalue(), error.getvalue()

    def test_cancelled_prompt_cleans_transport_and_never_outputs_exception(self):
        transport = FakeTransport()
        def cancel():
            raise _Cancelled()
        code, output, error = self.run_fake(transport, cancel)
        self.assertEqual(code, 84)
        self.assertEqual(output, "")
        self.assertEqual(error, "e2e_worker_failed\n")
        self.assertGreater(transport.cleared, 0)

    def test_pre_stdio_failures_have_fixed_exit_codes_without_exception_text(self):
        cases = [(WorkerError("e2e_proxy_unsupported"), 80),
                 (WorkerError("e2e_proxy_state_unknown"), 81),
                 (WorkerError("e2e_tty_required"), 82),
                 (WorkerError("e2e_credentials_required"), 83),
                 (KeyboardInterrupt(), 84), (RuntimeError("PRIVATE STARTUP ERROR"), 85),
                 (WorkerError("e2e_proxy_unsupported PRIVATE"), 85)]
        for failure, expected in cases:
            with self.subTest(expected=expected):
                def prompt():
                    raise failure
                code, output, error = self.run_fake(FakeTransport(), prompt)
                self.assertEqual(code, expected)
                self.assertEqual(output, "")
                self.assertEqual(error, "e2e_worker_failed\n")

    def test_pre_prompt_transport_failure_never_prompts_and_preserves_fixed_startup_code(self):
        def unavailable(_):
            raise WorkerError("e2e_proxy_state_unknown")
        with patch("sys.stdin", io.StringIO("")), patch("sys.stdout", new_callable=io.StringIO) as output, \
                patch("sys.stderr", new_callable=io.StringIO) as error, \
                patch("signal.signal"), patch("signal.alarm"):
            code = run_worker(plan(), credential_prompt=lambda: self.fail("must not prompt"),
                              transport_factory=unavailable)
        self.assertEqual(code, 81)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "e2e_worker_failed\n")

    def test_after_stdio_begins_startup_codes_are_not_reused(self):
        for failure in (WorkerError("e2e_tty_required"), _Cancelled(), RuntimeError("PRIVATE")):
            with self.subTest(failure=type(failure).__name__), \
                    patch("timetree_mcp.e2e_worker.run_stdio", side_effect=failure):
                code, output, error = self.run_fake(FakeTransport(), lambda: ("synthetic@example.invalid", "SYNTHETIC"))
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertEqual(error, "e2e_worker_failed\n")

    def test_cleanup_error_with_private_canary_is_suppressed_before_output(self):
        transport = FakeTransport()
        def bad_clear():
            raise RuntimeError("PRIVATE CLEANUP SECRET")
        transport.clear = bad_clear
        code, output, error = self.run_fake(transport, lambda: ("synthetic@example.invalid", "SYNTHETIC"))
        self.assertNotIn("PRIVATE", output + error)
        self.assertEqual(code, 1)

    def test_signal_handlers_raise_private_cancellation_and_alarm_is_bounded(self):
        transport, registered = FakeTransport(), {}
        def register(number, handler):
            registered[number] = handler
        with patch("sys.stdin", io.StringIO("")), patch("signal.signal", side_effect=register), \
                patch("signal.alarm") as alarm:
            def prompt():
                with self.assertRaises(_Cancelled):
                    registered[signal.SIGTERM](signal.SIGTERM, None)
                return "synthetic@example.invalid", "SYNTHETIC"
            self.assertEqual(run_worker(plan(), credential_prompt=prompt,
                                        transport_factory=lambda _: transport), 0)
        self.assertEqual(alarm.call_args_list[0].args, (600,))
        self.assertEqual(alarm.call_args_list[-1].args, (0,))

    def test_repeated_cancellation_during_cleanup_cannot_escape_or_skip_finally(self):
        transport, registered, cleared = FakeTransport(), {}, []
        def register(number, handler):
            registered[number] = handler
        def interrupted_clear():
            cleared.append(True)
            registered[signal.SIGTERM](signal.SIGTERM, None)
        transport.clear = interrupted_clear
        with patch("sys.stdin", io.StringIO("")), patch("sys.stderr", new_callable=io.StringIO) as error, \
                patch("signal.signal", side_effect=register), patch("signal.alarm") as alarm:
            self.assertEqual(run_worker(plan(), credential_prompt=lambda: ("synthetic@example.invalid", "SYNTHETIC"),
                                        transport_factory=lambda _: transport), 1)
        self.assertGreaterEqual(len(cleared), 2)
        self.assertEqual(error.getvalue(), "e2e_worker_failed\n")
        self.assertEqual(alarm.call_args_list[-1].args, (0,))
