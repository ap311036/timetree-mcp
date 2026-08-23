const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const path = require("node:path");
const { test } = require("node:test");

const wrapper = path.resolve(__dirname, "../bin/timetree-mcp.js");

function runWrapper(input, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [wrapper], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
    child.stdin.end(input);
  });
}

test("forwards MCP stdio to the bundled Python server", async () => {
  const result = await runWrapper(
    `${JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping" })}\n`,
    { TIMETREE_PYTHON: "python3" },
  );

  assert.equal(result.code, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    jsonrpc: "2.0",
    id: 1,
    result: {},
  });
});

test("reports when no supported Python interpreter is available", () => {
  const result = spawnSync(process.execPath, [wrapper], {
    env: { ...process.env, TIMETREE_PYTHON: "timetree-mcp-python-does-not-exist" },
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /Python 3\.10 or newer/);
});
