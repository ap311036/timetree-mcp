import json
import os
import tempfile
import unittest
from unittest.mock import patch

from timetree_mcp.http_reader import (
    DirectHttpsTransport, HttpResponse, TimeTreeHttpError, TimeTreeHttpReader,
    TransportError, UrllibTransport,
)
from timetree_mcp.server import TimeTreeMcpServer


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cleared = False
        self.clear_calls = 0

    def request(self, method, url, body=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}})
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def clear(self):
        self.cleared = True
        self.clear_calls += 1


class FakeCredentialStore:
    def __init__(self, credentials=(None, None)):
        self.credentials = credentials

    def read(self):
        return self.credentials

    def status(self):
        return {"available": True, "email_configured": bool(self.credentials[0]), "password_configured": bool(self.credentials[1])}


class UnusedCredentialStore:
    def read(self):
        raise AssertionError("Unused Keychain credentials must not be read")

    def status(self):
        raise AssertionError("Unused Keychain metadata must not be queried")


class SessionTransport(FakeTransport):
    """Synthetic cookie state for reader-level recovery regressions."""
    def __init__(self, responses):
        super().__init__(responses)
        self.session_present = False

    def request(self, method, url, body=None, headers=None, timeout=None):
        response = super().request(method, url, body, headers, timeout)
        if method == "PUT" and url.endswith("/auth/email/signin") and response.status == 200:
            self.session_present = True
        return response

    def clear(self):
        self.session_present = False
        super().clear()


class SessionInvalidationTests(unittest.TestCase):
    @staticmethod
    def login_responses():
        return [HttpResponse(200, {}, {}), HttpResponse(200, {"x-csrf-token": "SYNTHETIC-TOKEN"}, "")]

    @staticmethod
    def calendar_response():
        return HttpResponse(200, {}, {"calendars": [{"id": "42", "name": "Synthetic"}]})

    def setup_reader(self, failure):
        transport = SessionTransport([*self.login_responses(), self.calendar_response(), failure])
        reader = TimeTreeHttpReader(email="synthetic@example.invalid", password="SYNTHETIC PASSWORD",
                                    transport=transport, credential_store=UnusedCredentialStore())
        reader.login()
        self.assertTrue(reader._authenticated)
        self.assertTrue(transport.session_present)
        return reader, transport

    def assert_invalidated(self, reader, transport):
        self.assertFalse(reader._authenticated)
        self.assertIsNone(reader._csrf_token)
        self.assertFalse(transport.session_present)
        self.assertEqual(reader.email, "synthetic@example.invalid")
        self.assertEqual(reader.password, "SYNTHETIC PASSWORD")
        self.assertEqual(sum(call["method"] == "PUT" for call in transport.calls), 1)
        self.assertEqual(len(transport.calls), 4)

    def test_sync_transport_failure_invalidates_then_next_explicit_read_recovers(self):
        for failure in (TransportError("invalid_response"), TransportError("network_timeout"),
                        TimeoutError("synthetic timeout")):
            with self.subTest(kind=type(failure).__name__):
                reader, transport = self.setup_reader(failure)
                with self.assertRaises(BaseException) as caught:
                    reader.read("sync_events", {"calendar_id": "42"})
                self.assertIs(caught.exception, failure)
                self.assert_invalidated(reader, transport)
                transport.responses.extend([*self.login_responses(), self.calendar_response(),
                                            HttpResponse(200, {}, {"events": [], "chunk": 0})])
                result = reader.read("sync_events", {"calendar_id": "42"})
                self.assertTrue(result["sync_complete"])
                self.assertTrue(reader._authenticated)
                self.assertTrue(transport.session_present)
                self.assertEqual(sum(call["method"] == "PUT" for call in transport.calls), 2)
                self.assertEqual(len(transport.calls), 8)

    def test_returned_sync_401_and_403_invalidate_without_retrying_failed_call(self):
        for status in (401, 403):
            with self.subTest(status=status):
                reader, transport = self.setup_reader(HttpResponse(status, {"x-csrf-token": "ERROR-TOKEN"}, {}))
                with self.assertRaises(TimeTreeHttpError) as caught:
                    reader.read("sync_events", {"calendar_id": "42"})
                self.assertEqual(caught.exception.status, status)
                self.assert_invalidated(reader, transport)
                transport.responses.extend([*self.login_responses(), self.calendar_response(),
                                            HttpResponse(200, {}, {"events": [], "chunk": 0})])
                self.assertTrue(reader.read("sync_events", {"calendar_id": "42"})["sync_complete"])
                self.assertEqual(sum(call["method"] == "PUT" for call in transport.calls), 2)

    def test_cancellation_and_transport_error_survive_cleanup_failure(self):
        class Cancelled(BaseException):
            pass
        for failure in (Cancelled("synthetic cancellation"), KeyboardInterrupt(),
                        TransportError("invalid_response")):
            with self.subTest(kind=type(failure).__name__):
                reader, transport = self.setup_reader(failure)
                original = transport.clear
                def interrupted_clear():
                    original()
                    raise Cancelled("synthetic cleanup cancellation")
                transport.clear = interrupted_clear
                with self.assertRaises(BaseException) as caught:
                    reader.read("sync_events", {"calendar_id": "42"})
                self.assertIs(caught.exception, failure)
                self.assert_invalidated(reader, transport)

    def test_auth_status_is_preserved_when_cleanup_itself_fails(self):
        reader, transport = self.setup_reader(HttpResponse(403, {}, {}))
        original = transport.clear
        def failed_clear():
            original()
            raise RuntimeError("synthetic cleanup failure")
        transport.clear = failed_clear
        with self.assertRaises(TimeTreeHttpError) as caught:
            reader.read("sync_events", {"calendar_id": "42"})
        self.assertEqual(caught.exception.status, 403)
        self.assert_invalidated(reader, transport)


