const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const root = path.resolve(__dirname, "..");
const packageJson = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));

test("NPM metadata identifies the GitHub MCP server", () => {
  assert.equal(packageJson.mcpName, "io.github.ap311036/timetree-mcp");
  assert.deepEqual(packageJson.repository, {
    type: "git",
    url: "https://github.com/ap311036/timetree-mcp",
  });
  assert.ok(packageJson.files.includes("server.json"));
  assert.ok(packageJson.files.includes("skills/**/SKILL.md"));
});

test("MCP Registry manifest matches the published package", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "server.json"), "utf8"));
  const pyproject = fs.readFileSync(path.join(root, "pyproject.toml"), "utf8");
  const serverSource = fs.readFileSync(path.join(root, "src/timetree_mcp/server.py"), "utf8");
  const npmPackage = manifest.packages.find((item) => item.registryType === "npm");

  assert.equal(manifest.name, "io.github.ap311036/timetree-mcp");
  assert.equal(manifest.repository.id, "1343387026");
  assert.equal(manifest.version, packageJson.version);
  assert.match(pyproject, new RegExp(`version = "${packageJson.version}"`));
  assert.match(serverSource, new RegExp(`SERVER_VERSION = "${packageJson.version}"`));
  assert.equal(npmPackage.identifier, packageJson.name);
  assert.equal(npmPackage.version, packageJson.version);
  assert.equal(npmPackage.transport.type, "stdio");

  const variables = new Map(npmPackage.environmentVariables.map((item) => [item.name, item]));
  assert.equal(variables.get("TIMETREE_PASSWORD").isSecret, true);
  assert.equal(variables.get("TIMETREE_EMAIL").isRequired, false);
  assert.equal(variables.get("TIMETREE_PASSWORD_FILE").isSecret, true);
});

test("release documentation and workflow are present", () => {
  assert.ok(fs.existsSync(path.join(root, "docs/publishing.md")));
  assert.ok(fs.existsSync(path.join(root, "docs/agent-guide.md")));
  assert.ok(fs.existsSync(path.join(root, "skills/timetree-mcp/SKILL.md")));
  assert.ok(fs.existsSync(path.join(root, ".github/workflows/publish.yml")));
});
