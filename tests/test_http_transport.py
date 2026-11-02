"""Production HTTPS transport tests using in-memory HTTPResponse sockets only."""

import http.client
from dataclasses import FrozenInstanceError, fields
import io
import json
import os
import ssl
import unittest
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import patch

from timetree_mcp.http_transport import (
    CSRF_PATH, DEFAULT_ORIGIN, DirectHttpsTransport, HttpResponse,
    ResponseBudget, TimeTreeHttpError, TransportError, failure_reason,
    require_direct_connection,
)


def wire(body=b"{}", *, status="200 OK", headers=(), length=None, chunked=False):
    lines = ["HTTP/1.1 " + status, *[f"{key}: {value}" for key, value in headers]]
    if chunked:
        lines.append("Transfer-Encoding: chunked")
        body = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    else:
        lines.append(f"Content-Length: {len(body) if length is None else length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class MemorySocket:
    def __init__(self, data):
        self.data = data

    def makefile(self, mode):
        assert mode == "rb"
        return io.BytesIO(self.data)


class MemoryConnection:
    def __init__(self, data):
        self.data = data
        self.requests = []
        self.response = None
        self.reads = []
        self.closed = False
        self.close_error = None

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        self.response = http.client.HTTPResponse(MemorySocket(self.data),
                                                 method=self.requests[-1][0])
        self.response.begin()
        original = self.response.read

        def read(size=None):
            self.reads.append(size)
            return original(size)

        self.response.read = read
        return self.response

    def close(self):
        self.closed = True
        if self.response is not None:
            self.response.close()
        if self.close_error:
            raise self.close_error


class MemoryConnections:
    def __init__(self, replies):
        self.replies = list(replies)
        self.connections = []
        self.arguments = []

    def __call__(self, host, *, timeout, context):
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        self.arguments.append((host, timeout))
        connection = MemoryConnection(self.replies.pop(0))
        self.connections.append(connection)
        return connection


def cookies(headers):
    jar = SimpleCookie()
    for key, value in headers.items():
        if key.lower() == "cookie":
            jar.load(value)
    return {key: value.value for key, value in jar.items()}


class TransportTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "subprocess.run"):
            guard = patch(target, side_effect=AssertionError("offline_test_external_io_denied"))
            guard.start()
            self.addCleanup(guard.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def transport(self, replies, **kwargs):
        factory = MemoryConnections(replies)
        return DirectHttpsTransport(connection_factory=factory, proxy_check=lambda: None,
                                    **kwargs), factory

    def test_shared_types_and_safe_error_fields(self):
        self.assertEqual(HttpResponse(200, {}, {}).status, 200)
        error = TransportError("http_401", status=401)
        self.assertIsInstance(error, TimeTreeHttpError)
        self.assertEqual((error.code, error.status), ("http_401", 401))
        private = "SYNTHETIC_PRIVATE_EXCEPTION"
        malformed = TransportError(private, status=private, redirect_target=private)
        self.assertEqual(malformed.code, "internal_error")
        self.assertNotIn(private, str(malformed))
        self.assertIsNone(malformed.status)
        self.assertIsNone(malformed.redirect_target)
        self.assertEqual(failure_reason(TimeTreeHttpError(private, 429)), "http_429")
        self.assertEqual(failure_reason(RuntimeError(private)), "internal_error")

    def test_preflight_is_lazy_cached_and_failure_makes_no_connection(self):
        calls = []
        factory = MemoryConnections([wire(), wire()])
        transport = DirectHttpsTransport(connection_factory=factory,
                                        proxy_check=lambda: calls.append("check"))
        self.assertEqual(calls, [])
        transport.preflight()
        transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars?since=0")
        transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars?since=0")
        self.assertEqual(calls, ["check"])
        with patch("timetree_mcp.http_transport.require_direct_connection",
                   side_effect=TransportError("proxy_unsupported")):
            blocked = DirectHttpsTransport(connection_factory=factory)
            with self.assertRaisesRegex(TransportError, "proxy_unsupported"):
                blocked.request("GET", DEFAULT_ORIGIN + "/signin")
        self.assertEqual(len(factory.connections), 2)

    def test_production_headers_cookie_scope_and_clear_allow_new_session(self):
        replies = [
            wire(headers=(("Set-Cookie", "session=SYNTHETIC; Secure; Path=/"),
                          ("Set-Cookie", "foreign=PRIVATE; Domain=other.invalid; Path=/"),
                          ("Set-Cookie", "api=API_ONLY; Secure; Path=/api"))),
            wire(b'<meta name="csrf-token" content="SYNTHETIC-TOKEN">'),
            wire(status="201 Created"), wire(),
        ]
        transport, factory = self.transport(replies)
        auth_body = {"uid": "test@example.invalid", "password": "SYNTHETIC", "uuid": "0" * 32}
        transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", auth_body)
        csrf = transport.request("GET", DEFAULT_ORIGIN + CSRF_PATH)
        self.assertIsInstance(csrf.body, str)
        transport.request("POST", DEFAULT_ORIGIN + "/api/v1/calendar/42/event", {},
                          {"X-CSRF-Token": "SYNTHETIC-TOKEN"})
        page = factory.connections[1].requests[0]
        write = factory.connections[2].requests[0]
        self.assertEqual(cookies(page[3]), {"session": "SYNTHETIC"})
        self.assertEqual(cookies(write[3]), {"session": "SYNTHETIC", "api": "API_ONLY"})
        lower = {key.lower(): value for key, value in write[3].items()}
        self.assertEqual(lower["x-csrf-token"], "SYNTHETIC-TOKEN")
        self.assertEqual(lower["referer"], DEFAULT_ORIGIN + "/")
        self.assertEqual(lower["user-agent"], "timetree-mcp/0.2")
        transport.clear()
        transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", auth_body)
        self.assertEqual(cookies(factory.connections[3].requests[0][3]), {})
        self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_auth_and_redirect_failures_never_read_body_or_accept_cookie(self):
        for method, path, status, target in (
            ("PUT", "/api/v1/auth/email/signin", "401 Unauthorized", None),
            ("GET", "/signin", "403 Forbidden", None),
            ("GET", "/signin", "302 Found", "public_landing"),
            ("GET", "/api/v1/calendars", "302 Found", None),
        ):
            with self.subTest(method=method, status=status):
                transport, factory = self.transport([wire(b"SYNTHETIC_PRIVATE_BODY", status=status,
                    headers=(("Set-Cookie", "session=PRIVATE; Path=/"),
                             ("Location", DEFAULT_ORIGIN + "/intl/en/")))])
                with self.assertRaises(TransportError) as caught:
                    transport.request(method, DEFAULT_ORIGIN + path,
                                      {} if method == "PUT" else None)
                self.assertEqual(caught.exception.redirect_target, target)
                self.assertEqual(factory.connections[0].reads, [])
                self.assertEqual(len(list(transport.cookie_jar)), 0)
                self.assertNotIn("PRIVATE", str(caught.exception))

    def test_csrf_calendar_redirect_returns_empty_marker_without_follow_or_session_changes(self):
        from timetree_mcp import http_transport
        for status in (301, 302, 303, 307, 308):
            for target in ("/calendars", DEFAULT_ORIGIN + "/calendars"):
                with self.subTest(status=status, target_kind="absolute" if target.startswith("https") else "relative"):
                    transport, factory = self.transport([
                        wire(headers=(("Set-Cookie", "session=ORIGINAL_SYNTHETIC; Secure; Path=/"),)),
                        wire(b"PRIVATE_REDIRECT_BODY", status=f"{status} Redirect", headers=(
                            ("Location", target), ("Set-Cookie", "session=PRIVATE_ROTATION; Path=/"),
                            ("X-CSRF-Token", "PRIVATE_REDIRECT_TOKEN"))),
                        wire(b'<meta name="csrf-token" content="FINAL_SYNTHETIC_TOKEN">'),
                    ])
                    transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
                    budget = ResponseBudget(500)
                    marker = transport.request("GET", DEFAULT_ORIGIN + "/signin", budget=budget)
                    self.assertIsInstance(marker, http_transport.CsrfCalendarRedirect)
                    self.assertEqual(fields(marker), ())
                    self.assertNotIn("PRIVATE", repr(marker))
                    with self.assertRaises(FrozenInstanceError):
                        marker.target = "arbitrary"
                    self.assertEqual(len(factory.connections), 2)
                    self.assertEqual(len(factory.replies), 1)
                    self.assertEqual(factory.connections[1].reads, [])
                    self.assertTrue(factory.connections[1].closed)
                    self.assertEqual(budget.remaining_bytes, 500)
                    final = transport.request("GET", DEFAULT_ORIGIN + http_transport.CSRF_CALENDAR_PATH,
                                              budget=budget)
                    self.assertIsInstance(final, HttpResponse)
                    self.assertIsInstance(final.body, str)
                    self.assertEqual(cookies(factory.connections[2].requests[0][3]),
                                     {"session": "ORIGINAL_SYNTHETIC"})

    def test_csrf_calendar_marker_requires_exact_single_location_source_and_status(self):
        targets = ["/calendars/", "/calendars?", "/calendars#", "/%63alendars",
                   "//timetreeapp.com/calendars", "https://other.invalid/calendars",
                   "https://timetreeapp.com:443/calendars", "https://user@timetreeapp.com/calendars",
                   DEFAULT_ORIGIN + "/calendars?token=SYNTHETIC_PRIVATE", "/signin"]
        for target in targets:
            with self.subTest(target=target):
                transport, factory = self.transport([wire(status="302 Found", headers=(("Location", target),))])
                with self.assertRaisesRegex(TransportError, "http_redirect_rejected"):
                    transport.request("GET", DEFAULT_ORIGIN + "/signin")
                self.assertEqual(factory.connections[0].reads, [])
        for status in (300, 304, 305, 306, 309, 399):
            with self.subTest(status=status):
                transport, _ = self.transport([wire(status=f"{status} Redirect", headers=(("Location", "/calendars"),))])
                with self.assertRaisesRegex(TransportError, "http_redirect_rejected"):
                    transport.request("GET", DEFAULT_ORIGIN + "/signin")
        for headers in ((), (("Location", "/calendars"), ("Location", "/calendars"))):
            transport, _ = self.transport([wire(status="302 Found", headers=headers)])
            with self.assertRaisesRegex(TransportError, "http_redirect_rejected"):
                transport.request("GET", DEFAULT_ORIGIN + "/signin")
        for method, path in (("PUT", "/api/v1/auth/email/signin"), ("HEAD", "/signin"),
                             ("GET", "/signin?"), ("GET", "/api/v1/calendars"),
                             ("GET", "/calendars")):
            with self.subTest(method=method, path=path):
                transport, factory = self.transport([wire(status="302 Found", headers=(("Location", "/calendars"),))])
                with self.assertRaisesRegex(TransportError, "http_redirect_rejected"):
                    transport.request(method, DEFAULT_ORIGIN + path, {} if method == "PUT" else None)
                self.assertEqual(len(factory.connections), 1)

    def test_calendar_followup_errors_and_second_redirect_clear_session_without_body_read(self):
        for status, target, expected in (("302 Found", "/signin", "signin"),
                                         ("302 Found", "/calendars", "calendars"),
                                         ("401 Unauthorized", None, None),
                                         ("500 Server Error", None, None)):
            transport, factory = self.transport([
                wire(headers=(("Set-Cookie", "session=SYNTHETIC; Path=/"),)),
                wire(status="302 Found", headers=(("Location", "/calendars"),)),
                wire(b"PRIVATE_FINAL_BODY", status=status,
                     headers=(() if target is None else (("Location", target),))),
            ])
            transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
            transport.request("GET", DEFAULT_ORIGIN + "/signin")
            with self.assertRaises(TransportError) as caught:
                transport.request("GET", DEFAULT_ORIGIN + "/calendars")
            self.assertEqual(caught.exception.redirect_target, expected)
            self.assertEqual(factory.connections[2].reads, [])
            self.assertEqual(len(list(transport.cookie_jar)), 0)
            self.assertEqual(len(factory.connections), 3)

    def test_marker_path_still_enforces_header_bound_and_close_failure_cleanup(self):
        transport, factory = self.transport([wire(status="302 Found", headers=(
            ("Location", "/calendars"), ("X-A", "x" * 40_000), ("X-B", "x" * 40_000)))])
        with self.assertRaisesRegex(TransportError, "response_limit"):
            transport.request("GET", DEFAULT_ORIGIN + "/signin")
        self.assertEqual(factory.connections[0].reads, [])
        transport, factory = self.transport([
            wire(headers=(("Set-Cookie", "session=SYNTHETIC; Path=/"),)),
            wire(status="302 Found", headers=(("Location", "/calendars"),)),
        ])
        transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
        original = factory.__call__
        def bad_close_factory(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.close_error = RuntimeError("PRIVATE_CLOSE_FAILURE")
            return connection
        transport._connection_factory = bad_close_factory
        with self.assertRaisesRegex(TransportError, "internal_error"):
            transport.request("GET", DEFAULT_ORIGIN + "/signin")
        self.assertEqual(factory.connections[1].reads, [])
        self.assertEqual(len(list(transport.cookie_jar)), 0)

    def test_general_api_error_payload_and_empty_delete_are_preserved(self):
        transport, _ = self.transport([
            wire(b'{"error":{"code":-425}}', status="400 Bad Request",
                 headers=(("Set-Cookie", "bad=PRIVATE; Path=/"),)),
            wire(b"", status="204 No Content"), wire(b"", status="205 Reset Content"),
        ])
        response = transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendar/42/events/sync?since=0")
        self.assertEqual((response.status, response.body), (400, {"error": {"code": -425}}))
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        for status in (204, 205):
            self.assertEqual(transport.request("DELETE", DEFAULT_ORIGIN + "/api/v1/event/42").status, status)

    def test_general_success_keeps_empty_and_text_but_reader_rejects_noncalendar_text(self):
        transport, _ = self.transport([wire(b""), wire(b"OK"), wire(b"not a calendar index")])
        for expected in ("", "OK"):
            response = transport.request("DELETE", DEFAULT_ORIGIN + "/api/v1/event/42")
            self.assertEqual((response.status, response.body), (200, expected))
        from timetree_mcp.http_reader import TimeTreeHttpReader
        reader = TimeTreeHttpReader(email="test@example.invalid", password="SYNTHETIC",
                                    transport=transport, credential_store=object())
        with self.assertRaises(ValueError):
            reader._get_calendars()

    def test_raw_budget_limits_read_before_decoding_and_exhaustion_prevents_request(self):
        transport, factory = self.transport([wire(b"{}"), wire(b"{}")])
        budget = ResponseBudget(2)
        transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars", budget=budget)
        self.assertEqual(budget.remaining_bytes, 0)
        self.assertEqual(factory.connections[0].reads, [3])
        with self.assertRaisesRegex(TransportError, "session_limit"):
            transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars", budget=budget)
        self.assertEqual(len(factory.connections), 1)
        transport, factory = self.transport([wire(b'{"larger":true}')])
        budget = ResponseBudget(4)
        with self.assertRaisesRegex(TransportError, "response_limit"):
            transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars", budget=budget)
        self.assertEqual(factory.connections[0].reads, [5])
        self.assertLessEqual(budget.remaining_bytes, 0)

    def test_truncation_chunk_errors_utf8_duplicate_json_and_auth_shape_fail_closed(self):
        invalid = [wire(b"{}", length=8),
                   b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
                   wire(b"\xff"), wire(b'{"x":1,"x":2}'), wire(b'{"x":NaN}'),
                   wire(b'{"x":1e10000}'), wire(b"[]"), wire(b""),
                   wire(b'{"error":{"code":1}}')]
        for index, raw in enumerate(invalid):
            with self.subTest(case=index):
                transport, factory = self.transport([raw])
                with self.assertRaisesRegex(TransportError, "invalid_response"):
                    transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
                self.assertEqual(len(list(transport.cookie_jar)), 0)
                self.assertTrue(factory.connections[0].closed)

    def test_wrong_origin_or_unsafe_headers_are_rejected_before_connection(self):
        transport, factory = self.transport([])
        for target in ("http://timetreeapp.com/signin", "https://other.invalid/signin",
                       "https://user@timetreeapp.com/signin", DEFAULT_ORIGIN + "/signin#x",
                       DEFAULT_ORIGIN + "\\signin", DEFAULT_ORIGIN + ":443/signin"):
            with self.subTest(target=target), self.assertRaisesRegex(TransportError, "request_denied"):
                transport.request("GET", target)
        for headers in ({"Host": "other.invalid"}, {"Cookie": "PRIVATE"},
                        {"Authorization": "PRIVATE"}, {"X-CSRF-Token": "x\r\ny"},
                        {"Referer": "https://other.invalid/"}):
            with self.subTest(header=next(iter(headers))), self.assertRaises(TransportError):
                transport.request("POST", DEFAULT_ORIGIN + "/api/v1/event", {}, headers)
        self.assertEqual(factory.connections, [])

    def test_valid_developer_origin_is_exact_and_uses_own_referer(self):
        transport, factory = self.transport([wire()], origin="https://fixture.invalid:8443",
                                            user_agent="synthetic-agent/1")
        transport.request("GET", "https://fixture.invalid:8443/api/v1/calendars")
        self.assertEqual(factory.arguments[0][0], "fixture.invalid:8443")
        headers = {k.lower(): v for k, v in factory.connections[0].requests[0][3].items()}
        self.assertEqual(headers["referer"], "https://fixture.invalid:8443/")
        self.assertEqual(headers["user-agent"], "synthetic-agent/1")
        for origin in ("http://fixture.invalid", "https://u:p@fixture.invalid",
                       "https://fixture.invalid/path", "https://fixture.invalid?x=1",
                       "https://fixture.invalid#x"):
            with self.subTest(origin=origin), self.assertRaises(TransportError):
                DirectHttpsTransport(origin=origin, proxy_check=lambda: None)

    def test_user_agent_environment_is_validated_and_explicit_argument_takes_priority(self):
        with patch.dict(os.environ, {"TIMETREE_USER_AGENT": "synthetic-environment/1"}):
            transport, factory = self.transport([wire()])
            transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
            headers = {key.lower(): value for key, value in factory.connections[0].requests[0][3].items()}
            self.assertEqual(headers["user-agent"], "synthetic-environment/1")
        for configured in ("", "synthetic\r\nInjected: value", "synthetic\nvalue", "測試"):
            with self.subTest(configured_kind=repr(configured)), \
                    patch.dict(os.environ, {"TIMETREE_USER_AGENT": configured}):
                with self.assertRaisesRegex(TransportError, "request_denied"):
                    DirectHttpsTransport(proxy_check=lambda: None)
                explicit, factory = self.transport([wire()], user_agent="synthetic-explicit/1")
                explicit.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
                headers = {key.lower(): value for key, value in factory.connections[0].requests[0][3].items()}
                self.assertEqual(headers["user-agent"], "synthetic-explicit/1")

    def test_primary_failure_survives_close_and_cookie_cleanup_errors(self):
        transport, factory = self.transport([wire(status="401 Unauthorized")])
        original = factory.__call__
        def bad_close_factory(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.close_error = RuntimeError("PRIVATE_CLOSE_CANARY")
            return connection
        transport._connection_factory = bad_close_factory
        transport.cookie_jar.clear = lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE_COOKIE_CANARY"))
        with self.assertRaises(TransportError) as caught:
            transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
        self.assertEqual(caught.exception.code, "http_401")
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_header_limit_includes_rejected_auth_responses(self):
        transport, factory = self.transport([wire(status="401 Unauthorized", headers=(
            ("X-First", "x" * 40_000), ("X-Second", "x" * 40_000)))])
        with self.assertRaisesRegex(TransportError, "response_limit"):
            transport.request("PUT", DEFAULT_ORIGIN + "/api/v1/auth/email/signin", {})
        self.assertEqual(factory.connections[0].reads, [])

    def test_framing_ambiguity_is_rejected_and_valid_chunked_success_is_supported(self):
        invalid = [wire(headers=(("Content-Length", "2"),)),
                   wire(chunked=True, headers=(("Content-Length", "2"),)),
                   wire(headers=(("Content-Encoding", "gzip"),)),
                   wire(headers=(("X-CSRF-Token", "a"), ("x-csrf-token", "b")))]
        for raw in invalid:
            transport, factory = self.transport([raw])
            with self.assertRaisesRegex(TransportError, "invalid_response"):
                transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
            self.assertEqual(factory.connections[0].reads, [])
        transport, _ = self.transport([wire(b'{"value":1}', chunked=True)])
        self.assertEqual(transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars").body, {"value": 1})

    def test_cookie_count_limit_is_aggregate_and_failure_discards_session(self):
        replies = [wire(headers=tuple(("Set-Cookie", f"cookie{i}=synthetic; Path=/")
                                      for i in range(start, start + 65))) for start in (0, 65)]
        transport, _ = self.transport(replies)
        transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
        self.assertEqual(len(list(transport.cookie_jar)), 65)
        with self.assertRaisesRegex(TransportError, "response_limit"):
            transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
        self.assertEqual(len(list(transport.cookie_jar)), 0)

    def test_interrupted_read_exhausts_unknown_budget_and_preserves_interrupt(self):
        transport, factory = self.transport([wire(headers=(("Set-Cookie", "session=synthetic; Path=/"),))])
        original = factory.__call__
        def interrupted_factory(*args, **kwargs):
            connection = original(*args, **kwargs)
            original_response = connection.getresponse
            def interrupted_response():
                response = original_response()
                response.read = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
                return response
            connection.getresponse = interrupted_response
            connection.close_error = RuntimeError("PRIVATE_CLOSE_CANARY")
            return connection
        transport._connection_factory = interrupted_factory
        budget = ResponseBudget(200)
        with self.assertRaises(KeyboardInterrupt):
            transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars", budget=budget)
        self.assertEqual(budget.remaining_bytes, 0)
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertTrue(factory.connections[0].closed)

    def test_factory_is_selected_at_request_time_and_close_failure_is_sanitized(self):
        transport = DirectHttpsTransport(proxy_check=lambda: None)
        factory = MemoryConnections([wire()])
        def bad_close_factory(*args, **kwargs):
            connection = factory(*args, **kwargs)
            connection.close_error = TimeoutError("PRIVATE_TIMEOUT_CANARY")
            return connection
        with patch("timetree_mcp.http_transport.http.client.HTTPSConnection", bad_close_factory):
            with self.assertRaises(TransportError) as caught:
                transport.request("GET", DEFAULT_ORIGIN + "/api/v1/calendars")
        self.assertEqual(caught.exception.code, "network_timeout")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(len(factory.connections), 1)

    def test_malformed_mutable_error_code_cannot_escape_safe_classifier(self):
        error = TransportError("invalid_response")
        error.code = ["SYNTHETIC_PRIVATE"]
        self.assertEqual(failure_reason(error), "internal_error")

    def test_proxy_policy_rejects_active_or_unknown_and_keeps_mandatory_system_guard(self):
        calls = []
        require_direct_connection(environment={}, proxy_lookup=lambda: {"no": "localhost"},
                                  system_proxy_check=lambda: calls.append("system"))
        self.assertEqual(calls, ["system"])
        for environment, proxies in (({"HTTPS_PROXY": "PRIVATE"}, {}), ({"no_proxy": "x"}, {}),
                                     ({}, {"https": "PRIVATE"}), ({}, {"no": ""}), ({}, [])):
            with self.subTest(environment=bool(environment), kind=type(proxies).__name__), \
                    self.assertRaises(TransportError) as caught:
                require_direct_connection(environment=environment, proxy_lookup=lambda: proxies,
                                          system_proxy_check=lambda: None)
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_native_proxy_parser_rejects_pac_and_unknown_without_output(self):
        from timetree_mcp import http_transport
        for output, expected in (("<dictionary> {\n HTTPEnable : 0\n}", None),
                                 ("<dictionary> {\n ProxyAutoConfigEnable : 1\n}", "proxy_unsupported"),
                                 ("<dictionary> {\n ProxyAutoConfigURLString : PRIVATE\n}", "proxy_unsupported"),
                                 ("PRIVATE", "proxy_state_unknown")):
            with patch.object(http_transport.sys, "platform", "darwin"), \
                    patch.object(http_transport.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=output)) as run:
                if expected:
                    with self.assertRaisesRegex(TransportError, expected):
                        http_transport._system_proxy_check()
                else:
                    http_transport._system_proxy_check()
                self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})


if __name__ == "__main__":
    unittest.main()
