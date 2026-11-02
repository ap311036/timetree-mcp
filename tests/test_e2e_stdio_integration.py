"""Real subprocess and MCP stdio integration using only synthetic credentials."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from timetree_mcp.e2e_runner import _drive, validate_plan


FIXTURE = Path(__file__).with_name("e2e_fixture_worker.py")
SENTINELS = (
    "stdio-fixture@example.invalid",
    "SYNTHETIC_PASSWORD_DO_NOT_PRINT_93a0",
    "SYNTHETIC_SESSION_DO_NOT_PRINT_89f1",
    "SYNTHETIC_UNRELATED_PRIVATE_EVENT_723b",
    "SYNTHETIC_PARENT_ENV_DO_NOT_INHERIT_ba42",
)


def fixture_plan():
    rows = []
    for day, utc_hour, offset in ((8, 14, "-05:00"), (9, 14, "-05:00"), (10, 13, "-04:00")):
        rows.append({
            "event_uuid": "test-dst", "series_uuid": "test-dst", "source": "generated",
            "timezone": "America/New_York",
            "start_at": f"2030-03-{day:02d}T{utc_hour:02d}:00:00+00:00",
            "end_at": f"2030-03-{day:02d}T{utc_hour:02d}:30:00+00:00",
            "start_local": f"2030-03-{day:02d}T09:00:00{offset}",
            "end_local": f"2030-03-{day:02d}T09:30:00{offset}",
        })
    return {
        "version": 1, "calendar_id": "42", "calendar_name": "EWS",
        "records": [{"source_uuid": "test-dst", "title": "TIMETREE-MCP-TEST-DST"}],
        "checks": [{"label": "dst", "arguments": {
            "calendar_id": "42", "start_at": "2030-03-08T00:00:00+00:00",
            "end_at": "2030-03-11T23:00:00+00:00", "limit": 100,
        }, "expected": rows}],
    }


class StdioRunnerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="timetree-synthetic-e2e-")
        self.addCleanup(self.temp.cleanup)
        self.plan = validate_plan(fixture_plan())
        self.path = Path(self.temp.name) / "plan.json"
        self.path.write_text(json.dumps(self.plan), encoding="utf-8")
        self.path.chmod(0o600)
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def command(self, mode="happy"):
        return [sys.executable, "-I", "-B", str(FIXTURE), str(self.path), self.digest, mode]

    def drive(self, mode="happy", timeout=5):
        stdout, stderr = io.StringIO(), io.StringIO()
        before = set(Path(self.temp.name).iterdir())
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = _drive(copy.deepcopy(self.plan), self.command(mode), timeout=timeout)
        output = stdout.getvalue() + stderr.getvalue() + json.dumps(result)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, output)
        self.assertEqual(set(Path(self.temp.name).iterdir()), before)
        self.assertIn(result["status"], {"passed", "failed"})
        self.assertEqual(result["scope"], "planned_test_records")
        self.assertEqual(result["transport"], "stdio")
        return result

    def test_real_stdio_login_scoped_query_dst_and_logout(self):
        result = self.drive()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks_passed"], 1)

    def test_fixed_signin_page_meta_csrf_supports_complete_scoped_stdio_flow(self):
        result = self.drive("csrf_meta")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks_passed"], 1)
        process = self.raw_wire("csrf_meta")
        self.assertEqual(process.returncode, 0)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        query = next(item for item in replies if item.get("id") == 6)
        self.assertFalse(query["result"]["isError"])

    def test_private_unrelated_record_does_not_affect_fixture_projection(self):
        # The fixture includes an unsupported unrelated series and private note.
        # Success proves it was excluded before the recurrence query's diagnostics.
        self.assertEqual(self.drive()["status"], "passed")

    def test_missing_renamed_incomplete_or_wrong_calendar_sources_fail(self):
        for mode in ("missing", "renamed", "incomplete", "wrong_calendar",
                     "wrong_calendar_name", "wrong_event_calendar", "unplanned_child"):
            with self.subTest(mode=mode):
                result = self.drive(mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["checks_passed"], 0)
                self.assertIn(result["error"], {"mcp_tool_failed", "stdio_protocol_invalid"})

    def test_sensitive_parent_environment_is_not_inherited(self):
        secret = SENTINELS[-1]
        launches = []
        original_popen = subprocess.Popen

        def checked_popen(command, **kwargs):
            launches.append((command, kwargs["env"]))
            return original_popen(command, **kwargs)

        with patch.dict(os.environ, {
            "TIMETREE_EMAIL": secret, "TIMETREE_PASSWORD": secret,
            "TIMETREE_PASSWORD_FILE": secret, "TIMETREE_BASE_URL": "https://invalid.example/",
            "PYTHONPATH": secret, "AWS_SECRET_ACCESS_KEY": secret, "E2E_PARENT_SENTINEL": secret,
        }), patch("timetree_mcp.e2e_runner.subprocess.Popen", side_effect=checked_popen):
            self.assertEqual(self.drive()["status"], "passed")
        self.assertEqual(len(launches), 1)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, json.dumps(launches))

    def test_native_expected_row_mismatch_is_not_reported_as_success(self):
        expected = self.plan["checks"][0]["expected"][0]
        for key in ("start_at", "end_at", "start_local", "end_local"):
            expected[key] = expected[key].replace(":00:00", ":01:00").replace(":30:00", ":31:00")
        result = self.drive()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checks_passed"], 0)
        self.assertEqual(result["error"], "fixture_readback_mismatch")

    def test_transport_exception_details_never_escape_worker_or_driver(self):
        for mode in ("login_error", "read_error"):
            with self.subTest(mode=mode):
                result = self.drive(mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "login_failed" if mode == "login_error" else "mcp_tool_failed")
                if mode == "login_error":
                    self.assertEqual(result["login_diagnostic"],
                                     {"phase": "sign_in", "reason": "internal_error"})
                else:
                    self.assertNotIn("login_diagnostic", result)

    def test_fixed_login_diagnostics_survive_real_stdio_without_secrets(self):
        cases = [("login_reason_" + reason, "sign_in", reason) for reason in (
            "http_401", "http_403", "http_429", "http_redirect_rejected", "http_5xx",
            "tls_failed", "network_timeout", "invalid_response", "response_limit")]
        cases += [("csrf_reason_http_redirect_rejected", "csrf_fetch", "http_redirect_rejected"),
                  ("csrf_reason_network_failed", "csrf_fetch", "network_failed"),
                  ("csrf_missing", "csrf_fetch", "csrf_missing")]
        for mode, phase, reason in cases:
            with self.subTest(mode=mode):
                result = self.drive(mode)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["stage"], "login")
                self.assertEqual(result["checks_passed"], 0)
                self.assertEqual(result["error"], "login_failed")
                self.assertEqual(result["login_diagnostic"], {"phase": phase, "reason": reason})
                wire = self.raw_wire(mode)
                replies = [json.loads(line) for line in wire.stdout.splitlines()]
                login = next(item for item in replies if item.get("id") == 4)
                self.assertTrue(login["result"]["isError"])
                self.assertEqual(login["result"]["structuredContent"],
                                 {"login_diagnostic": {"phase": phase, "reason": reason}})
                later = next(item for item in replies if item.get("id") == 6)
                self.assertTrue("error" in later or later.get("result", {}).get("isError"))
                self.assertNotIn("login_diagnostic", later.get("result", {}).get("structuredContent", {}))

    def test_csrf_redirect_classes_cross_stdio_without_raw_destination(self):
        for target in ("public_landing", "signin", "calendars", "other_or_missing",
                       "SYNTHETIC_SESSION_DO_NOT_PRINT_89f1"):
            with self.subTest(target=target):
                result = self.drive("csrf_redirect_" + target)
                expected = {"phase": "csrf_fetch", "reason": "http_redirect_rejected"}
                if target != "SYNTHETIC_SESSION_DO_NOT_PRINT_89f1":
                    expected["redirect_target"] = target
                self.assertEqual(result["error"], "login_failed")
                self.assertEqual(result["checks_passed"], 0)
                self.assertEqual(result["login_diagnostic"], expected)
                wire = self.raw_wire("csrf_redirect_" + target)
                responses = [json.loads(line) for line in wire.stdout.splitlines()]
                login = next(item for item in responses if item.get("id") == 4)
                self.assertEqual(login["result"]["structuredContent"], {"login_diagnostic": expected})

    def wire_messages(self):
        return [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "synthetic-e2e-wire", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "timetree_login", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                "name": "timetree_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                "name": "timetree_list_occurrences", "arguments": self.plan["checks"][0]["arguments"]}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                "name": "timetree_logout", "arguments": {}}},
        ]

    def raw_wire(self, mode="happy", messages=None):
        result = subprocess.run(self.command(mode),
            input="".join(json.dumps(item) + "\n" for item in (messages or self.wire_messages())),
            text=True, capture_output=True, timeout=5,
            env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result

    def test_raw_mcp_wire_contains_no_credentials_session_or_other_calendar_content(self):
        for mode in ("happy", "login_error", "read_error", "renamed", "unplanned_child"):
            with self.subTest(mode=mode):
                process = self.raw_wire(mode)
                self.assertEqual(process.returncode, 0)
                responses = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertTrue(responses)
                if mode == "happy":
                    query = next(item for item in responses if item.get("id") == 6)
                    self.assertFalse(query["result"]["isError"])
                    self.assertEqual(query["result"]["structuredContent"]["count"], 3)

    def test_raw_mcp_write_and_keychain_calls_are_rejected_before_transport(self):
        messages = self.wire_messages()
        messages[6:6] = [
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call", "params": {
                "name": "timetree_delete_event", "arguments": {
                    "calendar_id": "42", "event_uuid": "test-dst", "confirm": True}}},
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {
                "name": "timetree_keychain_status", "arguments": {}}},
        ]
        process = self.raw_wire(messages=messages)
        self.assertEqual(process.returncode, 0)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        for identifier in (20, 21):
            reply = next(item for item in responses if item.get("id") == identifier)
            self.assertTrue("error" in reply or reply.get("result", {}).get("isError"))

    def test_worker_stderr_and_invalid_stdout_are_never_forwarded(self):
        for mode in ("stderr_secret", "stderr_flood", "stdout_secret"):
            with self.subTest(mode=mode):
                result = self.drive(mode)
                if mode != "stderr_secret":
                    self.assertEqual(result["status"], "failed")

    def test_timeout_reaps_the_worker_and_returns_only_safe_code(self):
        original_popen = subprocess.Popen
        children = []

        def recording_popen(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        for mode in ("hang", "ignore_signals"):
            with self.subTest(mode=mode):
                children.clear()
                started = time.monotonic()
                with patch("timetree_mcp.e2e_runner.subprocess.Popen", side_effect=recording_popen):
                    result = self.drive(mode, timeout=0.2)
                self.assertLess(time.monotonic() - started, 4)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"], "runner_timeout")
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())


if __name__ == "__main__":
    unittest.main()
