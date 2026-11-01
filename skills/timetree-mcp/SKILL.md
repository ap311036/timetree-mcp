---
name: timetree-mcp
description: Use when reading or safely managing TimeTree calendars, events, memos, comments, labels, members, or background change monitoring through the TimeTree MCP server.
---

# TimeTree MCP

Use the `timetree-mcp` MCP tools for direct HTTPS access to TimeTree.

## Safety rules

- Never request or repeat a TimeTree password in chat.
- Prefer macOS Keychain or `TIMETREE_PASSWORD_FILE` over `TIMETREE_PASSWORD`.
- Check the target calendar and event before any mutation.
- Every write requires `confirm: true`.
- Require a fresh confirmation before irreversible deletion.
- Do not infer a target event when multiple events match; ask the user to
  choose one.

## Typical workflow

1. Call `timetree_status` or `timetree_keychain_status` if setup is unclear.
2. Call `timetree_login` when an explicit login is needed.
3. Use `timetree_list_calendars` and read tools to inspect the account.
4. Explain the exact proposed event, memo, comment, or label change.
5. Call the corresponding write tool with `confirm: true` only after approval.
6. Use `timetree_monitor_status` when monitoring is enabled.

The server uses a direct HTTPS session by default. Cookies and CSRF tokens stay
in process memory. The TimeTree web API is unofficial and may change.

## Single-occurrence moves (0.3.0)

- Use `timetree_get_occurrence_for_edit` before `timetree_move_occurrence`.
- Only move the exact original occurrence when `editable` is true and the user
  approved the new time. Preserve the returned revision and use `confirm: true`.
- Keep one stable operation ID for the request. On interruption, read
  `timetree_occurrence_operation_status`; it reports local history only.
- Never bypass an uncertain series lock with another ID, journal path, host, or
  raw event write. Do not retry or roll back automatically.
- Do not substitute a whole-series update/delete for a single-occurrence request.
- Start/end must share a supported IANA zone: Asia/Taipei, Asia/Tokyo,
  Asia/Kolkata, America/New_York, or Europe/London. Supply whole-second times
  with the correct numeric offset and inspect the returned `timezone`.
- Reject ambiguous/nonexistent local times, cross-zone intervals, missing
  timezone data, or unsafe DST boundaries; do not bypass these errors.
- These tools require version 0.3.0 or newer; npm 0.2.4 lacks them.

## Read limits and other tools

- Event date queries use UTC-midnight boundaries, not the event zone's local
  days. Read a wider range when preparing a local-day view.
- Recurrence rules are not expanded by event reads. A complete sync does not
  prove an agenda is complete or that a time is free.
- In version 0.4.0, `timetree_list_occurrences` expands a narrow
  timed DAILY/WEEKLY/MONTHLY/YEARLY subset for at most 93 × 24 hours. Check
  `complete`, `coverage_complete`, `has_more`, and `unsupported`; never treat
  an incomplete or truncated result as evidence of availability. Do not pass a
  synthetic `occurrence_id` to a general event write. Monthly/yearly query
  support does not authorize or enable monthly/yearly occurrence moves.
- `timetree_timezone_status` diagnoses local timezone data without credentials
  or calendar access; available data does not prove an event is editable.
- Both new read tools require 0.4.0 and are absent from npm 0.3.0. Check
  `tools/list` to verify availability.
- Version 0.4.0 also uses fresh timezone-data lookups for new occurrence query/edit
  validation and rejects weekly `BYDAY` rules that omit the anchor weekday.
  Do not repair those inputs silently. Saved operation replay remains offline
  and does not require timezone data; these new guards are not in npm 0.3.0.
- Inspect `sync_complete`, `incomplete_calendars`, and `skipped_calendars` on
  event reads. Memo reads currently omit these completeness fields.
- See [the complete tool reference](../../docs/tools.md) for all 29 tools,
  supported event/memo metadata, comments, labels, member reads, and settings.
