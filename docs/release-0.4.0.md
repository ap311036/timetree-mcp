# 0.4.0

Read dated occurrences of supported recurring events and diagnose timezone data
with two new tools. The server now exposes 29 MCP tools.

- `timetree_list_occurrences` reads a complete selected-calendar snapshot and
  projects supported timed DAILY/WEEKLY/MONTHLY/YEARLY rules into a window of
  at most 93 days. Month-end and leap dates follow the documented subset;
  invalid calendar dates do not consume COUNT. Results include UTC/local times,
  source identities, truncation and coverage flags, and unsupported-record
  diagnostics. Unsafe DST or parent/child relationships cannot produce a
  falsely complete agenda.
- `timetree_timezone_status` checks local IANA data without authentication,
  calendar access, or writes. It distinguishes data availability from supported
  zones, checks fresh data rather than cached objects, and provides repair
  guidance without returning local paths or credentials.
- New query and edit validation uses fresh timezone data, handles corrupt TZif
  files with safe errors, and rejects weekly anchors inconsistent with BYDAY.
  Saved operation replay remains local and independent of timezone data.
- English, Traditional Chinese, and Japanese documentation covers all 29 tools,
  installation, completeness flags, and safety boundaries. Project metadata
  uses functional descriptions while retaining the clear non-affiliation
  notice. Search appearance or ranking improvements are not guaranteed.

Monthly/yearly expansion is **read-only** and supports a restricted rule subset;
single-occurrence moves remain limited to the validated DAILY/WEEKLY subset.
All-day events, unsupported zones, cross-zone endpoints, ambiguous DST times,
and other unsupported cases are rejected or reported as incomplete. Empty or
incomplete results do not prove a time is free. Existing confirmation, snapshot
revision, journal and uncertain-outcome protections remain in place; writes
still lack an upstream atomic transaction or compare-and-swap.

Validation: 5 Node and 184 Python tests pass, including synthetic month-end,
leap-year, DST cutoff, missing/corrupt timezone data, read-only HTTP, and journal
regressions. Independent recurrence enumeration and code review also passed.
The packed launcher passed MCP initialization, discovery of all 29 tools, ping,
and local timezone diagnostics without credentials or calendar requests. All
three READMEs and both new modules are included in the package.
No live calendar writes or client GUI round trips were performed. The isolated
calendar verification plan remains a plan, not a successful live-test record.

Install with `npx --yes timetree-mcp@0.4.0`. Node.js 18+ and Python 3.10+ are
required; occurrence tools also need IANA timezone data. See the
[occurrence query guide](occurrence-queries.md) and
[single-occurrence edit limits](recurring-events.md).
