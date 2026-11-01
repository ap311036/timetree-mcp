"""Read local timezone availability without credentials, paths, or calendar I/O.

Fresh ZoneInfo lookups avoid reporting stale cache entries as installed data.
System-file and tzdata-package checks are separate observations; they do not
identify which source an earlier or concurrent ZoneInfo object actually uses.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import re
import stat
import sys
from typing import Any
import zoneinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .occurrence_rules import SUPPORTED_TIMEZONES


ZONE_KEY = re.compile(r"(?:[A-Za-z0-9_+-]+/)*[A-Za-z0-9_+-]+")
PACKAGE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}")


def validate_timezone_name(value: Any) -> str:
    """Accept a bounded relative IANA key, never a path or a fixed UTC offset."""
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or not ZONE_KEY.fullmatch(value):
        raise ValueError("invalid_timezone_name")
    return value


def _system_data_status(key: str) -> str:
    # Inspect the first matching system file, matching TZPATH precedence.
    # Nothing about the locations or exception messages enters the result.
    for directory in zoneinfo.TZPATH:
        try:
            path = Path(directory) / key
            if not stat.S_ISREG(path.stat().st_mode):
                continue
            with path.open("rb") as source:
                ZoneInfo.from_file(source, key=key)
            return "available"
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            continue
        except Exception:
            return "error"
    return "missing"


def _package_status() -> dict[str, Any]:
    try:
        version = metadata.version("tzdata")
        if not isinstance(version, str) or not PACKAGE_VERSION.fullmatch(version):
            return {"status": "error", "version": None}
        return {"status": "installed", "version": version}
    except metadata.PackageNotFoundError:
        return {"status": "not_installed", "version": None}
    except Exception:
        return {"status": "error", "version": None}


def timezone_status(timezone_name: str | None = None) -> dict[str, Any]:
    """Check one IANA zone or all supported occurrence zones, without writes.

    Availability says only that this process can freshly load timezone data.
    It does not verify the database's age, TimeTree behavior, or whether an
    event satisfies the remaining recurrence, metadata and DST requirements.
    """
    names = sorted(SUPPORTED_TIMEZONES) if timezone_name is None else [validate_timezone_name(timezone_name)]
    zones = []
    for name in names:
        try:
            ZoneInfo.no_cache(name)
            status = "available"
        except ZoneInfoNotFoundError:
            status = "missing"
        except Exception:
            status = "error"
        zones.append({
            "timezone": name,
            "status": status,
            "available": status == "available",
            "supported_for_occurrence_tools": name in SUPPORTED_TIMEZONES,
            "system_data_status": _system_data_status(name),
        })
    return {
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "zones": zones,
        "all_requested_available": all(item["available"] for item in zones),
        "supported_timezones": sorted(SUPPORTED_TIMEZONES),
        "cross_timezone_events_supported": False,
        "tzdata_package": _package_status(),
        "source_note": (
            "Availability uses fresh ZoneInfo lookups. System files and tzdata package metadata "
            "are checked separately; the active data source and database freshness are not inferred. "
            "This does not validate TimeTree behavior or event editability."
        ),
        "install_hint": (
            "If data is missing, install tzdata into the same Python environment selected by "
            "TIMETREE_PYTHON (for example: python -m pip install tzdata in that environment), "
            "then restart the MCP server. If loading reports an error, repair the system timezone "
            "database or its permissions; installing tzdata may not override a broken system file. "
            "This diagnostic does not install or change anything."
        ),
    }