class SharedAuthLifecycleTests(unittest.TestCase):
    def reader(self, responses, **kwargs):
        transport = FakeTransport(responses)
        reader = TimeTreeHttpReader(email="synthetic@example.invalid", password="SYNTHETIC PASSWORD",
                                    transport=transport, credential_store=UnusedCredentialStore(), **kwargs)
        return reader, transport

    def test_calendar_csrf_marker_adds_only_one_fixed_bodyless_get_before_auth_commit(self):
        from timetree_mcp.http_transport import CsrfCalendarRedirect
        for headers, body, expected in (
            ({"X-Csrf-Token": "HEADER"}, "", "HEADER"),
            ({}, '<meta name="csrf-token" content="META">', "META"),
        ):
            with self.subTest(token_source=expected):
                reader, transport = self.reader([
                    HttpResponse(200, {}, {}), CsrfCalendarRedirect(), HttpResponse(200, headers, body),
                ], base_url="https://example.test")
                original_request = transport.request
                def request(*args, **kwargs):
                    self.assertFalse(reader._authenticated)
                    self.assertIsNone(reader._csrf_token)
                    return original_request(*args, **kwargs)
                transport.request = request
                self.assertTrue(reader.login()["authenticated"])
                self.assertEqual(reader._csrf_token, expected)
                self.assertIsNone(reader.login_diagnostic)
                self.assertEqual([(call["method"], call["url"]) for call in transport.calls], [
                    ("PUT", "https://example.test/api/v1/auth/email/signin"),
                    ("GET", "https://example.test/signin"),
                    ("GET", "https://example.test/calendars"),
                ])
                self.assertEqual([call["body"] for call in transport.calls[1:]], [None, None])
                self.assertTrue(all("X-CSRF-Token" not in call["headers"] for call in transport.calls))
                self.assertEqual(transport.clear_calls, 1)

    def test_plain_redirect_response_never_authorizes_calendar_csrf_hop(self):
        for location in ("/calendars", "https://timetreeapp.com/calendars",
                         "https://other.invalid/calendars", "/calendars?private=CANARY"):
            with self.subTest(location=location):
                reader, transport = self.reader([
                    HttpResponse(200, {}, {}),
                    HttpResponse(302, {"Location": location, "x-csrf-token": "IGNORED"}, ""),
                ])
                with self.assertRaises(TransportError) as caught:
                    reader.login()
                self.assertEqual(caught.exception.code, "http_redirect_rejected")
                self.assertEqual(reader.login_diagnostic,
                                 {"phase": "csrf_fetch", "reason": "http_redirect_rejected"})
                self.assertEqual(len(transport.calls), 2)
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)

    def test_calendar_csrf_hop_failure_cleans_session_without_retry_or_loop(self):
        from timetree_mcp.http_transport import CsrfCalendarRedirect
        for failure, reason in (
            (CsrfCalendarRedirect(), "http_redirect_rejected"),
            (HttpResponse(302, {"Location": "/calendars"}, ""), "http_redirect_rejected"),
            (HttpResponse(200, {}, ""), "csrf_missing"),
            (HttpResponse(200, {"x-csrf-token": "BAD\r\nTOKEN"}, ""), "invalid_response"),
            (HttpResponse(401, {"x-csrf-token": "IGNORED"}, ""), "http_401"),
            (HttpResponse(500, {"x-csrf-token": "IGNORED"}, ""), "http_5xx"),
            (TransportError("network_timeout"), "network_timeout"),
        ):
            with self.subTest(reason=reason, failure_type=type(failure).__name__):
                transport = SessionTransport([HttpResponse(200, {}, {}), CsrfCalendarRedirect(), failure])
                reader = TimeTreeHttpReader(email="synthetic@example.invalid", password="SYNTHETIC PASSWORD",
                                            transport=transport, credential_store=UnusedCredentialStore())
                with self.assertRaises(TransportError) as caught:
                    reader.login()
                self.assertEqual(caught.exception.code, reason)
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": reason})
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)
                self.assertFalse(transport.session_present)
                self.assertEqual(len(transport.calls), 3)
                self.assertEqual(sum(call["method"] == "PUT" for call in transport.calls), 1)

    def test_marker_outside_exact_initial_csrf_get_is_rejected(self):
        from timetree_mcp.http_transport import CsrfCalendarRedirect
        for method, path, body, csrf in (
            ("GET", "/", None, False), ("GET", "/signin/", None, False),
            ("GET", "/signin?next=calendars", None, False), ("GET", "/calendars", None, False),
            ("GET", "/api/v1/calendars?since=0", None, False),
            ("PUT", "/api/v1/auth/email/signin", {}, False),
            ("GET", "/signin", {}, False), ("GET", "/signin", None, True),
        ):
            with self.subTest(method=method, path=path, body_present=body is not None, csrf=csrf):
                reader, transport = self.reader([CsrfCalendarRedirect()])
                reader._authenticated, reader._csrf_token = True, "OLD"
                with self.assertRaises(TransportError) as caught:
                    reader._request_raw(method, f"{reader.base_url}{path}", body=body, csrf=csrf)
                self.assertEqual(caught.exception.code, "http_redirect_rejected")
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)
                self.assertEqual(len(transport.calls), 1)

    def test_missing_token_and_failed_signin_csrf_never_try_calendar_page(self):
        for response, reason in ((HttpResponse(200, {}, ""), "csrf_missing"),
                                 (HttpResponse(500, {}, ""), "http_5xx")):
            reader, transport = self.reader([HttpResponse(200, {}, {}), response])
            with self.assertRaises(TransportError) as caught:
                reader.login()
            self.assertEqual(caught.exception.code, reason)
            self.assertEqual(len(transport.calls), 2)

    def test_calendar_csrf_failure_preserves_cancellation_and_safe_redirect_diagnostic(self):
        from timetree_mcp.http_transport import CsrfCalendarRedirect
        class Cancelled(BaseException):
            pass
        for failure, diagnostic in (
            (Cancelled("PRIVATE CANCELLATION"), {"phase": "csrf_fetch", "reason": "internal_error"}),
            (TransportError("http_redirect_rejected", status=302, redirect_target="signin"),
             {"phase": "csrf_fetch", "reason": "http_redirect_rejected", "redirect_target": "signin"}),
        ):
            transport = SessionTransport([HttpResponse(200, {}, {}), CsrfCalendarRedirect(), failure])
            reader = TimeTreeHttpReader(email="synthetic@example.invalid", password="SYNTHETIC PASSWORD",
                                        transport=transport, credential_store=UnusedCredentialStore())
            original_clear = transport.clear
            def clear():
                original_clear()
                if transport.clear_calls > 1:
                    raise Cancelled("PRIVATE CLEANUP")
            transport.clear = clear
            with self.assertRaises(BaseException) as caught:
                reader.login()
            self.assertIs(caught.exception, failure)
            self.assertEqual(reader.login_diagnostic, diagnostic)
            self.assertFalse(reader._authenticated)
            self.assertIsNone(reader._csrf_token)
            self.assertFalse(transport.session_present)
            self.assertEqual(len(transport.calls), 3)

    def test_default_and_compatibility_name_share_one_lazy_transport_class(self):
        self.assertIs(UrllibTransport, DirectHttpsTransport)
        reader = TimeTreeHttpReader(email="synthetic@example.invalid", password="SYNTHETIC")
        self.assertIs(type(reader.transport), DirectHttpsTransport)

    def test_login_commits_only_after_fixed_signin_csrf_and_uses_instance_referer(self):
        reader, transport = self.reader([
            HttpResponse(200, {}, {}),
            HttpResponse(200, {}, '<meta name="csrf-token" content="SYNTHETIC-CSRF">'),
        ], base_url="https://example.test")
        reader._authenticated, reader._csrf_token = True, "OLD-TOKEN"
        request = transport.request
        def guarded_request(*args, **kwargs):
            self.assertFalse(reader._authenticated)
            self.assertIsNone(reader._csrf_token)
            self.assertGreaterEqual(transport.clear_calls, 1)
            return request(*args, **kwargs)
        transport.request = guarded_request
        result = reader.login()
        self.assertTrue(result["authenticated"] and result["csrf_available"])
        self.assertEqual([(call["method"], call["url"]) for call in transport.calls], [
            ("PUT", "https://example.test/api/v1/auth/email/signin"),
            ("GET", "https://example.test/signin")])
        self.assertIsNone(transport.calls[1]["body"])
        self.assertEqual({call["headers"]["Referer"] for call in transport.calls}, {"https://example.test/"})
        self.assertIsNone(reader.login_diagnostic)

    def test_signin_requires_json_object_without_error_before_any_csrf_fetch(self):
        for body in (None, "", "PRIVATE PLAINTEXT", [], {"error": "PRIVATE"}, {"error": {}}):
            with self.subTest(shape=type(body).__name__):
                reader, transport = self.reader([HttpResponse(200, {}, body)])
                with self.assertRaises(RuntimeError) as caught:
                    reader.login()
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(reader.login_diagnostic, {"phase": "sign_in", "reason": "invalid_response"})
                self.assertEqual(len(transport.calls), 1)
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)

    def test_http_failures_never_accept_error_tokens_or_keep_stale_authentication(self):
        for phase in ("sign_in", "csrf_fetch"):
            for status, reason in ((401, "http_401"), (403, "http_403"), (429, "http_429"),
                                   (404, "http_4xx"), (500, "http_5xx"), (302, "http_redirect_rejected")):
                with self.subTest(phase=phase, status=status):
                    responses = [] if phase == "sign_in" else [HttpResponse(200, {}, {})]
                    responses.append(HttpResponse(status, {"x-csrf-token": "PRIVATE ERROR TOKEN"},
                                                  '<meta name="csrf-token" content="PRIVATE">'))
                    reader, transport = self.reader(responses)
                    reader._authenticated, reader._csrf_token = True, "OLD TOKEN"
                    with self.assertRaises(RuntimeError) as caught:
                        reader.login()
                    self.assertNotIn("PRIVATE", str(caught.exception))
                    self.assertEqual(reader.login_diagnostic, {"phase": phase, "reason": reason})
                    self.assertFalse(reader._authenticated)
                    self.assertIsNone(reader._csrf_token)
                    self.assertGreaterEqual(transport.clear_calls, 2)
                    self.assertEqual(reader.password, "SYNTHETIC PASSWORD")

    def test_missing_and_invalid_csrf_tokens_fail_with_fixed_codes(self):
        for token, reason in ((None, "csrf_missing"), ("", "csrf_missing"),
                              ("   ", "csrf_missing"), ("x" * 4097, "invalid_response"),
                              ("PRIVATE\r\nINJECTED", "invalid_response"), (123, "invalid_response")):
            with self.subTest(reason=reason, token_type=type(token).__name__):
                headers = {} if token is None else {"X-CsRf-ToKeN": token}
                reader, transport = self.reader([HttpResponse(200, {}, {}), HttpResponse(200, headers, "")])
                with self.assertRaises(RuntimeError) as caught:
                    reader.login()
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": reason})
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)

    def test_header_meta_precedence_and_token_size_limit(self):
        for headers, body, expected in (
            ({"X-CsRf-ToKeN": "HEADER"}, '<meta name="csrf-token" content="META">', "HEADER"),
            ({"x-csrf-token": ""}, '<meta name="csrf-token" content="META">', "META"),
            ({}, '<meta name="csrf-token" content="META">', "META"),
            ({"x-csrf-token": "x" * 4096}, "", "x" * 4096),
        ):
            reader, _ = self.reader([HttpResponse(200, {}, {}), HttpResponse(200, headers, body)])
            self.assertTrue(reader.login()["csrf_available"])
            self.assertEqual(reader._csrf_token, expected)

    def test_meta_token_must_be_usable_by_shared_http_header_contract(self):
        for token in ("TOKEN\x00BAD", "TOKEN\tBAD", "TOKEN\x7fBAD", "tökén", "秘密"):
            with self.subTest(size=len(token)):
                reader, transport = self.reader([
                    HttpResponse(200, {}, {}),
                    HttpResponse(200, {}, f'<meta name="csrf-token" content="{token}">'),
                ])
                with self.assertRaises(TransportError) as caught:
                    reader.login()
                self.assertEqual(caught.exception.code, "invalid_response")
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": "invalid_response"})
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)
                self.assertEqual(len(transport.calls), 2)
                self.assertGreaterEqual(transport.clear_calls, 2)

    def test_exception_and_cancellation_cleanup_preserves_original_failure(self):
        class Cancelled(BaseException):
            pass
        cases = [(TimeoutError("PRIVATE TIMEOUT"), "network_timeout"),
                 (TimeTreeHttpError("PRIVATE ERROR", 403), "http_403"),
                 (RuntimeError("PRIVATE ERROR"), "internal_error"),
                 (Cancelled("PRIVATE CANCEL"), "internal_error")]
        for failure, reason in cases:
            with self.subTest(kind=type(failure).__name__):
                reader, transport = self.reader([HttpResponse(200, {}, {}), failure])
                original_clear = transport.clear
                def clear():
                    original_clear()
                    if transport.clear_calls > 1:
                        raise RuntimeError("PRIVATE CLEANUP")
                transport.clear = clear
                with self.assertRaises(BaseException) as caught:
                    reader.login()
                if isinstance(failure, Exception):
                    self.assertNotIn("PRIVATE", str(caught.exception))
                else:
                    self.assertIs(caught.exception, failure)
                self.assertEqual(reader.login_diagnostic, {"phase": "csrf_fetch", "reason": reason})
                self.assertFalse(reader._authenticated)
                self.assertIsNone(reader._csrf_token)
                self.assertGreaterEqual(transport.clear_calls, 2)

    def test_transport_diagnostic_uses_neutral_code_and_only_scoped_redirect_metadata(self):
        for phase in ("sign_in", "csrf_fetch"):
            with self.subTest(phase=phase):
                failure = TransportError("http_redirect_rejected", status=302, redirect_target="public_landing")
                responses = [] if phase == "sign_in" else [HttpResponse(200, {}, {})]
                reader, transport = self.reader([*responses, failure])
                with self.assertRaises(TransportError) as caught:
                    reader.login()
                self.assertIs(caught.exception, failure)
                expected = {"phase": phase, "reason": "http_redirect_rejected"}
                if phase == "csrf_fetch":
                    expected["redirect_target"] = "public_landing"
                self.assertEqual(reader.login_diagnostic, expected)
                self.assertIsNone(reader._csrf_token)
                self.assertGreaterEqual(transport.clear_calls, 2)

    def test_initial_cleanup_failure_prevents_credential_resolution_and_network(self):
        reader, transport = self.reader([])
        reader._authenticated, reader._csrf_token = True, "OLD"
        def failed_clear():
            raise RuntimeError("PRIVATE CLEANUP")
        transport.clear = failed_clear
        with patch.object(reader, "_credentials", side_effect=AssertionError("must not resolve credentials")), \
                self.assertRaises(RuntimeError) as caught:
            reader.login()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(transport.calls, [])
        self.assertFalse(reader._authenticated)
        self.assertIsNone(reader._csrf_token)
        self.assertEqual(reader.login_diagnostic, {"phase": "sign_in", "reason": "internal_error"})

    def test_explicit_retry_reuses_selected_constructor_pair_and_resets_diagnostic(self):
        reader, transport = self.reader([HttpResponse(401, {}, {}), HttpResponse(200, {}, {}),
                                        HttpResponse(200, {"x-csrf-token": "TOKEN"}, "")])
        with self.assertRaises(RuntimeError):
            reader.login()
        self.assertEqual(reader.email, "synthetic@example.invalid")
        self.assertEqual(reader.password, "SYNTHETIC PASSWORD")
        self.assertTrue(reader.login()["authenticated"])
        self.assertIsNone(reader.login_diagnostic)
        self.assertEqual([call["body"]["uid"] for call in transport.calls if call["method"] == "PUT"],
                         ["synthetic@example.invalid", "synthetic@example.invalid"])

    def test_token_rotation_only_accepts_successful_valid_headers(self):
        reader, transport = self.reader([HttpResponse(500, {"x-csrf-token": "ERROR"}, {}),
                                        HttpResponse(200, {"x-csrf-token": "NEW"}, {})],
                                       base_url="https://example.test")
        reader._csrf_token = "OLD"
        reader._request_raw("GET", "https://example.test/api/v1/calendars?since=0")
        self.assertEqual(reader._csrf_token, "OLD")
        reader._request_raw("GET", "https://example.test/api/v1/calendars?since=0")
        self.assertEqual(reader._csrf_token, "NEW")
        self.assertEqual(transport.calls[0]["headers"]["Referer"], "https://example.test/")

    def test_token_rotation_does_not_accept_values_the_shared_core_cannot_send(self):
        for token in ("TOKEN\x00BAD", "TOKEN\tBAD", "TOKEN\x7fBAD", "tökén", "秘密"):
            reader, _ = self.reader([HttpResponse(200, {"x-csrf-token": token}, {})])
            reader._csrf_token = "OLD"
            reader._request_raw("GET", "https://timetreeapp.com/api/v1/calendars?since=0")
            self.assertEqual(reader._csrf_token, "OLD")

    def test_logout_resets_flags_even_if_transport_cleanup_raises(self):
        reader, transport = self.reader([])
        reader._authenticated, reader._csrf_token = True, "OLD"
        def failed_clear():
            raise RuntimeError("PRIVATE CLEANUP")
        transport.clear = failed_clear
        with self.assertRaises(RuntimeError):
            reader.logout()
        self.assertFalse(reader._authenticated)
        self.assertIsNone(reader._csrf_token)
        self.assertEqual(reader.password, "SYNTHETIC PASSWORD")


