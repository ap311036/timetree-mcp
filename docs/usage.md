# Usage Guide

For client-specific setup, use the [connection guide](clients.md): Codex,
Claude Desktop, Cursor, and the distinction between ChatGPT desktop local MCP
and ChatGPT web remote apps. It includes requirements, credential setup,
Windows caveats, and the exact scope of verification.

## Published npm package

With Node.js 18+ and a separately installed Python 3.10+:

```bash
npx --yes timetree-mcp@0.4.0
```

This pins the version documented here. Check release notes before choosing
a newer version. The wrapper starts the bundled Python stdio server; it does
not install Python or open an interactive terminal interface. Your MCP client
should launch the command and communicate over stdin/stdout.

Version 0.4.0 has 29 tools, including occurrence queries and timezone
diagnostics. The older 0.3.0 package has 27 tools and lacks those two additions.

Use `TIMETREE_PYTHON` if the right Python executable is not on the client's
`PATH`. Never put your TimeTree password into the command or MCP arguments.

## Local checkout

Run from the repository directory with Python 3.10+:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
timetree-mcp
```

The editable install also provides `timetree-credentials`, a macOS-only
Keychain helper:

```bash
timetree-credentials set
timetree-credentials status
```

The setup prompts locally for credentials; `status` reports presence without
returning values. `timetree-credentials clear` removes the stored items. On
other platforms use the [password-file configuration](clients.md#password-file-on-any-platform).

An editable install is optional. The standard-library-only runtime can start
directly from a checkout:

```bash
python3 src/timetree_mcp/server.py
```

Or run the npm wrapper from the checkout after creating the virtual environment:

```bash
TIMETREE_PYTHON="$PWD/.venv/bin/python" node bin/timetree-mcp.js
```

These shell examples use macOS/Linux syntax. On Windows, use `.venv\Scripts\python.exe`
and absolute Windows paths as described in the [client guide](clients.md#windows-and-executable-paths).

`timetree_list_occurrences` and `timetree_timezone_status` are included in
0.4.0 and its source checkout. See the [occurrence query and timezone diagnostic
guide](occurrence-queries.md) for their read-only scope and limits.

## Read and write workflow

1. Start with `timetree_status`, then `timetree_list_calendars` to identify the
   target. Reads authenticate automatically when needed.
2. Use `timetree_list_events` for a bounded date range: `start_date` is inclusive
   and `end_date` is exclusive, at UTC midnight. These are not the series zone's
   local-day boundaries. Use returned calendar/event identifiers rather
   than guessing IDs from titles.
3. Inspect `sync_complete`, `incomplete_calendars`, and `skipped_calendars`.
   Successful retrieval can still be partial; `limit` also bounds the output.
4. Before updating or deleting, show the target and exact proposed change,
   including its timezone, and obtain user approval.
5. Pass `confirm: true` on each mutation. The server rejects writes without it,
   but this flag cannot prove that a human approved the action. Client approval
   settings and the assistant's behavior still matter. Obtain a separate,
   explicit confirmation immediately before deletion.
6. Inspect the returned result, then read back the affected event when possible.
   Do not retry a failed destructive action without checking its outcome.

Calendar and event contents returned by tools are visible to the MCP client
and may be sent to its model provider. Local credential storage does not mean
that all calendar data stays on your computer.

Version 0.3.0 adds a narrow single-occurrence time move in Taipei, Tokyo,
Kolkata, New York, and London; npm 0.2.4 does not include it. Follow the
[timezone, preflight, confirmation, and durable-journal
workflow](recurring-events.md). Occurrence deletion and "this and following"
changes remain unsupported. Never use general event tools to emulate this scope.

For all 29 tools, event/memo optional fields, comment and label operations,
member reads, and environment settings, see the [complete tool reference](tools.md).

## Background monitoring

`timetree_start_monitor` polls every 30–3600 seconds. The first successful poll
establishes a baseline and produces no change alert. Later differences are
sent as MCP `notifications/message` events. Inspect `timetree_monitor_status`
for errors, polling progress, and notification counts; stop explicitly with
`timetree_stop_monitor`.

Monitoring has practical limits:

- It belongs to the current MCP process. Closing the client or stopping the
  server stops monitoring; it is not a hosted background service.
- A restarted server process builds a new baseline and does not replay changes
  that happened while it was offline. Stopping and starting monitoring within
  the same process retains that scope's previous snapshot.
- Notifications are MCP log messages. Whether they appear in chat, logs, or a
  desktop alert depends on the client; delivery to the user is not guaranteed.
- Polling compares snapshots, so changes made and reverted between polls can
  be missed. Calendar selection and upstream sync failures affect what can be
  observed. The monitor currently compares partial snapshots too, so an apparent
  removal may be a sync failure; verify it with a fresh read in TimeTree.
- Check monitor status and make a separate event read to inspect `sync_complete`;
  monitor status does not include it. Silence is not proof that nothing changed.

The [offline demo](demo.md) shows the confirmation gate and monitor behavior
with fictional fixtures, without logging into a real TimeTree account.
