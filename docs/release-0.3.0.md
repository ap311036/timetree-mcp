# 0.3.0

Move one supported occurrence of a recurring event through three new MCP tools:
preflight the original date, move it with confirmation and a snapshot revision,
and inspect the durable operation journal. The server now exposes 27 tools.

- Preserve supported event metadata and duration while creating a linked child
  and appending a UTC exclusion for the original occurrence. Dispatch phases are
  recorded before each write; uncertain results remain blocked across restarts.
- Support timed DAILY/WEEKLY series in `Asia/Taipei`, `Asia/Tokyo`, `Asia/Kolkata`,
  `America/New_York`, and `Europe/London`. Validate explicit numeric offsets
  against the IANA database and preserve local recurrence times across safe DST
  changes. Reject missing timezone data, cross-zone start/end times, ambiguous or
  nonexistent local times, and intervals crossing an offset transition.
- Bound New York/London recurrence validation to 3660 local days from the series
  anchor to the original occurrence, rejecting unsafe intervening candidates.
  Supported dates start in 1980; event duration must be at most 24 hours.
- Add a Japanese README alongside English and Traditional Chinese, audit all
  27 tools, and document installation, credentials, client verification,
  recurrence scope, read completeness, and monitoring limitations.

The two calendar writes are not atomic and have no verified upstream
compare-and-swap. Concurrent recurrence edits can still be overwritten in the
final read/write gap. Never retry an uncertain move with a new operation ID or
automatically roll it back. The current snapshot must already corroborate the
series' parent/child links. Occurrence deletion, "this and following" edits,
and recurrence expansion in event listings remain unsupported. See the
[occurrence guide](recurring-events.md) before use.

Validation: 5 Node and 134 Python tests pass with `npm test`, including synthetic
DST boundaries, missing timezone data, HTTP dispatch, journal recovery and
concurrency cases. The packed launcher passed MCP `initialize`, `tools/list`
(27 tools), and `ping` without credentials or calendar requests. All three
READMEs are included in the package. No live calendar writes or client GUI
round trips were performed for this implementation.

Install with `npx --yes timetree-mcp@0.3.0`. Node.js 18+ and Python 3.10+ are
required. Occurrence moves also require an IANA timezone database; install
`tzdata` into the selected Python environment when the OS does not provide it.
