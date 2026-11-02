# TimeTree MCP Agent Guide

Use this guide when connecting an assistant or coding agent to TimeTree MCP.

## Start and setup

The npm wrapper starts the stdio MCP server:

```bash
npx --yes timetree-mcp@0.5.0
```

For normal server launches, configure a complete `TIMETREE_EMAIL` and
`TIMETREE_PASSWORD` or `TIMETREE_PASSWORD_FILE` pair. Clear all three variables
to use Keychain; do not mix credentials from different accounts. Never put a real
password in a prompt, MCP tool argument, repository file, or chat message.

From a source checkout on macOS (the npm wrapper does not install these CLI helpers):

```bash
python3 src/timetree_mcp/credentials.py set
python3 src/timetree_mcp/credentials.py status
```

`timetree_keychain_status` reports presence only. `timetree_status` reports
authentication and transport state without returning credential values.
Since 0.5.0, status checks configuration/presence without reading
password-file contents or Keychain secrets; `credentials_configured` is not
proof of valid credentials. Explicit sources skip Keychain and partial/empty
explicit configurations fail instead of falling back. See
[credential resolution](clients.md#credential-resolution-and-status).
The public package needs its own local credentials; do not import a browser
or private cloud MCP session to make its login check pass.

Since version 0.4.0, `timetree_timezone_status` can diagnose IANA
data before authentication. It performs no calendar or credential access.
This and `timetree_list_occurrences` require 0.4.0 or newer; npm 0.3.0 has 27 tools and
does not contain them. Inspect `tools/list` instead of assuming availability.

## Tool workflow

Use read tools freely after authentication:

1. `timetree_login` when an explicit login is useful.
2. `timetree_list_calendars` to identify the target calendar.
3. `timetree_list_events` for date-range or keyword queries.
4. `timetree_list_memos` for category-2 memos.
5. Metadata tools for comments, labels, members, and virtual members.

When `timetree_list_occurrences` is available, use it to read
dated occurrences in a window of at most 93 × 24 hours. Inspect `complete`,
`coverage_complete`, `has_more`, and `unsupported`. Use actual event/series UUIDs
from the result, not its synthetic `occurrence_id`, when preparing a later
operation. MONTHLY/YEARLY support is query-only; a returned occurrence does not
make the series editable. See [query scope and rules](occurrence-queries.md).

For writes, follow this sequence:

```text
inspect the target
-> explain the requested mutation
-> obtain the user's approval
-> call the write tool with confirm=true
-> report the returned result
```

The flag is a programmatic guard, not proof of user approval; the agent must
obtain that approval. Every write tool rejects calls without `confirm: true`.
This includes event, memo, comment, and label changes. Deletion is irreversible, so require a
separate explicit user confirmation immediately before the call.

## Single-occurrence moves (0.3.0)

Use `timetree_get_occurrence_for_edit` for a known series UUID and exact original
`occurrence_start_at`. Proceed only if `editable` is true, explain the original
and proposed times, and obtain approval. Call `timetree_move_occurrence` with
that revision, the desired start, a stable `operation_id`, and `confirm: true`.
It preserves duration and supported metadata; narrow rule/identity checks can
reject a series. Supported zones are Taipei, Tokyo, Kolkata, New York, and
London, with the same IANA zone at both ends. Include seconds and a numeric
offset matching that zone and date. Inspect the returned `timezone`; never
silently correct a DST ambiguity or offset mismatch. See [the full supported
scope and timezone requirements](recurring-events.md).

After interruption, inspect `timetree_occurrence_operation_status` using the
same operation ID. This is a local record, not a live calendar verification.
Never retry uncertainty with another ID, delete/change the journal, or use raw
event tools to roll back. Two writes are not atomic and a partial edit can
remain; this version has no automatic resume or unlock. Do not run write tools
during automatic monitoring. npm 0.2.4 does not have these tools.

## Monitoring

`timetree_start_monitor` polls every 30–3600 seconds. The first poll establishes
a baseline; later additions, updates, and removals are emitted as
`notifications/message`. Monitoring belongs to the MCP process and stops when
the process exits. A restarted process establishes a new baseline. Clients may
hide logging notifications; delivery does not guarantee a user-visible alert.
This is not a hosted push service.

Version **0.5.0** accepts only complete, validated snapshots and keeps
stable pending batches in bounded memory. Inspect `last_check_complete`,
`last_success`, `pending_batches`, and `delivery_blocked`, as well as
`last_error`. `not_sent` batches can be retried after a valid source check;
`unknown` means a write may have occurred, blocks later delivery across scopes,
and must not be retried automatically. Successful callback/stdio writes only
increment a local transport count; they do not establish client receipt.

Stop/start preserves this state within the same process. Process restart loses
it without resolving unknown results. A stop may return `stopping: true` while
an old read/callback finishes; do not start another worker until it exits.
These changes are not in npm 0.4.0. See [monitoring state and delivery](monitoring.md).

## Error handling

- If credentials are missing, show the setup action and stop; do not ask the
  user to paste a password into chat.
- If a calendar returns sync error `-425`, preserve the usable calendars and
  report `skipped_calendars`.
- If a write fails, report the error without retrying destructive operations
  automatically.
- Treat the TimeTree web endpoints as unofficial and expect service changes.

## Client setup and limits

See [client configurations and validation status](clients.md), the
[complete tool reference](tools.md), [reproducible offline demo](demo.md),
and [recurring-event limitations](recurring-events.md).
Event content returned by tools is visible to the MCP client and may be sent to
its model provider; keeping credentials local does not keep tool results local.
