"""Synthetic subprocess fixture: no real network, credentials, or proxy access.

Secrets below are deliberate test sentinels, never real account information.
This file is not shipped in the npm package and is not a production CLI mode.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from timetree_mcp.http_reader import HttpResponse
from timetree_mcp.occurrence_rules import parse_occurrence_time

EMAIL = "stdio-fixture@example.invalid"
PASSWORD = "SYNTHETIC_PASSWORD_DO_NOT_PRINT_93a0"
SESSION = "SYNTHETIC_SESSION_DO_NOT_PRINT_89f1"
PRIVATE_NOTE = "SYNTHETIC_UNRELATED_PRIVATE_EVENT_723b"
PARENT_ENV = "SYNTHETIC_PARENT_ENV_DO_NOT_INHERIT_ba42"


def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
        raise RuntimeError("fixture_external_io_forbidden")
    if event == "open":
        flags = args[2] if len(args) > 2 else 0
        mode = args[1] if len(args) > 1 else None
        if flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_WRONLY | os.O_RDWR):
            raise RuntimeError("fixture_file_write_forbidden")
        if isinstance(mode, str) and any(char in mode for char in "wax+"):
            raise RuntimeError("fixture_file_write_forbidden")


def source_event():
    return {
        "id": "test-dst", "uuid": "test-dst", "calendar_id": "42",
        "title": "TIMETREE-MCP-TEST-DST", "all_day": False,
        "category": 1, "type": 0,
        "start_at": parse_occurrence_time("2030-03-08T09:00:00-05:00"),
        "end_at": parse_occurrence_time("2030-03-08T09:30:00-05:00"),
        "start_timezone": "America/New_York", "end_timezone": "America/New_York",
        "recurrences": ["RRULE:FREQ=DAILY;COUNT=3"],
        "parent_id": None, "recurring_uuid": "test-dst",
    }


class FakeTransport:
    def __init__(self, plan, mode):
        self.plan = plan
        self.mode = mode
        self.login_calls = 0
        self.csrf_calls = 0
        self.sync_calls = 0
        self.cleared = False
        self.forbidden_requests = 0

    def request(self, method, url, body=None, headers=None, timeout=None):
        if method == "PUT" and url == "https://timetreeapp.com/api/v1/auth/email/signin":
            self.login_calls += 1
            if self.login_calls != 1 or body.get("uid") != EMAIL or body.get("password") != PASSWORD:
                raise AssertionError("fixture_login_contract_failed")
            if self.mode == "login_error":
                raise RuntimeError(PASSWORD + SESSION + PRIVATE_NOTE)
            if self.mode.startswith("login_reason_"):
                from timetree_mcp.e2e_worker import WorkerError
                raise WorkerError("e2e_" + self.mode.removeprefix("login_reason_"))
            return HttpResponse(200, {"set-cookie": "_session=" + SESSION}, {})
        if method == "GET" and url == "https://timetreeapp.com/signin":
            self.csrf_calls += 1
            if self.login_calls != 1 or self.csrf_calls != 1:
                raise AssertionError("fixture_csrf_order_failed")
            if self.mode.startswith("csrf_redirect_"):
                from timetree_mcp.e2e_worker import WorkerError
                self.csrf_redirect_target = self.mode.removeprefix("csrf_redirect_")
                raise WorkerError("e2e_http_redirect_rejected", redirect_target=self.csrf_redirect_target)
            if self.mode.startswith("csrf_reason_"):
                from timetree_mcp.e2e_worker import WorkerError
                raise WorkerError("e2e_" + self.mode.removeprefix("csrf_reason_"))
            if self.mode == "csrf_missing":
                return HttpResponse(200, {}, "<html>" + PASSWORD + SESSION + "</html>")
            if self.mode == "csrf_meta":
                return HttpResponse(200, {}, '<meta name="csrf-token" content="' + SESSION + '">')
            return HttpResponse(200, {"x-csrf-token": SESSION}, "")
        if method == "GET" and url == "https://timetreeapp.com/api/v1/calendars?since=0":
            calendar_id = "43" if self.mode == "wrong_calendar" else "42"
            name = "Other" if self.mode == "wrong_calendar_name" else "EWS"
            return HttpResponse(200, {}, {"calendars": [{"id": calendar_id, "name": name},
                                                        {"id": "99", "name": PRIVATE_NOTE}]})
        if method == "GET" and url == "https://timetreeapp.com/api/v1/calendar/42/events/sync?since=0":
            self.sync_calls += 1
            if self.mode == "read_error":
                raise RuntimeError(PASSWORD + SESSION + PRIVATE_NOTE)
            event = source_event()
            if self.mode == "renamed":
                event["title"] = PRIVATE_NOTE
            if self.mode == "wrong_event_calendar":
                event["calendar_id"] = "43"
            unrelated = {**source_event(), "id": "private-unrelated", "uuid": "private-unrelated",
                         "title": PRIVATE_NOTE, "note": PRIVATE_NOTE,
                         "recurring_uuid": "private-unrelated",
                         "start_timezone": "Invalid/Private_Zone",
                         "end_timezone": "Invalid/Private_Zone",
                         "recurrences": ["RRULE:FREQ=HOURLY;COUNT=3"]}
            events = [unrelated] if self.mode == "missing" else [event, unrelated]
            if self.mode == "unplanned_child":
                events.append({**source_event(), "id": "unplanned-child", "uuid": "unplanned-child",
                               "title": PRIVATE_NOTE, "recurrences": [],
                               "parent_id": "test-dst", "recurring_uuid": "test-dst"})
            return HttpResponse(200, {}, {"events": copy.deepcopy(events),
                                         "chunk": self.mode == "incomplete", "since": 0})
        self.forbidden_requests += 1
        raise AssertionError("fixture_unexpected_request")

    def clear(self):
        self.cleared = True


def main():
    sys.addaudithook(audit)
    forbidden = {"TIMETREE_EMAIL", "TIMETREE_PASSWORD", "TIMETREE_PASSWORD_FILE",
                 "TIMETREE_BASE_URL", "PYTHONPATH", "AWS_SECRET_ACCESS_KEY", "E2E_PARENT_SENTINEL"}
    if forbidden.intersection(os.environ) or PARENT_ENV in json.dumps(dict(os.environ)):
        return 41
    from timetree_mcp.e2e_runner import load_plan
    from timetree_mcp.e2e_worker import run_worker
    plan_path, digest, mode = sys.argv[1:]
    plan = load_plan(plan_path, digest)
    if mode in {"hang", "ignore_signals"}:
        if mode == "ignore_signals":
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            signal.pause()
    if mode == "stderr_secret":
        sys.stderr.write(PASSWORD + SESSION + PRIVATE_NOTE + "\n")
        sys.stderr.flush()
    if mode == "stderr_flood":
        sys.stderr.write((PASSWORD + "\n") * 1000)
        sys.stderr.flush()
    if mode == "stdout_secret":
        sys.stdout.write(PASSWORD + SESSION + PRIVATE_NOTE + "\n")
        sys.stdout.flush()
        return 0
    transport = FakeTransport(plan, mode)
    run_worker(plan, credential_prompt=lambda: (EMAIL, PASSWORD),
               transport_factory=lambda _: transport)
    if not transport.cleared:
        return 42
    if transport.forbidden_requests:
        return 44
    if mode == "happy" and (transport.login_calls != 1 or transport.csrf_calls != 1):
        return 48
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        # The fixture itself must not turn an assertion's context into a leak.
        raise SystemExit(43) from None
