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

## Single-occurrence moves (unreleased source feature)

- Use `timetree_get_occurrence_for_edit` before `timetree_move_occurrence`.
- Only move the exact original occurrence when `editable` is true and the user
  approved the new time. Preserve the returned revision and use `confirm: true`.
- Keep one stable operation ID for the request. On interruption, read
  `timetree_occurrence_operation_status`; it reports local history only.
- Never bypass an uncertain series lock with another ID, journal path, host, or
  raw event write. Do not retry or roll back automatically.
- Do not substitute a whole-series update/delete for a single-occurrence request.
- These tools are available from the source checkout, not published npm 0.2.4.
