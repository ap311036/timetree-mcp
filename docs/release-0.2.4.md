# 0.2.4

Event queries now accept numeric calendar IDs consistently. Previously, passing
an integer calendar ID could return no events because normalized event records
store their calendar IDs as strings. The query filter now converts the requested
ID to a string before comparing it. Regression tests cover numeric and string IDs
and filtering across calendars. Thanks to @YINWEIHSU for the fix in
[PR #1](https://github.com/ap311036/timetree-mcp/pull/1).

- Add MCP tool annotations describing read-only, destructive, idempotent, and
  external-world behavior. Existing confirmation requirements for TimeTree writes
  remain enforced.
- Add the M8ven project badge to the README.
- Synchronize the npm, Python, server, and MCP Registry manifest versions.

Validation: 5 Node tests and 54 Python tests pass with `npm test`.
`npm run pack:check` passes and includes the launcher, Python source, manifest,
documentation, and companion skill without credentials, tests, or Python caches.
