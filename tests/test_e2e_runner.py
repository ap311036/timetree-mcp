import hashlib
import json
import os
import signal
import sys
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from timetree_mcp.e2e_runner import RunnerFailure, _Channel, _cleanup_worker, _drive, _restore_terminal_snapshot, load_plan, validate_plan, worker_environment
from timetree_mcp import e2e_runner


def sample_plan():
    return {"version": 1, "calendar_id": "42", "calendar_name": "EWS",
            "records": [{"source_uuid": "test-series", "title": "TIMETREE-MCP-TEST-DST"}],
            "checks": [{"label": "dst", "arguments": {"calendar_id": "42",
                "start_at": "2030-03-08T00:00:00+00:00", "end_at": "2030-03-09T00:00:00+00:00",
                "limit": 100}, "expected": [{"event_uuid": "test-series", "series_uuid": "test-series",
                "start_at": "2030-03-08T14:00:00+00:00", "end_at": "2030-03-08T14:30:00+00:00",
                "start_local": "2030-03-08T09:00:00-05:00", "end_local": "2030-03-08T09:30:00-05:00",
                "timezone": "America/New_York", "source": "generated"}]}]}


class E2ePlanTests(unittest.TestCase):
    def test_login_diagnostics_accept_only_closed_non_sensitive_schema(self):
        for phase in ("sign_in", "csrf_fetch"):
            for reason in ("http_401", "http_403", "http_429", "http_4xx", "http_5xx",
                           "http_redirect_rejected", "http_rejected", "tls_failed",
                           "network_timeout", "network_failed", "invalid_response",
                           "response_limit", "session_limit", "request_denied", "internal_error"):
                original = {"phase": phase, "reason": reason}
                valid = e2e_runner.safe_login_diagnostic(original)
                self.assertEqual(valid, original)
                self.assertIsNot(valid, original)
        self.assertEqual(e2e_runner.safe_login_diagnostic(
            {"phase": "csrf_fetch", "reason": "csrf_missing"}),
            {"phase": "csrf_fetch", "reason": "csrf_missing"})
        canary = "synthetic-private-error-do-not-echo"
        invalid = [None, [], canary, {}, {"phase": "sign_in", "reason": "csrf_missing"},
                   {"phase": canary, "reason": "http_401"}, {"phase": "sign_in", "reason": canary},
                   {"phase": "sign_in", "reason": "http_401", "body": canary},
                   {"phase": [], "reason": "http_401"}, {"phase": "sign_in", "reason": {}},
                   {"phase": True, "reason": "http_401"}]
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(e2e_runner.safe_login_diagnostic(value))

    def test_redirect_target_is_allowed_only_for_csrf_redirect_failure(self):
        for target in ("public_landing", "signin", "calendars", "other_or_missing"):
            diagnostic = {"phase": "csrf_fetch", "reason": "http_redirect_rejected",
                          "redirect_target": target}
            self.assertEqual(e2e_runner.safe_login_diagnostic(diagnostic), diagnostic)
            self.assertIsNone(e2e_runner.safe_login_diagnostic({**diagnostic, "phase": "sign_in"}))
            self.assertIsNone(e2e_runner.safe_login_diagnostic({**diagnostic, "reason": "http_403"}))
        for target in (None, [], {}, "https://timetreeapp.com/", "synthetic-private-location"):
            self.assertIsNone(e2e_runner.safe_login_diagnostic({
                "phase": "csrf_fetch", "reason": "http_redirect_rejected", "redirect_target": target}))
        self.assertIsNone(e2e_runner.safe_login_diagnostic({
            "phase": "csrf_fetch", "reason": "http_redirect_rejected",
            "redirect_target": "signin", "location": "synthetic-private-location"}))

    def test_driver_exports_only_validated_login_failure_diagnostic(self):
        canary = "synthetic-private-error-do-not-echo"
        good = {"phase": "sign_in", "reason": "http_403"}
        valid_redirect = {"phase": "csrf_fetch", "reason": "http_redirect_rejected", "redirect_target": "signin"}
        cases = [good, valid_redirect, {**valid_redirect, "redirect_target": canary},
                 {**valid_redirect, "phase": "sign_in"},
                 {**good, "body": canary}, {"phase": "sign_in", "reason": canary},
                 {"phase": "sign_in", "reason": "csrf_missing"}, None]
        for diagnostic in cases:
            responses = {
                "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {
                    "name": "timetree-mcp", "version": e2e_runner.SERVER_VERSION}},
                "tools/list": {"tools": [{"name": item["name"]} for item in
                                         e2e_runner.TimeTreeMcpServer.tool_definitions()]},
                "ping": {}, "tools/call": {"isError": True,
                    "structuredContent": {"error": canary, "login_diagnostic": diagnostic},
                    "content": [{"type": "text", "text": canary}]},
            }
            script = ("import json,sys\nresponses=json.loads(sys.argv[1])\n"
                      "for line in sys.stdin:\n"
                      " message=json.loads(line)\n"
                      " if 'id' not in message: continue\n"
                      " print(json.dumps({'jsonrpc':'2.0','id':message['id'],"
                      "'result':responses[message['method']]}),flush=True)\n")
            with self.subTest(diagnostic=diagnostic), patch.dict(os.environ, {}, clear=True):
                result = _drive(sample_plan(), [sys.executable, "-I", "-B", "-c", script,
                                               json.dumps(responses)], timeout=3)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "login")
            self.assertEqual(result["checks_passed"], 0)
            self.assertNotIn(canary, json.dumps(result))
            if diagnostic in (good, valid_redirect):
                self.assertEqual(result["error"], "login_failed")
                self.assertEqual(result["login_diagnostic"], diagnostic)
            else:
                self.assertEqual(result["error"], "mcp_tool_failed")
                self.assertNotIn("login_diagnostic", result)

    def test_valid_plan_is_copied_and_does_not_accept_extra_secret_fields(self):
        original = sample_plan()
        valid = validate_plan(original)
        self.assertEqual(valid, original)
        original["records"][0]["title"] = "Changed"
        self.assertNotEqual(valid, original)
        for extra in ("password", "session", "base_url", "command", "env"):
            plan = sample_plan()
            plan[extra] = "synthetic-sensitive-canary"
            with self.assertRaisesRegex(RunnerFailure, "plan_invalid"):
                validate_plan(plan)

    def test_nonblocking_send_preserves_deadline_and_handles_partial_writes(self):
        channel = _Channel.__new__(_Channel)
        channel.deadline = 0
        with self.assertRaisesRegex(RunnerFailure, "^runner_timeout$"):
            channel.send(b"message")
        channel.deadline = time.monotonic() + 10
        channel.process = SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: 123))
        sent = bytearray()
        def short_write(fd, data):
            self.assertEqual(fd, 123)
            sent.extend(data[:2])
            return len(data[:2])
        with patch("timetree_mcp.e2e_runner.os.write", side_effect=short_write):
            channel.send(b"message")
        self.assertEqual(sent, b"message")
        channel.pump = lambda: (_ for _ in ()).throw(RunnerFailure("worker_output_limit"))
        with patch("timetree_mcp.e2e_runner.os.write", side_effect=BlockingIOError()):
            with self.assertRaisesRegex(RunnerFailure, "^worker_output_limit$"):
                channel.send(b"message")

    def test_wrong_scope_unmarked_targets_duplicate_ids_and_unplanned_expected_rows_fail(self):
        cases = []
        plan = sample_plan(); plan["calendar_name"] = "Personal"; cases.append(plan)
        plan = sample_plan(); plan["records"][0]["title"] = "Existing work"; cases.append(plan)
        plan = sample_plan(); plan["records"] *= 2; cases.append(plan)
        plan = sample_plan(); plan["checks"][0]["arguments"]["calendar_id"] = "43"; cases.append(plan)
        plan = sample_plan(); plan["checks"][0]["expected"][0]["event_uuid"] = "not-planned"; cases.append(plan)
        plan = sample_plan(); plan["checks"][0]["arguments"]["limit"] = True; cases.append(plan)
        plan = sample_plan(); plan["checks"][0]["expected"][0]["start_at"] = "bad"; cases.append(plan)
        plan = sample_plan(); plan["records"][0]["source_uuid"] = "../../outside"; cases.append(plan)
        plan = sample_plan(); plan["checks"] *= 13; cases.append(plan)
        for plan in cases:
            with self.subTest(case=cases.index(plan)), self.assertRaisesRegex(RunnerFailure, "plan_invalid"):
                validate_plan(plan)

    def test_plan_file_requires_exact_hash_owner_only_mode_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            raw = json.dumps(sample_plan()).encode()
            path.write_bytes(raw); path.chmod(0o600)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(load_plan(path, digest), sample_plan())
            with self.assertRaisesRegex(RunnerFailure, "plan_invalid"):
                load_plan(path, "0" * 64)
            path.chmod(0o644)
            with self.assertRaisesRegex(RunnerFailure, "plan_invalid"):
                load_plan(path, digest)
            path.chmod(0o600)
            link = Path(directory) / "link.json"; link.symlink_to(path)
            with self.assertRaisesRegex(RunnerFailure, "plan_invalid"):
                load_plan(link, digest)

    def test_duplicate_keys_and_oversized_file_are_rejected_without_echoing_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            for raw in (b'{"version":1,"version":1,"password":"canary"}', b"x" * 65537):
                path.write_bytes(raw); path.chmod(0o600)
                with self.assertRaisesRegex(RunnerFailure, "^plan_invalid$"):
                    load_plan(path, hashlib.sha256(raw).hexdigest())

    def test_worker_environment_never_copies_parent_values_and_rejects_proxy_keys(self):
        environment = worker_environment({"TIMETREE_PASSWORD": "sensitive-value",
                                          "AWS_SECRET_ACCESS_KEY": "other-sensitive",
                                          "PATH": "untrusted", "PYTHONPATH": "untrusted"})
        self.assertEqual(environment, {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        for key in ("HTTP_PROXY", "https_proxy", "ALL_PROXY", "no_proxy", "SOCKS_PROXY"):
            with self.assertRaisesRegex(RunnerFailure, "proxy_environment_not_supported"):
                worker_environment({key: "sensitive-proxy-url"})

    def test_live_terminal_snapshot_failure_cannot_silently_disable_cleanup(self):
        with patch("timetree_mcp.e2e_runner.os.open", side_effect=OSError()):
            self.assertIsNone(_restore_terminal_snapshot()())
            with self.assertRaisesRegex(RunnerFailure, "terminal_snapshot_failed"):
                _restore_terminal_snapshot(required=True)
        with patch("timetree_mcp.e2e_runner.os.open", return_value=123), \
             patch("timetree_mcp.e2e_runner.os.close") as close, \
             patch("termios.tcgetattr", side_effect=OSError()):
            with self.assertRaisesRegex(RunnerFailure, "terminal_snapshot_failed"):
                _restore_terminal_snapshot()
            close.assert_called_once_with(123)

    def test_repeated_cancellation_during_cleanup_cannot_skip_terminal_restore(self):
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        observed = []
        def restore():
            self.assertEqual(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
            self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGTERM)
            observed.append("restored")
        self.assertIsNone(_cleanup_worker(None, None, restore))
        self.assertEqual(observed, ["restored"])
        self.assertEqual({sig: signal.getsignal(sig) for sig in before}, before)

    def test_pre_stdio_exit_codes_report_fixed_causes_without_forwarding_stderr(self):
        errors = {80: "proxy_not_supported", 81: "proxy_state_unknown", 82: "terminal_unavailable",
                  83: "credential_input_unavailable", 84: "operator_cancelled", 85: "worker_startup_failed"}
        for code, expected in errors.items():
            with self.subTest(code=code), patch.dict(os.environ, {}, clear=True):
                command = [sys.executable, "-I", "-B", "-c",
                           "import sys;sys.stderr.write('synthetic-private-startup-canary');raise SystemExit(" + str(code) + ")"]
                result = _drive(sample_plan(), command, timeout=3)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "initialize")
            self.assertEqual(result["checks_passed"], 0)
            self.assertEqual(result["error"], expected)
            self.assertNotIn("synthetic-private-startup-canary", json.dumps(result))

    def test_unknown_exit_code_cannot_invent_a_diagnostic_from_stderr(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _drive(sample_plan(), [sys.executable, "-I", "-B", "-c",
                "import sys;sys.stderr.write('terminal_unavailable');raise SystemExit(42)"], timeout=3)
        self.assertEqual(result["error"], "worker_unavailable")

    def test_worker_shutdown_lag_preserves_natural_startup_exit(self):
        with patch.dict(os.environ, {}, clear=True):
            result = _drive(sample_plan(), [sys.executable, "-I", "-B", "-c",
                "import os,time;os.close(1);time.sleep(0.25);raise SystemExit(82)"], timeout=3)
        self.assertEqual(result["error"], "terminal_unavailable")

    def test_driver_cleanup_signal_cannot_masquerade_as_operator_cancellation(self):
        script = ("import os,signal,time;"
                  "signal.signal(signal.SIGINT,lambda *_:exit(84));"
                  "os.close(1);time.sleep(2);raise SystemExit(82)")
        with patch.dict(os.environ, {}, clear=True):
            result = _drive(sample_plan(), [sys.executable, "-I", "-B", "-c", script], timeout=3)
        self.assertEqual(result["error"], "worker_unavailable")


if __name__ == "__main__":
    unittest.main()
