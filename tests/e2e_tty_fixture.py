"""Use the real terminal prompt with synthetic HTTPS and a private test PTY.

The driver and worker are launched only by test_e2e_tty.py. No production
credential source, network connection, or current operator TTY is used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import termios

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from e2e_fixture_worker import FakeTransport
from timetree_mcp.e2e_runner import _drive, load_plan
from timetree_mcp.e2e_worker import run_worker


def worker_audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen"}:
        raise RuntimeError("tty_fixture_external_io_forbidden")
    if event == "open":
        # getpass and the runner may open only their own controlling PTY for
        # terminal input/output; every ordinary file remains read-only.
        if args[0] == "/dev/tty" or isinstance(args[0], int) and os.isatty(args[0]):
            return
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_WRONLY | os.O_RDWR):
            raise RuntimeError("tty_fixture_file_write_forbidden")
        if isinstance(mode, str) and any(char in mode for char in "wax+"):
            raise RuntimeError("tty_fixture_file_write_forbidden")


def main():
    role, plan_path, digest = sys.argv[1:]
    plan = load_plan(plan_path, digest)
    if role == "worker":
        sys.addaudithook(worker_audit)
        transport = FakeTransport(plan, "happy")
        # Deliberately omit credential_prompt: this must use actual getpass.
        result = run_worker(plan, transport_factory=lambda _: transport)
        if not transport.cleared or transport.forbidden_requests:
            return 45
        return result
    if role != "driver":
        return 46
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
               "worker", plan_path, digest]
    before = termios.tcgetattr(0)
    result = _drive(plan, command, timeout=8)
    # Check while this isolated session still owns the terminal; macOS may
    # return EIO for the slave after its session leader has exited.
    result["terminal_restored"] = termios.tcgetattr(0) == before
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        # Never print exception text, even if a fixture assertion fails.
        raise SystemExit(47) from None
