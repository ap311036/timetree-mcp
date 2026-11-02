"""Real getpass/default-worker integration on newly allocated synthetic PTYs.

These tests require PTY access. They never read the executing user's terminal;
all input is a synthetic sentinel written only after its hidden prompt appears.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from e2e_fixture_worker import EMAIL, PASSWORD, PRIVATE_NOTE, SESSION
from test_e2e_stdio_integration import fixture_plan

FIXTURE = Path(__file__).with_name("e2e_tty_fixture.py")
EMAIL_PROMPT = b"TimeTree email (hidden): "
PASSWORD_PROMPT = b"TimeTree password (hidden): "


def _own_controlling_terminal():
    # Popen has already duplicated the newly allocated slave to fd 0, and
    # start_new_session=True detached this test child from the caller's TTY.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())


class ActualTtyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="timetree-synthetic-tty-")
        self.addCleanup(self.directory.cleanup)
        self.plan_path = Path(self.directory.name) / "plan.json"
        raw = json.dumps(fixture_plan()).encode()
        self.plan_path.write_bytes(raw)
        self.plan_path.chmod(0o600)
        self.digest = hashlib.sha256(raw).hexdigest()
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        self.output = bytearray()
        self.sensitive_inputs = [EMAIL, PASSWORD, SESSION, PRIVATE_NOTE]
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(FIXTURE), "driver", str(self.plan_path), self.digest],
            stdin=self.slave, stdout=self.slave, stderr=self.slave,
            start_new_session=True, preexec_fn=_own_controlling_terminal,
            close_fds=True, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.addCleanup(self._stop_owned_processes)

    def _stop_owned_processes(self):
        if self.process.poll() is None:
            # This group belongs exclusively to the new test session above.
            # It never includes the user's shell, runner, or existing terminal.
            try:
                os.killpg(self.process.pid, signal.SIGINT)
                self.process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)

    def _pump(self, timeout=0.1):
        if not select.select([self.master], [], [], timeout)[0]:
            return
        try:
            data = os.read(self.master, 65536)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        self.output.extend(data)
        self.assertLess(len(self.output), 65536)

    def _await_prompt(self, prompt):
        deadline = time.monotonic() + 5
        while prompt not in self.output:
            self._pump()
            if self.process.poll() is not None or time.monotonic() >= deadline:
                self.fail("Expected hidden prompt did not appear")
        # Input is sent only after both the actual prompt and disabled ECHO
        # have been observed on this newly allocated terminal.
        self.assertFalse(termios.tcgetattr(self.slave)[3] & termios.ECHO)

    def _finish(self):
        deadline = time.monotonic() + 5
        while self.process.poll() is None:
            self._pump()
            if time.monotonic() >= deadline:
                self.fail("Synthetic TTY driver did not finish")
        for _ in range(3):
            self._pump(0.01)
        text = self.output.decode(errors="replace")
        self.assertFalse(any(sentinel in text for sentinel in self.sensitive_inputs),
                         "A sensitive sentinel appeared on the synthetic terminal")
        # Prompts can share a line with the fixed JSON summary; parse only the
        # final summary object and never print terminal data after input.
        position = text.rfind('{"status":')
        self.assertGreaterEqual(position, 0)
        summary = json.loads(text[position:].strip())
        self.assertIs(summary["terminal_restored"], True)
        return summary

    def test_default_worker_real_getpass_hides_input_and_completes_stdio(self):
        self._await_prompt(EMAIL_PROMPT)
        os.write(self.master, EMAIL.encode() + b"\n")
        self._await_prompt(PASSWORD_PROMPT)
        os.write(self.master, PASSWORD.encode() + b"\n")
        summary = self._finish()
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["checks_passed"], 1)
        self.assertEqual(self.process.returncode, 0)

    def test_ctrl_c_during_hidden_password_restores_terminal_and_exits(self):
        self._await_prompt(EMAIL_PROMPT)
        os.write(self.master, EMAIL.encode() + b"\n")
        self._await_prompt(PASSWORD_PROMPT)
        partial = PASSWORD[:12]
        self.sensitive_inputs.append(partial)
        os.write(self.master, partial.encode())
        os.write(self.master, b"\x03\x03")
        summary = self._finish()
        self.assertEqual(summary["status"], "failed")
        self.assertIn(summary["error"], {"runner_cancelled", "operator_cancelled", "worker_unavailable"})
        self.assertNotEqual(self.process.returncode, 0)

    def test_eof_during_hidden_password_restores_terminal_and_exits(self):
        self._await_prompt(EMAIL_PROMPT)
        os.write(self.master, EMAIL.encode() + b"\n")
        self._await_prompt(PASSWORD_PROMPT)
        os.write(self.master, b"\x04")
        summary = self._finish()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["checks_passed"], 0)
        self.assertNotEqual(self.process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
