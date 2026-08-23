#!/usr/bin/env node

"use strict";

const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const pythonCandidates = process.env.TIMETREE_PYTHON
  ? [process.env.TIMETREE_PYTHON]
  : process.platform === "win32"
    ? ["python.exe", "python"]
    : ["python3", "python"];

function supportsRequiredPython(command) {
  const result = spawnSync(
    command,
    ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"],
    { stdio: "ignore" },
  );
  return result.status === 0;
}

const python = pythonCandidates.find(supportsRequiredPython);
if (!python) {
  console.error(
    "timetree-mcp requires Python 3.10 or newer. Install it or set TIMETREE_PYTHON to its executable path.",
  );
  process.exit(1);
}

const serverPath = path.join(__dirname, "..", "src", "timetree_mcp", "server.py");
const child = spawn(python, [serverPath, ...process.argv.slice(2)], {
  env: process.env,
  stdio: "inherit",
});

child.once("error", (error) => {
  console.error(`Unable to start Python MCP server: ${error.message}`);
  process.exit(1);
});

child.once("exit", (code) => {
  process.exit(code ?? 1);
});
