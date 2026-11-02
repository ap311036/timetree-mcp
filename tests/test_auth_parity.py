"""Exercise production authentication and HTTP machinery over memory sockets.

Only the TLS connection boundary and proxy preflight are substituted. Login,
CSRF extraction, request encoding, cookie handling and HTTPResponse parsing are
the actual implementations used by both public and scoped readers.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import runpy
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and "--production-worker" in sys.argv:
    sys.path.insert(0, str(ROOT / "src"))

from timetree_mcp import http_transport
from timetree_mcp.e2e_worker import ORIGIN, ScopedReader, ScopedTransport
from timetree_mcp.http_reader import TimeTreeHttpReader, UrllibTransport


EMAIL = "auth-parity@example.invalid"
PASSWORD = "SYNTHETIC_AUTH_PARITY_PASSWORD"
SESSION = "SYNTHETIC_AUTH_PARITY_SESSION"
TOKEN = "SYNTHETIC_AUTH_PARITY_CSRF"
PLAN = {"version": 1, "calendar_id": "42", "calendar_name": "EWS",
        "records": [{"source_uuid": "synthetic-parity", "title": "TIMETREE-MCP-TEST PARITY"}],
        "checks": [{"label": "parity", "arguments": {
            "calendar_id": "42", "start_at": "2030-01-01T00:00:00+00:00",
            "end_at": "2030-01-02T00:00:00+00:00", "limit": 100}, "expected": []}]}


def wire(body=b"{}", *, status="200 OK", headers=(), length=None):
    lines = ["HTTP/1.1 " + status, *[f"{key}: {value}" for key, value in headers],
             "Content-Length: " + str(len(body) if length is None else length)]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class NoCredentials:
    def read(self):
        raise AssertionError("parity_stored_credentials_forbidden")
    status = write = clear = read


class MemoryFile(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.body_reads = []

    def read(self, amount=-1):
        self.body_reads.append(amount)
        return super().read(amount)


class MemorySocket:
    def __init__(self, raw):
        self.file = MemoryFile(raw)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(bytes(data))

    def makefile(self, mode):
        if mode != "rb":
            raise AssertionError("parity_socket_mode_invalid")
        return self.file

    def close(self):
        self.closed = True


class WireNetwork:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sockets = []

    def connect(self, connection):
        if (connection.host != "timetreeapp.com" or connection.port != 443
                or connection._tunnel_host is not None
                or not connection._context.check_hostname
                or connection._context.verify_mode != ssl.CERT_REQUIRED):
            raise AssertionError("parity_tls_boundary_invalid")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        connection.sock = MemorySocket(response)
        self.sockets.append(connection.sock)

    def requests(self):
        result = []
        for socket in self.sockets:
            head, body = b"".join(socket.sent).split(b"\r\n\r\n", 1)
            lines = head.decode().split("\r\n")
            headers = tuple((key.lower(), value.strip()) for key, value in
                            (line.split(":", 1) for line in lines[1:]))
            if body:
                body = json.loads(body)
                if lines[0].startswith("PUT /api/v1/auth/email/signin "):
                    if not re.fullmatch(r"[a-f0-9]{32}", body["uuid"]):
                        raise AssertionError("parity_uuid_shape_invalid")
                    body["uuid"] = "<fresh-uuid>"
            result.append((lines[0], headers, body))
        return result


@contextmanager
def executing_shared_code(readers=None):
    watched = {"login": TimeTreeHttpReader.login.__code__,
               "csrf": TimeTreeHttpReader._refresh_csrf.__code__,
               "transport": http_transport.DirectHttpsTransport.request.__code__,
               "http_request": http.client.HTTPConnection.request.__code__,
               "response_parser": http.client.HTTPResponse.begin.__code__}
    counts = dict.fromkeys(watched, 0)
    previous = sys.getprofile()
    def profile(frame, event, value):
        if event == "call":
            for name, code in watched.items():
                if frame.f_code is code:
                    counts[name] += 1
                    if name == "login" and readers is not None:
                        readers.append(frame.f_locals["self"])
    sys.setprofile(profile)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


class AuthenticationParityTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "subprocess.run",
                       "getpass.getpass", "builtins.input"):
            guard = patch(target, side_effect=AssertionError("parity_external_io_forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        clean = patch.dict(os.environ, {}, clear=True)
        clean.start()
        self.addCleanup(clean.stop)
        preflight = patch.object(http_transport, "require_direct_connection", return_value=None)
        preflight.start()
        self.addCleanup(preflight.stop)

    @contextmanager
    def reader(self, scoped, responses):
        network = WireNetwork(responses)
        def connect(connection):
            return network.connect(connection)
        with patch.object(http.client.HTTPSConnection, "connect", connect):
            if scoped:
                transport = ScopedTransport(PLAN, proxy_check=lambda: None)
                reader = ScopedReader(PLAN, EMAIL, PASSWORD, transport)
                self.assertIs(type(transport.delegate), http_transport.DirectHttpsTransport)
            else:
                # The default public constructor must choose the shared core.
                reader = TimeTreeHttpReader(email=EMAIL, password=PASSWORD,
                                            credential_store=NoCredentials())
                self.assertIs(type(reader.transport), http_transport.DirectHttpsTransport)
            try:
                yield reader, network
            finally:
                reader.logout()

    def test_both_readers_inherit_identical_authentication_and_transport_core(self):
        self.assertIs(UrllibTransport, http_transport.DirectHttpsTransport)
        # Scoped login may gate reuse and redact its result; the tests below
        # prove it still executes the original production login code object.
        self.assertIs(ScopedReader._refresh_csrf, TimeTreeHttpReader._refresh_csrf)

    def test_real_auth_wire_and_code_execution_match_for_header_and_meta_csrf(self):
        for header_token in (False, True):
            with self.subTest(header_token=header_token):
                csrf_headers = (("x-CsRf-ToKeN", TOKEN),) if header_token else ()
                page = b"<html></html>" if header_token else f'<meta name="csrf-token" content="{TOKEN}">'.encode()
                replies = [wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),
                                         ("Set-Cookie", "page=synthetic-page; Secure; Path=/signin"),
                                         ("Set-Cookie", "api=synthetic-api; Secure; Path=/api"))),
                           wire(page, headers=csrf_headers), wire(b'{"calendars":[]}')]
                observations = []
                for scoped in (False, True):
                    with self.reader(scoped, replies) as (reader, network), executing_shared_code() as counts:
                        accepted = reader.login()
                        self.assertTrue(accepted["authenticated"] and accepted["csrf_available"])
                        self.assertTrue(reader._csrf_token == TOKEN, "Shared CSRF extraction differed")
                        reader.transport.request("GET", ORIGIN + "/api/v1/calendars?since=0")
                        self.assertEqual(counts, {"login": 1, "csrf": 1, "transport": 3,
                                                  "http_request": 3, "response_parser": 3})
                        self.assertEqual(network.responses, [])
                        observations.append(network.requests())
                self.assertTrue(observations[0] == observations[1], "Public and E2E wire requests differed")
                self.assertEqual([item[0] for item in observations[0]],
                                 ["PUT /api/v1/auth/email/signin HTTP/1.1", "GET /signin HTTP/1.1",
                                  "GET /api/v1/calendars?since=0 HTTP/1.1"])
                page_cookie = dict(observations[0][1][1]).get("cookie", "")
                api_cookie = dict(observations[0][2][1]).get("cookie", "")
                self.assertTrue("page=synthetic-page" in page_cookie and "api=synthetic-api" not in page_cookie)
                self.assertTrue("api=synthetic-api" in api_cookie and "page=synthetic-page" not in api_cookie)

    def test_auth_failures_share_wire_state_cleanup_and_no_retry(self):
        csrf_ok = wire(f'<meta name="csrf-token" content="{TOKEN}">'.encode())
        session_header = (("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)
        cases = [
            ("sign_in", [wire(b"", headers=session_header), csrf_ok]),
            ("sign_in", [wire(b"[]", headers=session_header), csrf_ok]),
            ("sign_in", [wire(b"plain text", headers=session_header), csrf_ok]),
            ("sign_in", [wire(b"{}", status="204 No Content", headers=session_header), csrf_ok]),
            ("sign_in", [wire(status="401 Unauthorized", headers=session_header), csrf_ok]),
            ("sign_in", [wire(status="302 Found", headers=(("Location", "/signin"),)), csrf_ok]),
            ("sign_in", [wire(b"{}", length=9, headers=session_header), csrf_ok]),
            ("csrf_fetch", [wire(headers=session_header), wire(b"<html></html>"), csrf_ok]),
            ("csrf_fetch", [wire(headers=session_header), wire(b"", status="403 Forbidden"), csrf_ok]),
            ("csrf_fetch", [wire(headers=session_header), wire(status="302 Found", headers=(("Location", "/signin"),)), csrf_ok]),
            ("csrf_fetch", [wire(headers=session_header), wire(b"\xff"), csrf_ok]),
        ]
        for index, (phase, replies) in enumerate(cases):
            with self.subTest(case=index):
                observations = []
                for scoped in (False, True):
                    with self.reader(scoped, replies) as (reader, network), executing_shared_code() as counts:
                        with self.assertRaises(Exception):
                            reader.login()
                        request_count = 1 if phase == "sign_in" else 2
                        self.assertEqual(counts["login"], 1)
                        self.assertEqual(counts["csrf"], int(phase == "csrf_fetch"))
                        self.assertEqual(counts["transport"], request_count)
                        self.assertEqual(len(network.sockets), request_count)
                        self.assertEqual(len(network.responses), 1)
                        self.assertFalse(reader._authenticated)
                        self.assertIsNone(reader._csrf_token)
                        self.assertEqual(list(reader.transport.cookie_jar), [])
                        self.assertEqual(reader.login_diagnostic["phase"], phase)
                        observations.append((network.requests(), reader.login_diagnostic))
                self.assertTrue(observations[0] == observations[1],
                                "Public and E2E failure requests or diagnostics differed")

    def test_one_calendar_hop_has_identical_wire_and_ignores_redirect_credentials(self):
        for status in (301, 302, 303, 307, 308):
            for target in ("/calendars", ORIGIN + "/calendars"):
                with self.subTest(status=status, absolute=target.startswith("https:")):
                    final_page = (wire(b"<html></html>", headers=(("x-CsRf-ToKeN", TOKEN),))
                                  if status in (303, 308)
                                  else wire(f'<meta name="csrf-token" content="{TOKEN}">'.encode()))
                    replies = [
                        wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                        wire(b'<meta name="csrf-token" content="synthetic-poison">',
                             status=f"{status} Redirect", headers=(("Location", target),
                             ("Set-Cookie", "session=synthetic-poison; Secure; Path=/"),
                             ("Set-Cookie", "poison=synthetic; Secure; Path=/"),
                             ("X-CSRF-Token", "synthetic-poison"))),
                        final_page,
                    ]
                    observations = []
                    for scoped in (False, True):
                        with self.reader(scoped, replies) as (reader, network), executing_shared_code() as counts:
                            accepted = reader.login()
                            self.assertTrue(accepted["authenticated"] and accepted["csrf_available"])
                            self.assertTrue(reader._csrf_token == TOKEN, "Redirect token displaced final token")
                            self.assertEqual(counts, {"login": 1, "csrf": 1, "transport": 3,
                                                      "http_request": 3, "response_parser": 3})
                            self.assertEqual(network.sockets[1].file.body_reads, [])
                            self.assertEqual(network.responses, [])
                            requests = network.requests()
                            self.assertEqual([item[0] for item in requests], [
                                "PUT /api/v1/auth/email/signin HTTP/1.1", "GET /signin HTTP/1.1",
                                "GET /calendars HTTP/1.1"])
                            final_headers = dict(requests[2][1])
                            self.assertTrue(final_headers.get("cookie") == "session=" + SESSION,
                                            "Redirect cookies affected the follow-up")
                            self.assertNotIn("x-csrf-token", final_headers)
                            self.assertEqual(requests[2][2], b"")
                            self.assertEqual(len(list(reader.transport.cookie_jar)), 1)
                            observations.append(requests)
                    self.assertTrue(observations[0] == observations[1], "Calendar-hop wire requests differed")

    def test_calendar_hop_final_failures_never_retry_or_follow_a_second_redirect(self):
        cases = [
            (wire(b"<html></html>"), "csrf_missing"),
            (wire(b"\xff"), "invalid_response"),
            (wire(status="401 Unauthorized"), "http_401"),
            (wire(status="403 Forbidden"), "http_403"),
            (wire(status="500 Error"), "http_5xx"),
        ]
        for status, target in ((301, "/calendars"), (302, "/signin"),
                               (303, ORIGIN + "/calendars"), (307, "/intl/en/"),
                               (308, "https://other.invalid/")):
            cases.append((wire(status=f"{status} Redirect", headers=(("Location", target),)),
                          "http_redirect_rejected"))
        for index, (final, reason) in enumerate(cases):
            with self.subTest(case=index):
                replies = [
                    wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
                    wire(b'<meta name="csrf-token" content="synthetic-poison">', status="302 Redirect",
                         headers=(("Location", "/calendars"), ("X-CSRF-Token", "synthetic-poison"),
                                  ("Set-Cookie", "session=synthetic-poison; Secure; Path=/"))),
                    final, wire(b"{}"),
                ]
                observations = []
                for scoped in (False, True):
                    with self.reader(scoped, replies) as (reader, network), executing_shared_code() as counts:
                        with self.assertRaises(Exception):
                            reader.login()
                        self.assertEqual(counts, {"login": 1, "csrf": 1, "transport": 3,
                                                  "http_request": 3, "response_parser": 3})
                        self.assertEqual(len(network.responses), 1)
                        self.assertFalse(reader._authenticated)
                        self.assertIsNone(reader._csrf_token)
                        self.assertEqual(list(reader.transport.cookie_jar), [])
                        self.assertEqual(reader.login_diagnostic["phase"], "csrf_fetch")
                        self.assertEqual(reader.login_diagnostic["reason"], reason)
                        self.assertEqual(network.sockets[1].file.body_reads, [])
                        if reason.startswith("http_"):
                            self.assertEqual(network.sockets[2].file.body_reads, [])
                        requests = network.requests()
                        self.assertEqual([item[0] for item in requests], [
                            "PUT /api/v1/auth/email/signin HTTP/1.1", "GET /signin HTTP/1.1",
                            "GET /calendars HTTP/1.1"])
                        observations.append((requests, reader.login_diagnostic))
                self.assertTrue(observations[0] == observations[1], "Calendar-hop failures differed")

    def test_scoped_calendar_grant_is_consumed_and_does_not_permit_extra_login(self):
        replies = [wire(), wire(status="302 Redirect", headers=(("Location", "/calendars"),)),
                   wire(f'<meta name="csrf-token" content="{TOKEN}">'.encode())]
        with self.reader(True, replies) as (reader, network):
            reader.login()
            with executing_shared_code() as counts:
                for method, url, body in (("GET", ORIGIN + "/calendars", None),
                                          ("PUT", ORIGIN + "/api/v1/auth/email/signin",
                                           {"uid": EMAIL, "password": PASSWORD, "uuid": "0" * 32})):
                    with self.assertRaises(Exception):
                        reader.transport.request(method, url, body)
                self.assertEqual(counts["transport"], 0)
            self.assertEqual(len(network.sockets), 3)

    def test_scope_denials_never_enter_shared_transport_or_socket(self):
        for method, target, body in (("GET", ORIGIN + "/", None),
                                     ("GET", ORIGIN + "/calendars", None),
                                     ("GET", ORIGIN + "/signin?", None),
                                     ("GET", "https://other.invalid/signin", None),
                                     ("POST", ORIGIN + "/api/v1/calendar/42/event", {})):
            with self.subTest(method=method, target=target):
                with self.reader(True, []) as (reader, network), executing_shared_code() as counts:
                    with self.assertRaises(Exception):
                        reader.transport.request(method, target, body)
                    self.assertEqual(counts["transport"], 0)
                    self.assertEqual(network.sockets, [])

    def test_actual_stdio_worker_runs_production_authentication_and_scope(self):
        self.check_actual_stdio_worker(calendar_hop=False)

    def test_actual_stdio_worker_runs_single_calendar_hop_and_scope(self):
        self.check_actual_stdio_worker(calendar_hop=True)

    def check_actual_stdio_worker(self, *, calendar_hop):
        from timetree_mcp import e2e_runner
        with tempfile.TemporaryDirectory(prefix="timetree-auth-parity-synthetic-") as directory:
            plan_path = Path(directory) / "plan.json"
            raw = json.dumps(PLAN).encode()
            descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                       "--production-worker", str(ROOT / "scripts/e2e_stdio.py"), "--worker",
                       "--plan", str(plan_path), "--sha256", hashlib.sha256(raw).hexdigest()]
            if calendar_hop:
                command.append("--synthetic-calendar-hop")
            with patch.object(e2e_runner, "_restore_terminal_snapshot", return_value=lambda: None):
                result = e2e_runner._drive(PLAN, command, timeout=10)
        self.assertEqual(result, {"status": "passed", "stage": "complete", "checks_passed": 1,
                                  "scope": "planned_test_records", "transport": "stdio"})
        self.assertFalse(any(value in json.dumps(result) for value in (EMAIL, PASSWORD, SESSION, TOKEN)))


def production_worker_bootstrap():
    """Keep all production MCP/auth code; substitute only I/O boundaries."""
    from timetree_mcp import e2e_worker
    from timetree_mcp.occurrence_rules import parse_occurrence_time
    calendar_hop = "--synthetic-calendar-hop" in sys.argv
    if calendar_hop:
        sys.argv.remove("--synthetic-calendar-hop")
    if Path(sys.argv[2]).resolve() != ROOT / "scripts/e2e_stdio.py":
        return 1
    sys.argv = sys.argv[2:]

    def forbidden(*args, **kwargs):
        raise AssertionError("parity_external_io_forbidden")
    def audit(event, args):
        if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            forbidden()
        if event == "open":
            target, mode, flags = args
            if target == "/dev/tty" or (isinstance(flags, int) and flags &
                    (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                forbidden()
    sys.addaudithook(audit)

    class SyntheticTerminal(io.StringIO):
        def isatty(self):
            return True
    prompts = []
    def open_terminal(path, mode, **kwargs):
        if path != "/dev/tty" or mode != "w":
            forbidden()
        return SyntheticTerminal()
    def hidden_input(prompt, *, stream):
        if not isinstance(stream, SyntheticTerminal):
            forbidden()
        expected = ("TimeTree email (hidden): ", "TimeTree password (hidden): ")
        if len(prompts) >= 2 or prompt != expected[len(prompts)]:
            forbidden()
        prompts.append(True)
        return EMAIL if len(prompts) == 1 else PASSWORD

    event = {"id": "synthetic-parity", "uuid": "synthetic-parity", "calendar_id": "42",
             "title": "TIMETREE-MCP-TEST PARITY", "all_day": False, "category": 1, "type": 0,
             "start_at": parse_occurrence_time("2030-01-03T09:00:00+08:00"),
             "end_at": parse_occurrence_time("2030-01-03T09:30:00+08:00"),
             "start_timezone": "Asia/Taipei", "end_timezone": "Asia/Taipei",
             "recurrences": [], "parent_id": None, "recurring_uuid": None}
    replies = [
        wire(headers=(("Set-Cookie", "session=" + SESSION + "; Secure; Path=/"),)),
        wire(f'<meta name="csrf-token" content="{TOKEN}">'.encode()),
        wire(b'{"calendars":[{"id":"42","name":"EWS"}]}'),
        wire(json.dumps({"events": [event], "chunk": 0}).encode()),
    ]
    if calendar_hop:
        replies.insert(1, wire(b'<meta name="csrf-token" content="synthetic-poison">',
            status="302 Redirect", headers=(("Location", "/calendars"),
            ("X-CSRF-Token", "synthetic-poison"),
            ("Set-Cookie", "session=synthetic-poison; Secure; Path=/"))))
    network = WireNetwork(replies)
    def connect(connection):
        return network.connect(connection)
    readers = []
    try:
        # Defaults are input-provider seams only; prompt_credentials itself,
        # run_worker, main, login and both HTTP layers remain real code.
        defaults = {"tty_opener": open_terminal, "prompt": hidden_input}
        with patch.object(e2e_worker.prompt_credentials, "__kwdefaults__", defaults), \
             patch.object(http_transport, "require_direct_connection", return_value=None), \
             patch.object(http.client.HTTPSConnection, "connect", connect), \
             executing_shared_code(readers) as counts:
            try:
                runpy.run_path(sys.argv[0], run_name="__main__")
            except SystemExit as stopped:
                code = stopped.code
            else:
                code = 1
        assert code == 0 and prompts == [True, True]
        expected_requests = 5 if calendar_hop else 4
        assert counts == {"login": 1, "csrf": 1, "transport": expected_requests,
                          "http_request": expected_requests, "response_parser": expected_requests}
        assert len(readers) == 1 and not network.responses
        reader = readers[0]
        assert reader.email is reader.password is reader._csrf_token is None
        assert not reader._authenticated and reader.transport._closed
        assert not list(reader.transport.cookie_jar)
        paths = [
            "PUT /api/v1/auth/email/signin HTTP/1.1", "GET /signin HTTP/1.1",
            "GET /api/v1/calendars?since=0 HTTP/1.1", "GET /api/v1/calendar/42/events/sync?since=0 HTTP/1.1"]
        if calendar_hop:
            paths.insert(2, "GET /calendars HTTP/1.1")
            assert not network.sockets[1].file.body_reads
            assert dict(network.requests()[2][1])["cookie"] == "session=" + SESSION
        assert [item[0] for item in network.requests()] == paths
        return 0
    except BaseException:
        sys.stderr.write("parity_production_worker_failed\n")
        return 1


if __name__ == "__main__":
    if "--production-worker" in sys.argv:
        raise SystemExit(production_worker_bootstrap())
    unittest.main()
