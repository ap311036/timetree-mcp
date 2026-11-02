"""Operator-run, bounded stdio checks. Credentials belong only to the worker TTY.

No report files, credential environment, remote writes, or automatic retries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Mapping

from .recurrence_query import validate_query_arguments
from .server import SERVER_VERSION, TimeTreeMcpServer

MAX_PLAN_BYTES = 65_536
MAX_FRAME_BYTES = 1_000_000
MAX_OUTPUT_BYTES = 8_000_000
MAX_STDERR_BYTES = 8_192
ROW_KEYS = frozenset({"event_uuid", "series_uuid", "start_at", "end_at", "timezone",
                      "start_local", "end_local", "source"})
STARTUP_EXIT_ERRORS = {
    80: "proxy_not_supported", 81: "proxy_state_unknown", 82: "terminal_unavailable",
    83: "credential_input_unavailable", 84: "operator_cancelled", 85: "worker_startup_failed",
}
LOGIN_DIAGNOSTIC_REASONS = frozenset({
    "http_401", "http_403", "http_429", "http_4xx", "http_5xx",
    "http_redirect_rejected", "http_rejected", "tls_failed", "network_timeout",
    "network_failed", "invalid_response", "response_limit", "session_limit",
    "request_denied", "internal_error", "csrf_missing",
})
CSRF_REDIRECT_TARGETS = frozenset({"public_landing", "signin", "calendars", "other_or_missing"})


def safe_login_diagnostic(value):
    """Copy only the closed diagnostic vocabulary; never forward provider data."""
    if (not isinstance(value, dict) or set(value) not in ({"phase", "reason"},
                                                        {"phase", "reason", "redirect_target"})
            or type(value["phase"]) is not str or type(value["reason"]) is not str
            or value["phase"] not in {"sign_in", "csrf_fetch"}
            or value["reason"] not in LOGIN_DIAGNOSTIC_REASONS
            or value["reason"] == "csrf_missing" and value["phase"] != "csrf_fetch"):
        return None
    result = {"phase": value["phase"], "reason": value["reason"]}
    if "redirect_target" in value:
        if (value["phase"] != "csrf_fetch" or value["reason"] != "http_redirect_rejected"
                or type(value["redirect_target"]) is not str
                or value["redirect_target"] not in CSRF_REDIRECT_TARGETS):
            return None
        result["redirect_target"] = value["redirect_target"]
    return result


class RunnerFailure(ValueError):
    """Only fixed non-sensitive codes may be used here."""


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RunnerFailure("plan_invalid")
        result[key] = value
    return result


def _text(value, pattern, maximum):
    return isinstance(value, str) and len(value) <= maximum and re.fullmatch(pattern, value) is not None


def validate_plan(obj: Any) -> dict[str, Any]:
    """Validate a private exact-fixture allowlist without interpreting credentials."""
    try:
        if (not isinstance(obj, dict) or set(obj) != {"version", "calendar_id", "calendar_name", "records", "checks"}
                or type(obj["version"]) is not int or obj["version"] != 1
                or obj["calendar_name"] not in ("EWS", "EWS 行事曆")
                or not _text(obj["calendar_id"], r"[0-9]{1,40}", 40)
                or not isinstance(obj["records"], list) or not 1 <= len(obj["records"]) <= 6
                or not isinstance(obj["checks"], list) or not 1 <= len(obj["checks"]) <= 12):
            raise ValueError()
        identifiers = set()
        for record in obj["records"]:
            if (not isinstance(record, dict) or set(record) != {"source_uuid", "title"}
                    or not _text(record["source_uuid"], r"[A-Za-z0-9_-]{1,160}", 160)
                    or not isinstance(record["title"], str) or not record["title"].startswith("TIMETREE-MCP-TEST")
                    or not 18 <= len(record["title"]) <= 200
                    or any(ord(c) < 32 or ord(c) == 127 for c in record["title"])
                    or record["source_uuid"] in identifiers):
                raise ValueError()
            identifiers.add(record["source_uuid"])
        labels, arguments_seen = set(), set()
        for check in obj["checks"]:
            if (not isinstance(check, dict) or set(check) != {"label", "arguments", "expected"}
                    or not _text(check["label"], r"[a-z][a-z0-9_]{0,39}", 40)
                    or check["label"] in labels):
                raise ValueError()
            labels.add(check["label"])
            arguments = check["arguments"]
            parsed = validate_query_arguments(arguments)
            encoded = json.dumps(arguments, sort_keys=True)
            if (parsed["calendar_id"] != obj["calendar_id"] or parsed["limit"] > 100
                    or encoded in arguments_seen or "limit" not in arguments
                    or not isinstance(check["expected"], list)
                    or len(check["expected"]) > min(50, parsed["limit"])):
                raise ValueError()
            arguments_seen.add(encoded)
            seen_rows = set()
            for row in check["expected"]:
                if (not isinstance(row, dict) or set(row) != ROW_KEYS
                        or row["event_uuid"] not in identifiers
                        or row["series_uuid"] is not None and row["series_uuid"] not in identifiers
                        or row["source"] not in {"generated", "single", "detached"}
                        or not _text(row["timezone"], r"[A-Za-z0-9_+/-]{1,128}", 128)):
                    raise ValueError()
                instants = []
                for field in ("start_at", "end_at", "start_local", "end_local"):
                    value = row[field]
                    if not isinstance(value, str) or len(value) > 40:
                        raise ValueError()
                    instant = datetime.fromisoformat(value)
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        raise ValueError()
                    instants.append(instant)
                if (instants[1] <= instants[0] or instants[0] != instants[2] or instants[1] != instants[3]
                        or not parsed["start_ms"] < instants[1].timestamp() * 1000
                        or not instants[0].timestamp() * 1000 < parsed["end_ms"]):
                    raise ValueError()
                row_key = (row["event_uuid"], row["start_at"])
                if row_key in seen_rows:
                    raise ValueError()
                seen_rows.add(row_key)
        serialized = json.dumps(obj, allow_nan=False)
        if len(serialized.encode("utf-8")) > MAX_PLAN_BYTES:
            raise ValueError()
        return json.loads(serialized)
    except Exception:
        raise RunnerFailure("plan_invalid") from None


def load_plan(path: str | Path, sha256: str) -> dict[str, Any]:
    """Hash-pin and read a regular, owner-only file; no symlink following."""
    descriptor = None
    try:
        if not _text(sha256, r"[a-f0-9]{64}", 64):
            raise ValueError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or not 0 < info.st_size <= MAX_PLAN_BYTES):
            raise ValueError()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            raw = handle.read(MAX_PLAN_BYTES + 1)
        if len(raw) > MAX_PLAN_BYTES or hashlib.sha256(raw).hexdigest() != sha256:
            raise ValueError()
        return validate_plan(json.loads(raw, object_pairs_hook=_unique_pairs))
    except Exception:
        raise RunnerFailure("plan_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def worker_environment(parent: Mapping[str, str]) -> dict[str, str]:
    # Inspect only key names, never read/copy any parent credential value.
    if any(key.lower().endswith("_proxy") for key in parent):
        raise RunnerFailure("proxy_environment_not_supported")
    return {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


class _Channel:
    def __init__(self, process, deadline):
        self.process, self.deadline = process, deadline
        self.selector = selectors.DefaultSelector()
        self.buffer = bytearray()
        self.total = self.stderr = 0
        self.stdout_closed = False
        os.set_blocking(process.stdin.fileno(), False)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, name)

    def pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RunnerFailure("runner_timeout")
        for key, _ in self.selector.select(min(0.1, remaining)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.data == "stdout":
                    self.stdout_closed = True
                continue
            if key.data == "stderr":
                self.stderr += len(chunk)  # Never retain or print stderr content.
                if self.stderr > MAX_STDERR_BYTES:
                    raise RunnerFailure("worker_output_limit")
            else:
                self.total += len(chunk)
                self.buffer.extend(chunk)
                if self.total > MAX_OUTPUT_BYTES or len(self.buffer) > MAX_FRAME_BYTES:
                    raise RunnerFailure("worker_output_limit")

    def send(self, data):
        offset = 0
        try:
            while offset < len(data):
                if time.monotonic() >= self.deadline:
                    raise RunnerFailure("runner_timeout")
                try:
                    written = os.write(self.process.stdin.fileno(), data[offset:])
                except BlockingIOError:
                    self.pump()  # Drain output while waiting, with the same deadline.
                    continue
                if written <= 0:
                    raise RunnerFailure("worker_unavailable")
                offset += written
        except RunnerFailure:
            raise
        except (OSError, ValueError):
            raise RunnerFailure("worker_unavailable") from None

    def response(self, request):
        self.send(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        while b"\n" not in self.buffer:
            if self.stdout_closed:
                raise RunnerFailure("worker_unavailable")
            self.pump()
        raw, _, tail = self.buffer.partition(b"\n")
        self.buffer = bytearray(tail)
        try:
            value = json.loads(raw, object_pairs_hook=_unique_pairs)
            if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
                    or type(value.get("id")) is not int or value["id"] != request["id"]
                    or set(value) != {"jsonrpc", "id", "result"} or not isinstance(value["result"], dict)):
                raise ValueError()
            return value["result"]
        except Exception:
            raise RunnerFailure("stdio_protocol_invalid") from None

    def finish(self):
        self.process.stdin.close()
        finish_deadline = min(self.deadline, time.monotonic() + 3)
        while self.selector.get_map():
            if time.monotonic() >= finish_deadline:
                raise RunnerFailure("worker_did_not_exit")
            self.pump()
        if self.buffer or self.stderr:
            raise RunnerFailure("worker_unexpected_output")
        try:
            if self.process.wait(timeout=max(0.01, finish_deadline - time.monotonic())) != 0:
                raise RunnerFailure("worker_failed")
        except subprocess.TimeoutExpired:
            raise RunnerFailure("worker_did_not_exit") from None

    def close(self):
        self.selector.close()


def _restore_terminal_snapshot(required=False):
    """Return a cleanup callback; a test without a controlling terminal is fine."""
    import termios
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        if required:
            raise RunnerFailure("terminal_snapshot_failed") from None
        return lambda: None
    try:
        settings = termios.tcgetattr(descriptor)
    except BaseException:
        os.close(descriptor)
        raise RunnerFailure("terminal_snapshot_failed") from None

    def restore():
        try:
            termios.tcsetattr(descriptor, termios.TCSANOW, settings)
        finally:
            os.close(descriptor)
    return restore


def _cleanup_worker(process, channel, restore):
    """Bounded cleanup cannot be interrupted by a second operator cancellation."""
    handlers = {}
    error = None
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, signal.SIG_IGN)
        if process is not None:
            for action in (lambda: process.send_signal(signal.SIGINT), process.terminate, process.kill):
                if process.poll() is not None:
                    break
                try:
                    action()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                error = "worker_cleanup_failed"
    except BaseException:
        error = "worker_cleanup_failed"
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=1)
            except BaseException:
                pass
    finally:
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except BaseException:
                    error = error or "worker_cleanup_failed"
        try:
            if channel is not None:
                channel.close()
        except BaseException:
            error = error or "worker_cleanup_failed"
        try:
            restore()
        except BaseException:
            error = "terminal_restore_failed"
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    return error


def _drive(plan, command, *, timeout=600, require_terminal=False):
    """Drive real stdio pipes. command is an internal seam, never a CLI option."""
    summary = {"status": "failed", "stage": "starting", "checks_passed": 0,
               "scope": "planned_test_records", "transport": "stdio"}
    process = channel = None
    startup_exit = None
    restore = lambda: None
    try:
        plan = validate_plan(plan)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise RunnerFailure("runner_timeout_invalid")
        environment = worker_environment(os.environ)
        restore = _restore_terminal_snapshot(required=require_terminal)
        deadline = time.monotonic() + timeout
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0, close_fds=True, env=environment)
        channel = _Channel(process, deadline)
        sequence = 0

        def request(method, params=None):
            nonlocal sequence
            sequence += 1
            message = {"jsonrpc": "2.0", "id": sequence, "method": method}
            if params is not None:
                message["params"] = params
            return channel.response(message)

        def tool(name, arguments):
            result = request("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError") is not False or not isinstance(result.get("structuredContent"), dict):
                if (name == "timetree_login" and result.get("isError") is True
                        and isinstance(result.get("structuredContent"), dict)):
                    diagnostic = safe_login_diagnostic(result["structuredContent"].get("login_diagnostic"))
                    if diagnostic is not None:
                        summary["login_diagnostic"] = diagnostic
                        raise RunnerFailure("login_failed")
                raise RunnerFailure("mcp_tool_failed")
            return result["structuredContent"]

        summary["stage"] = "initialize"
        initialized = request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "timetree-local-e2e", "version": "1"}})
        if (initialized.get("protocolVersion") != "2025-06-18"
                or initialized.get("serverInfo") != {"name": "timetree-mcp", "version": SERVER_VERSION}):
            raise RunnerFailure("initialize_mismatch")
        channel.send(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        summary["stage"] = "inventory"
        listed = request("tools/list").get("tools")
        expected_names = sorted(item["name"] for item in TimeTreeMcpServer.tool_definitions())
        if not isinstance(listed, list) or sorted(item.get("name", "") for item in listed) != expected_names:
            raise RunnerFailure("tool_inventory_mismatch")
        summary["stage"] = "ping"
        if request("ping") != {}:
            raise RunnerFailure("ping_mismatch")
        summary["stage"] = "login"
        login = tool("timetree_login", {})
        if login.get("authenticated") is not True or login.get("csrf_available") is not True:
            raise RunnerFailure("login_incomplete")
        summary["stage"] = "status"
        status = tool("timetree_status", {})
        if (status.get("authenticated") is not True or status.get("csrf_available") is not True
                or status.get("credential_source") != "constructor"
                or status.get("keychain", {}).get("checked") is not False):
            raise RunnerFailure("status_mismatch")
        summary["stage"] = "occurrences"
        for check in plan["checks"]:
            found = tool("timetree_list_occurrences", check["arguments"])
            if (found.get("complete") is not True or found.get("coverage_complete") is not True
                    or found.get("has_more") is not False or found.get("scope") != "plan_records"
                    or type(found.get("count")) is not int or found["count"] != len(check["expected"])
                    or found.get("occurrences") != check["expected"]):
                raise RunnerFailure("fixture_readback_mismatch")
            summary["checks_passed"] += 1
        summary["stage"] = "logout"
        if tool("timetree_logout", {}).get("authenticated") is not False:
            raise RunnerFailure("logout_incomplete")
        summary["stage"] = "shutdown"
        channel.finish()
        summary.update(status="passed", stage="complete")
    except RunnerFailure as error:
        summary["error"] = str(error)
        if (str(error) == "worker_unavailable" and summary["stage"] == "initialize"
                and process is not None):
            # EOF may precede the natural exit status. Capture it BEFORE
            # cleanup signals, which must never become a startup diagnosis.
            try:
                process.wait(timeout=min(0.5, max(0.001, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
            startup_exit = process.poll()
    except (KeyboardInterrupt, SystemExit):
        summary["error"] = "runner_cancelled"
    except BaseException:
        summary["error"] = "runner_failed"
    finally:
        error = _cleanup_worker(process, channel, restore)
        if error:
            summary.update(status="failed", error=error)
    if (summary.get("error") == "worker_unavailable" and summary["stage"] == "initialize"
            and summary["checks_passed"] == 0 and process is not None
            and startup_exit in STARTUP_EXIT_ERRORS):
        summary["error"] = STARTUP_EXIT_ERRORS[startup_exit]
    return summary


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RunnerFailure("runner_arguments_invalid")


def _main(argv=None):
    try:
        parser = _Parser(description="Operator-only, read-only EWS fixture checks over MCP stdio. No credentials in arguments.")
        parser.add_argument("--plan", required=True, help="Private owner-only fixture plan path")
        parser.add_argument("--sha256", required=True, help="Reviewed plan SHA-256")
        parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
        plan = load_plan(args.plan, args.sha256)
        if args.worker:
            from .e2e_worker import run_worker
            return run_worker(plan)
        # Prevent an inaccessible prompt before any worker is started.
        descriptor = os.open("/dev/tty", os.O_RDWR)
        try:
            if not os.isatty(descriptor):
                raise RunnerFailure("interactive_terminal_required")
        finally:
            os.close(descriptor)
        launcher = Path(__file__).resolve().parents[2] / "scripts" / "e2e_stdio.py"
        command = [sys.executable, "-I", "-B", str(launcher), "--worker",
                   "--plan", str(Path(args.plan).absolute()), "--sha256", args.sha256]
        print("Read-only EWS fixture check. Enter credentials only at the worker's hidden Terminal prompts.", file=sys.stderr)
        result = _drive(plan, command, require_terminal=True)
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["status"] == "passed" else 1
    except RunnerFailure as error:
        print(json.dumps({"status": "failed", "error": str(error)}, separators=(",", ":")))
        return 1
    except (KeyboardInterrupt, OSError):
        print('{"status":"failed","error":"runner_start_or_input_failed"}')
        return 1
    except Exception:
        print('{"status":"failed","error":"runner_failed"}')
        return 1


def main(argv=None):
    # Let SIGTERM unwind the same cleanup path as an operator's Ctrl-C.
    def cancel(signum, frame):
        raise KeyboardInterrupt()

    previous = signal.signal(signal.SIGTERM, cancel)
    try:
        return _main(argv)
    finally:
        signal.signal(signal.SIGTERM, previous)
