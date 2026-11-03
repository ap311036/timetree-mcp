"""Offline contracts for user-selected prompts and static public guidance."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from timetree_mcp.server import TimeTreeMcpServer


PROMPT = "timetree_review_schedule"
URI = "timetree://guides/safe-usage"
ARGUMENTS = {"calendar_id": "42", "start_date": "2030-03-08", "end_date": "2030-03-11"}


class NoAccessReader:
    def read(self, *args, **kwargs):
        raise AssertionError("Guidance must not access credentials or calendar data")


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        self.server = TimeTreeMcpServer(NoAccessReader())
        self.addCleanup(self.server.shutdown)

    def request(self, method, params=None):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": method,
                                   "params": {} if params is None else params})

    def test_capabilities_and_static_catalogs_for_all_legacy_versions(self):
        for version in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
            with self.subTest(version=version):
                result = self.request("initialize", {"protocolVersion": version})["result"]
                self.assertEqual(result["protocolVersion"], version)
                self.assertEqual(result["capabilities"], {
                    "tools": {}, "logging": {}, "prompts": {}, "resources": {}})
        prompt = self.request("prompts/list")["result"]["prompts"]
        self.assertEqual([p["name"] for p in prompt], [PROMPT])
        self.assertEqual({a["name"] for a in prompt[0]["arguments"]}, set(ARGUMENTS))
        self.assertTrue(all(a["required"] for a in prompt[0]["arguments"]))
        resources = self.request("resources/list")["result"]["resources"]
        self.assertEqual([r["uri"] for r in resources], [URI])
        self.assertEqual(resources[0]["mimeType"], "text/markdown")
        self.assertEqual(self.request("resources/templates/list")["result"], {"resourceTemplates": []})
        self.assertEqual(len(self.request("tools/list")["result"]["tools"]), 29)

    def test_does_not_claim_unimplemented_2026_protocol(self):
        result = self.request("initialize", {"protocolVersion": "2026-07-28"})["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")

    def test_prompt_is_read_only_and_uses_exact_utc_window(self):
        result = self.request("prompts/get", {"name": PROMPT, "arguments": ARGUMENTS})["result"]
        self.assertEqual(len(result["messages"]), 1)
        message = result["messages"][0]
        self.assertEqual((message["role"], message["content"]["type"]), ("user", "text"))
        text = message["content"]["text"]
        self.assertIn("read-only", text)
        self.assertIn("UTC", text)
        self.assertIn("2030-03-08T00:00:00+00:00", text)
        self.assertIn("2030-03-11T00:00:00+00:00", text)
        self.assertIn('"calendar_id": "42"', text)
        for warning in ("complete", "has_more", "free", "untrusted", "credentials"):
            self.assertIn(warning, text)

    def test_prompt_rejects_invalid_arguments_without_echoing_input(self):
        cases = [None, [], {}, {**ARGUMENTS, "extra": "DO_NOT_ECHO"}]
        for name in ARGUMENTS:
            for value in (None, True, 42, [], {}, "DO_NOT_ECHO"):
                cases.append({**ARGUMENTS, name: value})
        for calendar in ("web-calendar-code", "１", "1" * 41, "42\n", "../42"):
            cases.append({**ARGUMENTS, "calendar_id": calendar})
        for start, end in (("2030-02-30", "2030-03-02"), ("2030-3-08", "2030-03-11"),
                           ("2030-03-08", "2030-03-08"), ("2030-03-09", "2030-03-08"),
                           ("1979-12-31", "1980-01-01"), ("2030-01-01", "2030-04-05")):
            cases.append({**ARGUMENTS, "start_date": start, "end_date": end})
        for arguments in cases:
            with self.subTest(arguments=arguments):
                response = self.request("prompts/get", {"name": PROMPT, "arguments": arguments})
                self.assertEqual(response["error"]["code"], -32602)
                self.assertNotIn("DO_NOT_ECHO", json.dumps(response))

    def test_prompt_allows_exact_93_day_limit(self):
        result = self.request("prompts/get", {"name": PROMPT, "arguments": {
            **ARGUMENTS, "start_date": "2030-01-01", "end_date": "2030-04-04"}})
        self.assertIn("result", result)

    def test_static_resource_has_no_reader_or_environment_dependency(self):
        expected = self.request("resources/read", {"uri": URI})["result"]
        with patch.dict(os.environ, {"TIMETREE_PASSWORD": "PRIVATE_SENTINEL", "TIMETREE_PASSWORD_FILE": "/private/credential"}):
            actual = self.request("resources/read", {"uri": URI})["result"]
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual["contents"]), 1)
        content = actual["contents"][0]
        self.assertEqual((content["uri"], content["mimeType"]), (URI, "text/markdown"))
        self.assertIn("confirm", content["text"])
        self.assertIn("not affiliated", content["text"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(actual))
        self.assertNotIn("/private/credential", json.dumps(actual))

    def test_rejects_arbitrary_resource_paths_and_does_not_echo_them(self):
        for uri in ("file:///PRIVATE_SENTINEL", "https://PRIVATE_SENTINEL", URI + "?secret=PRIVATE_SENTINEL",
                    URI + "/../PRIVATE_SENTINEL", URI.upper()):
            with self.subTest(uri=uri):
                response = self.request("resources/read", {"uri": uri})
                self.assertEqual(response["error"]["code"], -32002)
                self.assertNotIn("PRIVATE_SENTINEL", json.dumps(response))
        for uri in (None, 42, True, [], {}):
            self.assertEqual(self.request("resources/read", {"uri": uri})["error"]["code"], -32602)

    def test_valid_mcp_metadata_is_accepted_without_being_embedded(self):
        for method, params in (("prompts/list", {}), ("resources/list", {}),
                               ("resources/templates/list", {}),
                               ("prompts/get", {"name": PROMPT, "arguments": ARGUMENTS}),
                               ("resources/read", {"uri": URI})):
            response = self.request(method, {**params, "_meta": {"progressToken": "PRIVATE_SENTINEL"}})
            self.assertIn("result", response)
            self.assertNotIn("PRIVATE_SENTINEL", json.dumps(response))

    def test_invalid_params_and_unsupported_pagination_fail_as_protocol_errors(self):
        for method in ("prompts/list", "resources/list", "resources/templates/list", "prompts/get", "resources/read"):
            null_response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": None})
            self.assertEqual(null_response["error"]["code"], -32602)
            for params in ([], "DO_NOT_ECHO", {"unexpected": "DO_NOT_ECHO"}, {"_meta": "DO_NOT_ECHO"}):
                with self.subTest(method=method, params=params):
                    response = self.request(method, params)
                    self.assertEqual(response["error"]["code"], -32602)
                    self.assertNotIn("DO_NOT_ECHO", json.dumps(response))
        for method in ("prompts/list", "resources/list", "resources/templates/list"):
            self.assertEqual(self.request(method, {"cursor": "DO_NOT_ECHO"})["error"]["code"], -32602)
        response = self.request("prompts/get", {"name": "DO_NOT_ECHO", "arguments": ARGUMENTS})
        self.assertEqual(response["error"]["code"], -32602)
        self.assertNotIn("DO_NOT_ECHO", json.dumps(response))

    def test_catalog_mutation_does_not_change_later_responses(self):
        self.request("prompts/list")["result"]["prompts"][0]["arguments"].clear()
        self.request("resources/list")["result"]["resources"][0]["uri"] = "file:///wrong"
        self.assertEqual(len(self.request("prompts/list")["result"]["prompts"][0]["arguments"]), 3)
        self.assertEqual(self.request("resources/list")["result"]["resources"][0]["uri"], URI)

    def test_formal_stdio_entrypoint_works_offline_without_credential_environment(self):
        root = Path(__file__).resolve().parents[1]
        messages = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "prompts/list"},
            {"id": 3, "method": "prompts/get", "params": {"name": PROMPT, "arguments": ARGUMENTS}},
            {"id": 4, "method": "resources/list"},
            {"id": 5, "method": "resources/read", "params": {"uri": URI}},
        ]
        process = subprocess.run([sys.executable, "-I", "-B", str(root / "src/timetree_mcp/server.py")],
            input="".join(json.dumps({"jsonrpc": "2.0", **m}) + "\n" for m in messages),
            capture_output=True, text=True, timeout=10, env={"PATH": os.defpath})
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])
        self.assertTrue(all("result" in r for r in replies), replies)
        self.assertEqual(replies[3]["result"]["resources"][0]["uri"], URI)


if __name__ == "__main__":
    unittest.main()
