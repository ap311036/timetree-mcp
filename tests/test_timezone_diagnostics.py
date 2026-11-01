"""Offline timezone diagnostics without credentials or calendar access."""

import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import struct
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import zoneinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from timetree_mcp.timezone_diagnostics import timezone_status, validate_timezone_name


def utc_tzif():
    # Minimal v1 TZif: one UTC type and no transitions.
    return b"TZif\0" + b"\0" * 15 + struct.pack(">6l", 0, 0, 0, 0, 1, 4) + struct.pack(">lbb", 0, 0, 0) + b"UTC\0"


class TimezoneDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="timezone-private-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.original_paths = zoneinfo.TZPATH
        self.paths = patch("timetree_mcp.timezone_diagnostics.zoneinfo.TZPATH", (str(self.root),))
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.metadata = patch("timetree_mcp.timezone_diagnostics.metadata.version", side_effect=PackageNotFoundError)
        self.version = self.metadata.start()
        self.addCleanup(self.metadata.stop)

    def system_file(self, key, data=None):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(utc_tzif() if data is None else data)
        return path

    def test_defaults_check_all_five_supported_zones_with_fresh_lookups(self):
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
            zone.no_cache.return_value = Mock()
            result = timezone_status()
        expected = {"Asia/Taipei", "Asia/Tokyo", "Asia/Kolkata", "America/New_York", "Europe/London"}
        self.assertEqual({item["timezone"] for item in result["zones"]}, expected)
        self.assertEqual(set(result["supported_timezones"]), expected)
        self.assertEqual({call.args[0] for call in zone.no_cache.call_args_list}, expected)
        zone.assert_not_called()
        self.assertTrue(result["all_requested_available"])
        self.assertTrue(all(item["supported_for_occurrence_tools"] for item in result["zones"]))
        self.assertFalse(result["cross_timezone_events_supported"])
        self.assertRegex(result["python_version"], r"^\d+\.\d+\.\d+$")

    def test_invalid_names_reject_before_any_probe(self):
        for value in (True, 0, [], {}, "", " ", "/etc/passwd", "Asia/../Tokyo", "Asia/./Tokyo",
                      "Asia//Tokyo", "Asia/Tokyo/", "Asia\\Tokyo", "Asia/Tokyo\n", "x" * 129):
            with self.subTest(value=value), patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
                with self.assertRaisesRegex(ValueError, "^invalid_timezone_name$"):
                    timezone_status(value)
                zone.no_cache.assert_not_called()
        self.version.assert_not_called()

    def test_validator_accepts_only_bounded_relative_timezone_keys(self):
        for key in ("Asia/Tokyo", "UTC", "Etc/GMT+5", "America/Argentina/Buenos_Aires"):
            self.assertEqual(validate_timezone_name(key), key)

    def test_available_zone_outside_allowlist_is_not_misrepresented_as_supported(self):
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
            result = timezone_status("UTC")
        self.assertEqual(len(result["zones"]), 1)
        self.assertTrue(result["zones"][0]["available"])
        self.assertFalse(result["zones"][0]["supported_for_occurrence_tools"])
        zone.no_cache.assert_called_once_with("UTC")

    def test_missing_data_and_package_are_explicit_without_fixed_offset_fallback(self):
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
            zone.no_cache.side_effect = ZoneInfoNotFoundError("/private/secret/path")
            result = timezone_status("Asia/Tokyo")
        item = result["zones"][0]
        self.assertEqual(item["status"], "missing")
        self.assertFalse(item["available"])
        self.assertEqual(item["system_data_status"], "missing")
        self.assertEqual(result["tzdata_package"], {"status": "not_installed", "version": None})
        self.assertFalse(result["all_requested_available"])
        self.assertIn("TIMETREE_PYTHON", result["install_hint"])
        self.assertIn("python -m pip install tzdata", result["install_hint"])
        self.assertNotIn("/private/secret", json.dumps(result))

    def test_effective_load_errors_are_distinguished_from_missing_data(self):
        for error in (ValueError("secret malformed path"), OSError("private permission path"), EOFError("private file")):
            with self.subTest(error=type(error).__name__), patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
                zone.no_cache.side_effect = error
                result = timezone_status("Asia/Tokyo")
            self.assertEqual(result["zones"][0]["status"], "error")
            self.assertFalse(result["zones"][0]["available"])
            self.assertNotIn(str(error), json.dumps(result))

    def test_system_tzif_probe_confirms_usable_data_without_returning_paths(self):
        self.system_file("Asia/Tokyo")
        result = timezone_status("Asia/Tokyo")
        self.assertEqual(result["zones"][0]["system_data_status"], "available")
        self.assertTrue(result["zones"][0]["available"])
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertNotIn("TZPATH", result)

    def test_corrupt_system_data_is_reported_not_treated_as_missing(self):
        self.system_file("Asia/Tokyo", b"not a timezone file")
        result = timezone_status("Asia/Tokyo")
        self.assertEqual(result["zones"][0]["system_data_status"], "error")

    def test_system_probe_does_not_open_non_regular_files(self):
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo"), \
                patch("timetree_mcp.timezone_diagnostics.Path.stat", return_value=Mock(st_mode=stat.S_IFIFO)), \
                patch("timetree_mcp.timezone_diagnostics.Path.open") as opened:
            result = timezone_status("Asia/Tokyo")
        self.assertEqual(result["zones"][0]["system_data_status"], "missing")
        opened.assert_not_called()

    def test_tzdata_metadata_does_not_claim_the_active_data_source(self):
        self.version.side_effect = None
        self.version.return_value = "2030.1"
        self.system_file("Asia/Tokyo")
        result = timezone_status("Asia/Tokyo")
        self.assertEqual(result["tzdata_package"], {"status": "installed", "version": "2030.1"})
        self.assertEqual(result["zones"][0]["system_data_status"], "available")
        self.assertNotIn("active_source", result)
        self.assertIn("not inferred", result["source_note"])

    def test_installed_package_does_not_imply_usable_zone_data(self):
        self.version.side_effect = None
        self.version.return_value = "2030.1"
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo") as zone:
            zone.no_cache.side_effect = ZoneInfoNotFoundError
            result = timezone_status("Asia/Tokyo")
        self.assertEqual(result["tzdata_package"]["status"], "installed")
        self.assertEqual(result["zones"][0]["status"], "missing")

    def test_unavailable_or_malformed_package_metadata_is_sanitized(self):
        for metadata_result in (OSError("/private/package/path"), "/private/version/path", "invalid\nversion"):
            with self.subTest(metadata_result=metadata_result):
                self.version.side_effect = metadata_result if isinstance(metadata_result, Exception) else None
                self.version.return_value = metadata_result
                with patch("timetree_mcp.timezone_diagnostics.ZoneInfo"):
                    result = timezone_status("Asia/Tokyo")
                self.assertEqual(result["tzdata_package"], {"status": "error", "version": None})
                self.assertNotIn("/private/", json.dumps(result))

    def test_diagnostic_does_not_create_files(self):
        before = list(self.root.rglob("*"))
        with patch("timetree_mcp.timezone_diagnostics.ZoneInfo"):
            timezone_status()
        self.assertEqual(list(self.root.rglob("*")), before)

    def test_real_cached_zone_cannot_hide_removed_database_file(self):
        key = "Test/LocalZone"
        path = self.system_file(key)
        try:
            zoneinfo.reset_tzpath((str(self.root),))
            cached = ZoneInfo(key)
            self.assertTrue(timezone_status(key)["all_requested_available"])
            path.unlink()
            self.assertIs(ZoneInfo(key), cached)
            result = timezone_status(key)
            self.assertEqual(result["zones"][0]["status"], "missing")
            self.assertEqual(result["zones"][0]["system_data_status"], "missing")
        finally:
            ZoneInfo.clear_cache(only_keys=[key])
            zoneinfo.reset_tzpath(self.original_paths)


if __name__ == "__main__":
    unittest.main()
