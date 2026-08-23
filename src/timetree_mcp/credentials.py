"""macOS Keychain storage for TimeTree credentials and a secure setup CLI."""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
from pathlib import Path
from typing import Any, Callable


SERVICE_PREFIX = "com.codex.timetree-mcp"
EMAIL_SERVICE = f"{SERVICE_PREFIX}.email"
PASSWORD_SERVICE = f"{SERVICE_PREFIX}.password"
KEYCHAIN_ACCOUNT = "default"
SECURITY_COMMAND = "/usr/bin/security"


class KeychainUnavailable(RuntimeError):
    pass


class MacOSKeychainCredentialStore:
    """Store two generic-password items; secret values never enter MCP messages."""

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess] | None = None):
        self.runner = runner or self._run_subprocess

    @staticmethod
    def _run_subprocess(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, **kwargs)
        except FileNotFoundError as exc:
            raise KeychainUnavailable("macOS security command is unavailable") from exc

    def write(self, email: str, password: str) -> None:
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email must not be empty")
        if not isinstance(password, str) or not password:
            raise ValueError("password must not be empty")
        self._write_item(EMAIL_SERVICE, email.strip())
        self._write_item(PASSWORD_SERVICE, password)

    def read(self) -> tuple[str | None, str | None]:
        try:
            return self._find_item(EMAIL_SERVICE), self._find_item(PASSWORD_SERVICE)
        except KeychainUnavailable:
            return None, None

    def status(self) -> dict[str, bool]:
        try:
            email, password = self._find_item(EMAIL_SERVICE), self._find_item(PASSWORD_SERVICE)
        except KeychainUnavailable:
            return {"available": False, "email_configured": False, "password_configured": False}
        return {"available": True, "email_configured": bool(email), "password_configured": bool(password)}

    def clear(self) -> None:
        for service in (EMAIL_SERVICE, PASSWORD_SERVICE):
            try:
                self._delete_item(service)
            except KeychainUnavailable:
                return

    def _find_item(self, service: str) -> str | None:
        result = self._run(
            [SECURITY_COMMAND, "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service, "-w"]
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        if self._is_missing(result):
            return None
        raise RuntimeError("macOS Keychain read failed")

    def _write_item(self, service: str, value: str) -> None:
        result = self._run(
            [
                SECURITY_COMMAND,
                "add-generic-password",
                "-a",
                KEYCHAIN_ACCOUNT,
                "-s",
                service,
                "-w",
                value,
                "-U",
            ]
        )
        if result.returncode != 0:
            raise RuntimeError("macOS Keychain write failed")

    def _delete_item(self, service: str) -> None:
        result = self._run(
            [SECURITY_COMMAND, "delete-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service]
        )
        if result.returncode != 0 and not self._is_missing(result):
            raise RuntimeError("macOS Keychain delete failed")

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return self.runner(args, capture_output=True, text=True, timeout=10, check=False)
        except FileNotFoundError as exc:
            raise KeychainUnavailable("macOS security command is unavailable") from exc

    @staticmethod
    def _is_missing(result: subprocess.CompletedProcess) -> bool:
        text = f"{result.stdout}\n{result.stderr}".lower()
        return result.returncode in (44, 45) or "could not be found" in text or "item not found" in text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage TimeTree credentials in macOS Keychain")
    parser.add_argument("command", choices=("set", "status", "clear"))
    args = parser.parse_args(argv)
    store = MacOSKeychainCredentialStore()

    if args.command == "set":
        email = input("TimeTree email: ").strip()
        password = getpass.getpass("TimeTree password: ")
        store.write(email, password)
        print("TimeTree credentials stored in macOS Keychain.")
        return 0
    if args.command == "status":
        print(json.dumps(store.status(), ensure_ascii=False))
        return 0
    store.clear()
    print("TimeTree credentials removed from macOS Keychain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