class HttpReaderTests(unittest.TestCase):
    def test_complete_explicit_credentials_bypass_keychain_and_lower_priority_sources(self):
        cases = [
            ({"email": "constructor@example.invalid", "password": "constructor-password"},
             {"TIMETREE_EMAIL": "other@example.invalid", "TIMETREE_PASSWORD": ""},
             "constructor", "constructor@example.invalid", "constructor-password"),
            ({}, {"TIMETREE_EMAIL": "env@example.invalid", "TIMETREE_PASSWORD": "env-password",
                  "TIMETREE_PASSWORD_FILE": "/must/not/read"},
             "environment", "env@example.invalid", "env-password"),
        ]
        for constructor, environment, source, email, password in cases:
            with self.subTest(source=source), patch.dict(os.environ, environment, clear=True):
                transport = FakeTransport([
                    HttpResponse(200, {}, {}),
                    HttpResponse(200, {"x-csrf-token": "synthetic-csrf"}, ""),
                ])
                reader = TimeTreeHttpReader(**constructor, transport=transport,
                                            credential_store=UnusedCredentialStore())
                with patch("timetree_mcp.http_reader.Path.read_text", side_effect=AssertionError("Unused file")):
                    before = reader.status()
                    result = reader.login()
                self.assertTrue(before["credentials_configured"])
                self.assertEqual(before["credential_source"], source)
                self.assertEqual(before["keychain"], {"checked": False})
                self.assertTrue(result["authenticated"])
                self.assertEqual(transport.calls[0]["body"]["uid"], email)
                self.assertEqual(transport.calls[0]["body"]["password"], password)

    def test_partial_or_empty_explicit_credentials_fail_before_any_secret_or_network_access(self):
        complete_env = {"TIMETREE_EMAIL": "env@example.invalid", "TIMETREE_PASSWORD": "env-password"}
        cases = [
            ({"email": "constructor@example.invalid"}, complete_env),
            ({"password": "constructor-password"}, complete_env),
            ({"email": "", "password": "constructor-password"}, complete_env),
            ({"email": "constructor@example.invalid", "password": "  "}, complete_env),
            ({"email": [], "password": "constructor-password"}, complete_env),
            ({"email": "constructor@example.invalid", "password": False}, complete_env),
            ({}, {"TIMETREE_EMAIL": "env@example.invalid"}),
            ({}, {"TIMETREE_PASSWORD": "env-password"}),
            ({}, {"TIMETREE_PASSWORD_FILE": "/must/not/read"}),
            ({}, {"TIMETREE_EMAIL": "", "TIMETREE_PASSWORD": "env-password"}),
            ({}, {"TIMETREE_EMAIL": " \t", "TIMETREE_PASSWORD": "env-password"}),
            ({}, {"TIMETREE_EMAIL": "env@example.invalid", "TIMETREE_PASSWORD": "", "TIMETREE_PASSWORD_FILE": "/must/not/read"}),
            ({}, {"TIMETREE_EMAIL": "env@example.invalid", "TIMETREE_PASSWORD_FILE": " "}),
        ]
        for constructor, environment in cases:
            with self.subTest(constructor=constructor, fields=tuple(environment)), patch.dict(os.environ, environment, clear=True):
                transport = FakeTransport([])
                reader = TimeTreeHttpReader(**constructor, transport=transport,
                                            credential_store=UnusedCredentialStore())
                with patch("timetree_mcp.http_reader.Path.read_text", side_effect=AssertionError("Unselected file")):
                    for operation in (reader.login, reader.status):
                        with self.assertRaisesRegex(RuntimeError, "Incomplete or empty TimeTree credentials"):
                            operation()
                self.assertEqual(transport.calls, [])

    def test_password_file_status_uses_metadata_and_login_uses_only_selected_pair(self):
        with tempfile.NamedTemporaryFile("w", delete=True) as password_file:
            password_file.write("synthetic-file-password\n")
            password_file.flush()
            with patch.dict(os.environ, {"TIMETREE_EMAIL": "file@example.invalid", "TIMETREE_PASSWORD_FILE": password_file.name}, clear=True):
                transport = FakeTransport([
                    HttpResponse(200, {}, {}), HttpResponse(200, {"x-csrf-token": "synthetic-csrf"}, ""),
                ])
                reader = TimeTreeHttpReader(transport=transport, credential_store=UnusedCredentialStore())
                with patch("timetree_mcp.http_reader.Path.read_text", side_effect=AssertionError("Status must not read a password")):
                    status = reader.status()
                self.assertTrue(status["credentials_configured"])
                self.assertEqual(status["credential_source"], "password_file")
                self.assertEqual(status["keychain"], {"checked": False})
                reader.login()
                self.assertEqual(transport.calls[0]["body"]["uid"], "file@example.invalid")
                self.assertEqual(transport.calls[0]["body"]["password"], "synthetic-file-password")

    def test_empty_password_file_cannot_fall_back_to_keychain(self):
        with tempfile.NamedTemporaryFile("w", delete=True) as password_file:
            password_file.write("  \n")
            password_file.flush()
            with patch.dict(os.environ, {"TIMETREE_EMAIL": "file@example.invalid", "TIMETREE_PASSWORD_FILE": password_file.name}, clear=True):
                transport = FakeTransport([])
                reader = TimeTreeHttpReader(transport=transport, credential_store=UnusedCredentialStore())
                with self.assertRaisesRegex(RuntimeError, "Incomplete or empty TimeTree credentials"):
                    reader.login()
                self.assertEqual(transport.calls, [])

    def test_keychain_status_without_explicit_configuration_does_not_read_secret_values(self):
        store = FakeCredentialStore(("stored@example.invalid", "synthetic-stored"))
        with patch.dict(os.environ, {}, clear=True), patch.object(store, "read", side_effect=AssertionError("Status must not read secrets")):
            reader = TimeTreeHttpReader(transport=FakeTransport([]), credential_store=store)
            status = reader.status()
        self.assertTrue(status["credentials_configured"])
        self.assertEqual(status["credential_source"], "keychain")
        self.assertEqual(status["keychain"], {"checked": True, "available": True,
                                            "email_configured": True, "password_configured": True})

    def test_missing_password_file_status_is_unconfigured_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "TIMETREE_EMAIL": "file@example.invalid", "TIMETREE_PASSWORD_FILE": directory + "/missing",
        }, clear=True):
            reader = TimeTreeHttpReader(transport=FakeTransport([]), credential_store=UnusedCredentialStore())
            self.assertFalse(reader.status()["credentials_configured"])
            with self.assertRaisesRegex(RuntimeError, "^Unable to read TIMETREE_PASSWORD_FILE$"):
                reader.login()

    def test_comment_silent_rejects_non_booleans_before_credentials_or_http(self):
        transport = FakeTransport([])
        store = FakeCredentialStore()
        server = TimeTreeMcpServer(TimeTreeHttpReader(transport=transport, credential_store=store))
        arguments = {"calendar_id": "personal", "event_uuid": "event-demo", "content": "Comment", "confirm": True}
        with patch.object(store, "read", side_effect=AssertionError("Credentials must not be read")) as read:
            for value in (None, "false", "true", "", 0, 1, 0.0, {}, []):
                with self.subTest(value=value):
                    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "timetree_add_event_comment", "arguments": {**arguments, "silent": value}}})
                    self.assertTrue(response["result"]["isError"])
                    self.assertEqual(response["result"]["structuredContent"]["error"], "silent must be a boolean")
            read.assert_not_called()
        self.assertEqual(transport.calls, [])

    def test_comment_silent_default_and_booleans_reach_http_without_coercion(self):
        for supplied, expected in (({}, True), ({"silent": True}, True), ({"silent": False}, False)):
            with self.subTest(supplied=supplied):
                transport = FakeTransport([
                    HttpResponse(200, {}, {}),
                    HttpResponse(200, {}, '<meta name="csrf-token" content="synthetic-csrf">'),
                    HttpResponse(200, {}, {"event_activity": {"id": "comment-demo", "attachment": {"content": "Comment"}}}),
                ])
                reader = TimeTreeHttpReader(transport=transport, credential_store=FakeCredentialStore(("test@example.invalid", "synthetic")))
                arguments = {"calendar_id": "personal", "event_uuid": "event-demo", "content": "Comment", "confirm": True, **supplied}
                with patch.dict(os.environ, {}, clear=True):
                    response = TimeTreeMcpServer(reader).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "timetree_add_event_comment", "arguments": arguments}})
                self.assertFalse(response["result"]["isError"], response)
                self.assertEqual(len(transport.calls), 3)
                request = transport.calls[-1]
                self.assertEqual(request["method"], "POST")
                self.assertTrue(request["url"].endswith("/calendar/personal/event/event-demo/activity"))
                self.assertIs(request["body"]["silent"], expected)
                self.assertEqual(request["body"]["attachment"], {"content": "Comment"})
                self.assertNotIn("confirm", request["body"])

    def test_login_uses_put_credentials_then_fetches_csrf_without_exposing_password(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
            ]
        )
        reader = TimeTreeHttpReader(
            email="me@example.com",
            password="secret-value",
            transport=transport,
        )

        result = reader.login()

        self.assertTrue(result["authenticated"])
        self.assertTrue(result["csrf_available"])
        self.assertEqual(transport.calls[0]["method"], "PUT")
        self.assertTrue(transport.calls[0]["url"].endswith("/api/v1/auth/email/signin"))
        self.assertEqual(transport.calls[0]["body"]["uid"], "me@example.com")
        self.assertEqual(transport.calls[0]["body"]["password"], "secret-value")
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(reader.status()["authenticated"], True)

    def test_missing_credentials_fail_without_network_call(self):
        transport = FakeTransport([])
        reader = TimeTreeHttpReader(transport=transport, credential_store=FakeCredentialStore())

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TIMETREE_EMAIL.*TIMETREE_PASSWORD"):
                reader.login()

        self.assertEqual(transport.calls, [])

    def test_password_file_can_supply_secret_without_putting_it_in_mcp_arguments(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
            ]
        )
        reader = TimeTreeHttpReader(transport=transport)
        with tempfile.NamedTemporaryFile("w", delete=True) as secret_file:
            secret_file.write("file-secret\n")
            secret_file.flush()
            with patch.dict(
                os.environ,
                {"TIMETREE_EMAIL": "me@example.com", "TIMETREE_PASSWORD_FILE": secret_file.name},
                clear=True,
            ):
                reader.login()

        self.assertEqual(transport.calls[0]["body"]["password"], "file-secret")

    def test_write_request_uses_in_memory_csrf_token_and_logout_clears_session(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
                HttpResponse(201, {}, {"event": {"id": "created", "uuid": "uuid-1", "title": "Test", "start_at": 1, "end_at": 2}}),
            ]
        )
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)
        reader.login()

        result = reader.read(
            "create_event",
            {"calendar_id": "cal-1", "payload": {"title": "Test"}},
        )
        reader.logout()

        self.assertTrue(result["success"])
        write_call = transport.calls[2]
        self.assertEqual(write_call["method"], "POST")
        self.assertEqual(write_call["headers"]["X-CSRF-Token"], "csrf-123")
        self.assertTrue(transport.cleared)
        self.assertFalse(reader.status()["authenticated"])

    def test_direct_reader_lists_events_without_browser_calls(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
                HttpResponse(200, {}, {"calendars": [{"id": 1, "alias_code": "a", "name": "Personal"}]}),
                HttpResponse(200, {}, {"events": [{"id": "event-1", "uuid": "uuid-1", "calendar_id": 1, "title": "Lunch", "start_at": 1787619600000, "end_at": 1787623200000}], "chunk": False, "since": 0}),
            ]
        )
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)

        result = reader.read(
            "list_events",
            {"start_date": "2026-08-25", "end_date": "2026-08-26", "calendar_id": "1"},
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["calendar_name"], "Personal")
        self.assertEqual([call["method"] for call in transport.calls], ["PUT", "GET", "GET", "GET"])

    def test_login_falls_back_to_keychain_store_and_exposes_presence_only_status(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
            ]
        )
        store = FakeCredentialStore(("keychain@example.com", "keychain-secret"))
        reader = TimeTreeHttpReader(transport=transport, credential_store=store)

        result = reader.read("login", {})
        status = reader.read("keychain_status", {})

        self.assertTrue(result["authenticated"])
        self.assertEqual(transport.calls[0]["body"]["uid"], "keychain@example.com")
        self.assertEqual(transport.calls[0]["body"]["password"], "keychain-secret")
        self.assertNotIn("keychain-secret", json.dumps(status))
        self.assertTrue(status["password_configured"])

    def test_list_events_skips_unavailable_calendars_and_reports_them(self):
        transport = FakeTransport(
            [
                HttpResponse(200, {}, {}),
                HttpResponse(200, {}, '<meta name="csrf-token" content="csrf-123">'),
                HttpResponse(200, {}, {"calendars": [{"id": 1, "name": "Old"}, {"id": 2, "name": "Readable"}]}),
                HttpResponse(400, {}, {"error": {"code": -425, "message": "failed to api request"}}),
                HttpResponse(200, {}, {"events": [{"id": "event-1", "uuid": "uuid-1", "calendar_id": 2, "title": "Lunch", "start_at": 1787619600000, "end_at": 1787623200000}], "chunk": False, "since": 0}),
            ]
        )
        reader = TimeTreeHttpReader(email="me@example.com", password="secret", transport=transport)

        result = reader.read("list_events", {"start_date": "2026-08-25", "end_date": "2026-08-26"})

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["events"][0]["calendar_id"], "2")
        self.assertEqual(result["skipped_calendars"][0]["calendar_id"], "1")
        self.assertEqual(result["skipped_calendars"][0]["error_code"], -425)
        self.assertFalse(result["sync_complete"])


    def test_sync_follows_decreasing_cursor_until_terminal_page(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 200}),
            HttpResponse(200, {}, {"events": [{"id": "b"}], "chunk": True, "since": 100}),
            HttpResponse(200, {}, {"events": [{"id": "c"}], "chunk": False, "since": 50}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        events, issue = reader._sync_raw_events("1")
        self.assertEqual([e["id"] for e in events], ["a", "b", "c"])
        self.assertIsNone(issue)
        self.assertTrue(transport.calls[2]["url"].endswith("since=100"))

    def test_sync_reports_repeated_cursor_without_looping(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 200}),
            HttpResponse(200, {}, {"events": [{"id": "b"}], "chunk": True, "since": 100}),
            HttpResponse(200, {}, {"events": [{"id": "c"}], "chunk": True, "since": 200}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        events, issue = reader._sync_raw_events("1")
        self.assertEqual(len(events), 3)
        self.assertEqual(issue["reason"], "repeated_cursor")
        self.assertEqual(len(transport.calls), 3)

    def test_sync_reports_invalid_cursor_and_page_limit(self):
        for cursor in (None, True, 1.5, "100", float("nan"), float("inf")):
            with self.subTest(cursor=cursor):
                reader = TimeTreeHttpReader(transport=FakeTransport([
                    HttpResponse(200, {}, {"events": [], "chunk": True, "since": cursor}),
                ]))
                _, issue = reader._sync_raw_events("1")
                self.assertEqual(issue["reason"], "invalid_cursor")
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"events": [], "chunk": True, "since": i + 1}) for i in range(25)
        ]))
        _, issue = reader._sync_raw_events("1")
        self.assertEqual(issue["reason"], "page_limit")

    def test_sync_preserves_partial_events_on_upstream_425(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "a"}], "chunk": True, "since": 100}),
            HttpResponse(400, {}, {"error": {"code": -425, "message": "failed to api request"}}),
        ]))
        events, issue = reader._sync_raw_events("1")
        self.assertEqual(events, [{"id": "a"}])
        self.assertEqual(issue["error_code"], -425)

    def test_list_events_reports_partial_sync_and_retains_events(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1, "name": "Readable"}]}),
            HttpResponse(200, {}, {"events": [{"id": "a", "calendar_id": 1, "start_at": "2026-08-25T12:00:00Z", "end_at": "2026-08-25T13:00:00Z"}], "chunk": True, "since": 0}),
        ])
        reader = TimeTreeHttpReader(transport=transport)
        reader._authenticated = True
        result = reader.read("list_events", {"start_date": "2026-08-25", "end_date": "2026-08-26"})
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["sync_complete"])
        self.assertEqual(result["incomplete_calendars"][0]["reason"], "repeated_cursor")

    def test_malformed_or_incomplete_page_preserves_prior_events_without_claiming_complete(self):
        invalid_pages = [
            {}, {"chunk": False}, {"events": None, "chunk": False},
            {"events": {}, "chunk": False}, {"events": []},
            {"events": [], "chunk": "false"}, {"events": [], "chunk": 2},
            {"events": [], "chunk": False, "error": {}},
            {"events": [], "chunk": False, "error": {"code": "synthetic"}},
            {"events": [], "chunk": False, "sync_complete": False},
            {"events": [], "chunk": False, "truncated": True},
            {"events": [], "chunk": False, "has_more": True},
            {"events": [], "chunk": False, "incomplete": True},
            {"events": [], "chunk": False, "skipped_unreadable_events": [{"id": "missing"}]},
            {"events": [], "chunk": False, "incomplete_calendars": "invalid"},
        ]
        valid_event = {"id": "kept", "start_at": "2030-01-01T09:00:00Z", "end_at": "2030-01-01T10:00:00Z"}
        for page in invalid_pages:
            with self.subTest(page=page):
                reader = TimeTreeHttpReader(transport=FakeTransport([
                    HttpResponse(200, {}, {"calendars": [{"id": 1, "name": "Synthetic"}]}),
                    HttpResponse(200, {}, {"events": [valid_event], "chunk": True, "since": 10}),
                    HttpResponse(200, {}, page),
                ]))
                result = reader._sync_events_result("1")
                self.assertFalse(result["sync_complete"])
                self.assertEqual([e["id"] for e in result["events"]], ["kept"])
                self.assertEqual(result["events"][0]["calendar_id"], "1")
                self.assertEqual(result["incomplete_calendars"][0]["calendar_id"], "1")
                strict_reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, page)]))
                with self.assertRaises(ValueError):
                    strict_reader._sync_raw_events("1", strict=True)

    def test_invalid_event_records_make_sync_incomplete_instead_of_disappearing(self):
        invalid_records = [None, {}, {"id": True}, {"id": ""},
                           {"id": "bad", "attributes": []},
                           {"id": "bad", "calendar_id": "other"},
                           {"id": "bad", "calendar": {"id": "other"}},
                           {"id": "bad", "relationships": {"calendar": {"data": {"id": "other"}}}}]
        for record in invalid_records:
            for strict in (False, True):
                with self.subTest(record=record, strict=strict):
                    reader = TimeTreeHttpReader(transport=FakeTransport([
                        HttpResponse(200, {}, {"events": [{"id": "kept"}], "chunk": True, "since": 10}),
                        HttpResponse(200, {}, {"events": [record], "chunk": False}),
                    ]))
                    if strict:
                        with self.assertRaises(ValueError):
                            reader._sync_raw_events("1", strict=True)
                    else:
                        events, issue = reader._sync_raw_events("1")
                        self.assertEqual(events, [{"id": "kept"}])
                        self.assertIsNotNone(issue)

    def test_duplicate_event_identity_does_not_produce_a_complete_snapshot(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"events": [{"id": "same"}], "chunk": True, "since": 10}),
            HttpResponse(200, {}, {"events": [{"id": "same"}], "chunk": False}),
        ]))
        events, issue = reader._sync_raw_events("1")
        self.assertEqual(events, [{"id": "same"}])
        self.assertIsNotNone(issue)

    def test_failed_event_normalization_reports_incomplete_with_usable_events(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1}]}),
            HttpResponse(200, {}, {"events": [
                {"id": "kept", "start_at": "2030-01-01T09:00:00Z"},
                {"id": "missing-start"},
            ], "chunk": False}),
        ]))
        result = reader._sync_events_result("1")
        self.assertFalse(result["sync_complete"])
        self.assertEqual([e["id"] for e in result["events"]], ["kept"])
        self.assertTrue(result["incomplete_calendars"])

    def test_invalid_or_ambiguous_times_and_all_day_are_incomplete(self):
        valid = {"id": "invalid", "start_at": "2030-01-01T09:00:00Z", "end_at": "2030-01-01T10:00:00Z"}
        invalid = [
            {"start_at": "not-a-date"}, {"end_at": "also-not-a-date"},
            {"start_at": "2030-01-01T09:00:00"},
            {"start_at": "2030-01-01"}, {"end_at": "2029-12-31T10:00:00Z"},
            {"all_day": "false"}, {"all_day": 0}, {"all_day": []},
        ]
        for patch_fields in invalid:
            with self.subTest(patch_fields=patch_fields):
                reader = TimeTreeHttpReader(transport=FakeTransport([
                    HttpResponse(200, {}, {"calendars": [{"id": 1}]}),
                    HttpResponse(200, {}, {"events": [{**valid, **patch_fields}], "chunk": False}),
                ]))
                result = reader._sync_events_result("1")
                self.assertFalse(result["sync_complete"])
                self.assertFalse(result["events"])
                self.assertEqual(result["incomplete_calendars"][0]["reason"], "invalid_event_normalization")

    def test_valid_all_day_dates_and_epoch_times_remain_supported(self):
        records = [
            {"id": "all-day", "all_day": True, "start_at": "2030-01-01", "end_at": "2030-01-02"},
            {"id": "epoch", "all_day": False, "start_at": 1792447200000, "end_at": 1792450800000},
        ]
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1}]}),
            HttpResponse(200, {}, {"events": records, "chunk": False}),
        ]))
        result = reader._sync_events_result("1")
        self.assertTrue(result["sync_complete"])
        self.assertEqual(len(result["events"]), 2)

    def test_memo_reads_keep_sync_completeness_with_partial_results(self):
        reader = TimeTreeHttpReader(transport=FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1}]}),
            HttpResponse(200, {}, {"events": [{"id": "memo", "category": 2,
                "start_at": "2030-01-01T09:00:00Z"}], "chunk": True, "since": 0}),
        ]))
        result = reader._list_memos({"calendar_id": "1"})
        self.assertFalse(result["sync_complete"])
        self.assertEqual(result["memos"][0]["id"], "memo")
        self.assertEqual(result["incomplete_calendars"][0]["reason"], "repeated_cursor")

    def test_ordinary_sync_enforces_snapshot_count_and_byte_budgets(self):
        oversized_pages = [
            [{"id": str(i)} for i in range(20001)],
            [{"id": "large", "note": "x" * 8_000_001}],
        ]
        for events in oversized_pages:
            with self.subTest(count=len(events)):
                reader = TimeTreeHttpReader(transport=FakeTransport([
                    HttpResponse(200, {}, {"events": events, "chunk": False}),
                ]))
                accepted, issue = reader._sync_raw_events("1")
                self.assertFalse(accepted)
                self.assertIsNotNone(issue)

    def test_multi_calendar_sync_shares_count_and_byte_budgets_and_stops_acquisition(self):
        count_page = [{"id": str(i), "start_at": "2030-01-01T09:00:00Z"} for i in range(10001)]
        byte_page = [{"id": "large", "start_at": "2030-01-01T09:00:00Z", "note": "x" * 4_000_000}]
        for page in (count_page, byte_page):
            with self.subTest(count=len(page)):
                transport = FakeTransport([
                    HttpResponse(200, {}, {"calendars": [{"id": 1}, {"id": 2}, {"id": 3}]}),
                    HttpResponse(200, {}, {"events": page, "chunk": False}),
                    HttpResponse(200, {}, {"events": page, "chunk": False}),
                    HttpResponse(200, {}, {"events": page, "chunk": False}),
                ])
                result = TimeTreeHttpReader(transport=transport)._sync_events_result()
                self.assertFalse(result["sync_complete"])
                self.assertEqual(len(result["events"]), len(page))
                self.assertEqual(len(transport.calls), 3)
                self.assertEqual({i["calendar_id"] for i in result["incomplete_calendars"]}, {"2", "3"})
                self.assertEqual(result["incomplete_calendars"][-1]["not_attempted"], True)

    def test_calendar_index_capacity_is_checked_before_event_acquisition(self):
        transport = FakeTransport([HttpResponse(200, {}, {"calendars": [{"id": i} for i in range(513)]})])
        with self.assertRaisesRegex(ValueError, "calendar_index_capacity_exceeded"):
            TimeTreeHttpReader(transport=transport)._sync_events_result()
        self.assertEqual(len(transport.calls), 1)

    def test_request_budget_at_exact_limit_stops_before_fetching_another_page(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1}]}),
            HttpResponse(200, {}, {"events": [
                {"id": "first", "start_at": "2030-01-01T09:00:00Z"},
                {"id": "second", "start_at": "2030-01-01T10:00:00Z"},
            ], "chunk": True, "since": 10}),
        ])
        with patch("timetree_mcp.http_reader.MAX_SYNC_EVENTS", 2):
            result = TimeTreeHttpReader(transport=transport)._sync_events_result()
        self.assertFalse(result["sync_complete"])
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(result["incomplete_calendars"][0]["reason"], "request_capacity_exceeded")

    def test_calendar_index_malformed_metadata_and_identities_are_rejected(self):
        invalid = [
            {}, {"calendars": None}, {"calendars": {}}, {"calendars": [None]},
            {"calendars": [{}]}, {"calendars": [{"id": True}]},
            {"calendars": [{"id": ""}]}, {"calendars": [{"id": 1, "attributes": []}]},
            {"calendars": [{"id": 1}, {"id": "1"}]},
            {"calendars": [{"id": 1, "attributes": {"id": 2}}]},
            {"calendars": [], "error": {}}, {"calendars": [], "sync_complete": False},
            {"calendars": [], "has_more": True}, {"calendars": [], "chunk": True},
            {"calendars": [], "chunk": "false"},
        ]
        for body in invalid:
            with self.subTest(body=body):
                reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, body)]))
                with self.assertRaises(ValueError):
                    reader._get_calendars()


    def test_calendar_listing_filters_deactivated_flat_and_nested_records(self):
        calendars = [
            {"id": 1, "name": "Missing"},
            {"id": 2, "attributes": {"name": "Null", "deactivated_at": None}},
            {"id": 3, "name": "Zero", "deactivated_at": 0},
            {"id": 4, "name": "Inactive", "deactivated_at": 1700000000000},
            {"id": 5, "attributes": {"name": "Inactive nested", "deactivated_at": 1700000000000}},
        ]
        reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, {"calendars": calendars})]))
        reader._authenticated = True
        self.assertEqual([c["id"] for c in reader.read("list_calendars", {})["calendars"]], ["1", "2", "3"])

    def test_invalid_calendar_deactivation_value_is_not_silently_filtered(self):
        for value in (True, "1700000000000", "", [], float("nan"), float("inf")):
            with self.subTest(value=value):
                reader = TimeTreeHttpReader(transport=FakeTransport([HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": value}]})]))
                with self.assertRaisesRegex(ValueError, "deactivated_at"):
                    reader._get_calendars()

    def test_default_event_sync_never_requests_deactivated_calendar(self):
        transport = FakeTransport([
            HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": 123}, {"id": 2, "name": "Active", "deactivated_at": None}]}),
            HttpResponse(200, {}, {"events": [], "chunk": False}),
        ])
        reader = TimeTreeHttpReader(transport=transport);reader._authenticated = True
        result = reader.read("list_events", {"start_date": "2026-10-01", "end_date": "2026-10-31"})
        self.assertTrue(result["sync_complete"])
        self.assertTrue(transport.calls[1]["url"].endswith("/calendar/2/events/sync?since=0"))

    def test_explicit_deactivated_calendar_is_rejected_before_sync(self):
        transport = FakeTransport([HttpResponse(200, {}, {"calendars": [{"id": 1, "deactivated_at": 123}]})])
        reader = TimeTreeHttpReader(transport=transport);reader._authenticated = True
        with self.assertRaisesRegex(ValueError, "Unknown TimeTree calendar_id"):
            reader.read("sync_events", {"calendar_id": "1"})
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
