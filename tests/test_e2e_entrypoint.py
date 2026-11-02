"""Offline smoke of the shipped operator script and its real dispatch paths.

The isolated interpreter loads the checkout's actual entrypoint. Only its TTY
preflight and driver/worker dispatch seams are replaced; credentials, sockets,
subprocess creation inside the child, and real terminal access are forbidden.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "e2e_stdio.py"
PLAN = {"version": 1, "calendar_id": "42", "calendar_name": "EWS",
        "records": [{"source_uuid": "synthetic-entrypoint",
                     "title": "TIMETREE-MCP-TEST ENTRYPOINT"}],
        "checks": [{"label": "entrypoint", "arguments": {
            "calendar_id": "42", "start_at": "2030-01-01T00:00:00+00:00",
            "end_at": "2030-01-02T00:00:00+00:00", "limit": 100}, "expected": []}]}


SMOKE = r'''
import contextlib, inspect, io, json, os, runpy, sys
from pathlib import Path
from unittest.mock import patch

script, plan_path, digest, mode = sys.argv[1:]
script, plan_path = Path(script).resolve(), Path(plan_path).resolve()
root = script.parent.parent
expected_plan = json.loads(plan_path.read_text())

def forbidden(*args, **kwargs):
    raise AssertionError("entrypoint_external_io_forbidden")

def audit(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
        forbidden()
    if event == "open":
        target, file_mode, flags = args
        if target == "/dev/tty" or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
            forbidden()

sys.addaudithook(audit)
try:
    assert "timetree_mcp" not in sys.modules
    imported = runpy.run_path(str(script), run_name="entrypoint_import_probe")
    from timetree_mcp import e2e_runner as runner, e2e_worker as worker
    assert Path(runner.__file__).resolve() == root / "src/timetree_mcp/e2e_runner.py"
    assert Path(worker.__file__).resolve() == root / "src/timetree_mcp/e2e_worker.py"
    assert imported["main"] is runner.main
    assert Path(inspect.getsourcefile(worker.run_worker)).resolve() == root / "src/timetree_mcp/e2e_worker.py"
    assert worker.CSRF_URL == "https://timetreeapp.com/signin"

    # Exercise the imported production reader's actual CSRF method as well.
    class MemoryTransport:
        def __init__(self):
            self.requests = []
        def request(self, method, url, body=None, headers=None, timeout=None):
            self.requests.append((method, url, body))
            return worker.HttpResponse(200, {"x-csrf-token": "synthetic-entrypoint-csrf"}, "")
        def clear(self):
            pass
    transport = MemoryTransport()
    reader = worker.ScopedReader(expected_plan, "", "", transport)
    reader._refresh_csrf()
    assert transport.requests == [("GET", "https://timetreeapp.com/signin", None)]
    reader.close()

    worker_calls, driver_calls, tty_calls = [], [], []
    summary = {"status": "passed", "stage": "complete", "checks_passed": 1,
               "scope": "planned_test_records", "transport": "stdio"}
    real_open, real_close, real_isatty = os.open, os.close, os.isatty
    fake_fd = 987654321

    def fake_open(path, flags, *args, **kwargs):
        if os.fspath(path) == "/dev/tty":
            assert mode == "driver" and flags == os.O_RDWR
            tty_calls.append(True)
            return fake_fd
        assert Path(path).resolve() == plan_path
        return real_open(path, flags, *args, **kwargs)
    def fake_close(descriptor):
        if descriptor != fake_fd:
            return real_close(descriptor)
    def fake_isatty(descriptor):
        return True if descriptor == fake_fd else real_isatty(descriptor)
    def fake_drive(plan, command, *, require_terminal=False):
        assert mode == "driver" and plan == expected_plan and require_terminal is True
        assert command == [sys.executable, "-I", "-B", str(script), "--worker",
                           "--plan", str(plan_path), "--sha256", digest]
        driver_calls.append(True)
        return summary
    def fake_worker(plan):
        assert mode == "worker" and plan == expected_plan
        worker_calls.append(True)
        return 0

    argv = [str(script), "--plan", str(plan_path), "--sha256", digest]
    if mode in {"worker", "invalid_worker"}:
        argv.append("--worker")
    output, errors = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", argv), patch.object(sys, "path", list(sys.path)), \
         patch.object(runner, "_drive", fake_drive), patch.object(worker, "run_worker", fake_worker), \
         patch.object(os, "open", fake_open), patch.object(os, "close", fake_close), \
         patch.object(os, "isatty", fake_isatty), patch("getpass.getpass", forbidden), \
         patch("builtins.input", forbidden), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as stopped:
            exit_code = stopped.code
        else:
            raise AssertionError("entrypoint_did_not_exit")
    if mode == "driver":
        assert exit_code == 0 and driver_calls == [True] and not worker_calls and tty_calls == [True]
        assert json.loads(output.getvalue()) == summary
        assert "hidden Terminal prompts" in errors.getvalue()
    elif mode == "worker":
        assert exit_code == 0 and worker_calls == [True] and not driver_calls and not tty_calls
        assert not output.getvalue() and not errors.getvalue()
    else:
        assert exit_code == 1 and not worker_calls and not driver_calls and not tty_calls
        assert json.loads(output.getvalue()) == {"status": "failed", "error": "plan_invalid"}
        assert not errors.getvalue()
    print(json.dumps({"status": "passed", "mode": mode, "checkout_imports": True,
                      "fixed_csrf_path": True, "tty_preflights": len(tty_calls)}))
except BaseException:
    print('{"status":"failed","error":"entrypoint_smoke_failed"}')
    sys.exit(1)
'''


class E2eEntrypointTests(unittest.TestCase):
    def run_entrypoint(self, mode):
        with tempfile.TemporaryDirectory(prefix="timetree-entrypoint-synthetic-") as directory:
            plan_path = Path(directory) / "plan.json"
            raw = json.dumps(PLAN).encode()
            descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
            digest = "0" * 64 if mode.startswith("invalid_") else hashlib.sha256(raw).hexdigest()
            result = subprocess.run([sys.executable, "-I", "-B", "-c", SMOKE,
                                     str(SCRIPT), str(plan_path), digest, mode],
                                    cwd=directory, env={"PATH": os.defpath},
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, "Actual entrypoint smoke failed; output withheld")
        self.assertEqual(result.stderr, b"", "Actual entrypoint emitted unexpected stderr; output withheld")
        report = json.loads(result.stdout)
        self.assertEqual(report, {"status": "passed", "mode": mode, "checkout_imports": True,
                                  "fixed_csrf_path": True, "tty_preflights": int(mode == "driver")})

    def test_actual_script_driver_dispatch_uses_same_checkout_and_script(self):
        self.run_entrypoint("driver")

    def test_actual_script_worker_dispatch_loads_reviewed_signin_worker(self):
        self.run_entrypoint("worker")

    def test_actual_script_invalid_plan_never_reaches_tty_or_driver_or_worker(self):
        for mode in ("invalid_driver", "invalid_worker"):
            with self.subTest(mode=mode):
                self.run_entrypoint(mode)

    def test_package_manifest_includes_entrypoint_sources_with_matching_versions(self):
        package = json.loads((ROOT / "package.json").read_text())
        pyproject = (ROOT / "pyproject.toml").read_text()
        from timetree_mcp.server import SERVER_VERSION
        self.assertEqual(package["version"], SERVER_VERSION)
        self.assertEqual(re.search(r'^version = "([^\"]+)"$', pyproject, re.MULTILINE).group(1),
                         SERVER_VERSION)
        self.assertIn("scripts/e2e_stdio.py", package["files"])
        self.assertIn("src/timetree_mcp/*.py", package["files"])
        for relative in ("scripts/e2e_stdio.py", "src/timetree_mcp/e2e_runner.py",
                         "src/timetree_mcp/e2e_worker.py"):
            self.assertTrue((ROOT / relative).is_file())


if __name__ == "__main__":
    unittest.main()
