"""Use real HTTPResponse parsing over BytesIO sockets, never a network.

This checks the adapter boundary hidden by the simpler FakeResponse tests.
All credential/cookie values are explicitly synthetic and remain in memory.
"""

from __future__ import annotations

import http.client
from dataclasses import fields
from http.cookies import SimpleCookie
import io
import json
import os
import ssl
import unittest
from unittest.mock import patch

from timetree_mcp.e2e_worker import ORIGIN, ScopedReader, ScopedTransport, WorkerError
from timetree_mcp import http_transport
from timetree_mcp.http_reader import TimeTreeHttpReader, UrllibTransport

EMAIL = "http-response-fixture@example.invalid"
PASSWORD = "SYNTHETIC_HTTP_RESPONSE_PASSWORD_502e"
SESSION = "SYNTHETIC_HTTP_RESPONSE_SESSION_91b2"
CSRF = "SYNTHETIC_HTTP_RESPONSE_CSRF_733a"
PLAN = {"version": 1, "calendar_id": "42", "calendar_name": "EWS",
        "records": [{"source_uuid": "synthetic-series", "title": "TIMETREE-MCP-TEST HTTP"}],
        "checks": [{"label": "http", "arguments": {
            "calendar_id": "42", "start_at": "2030-01-01T00:00:00+00:00",
            "end_at": "2030-01-02T00:00:00+00:00", "limit": 100}, "expected": []}]}


