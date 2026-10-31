"""Durable, fail-closed journal for multi-request occurrence edits.

Only verified success releases a series lock. An uncertain result is terminal
and retains the lock without expiry. Callers must store identifiers/digests,
never credentials or calendar text, in journal data and results.
The private directory is a boundary against other OS users, not malicious
processes running as the same user. Keep state on a local filesystem.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any


MAX_JSON_BYTES = 350000
_EDGES = {
    "prepared": "create_dispatched",
    "create_dispatched": "child_created",
    "child_created": "child_verified",
    "child_verified": "exclude_dispatched",
}
_ACTIVE_PHASES = frozenset((*_EDGES, "exclude_dispatched"))
_ALL_PHASES = _ACTIVE_PHASES | {"verified_complete", "uncertain_blocked"}


class OccurrenceJournalError(RuntimeError):
    """A journal cannot be used safely; do not continue the calendar edit."""


def _identity(*values: Any) -> None:
    if any(not isinstance(value, str) or not 1 <= len(value) <= 512 or "\x00" in value
           for value in values):
        raise ValueError("invalid_occurrence_journal_identity")
    try:
        for value in values:
            value.encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid_occurrence_journal_identity") from None


def _json_object(value: Any) -> str:
    def check_keys(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError
            for nested in item.values():
                check_keys(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check_keys(nested)

    if not isinstance(value, dict):
        raise ValueError("invalid_occurrence_journal_data")
    try:
        check_keys(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise ValueError("invalid_occurrence_journal_data") from None
    return encoded


def _validate_path(path: Any) -> Path:
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or "\x00" in str(path) or len(str(path)) > 4096):
        raise ValueError("invalid_occurrence_journal_path")
    try:
        str(path).encode("utf-8")
    except UnicodeError:
        raise ValueError("invalid_occurrence_journal_path") from None
    return path


def default_journal_path() -> Path:
    """Return a path without reading credentials, creating directories or files."""
    state_dir = os.environ.get("TIMETREE_STATE_DIR")
    xdg_state = os.environ.get("XDG_STATE_HOME")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if state_dir is not None:
        base = _validate_path(Path(state_dir))
    elif xdg_state is not None:
        base = _validate_path(Path(xdg_state)) / "timetree-mcp"
    elif sys.platform == "win32" and local_app_data is not None:
        base = _validate_path(Path(local_app_data)) / "timetree-mcp"
    else:
        base = Path.home() / ".local" / "state" / "timetree-mcp"
    return _validate_path(base / "occurrence-edits.sqlite3")


class OccurrenceJournal:
    def __init__(self, path: Path):
        self.path = _validate_path(path)

    @staticmethod
    def _private(info: os.stat_result, mode: int) -> None:
        # Windows chmod does not describe ACLs; access there follows the user's
        # private state directory ACL. POSIX permissions are checked exactly.
        if os.name == "posix":
            if stat.S_IMODE(info.st_mode) != mode or info.st_uid != os.geteuid():
                raise OccurrenceJournalError("occurrence_journal_unavailable")

    def _directory(self, create: bool) -> bool:
        """Reject symlinks in every component; create only missing directories."""
        parts = self.path.parent.parts
        current = Path(parts[0])
        for part in parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                if not create:
                    return False
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass  # A competing process may have created it.
                info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise OccurrenceJournalError("occurrence_journal_unavailable")
        self._private(self.path.parent.lstat(), 0o700)
        return True

    def _files(self, create: bool) -> bool:
        if not self._directory(create):
            return False
        if create:
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self.path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OccurrenceJournalError("occurrence_journal_unavailable")
        self._private(info, 0o600)
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                info = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OccurrenceJournalError("occurrence_journal_unavailable")
            self._private(info, 0o600)
        return True

    def _connect(self, write: bool) -> sqlite3.Connection | None:
        if not self._files(create=write):
            return None
        before = self.path.lstat()
        mode = "rw" if write else "ro"
        connection = sqlite3.connect(
            self.path.as_uri() + f"?mode={mode}", uri=True,
            timeout=10, isolation_level=None,
        )
        try:
            # sqlite3 opens by path rather than accepting our own O_NOFOLLOW
            # descriptor. Check again before any SQL writes, including schema
            # setup, and detect replacement of the validated regular file.
            if not self._files(create=False):
                raise OccurrenceJournalError("occurrence_journal_unavailable")
            after = self.path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise OccurrenceJournalError("occurrence_journal_unavailable")
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA trusted_schema=OFF")
            if write:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS occurrence_edits (
                        operation_id TEXT PRIMARY KEY NOT NULL,
                        request_hash TEXT NOT NULL,
                        series_target TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        data_json TEXT NOT NULL,
                        result_json TEXT,
                        completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0,1))
                    )
                """)
                connection.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS occurrence_series_lock
                    ON occurrence_edits(series_target) WHERE completed = 0
                """)
            return connection
        except Exception:
            connection.close()
            raise

    def find(self, operation_id: str) -> dict[str, Any] | None:
        _identity(operation_id)
        connection = None
        try:
            connection = self._connect(write=False)
            if connection is None:
                return None
            row = connection.execute(
                "SELECT request_hash, series_target, phase, data_json, result_json, completed "
                "FROM occurrence_edits WHERE operation_id = ?", (operation_id,),
            ).fetchone()
            if row is None:
                return None
            _identity(row["request_hash"], row["series_target"])
            if row["phase"] not in _ALL_PHASES or row["completed"] not in (0, 1):
                raise ValueError
            data = json.loads(row["data_json"])
            _json_object(data)
            result = json.loads(row["result_json"]) if row["result_json"] is not None else None
            if result is not None:
                _json_object(result)
                if type(result.get("ok")) is not bool:
                    raise ValueError
            complete = row["completed"] == 1
            if row["phase"] == "verified_complete":
                if not complete or result is None or result["ok"] is not True:
                    raise ValueError
            elif row["phase"] == "uncertain_blocked":
                if complete or result is None or result["ok"] is not False:
                    raise ValueError
            elif complete or result is not None:
                raise ValueError
            return {
                "request_hash": row["request_hash"], "series_target": row["series_target"],
                "phase": row["phase"], "data": data, "result": result, "completed": complete,
            }
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError):
            raise OccurrenceJournalError("occurrence_journal_unavailable") from None
        finally:
            if connection is not None:
                connection.close()

    def _write(self, statement: str, parameters: tuple[Any, ...]) -> bool:
        connection = None
        try:
            connection = self._connect(write=True)
            if connection is None:
                raise OccurrenceJournalError("occurrence_journal_unavailable")
            changed = connection.execute(statement, parameters).rowcount == 1
            connection.commit()
            return changed
        except (OSError, sqlite3.Error):
            raise OccurrenceJournalError("occurrence_journal_unavailable") from None
        finally:
            if connection is not None:
                connection.close()  # Rolls back any failed transaction.

    def claim(self, operation_id: str, request_hash: str, series_target: str, data: dict) -> bool:
        _identity(operation_id, request_hash, series_target)
        encoded = _json_object(data)
        return self._write(
            "INSERT OR IGNORE INTO occurrence_edits "
            "(operation_id, request_hash, series_target, phase, data_json, completed) "
            "VALUES (?, ?, ?, 'prepared', ?, 0)",
            (operation_id, request_hash, series_target, encoded),
        )

    def transition(self, operation_id: str, request_hash: str, expected_phase: str,
                   next_phase: str, data: dict) -> bool:
        _identity(operation_id, request_hash)
        if (not isinstance(expected_phase, str) or not isinstance(next_phase, str)
                or _EDGES.get(expected_phase) != next_phase):
            raise ValueError("invalid_occurrence_journal_transition")
        encoded = _json_object(data)
        return self._write(
            "UPDATE occurrence_edits SET phase = ?, data_json = ? "
            "WHERE operation_id = ? AND request_hash = ? AND phase = ? "
            "AND completed = 0 AND result_json IS NULL",
            (next_phase, encoded, operation_id, request_hash, expected_phase),
        )

    def finish(self, operation_id: str, request_hash: str, expected_phase: str,
               result: dict, data: dict) -> bool:
        _identity(operation_id, request_hash)
        if (not isinstance(expected_phase, str) or expected_phase not in _ACTIVE_PHASES
                or not isinstance(result, dict) or type(result.get("ok")) is not bool
                or (result["ok"] and expected_phase != "exclude_dispatched")):
            raise ValueError("invalid_occurrence_journal_finish")
        encoded, result_json = _json_object(data), _json_object(result)
        completed = int(result["ok"])
        phase = "verified_complete" if completed else "uncertain_blocked"
        return self._write(
            "UPDATE occurrence_edits SET phase = ?, data_json = ?, result_json = ?, completed = ? "
            "WHERE operation_id = ? AND request_hash = ? AND phase = ? "
            "AND completed = 0 AND result_json IS NULL",
            (phase, encoded, result_json, completed, operation_id, request_hash, expected_phase),
        )