def wire(body=b"{}", *, status="200 OK", headers=(), declared_length=None, chunked=False):
    lines = ["HTTP/1.1 " + status, *[f"{name}: {value}" for name, value in headers]]
    if chunked:
        lines.append("Transfer-Encoding: chunked")
        body = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    else:
        lines.append(f"Content-Length: {len(body) if declared_length is None else declared_length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class MemorySocket:
    def __init__(self, data):
        self.data = data

    def makefile(self, mode):
        if mode != "rb":
            raise AssertionError("fixture_socket_mode_invalid")
        return io.BytesIO(self.data)


def response_from_wire(data, method="GET"):
    response = http.client.HTTPResponse(MemorySocket(data), method=method)
    response.begin()
    return response


class MemoryConnection:
    def __init__(self, data):
        self.data, self.requests = data, []
        self.response = None
        self.response_reads = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.requests.append({"method": method, "path": path, "body": body,
                              "headers": headers or {}})

    def getresponse(self):
        self.response = response_from_wire(self.data, self.requests[-1]["method"])
        original_read = self.response.read
        def recorded_read(amount=None):
            self.response_reads.append(amount)
            return original_read(amount)
        self.response.read = recorded_read
        return self.response

    def close(self):
        self.closed = True
        if self.response is not None:
            self.response.close()


class MemoryConnections:
    def __init__(self, responses):
        self.responses = list(responses)
        self.connections = []

    def __call__(self, host, *, timeout, context):
        if (host != "timetreeapp.com" or not context.check_hostname
                or context.verify_mode != ssl.CERT_REQUIRED):
            raise AssertionError("fixture_tls_boundary_invalid")
        connection = MemoryConnection(self.responses.pop(0))
        self.connections.append(connection)
        return connection


class NoCredentials:
    def read(self):
        raise AssertionError("fixture_stored_credentials_forbidden")

    status = read


def cookie_values(headers):
    raw = next((value for key, value in headers.items() if key.lower() == "cookie"), "")
    cookies = SimpleCookie()
    cookies.load(raw)
    return {name: morsel.value for name, morsel in cookies.items()}


class RealHttpResponseTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "subprocess.run"):
            guard = patch(target, side_effect=AssertionError("fixture_external_io_forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        clean_environment = patch.dict(os.environ, {}, clear=True)
        clean_environment.start()
        self.addCleanup(clean_environment.stop)

    def scoped(self, responses, *, max_response_bytes=None):
        factory = MemoryConnections(responses)
        limits = {} if max_response_bytes is None else {"max_response_bytes": max_response_bytes}
        transport = ScopedTransport(PLAN, proxy_check=lambda: None, connection_factory=factory, **limits)
        return transport, factory

    def ordinary(self, responses):
        factory = MemoryConnections(responses)
        transport = UrllibTransport(proxy_check=lambda: None, connection_factory=factory)
        return transport, factory

    def test_cookie_and_csrf_round_trip_matches_public_core_with_real_response_objects(self):
        replies = [
            wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; HttpOnly; Path=/"),
                          ("Set-Cookie", "preference=synthetic; Path=/"))),
            wire(b"<html></html>", headers=(("x-CsRf-ToKeN", CSRF),)),
            wire(b'{"calendars":[]}'),
        ]
        scoped, factory = self.scoped(replies)
        ordinary, public_factory = self.ordinary(replies)
        outputs = []
        for transport, reader in ((scoped, ScopedReader(PLAN, EMAIL, PASSWORD, scoped)),
                                  (ordinary, TimeTreeHttpReader(email=EMAIL, password=PASSWORD,
                                      transport=ordinary, credential_store=NoCredentials()))):
            outputs.append(reader.login())
            transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
            self.assertEqual(len(list(transport.cookie_jar)), 2)
        expected = {"session": SESSION, "preference": "synthetic"}
        for index in (1, 2):
            scoped_cookies = cookie_values(factory.connections[index].requests[0]["headers"])
            public_cookies = cookie_values(public_factory.connections[index].requests[0]["headers"])
            self.assertTrue(scoped_cookies == public_cookies == expected,
                            "Synthetic cookie state differed between transports")
        self.assertTrue(all(item["authenticated"] and item["csrf_available"] for item in outputs))
        for sentinel in (EMAIL, PASSWORD, SESSION, CSRF):
            self.assertFalse(sentinel in json.dumps(outputs), "Login result exposed a synthetic secret")
        self.assertEqual([item.requests[0]["path"] for item in factory.connections],
                         ["/api/v1/auth/email/signin", "/signin", "/api/v1/calendars?since=0"])
        self.assertEqual([item.requests[0]["path"] for item in public_factory.connections],
                         ["/api/v1/auth/email/signin", "/signin", "/api/v1/calendars?since=0"])
        self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_chunked_json_and_html_csrf_fallback_work_with_real_http_parser(self):
        replies = [wire(chunked=True, headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                   wire(f'<meta name="csrf-token" content="{CSRF}">'.encode(), chunked=True)]
        transport, factory = self.scoped(replies)
        reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
        self.assertTrue(reader.login()["csrf_available"])
        self.assertEqual(len(list(transport.cookie_jar)), 1)
        self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_cookie_domain_and_path_rules_match_public_core(self):
        replies = [wire(headers=(("Set-Cookie", "scoped=" + SESSION + "; Secure; Path=/api"),
                                 ("Set-Cookie", "foreign=synthetic; Domain=other.invalid; Path=/"))),
                   wire(b"<html></html>", headers=(("X-CSRF-Token", CSRF),)),
                   wire(b'{"calendars":[]}')]
        scoped, factory = self.scoped(replies)
        ordinary, public_factory = self.ordinary(replies)
        body = {"uid": EMAIL, "password": PASSWORD, "uuid": "0" * 32}
        for transport in (scoped, ordinary):
            transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin", body)
            transport.request("GET", ORIGIN + "/signin")
            transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
            self.assertEqual(len(list(transport.cookie_jar)), 1)
        self.assertEqual(cookie_values(factory.connections[1].requests[0]["headers"]), {})
        self.assertEqual(cookie_values(public_factory.connections[1].requests[0]["headers"]), {})
        self.assertTrue(cookie_values(factory.connections[2].requests[0]["headers"])
                        == cookie_values(public_factory.connections[2].requests[0]["headers"]) == {"scoped": SESSION})

    def test_fixed_signin_csrf_path_uses_one_put_then_one_bodyless_get(self):
        transport, factory = self.scoped([
            wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
            wire(f'<html><meta name="csrf-token" content="{CSRF}"></html>'.encode()),
            wire(b"{}"),  # Neither a root fallback nor another request is needed.
        ])
        reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
        self.assertEqual(reader.login(), {"authenticated": True, "csrf_available": True})
        requests = [connection.requests[0] for connection in factory.connections]
        self.assertEqual([(request["method"], request["path"]) for request in requests],
                         [("PUT", "/api/v1/auth/email/signin"), ("GET", "/signin")])
        self.assertIsNone(requests[1]["body"])
        self.assertFalse(any(name.lower() in ("authorization", "x-csrf-token")
                             for name in requests[1]["headers"]))
        self.assertFalse(any(value in json.dumps(requests[1]) for value in (EMAIL, PASSWORD, CSRF)),
                         "CSRF fetch exposed login credentials or a token")
        self.assertTrue(cookie_values(requests[1]["headers"]) == {"session": SESSION})
        self.assertEqual(len(factory.responses), 1)
        self.assertTrue(all(connection.closed for connection in factory.connections))
        self.assertEqual(reader.logout(), {"authenticated": False})
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertIsNone(reader.email)
        self.assertIsNone(reader.password)
        self.assertIsNone(reader._csrf_token)

    def test_csrf_page_allowlist_denies_root_and_all_nonexact_signin_variants(self):
        variants = [
            ORIGIN, ORIGIN + "/", ORIGIN + "/signin/", ORIGIN + "/signin?",
            ORIGIN + "/signin#", ORIGIN + "/signin?token=" + SESSION,
            ORIGIN + "/signin#" + SESSION, ORIGIN + "/%73ignin", ORIGIN + "/signin%2f",
            ORIGIN + "/signin/../signin", ORIGIN + "//signin", ORIGIN + r"\signin",
            "http://timetreeapp.com/signin", "https://TIMETREEAPP.COM/signin",
            "https://timetreeapp.com:443/signin", "https://timetreeapp.com:8443/signin",
            "https://person@timetreeapp.com/signin", "https://timetreeapp.com./signin",
            "https://other.invalid/signin", "//timetreeapp.com/signin", "/signin",
        ]
        for index, target in enumerate(variants):
            with self.subTest(case=index):
                transport, factory = self.scoped([wire(b"<html></html>")])
                with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                    transport.request("GET", target)
                self.assertEqual(len(factory.connections), 0)
                self.assertEqual(len(factory.responses), 1)
                self.assertIsNone(transport.csrf_redirect_target)
        for method, body in (("GET", {}), ("POST", None), ("PUT", None), ("HEAD", None)):
            with self.subTest(method=method):
                transport, factory = self.scoped([wire(b"<html></html>")])
                with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                    transport.request(method, ORIGIN + "/signin", body)
                self.assertEqual(len(factory.connections), 0)

    def test_signin_page_cookie_path_scope_is_preserved_without_copying_to_api(self):
        transport, factory = self.scoped([
            wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),
                          ("Set-Cookie", "api=synthetic-api; Secure; Path=/api"),
                          ("Set-Cookie", "page=synthetic-page; Secure; Path=/signin"),
                          ("Set-Cookie", "foreign=synthetic; Domain=other.invalid; Path=/"))),
            wire(f'<meta name="csrf-token" content="{CSRF}">'.encode(),
                 headers=(("Set-Cookie", "page=synthetic-refreshed; Secure; Path=/signin"),)),
            wire(b'{"calendars":[]}'),
        ])
        reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
        self.assertTrue(reader.login()["csrf_available"])
        transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
        page_cookies = cookie_values(factory.connections[1].requests[0]["headers"])
        api_cookies = cookie_values(factory.connections[2].requests[0]["headers"])
        self.assertTrue(page_cookies == {"session": SESSION, "page": "synthetic-page"},
                        "CSRF page cookie scope changed")
        self.assertTrue(api_cookies == {"session": SESSION, "api": "synthetic-api"},
                        "API received a cookie outside its path scope")
        self.assertEqual(len(list(transport.cookie_jar)), 3)
        reader.logout()
        self.assertEqual(len(list(transport.cookie_jar)), 0)

    def test_missing_signin_csrf_token_fails_without_root_fallback_or_cookie_retention(self):
        for index, (status, html) in enumerate((("200 OK", b"<html></html>"),
                                               ("204 No Content", b""),
                                               ("200 OK", b'<meta name="csrf-token" content="">'))):
            with self.subTest(case=index):
                transport, factory = self.scoped([
                    wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                    wire(html, status=status), wire(b"{}"),
                ])
                reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
                with self.assertRaisesRegex(WorkerError, "^e2e_operation_failed$"):
                    reader.login()
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": "csrf_missing"})
                self.assertEqual([item.requests[0]["path"] for item in factory.connections],
                                 ["/api/v1/auth/email/signin", "/signin"])
                self.assertEqual(len(factory.responses), 1)
                self.assertEqual(len(list(transport.cookie_jar)), 0)
                self.assertIsNone(reader.email)
                self.assertIsNone(reader.password)
                self.assertIsNone(reader._csrf_token)
                self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_invalid_signin_html_keeps_bounded_read_and_cleanup(self):
        html = f'<meta name="csrf-token" content="{CSRF}">'.encode()
        cases = [
            (wire(html, declared_length=len(html) + 1), "invalid_response", None),
            (wire(html, headers=(("Content-Encoding", "gzip"),)), "invalid_response", None),
            (wire(b"\xff"), "invalid_response", None),
            (wire(html), "response_limit", 16),
        ]
        for index, (reply, reason, bound) in enumerate(cases):
            with self.subTest(case=index):
                transport, factory = self.scoped([
                    wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                    reply, wire(b"{}"),
                ], max_response_bytes=bound)
                reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
                with self.assertRaisesRegex(WorkerError, "^e2e_operation_failed$"):
                    reader.login()
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": reason})
                self.assertEqual([item.requests[0]["path"] for item in factory.connections],
                                 ["/api/v1/auth/email/signin", "/signin"])
                self.assertEqual(len(factory.responses), 1)
                self.assertEqual(len(list(transport.cookie_jar)), 0)
                self.assertIsNone(reader.email)
                self.assertIsNone(reader.password)
                self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_truncated_content_length_rejected_before_cookies_are_accepted(self):
        truncated = wire(b"{}", declared_length=10,
                         headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),))
        response = response_from_wire(truncated)
        with self.assertRaises(http.client.IncompleteRead):
            response.read()  # Unlimited read detects this truncated body too.
        transport, factory = self.scoped([truncated])
        with self.assertRaisesRegex(WorkerError, "^e2e_invalid_response$"):
            transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin",
                              {"uid": EMAIL, "password": PASSWORD, "uuid": "0" * 32})
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertTrue(factory.connections[0].closed)

    def test_malformed_chunked_body_rejected_without_cookie_or_provider_text(self):
        malformed = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                     + ("Set-Cookie: session=" + SESSION + "; Secure; Path=/\r\n\r\n").encode()
                     + b"a\r\n{}")
        transport, factory = self.scoped([malformed])
        with self.assertRaisesRegex(WorkerError, "^e2e_invalid_response$"):
            transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertTrue(factory.connections[0].closed)

    def test_strict_signin_body_matrix_matches_public_authentication(self):
        # A scoped success must have the same production authentication meaning.
        for status, body in (("200 OK", b""), ("204 No Content", b""),
                             ("200 OK", b"synthetic plaintext"), ("200 OK", b"[]")):
            with self.subTest(status=status, shape=len(body)):
                replies = [wire(body, status=status,
                                headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                           wire(b"<html></html>", headers=(("X-CSRF-Token", CSRF),))]
                ordinary, _ = self.ordinary(replies)
                ordinary_reader = TimeTreeHttpReader(email=EMAIL, password=PASSWORD,
                    transport=ordinary, credential_store=NoCredentials())
                with self.assertRaises(Exception):
                    ordinary_reader.login()
                self.assertEqual(ordinary_reader.login_diagnostic,
                                 {"phase": "sign_in", "reason": "invalid_response"})
                self.assertFalse(ordinary_reader._authenticated)
                self.assertIsNone(ordinary_reader._csrf_token)
                self.assertEqual(len(list(ordinary.cookie_jar)), 0)

                scoped, factory = self.scoped(replies)
                scoped_reader = ScopedReader(PLAN, EMAIL, PASSWORD, scoped)
                with self.assertRaisesRegex(WorkerError, "^e2e_operation_failed$"):
                    scoped_reader.login()
                self.assertEqual(scoped_reader.login_diagnostic,
                                 {"phase": "sign_in", "reason": "invalid_response"})
                self.assertEqual(len(factory.connections), 1)
                self.assertIsNone(scoped_reader.email)
                self.assertIsNone(scoped_reader.password)
                self.assertFalse(scoped_reader._authenticated)
                self.assertEqual(len(list(scoped.cookie_jar)), 0)
                self.assertTrue(factory.connections[0].closed)

    def test_truncated_signin_clears_reader_credentials_and_cookie_state(self):
        truncated = wire(b"{}", declared_length=10,
                         headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),))
        transport, factory = self.scoped([truncated])
        reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
        with self.assertRaisesRegex(WorkerError, "^e2e_operation_failed$"):
            reader.login()
        self.assertEqual(reader.login_diagnostic, {"phase": "sign_in", "reason": "invalid_response"})
        self.assertIsNone(reader.email)
        self.assertIsNone(reader.password)
        self.assertIsNone(reader._csrf_token)
        self.assertFalse(reader._authenticated)
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertTrue(factory.connections[0].closed)

    def rejected_csrf_redirect(self, location_headers, *, status=302):
        replies = [
            wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
            wire((PASSWORD + SESSION).encode(), status=f"{status} Redirect", headers=(
                *location_headers, ("Set-Cookie", "poison=synthetic; Secure; Path=/"))),
            wire(b"{}"),  # Must remain unused: classification never follows.
        ]
        transport, factory = self.scoped(replies)
        reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
        with self.assertRaisesRegex(WorkerError, "^e2e_operation_failed$"):
            reader.login()
        self.assertEqual(len(factory.connections), 2)
        self.assertEqual(len(factory.responses), 1)
        self.assertEqual([connection.requests[0]["path"] for connection in factory.connections],
                         ["/api/v1/auth/email/signin", "/signin"])
        self.assertEqual(factory.connections[1].response_reads, [])
        self.assertTrue(all(connection.closed for connection in factory.connections))
        self.assertEqual(len(list(transport.cookie_jar)), 0)
        self.assertIsNone(reader.email)
        self.assertIsNone(reader.password)
        self.assertFalse(reader._authenticated)
        diagnostic = reader.login_diagnostic
        self.assertEqual(set(diagnostic), {"phase", "reason", "redirect_target"})
        self.assertEqual(diagnostic["phase"], "csrf_fetch")
        self.assertEqual(diagnostic["reason"], "http_redirect_rejected")
        self.assertFalse(any(value in json.dumps(diagnostic) for value in (ORIGIN, EMAIL, PASSWORD, SESSION)),
                         "Redirect diagnostic exposed response or credential data")
        return diagnostic

    def test_redirect_diagnostic_classifies_only_exact_known_literals_without_following(self):
        cases = [
            ("/intl/en/", "public_landing"), ("/intl/en", "public_landing"),
            (ORIGIN + "/intl/en/", "public_landing"), (ORIGIN + "/intl/en", "public_landing"),
            ("/signin", "signin"), (ORIGIN + "/signin", "signin"),
            ("/calendars", "calendars"), (ORIGIN + "/calendars", "calendars"),
        ]
        for index, (target, expected) in enumerate(cases):
            with self.subTest(case=index):
                # Calendar redirects have a deliberately narrower status set.
                status = 300 if expected == "calendars" else 302
                diagnostic = self.rejected_csrf_redirect((("lOcAtIoN", target),), status=status)
                self.assertEqual(diagnostic["redirect_target"], expected)

    def test_redirect_diagnostic_rejects_unsafe_or_normalized_location_variants(self):
        variants = [
            "https://other.invalid/intl/en/", "http://timetreeapp.com/intl/en/",
            "https://timetreeapp.com:443/intl/en/", "https://timetreeapp.com:8443/intl/en/",
            "https://person@timetreeapp.com/intl/en/", "https://TIMETREEAPP.COM/intl/en/",
            "https://timetreeapp.com./intl/en/", "//timetreeapp.com/intl/en/",
            "/intl/en/?", "/intl/en/#", "/intl/en/?token=" + SESSION,
            "/signin?password=" + PASSWORD, "/calendars#" + SESSION,
            "/intl/%65n/", "/intl/en%2f", "/intl/en/%252e", "/intl/en/../en/",
            "/intl//en/", r"/intl\en/", "intl/en/", "/signin/", "/calendars/",
            "/intl/en/ ", "/intl/en/\t", "/intl/en/\x00", "/intl/en/\r\n /signin",
            "/intl/en/, https://other.invalid/", "", "/",
            "/calendars?", "/calendars#", "/calendars?token=" + SESSION,
            "http://timetreeapp.com/calendars", "https://other.invalid/calendars",
            "https://timetreeapp.com:443/calendars", "https://person@timetreeapp.com/calendars",
            "https://TIMETREEAPP.COM/calendars", "//timetreeapp.com/calendars",
            "/%63alendars", "/calendars%2f", "/calendars/../calendars", "//calendars",
            "/calendars ", "/calendars\t", "/calendars\x00", "/calendars\r\n /signin",
        ]
        for index, target in enumerate(variants):
            with self.subTest(case=index):
                diagnostic = self.rejected_csrf_redirect((("Location", target),))
                self.assertEqual(diagnostic["redirect_target"], "other_or_missing")

    def test_redirect_diagnostic_missing_or_duplicate_location_is_other_or_missing(self):
        cases = [(), (("Location", "/intl/en/"), ("Location", "/intl/en/")),
                 (("Location", "/intl/en/"), ("location", "/signin")),
                 (("Location", "https://other.invalid/"), ("Location", "/intl/en/")),
                 (("Location", "/calendars"), ("Location", "/calendars")),
                 (("Location", "/calendars"), ("location", ORIGIN + "/calendars")),
                 (("Location", "https://other.invalid/"), ("Location", "/calendars"))]
        for index, headers in enumerate(cases):
            with self.subTest(case=index):
                diagnostic = self.rejected_csrf_redirect(headers)
                self.assertEqual(diagnostic["redirect_target"], "other_or_missing")

    def test_redirect_status_matrix_remains_rejected_without_body_cookie_or_second_hop(self):
        for status in (300, 304, 305, 306, 399):
            with self.subTest(status=status):
                diagnostic = self.rejected_csrf_redirect((("Location", ORIGIN + "/calendars"),),
                                                          status=status)
                self.assertEqual(diagnostic["redirect_target"], "calendars")

    def test_eligible_calendar_redirect_is_empty_marker_without_transport_follow(self):
        for status in (301, 302, 303, 307, 308):
            for target in ("/calendars", ORIGIN + "/calendars"):
                with self.subTest(status=status, absolute=target.startswith("https:")):
                    transport, factory = self.scoped([
                        wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                        wire((PASSWORD + SESSION).encode(), status=f"{status} Redirect", headers=(
                            ("Location", target), ("X-CSRF-Token", "synthetic-poison"),
                            ("Set-Cookie", "session=synthetic-poison; Secure; Path=/"))),
                        wire(f'<meta name="csrf-token" content="{CSRF}">'.encode()),
                    ])
                    transport.request("PUT", ORIGIN + "/api/v1/auth/email/signin",
                                      {"uid": EMAIL, "password": PASSWORD, "uuid": "0" * 32})
                    marker = transport.request("GET", ORIGIN + "/signin")
                    self.assertIs(type(marker), http_transport.CsrfCalendarRedirect)
                    self.assertEqual(fields(marker), ())
                    self.assertFalse(any(hasattr(marker, name) for name in ("status", "headers", "body", "url")))
                    self.assertEqual(len(factory.connections), 2)
                    self.assertEqual(len(factory.responses), 1)
                    self.assertEqual(factory.connections[1].response_reads, [])
                    self.assertTrue({cookie.name: cookie.value for cookie in transport.cookie_jar}
                                    == {"session": SESSION}, "Redirect altered the prior cookie jar")
                    final = transport.request("GET", ORIGIN + "/calendars")
                    self.assertEqual(final.status, 200)
                    self.assertIsInstance(final.body, str)
                    self.assertTrue(cookie_values(factory.connections[2].requests[0]["headers"])
                                    == {"session": SESSION}, "Follow-up received a redirect cookie")
                    with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
                        transport.request("GET", ORIGIN + "/calendars")
                    self.assertEqual(len(factory.connections), 3)
                    self.assertTrue(all(connection.closed for connection in factory.connections))

    def test_redirect_target_classification_is_absent_for_signin_put_and_api_get(self):
        body = {"uid": EMAIL, "password": PASSWORD, "uuid": "0" * 32}
        for method, path, payload in (("PUT", "/api/v1/auth/email/signin", body),
                                      ("GET", "/api/v1/calendars?since=0", None),
                                      ("GET", "/api/v1/calendar/42/events/sync?since=0", None)):
            with self.subTest(method=method, path=path):
                transport, factory = self.scoped([
                    wire((PASSWORD + SESSION).encode(), status="302 Redirect", headers=(
                        ("Location", ORIGIN + "/calendars"),
                        ("Set-Cookie", "poison=synthetic; Secure; Path=/"))), wire(b"{}")])
                with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
                    transport.request(method, ORIGIN + path, payload)
                self.assertIsNone(transport.csrf_redirect_target)
                self.assertEqual(len(factory.connections), 1)
                self.assertEqual(len(factory.responses), 1)
                self.assertEqual(factory.connections[0].response_reads, [])
                self.assertEqual(len(list(transport.cookie_jar)), 0)
                self.assertTrue(factory.connections[0].closed)

    def test_denied_request_cannot_reuse_an_earlier_redirect_classification(self):
        transport, factory = self.scoped([wire(status="302 Redirect",
            headers=(("Location", "/intl/en/"),))])
        with self.assertRaisesRegex(WorkerError, "^e2e_http_redirect_rejected$"):
            transport.request("GET", ORIGIN + "/signin")
        self.assertEqual(transport.csrf_redirect_target, "public_landing")
        with self.assertRaisesRegex(WorkerError, "^e2e_request_denied$"):
            transport.request("GET", ORIGIN + "/signin", {"unexpected": "body"})
        self.assertIsNone(transport.csrf_redirect_target)
        self.assertEqual(len(factory.connections), 1)


if __name__ == "__main__":
    unittest.main()
