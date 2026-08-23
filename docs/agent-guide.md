# TimeTree MCP Agent Guide

Use this guide when connecting an assistant or coding agent to TimeTree MCP.

## Start and setup

The published NPM wrapper starts the stdio MCP server:

```bash
npx --yes timetree-mcp
```

Credentials are resolved from macOS Keychain first, then the process
environment and optional password file. Never put a real password in a prompt,
MCP tool argument, repository file, or chat message.

Useful setup commands:

```bash
./scripts/setup_timetree_keychain.sh
timetree-credentials status
```

`timetree_keychain_status` reports presence only. `timetree_status` reports
authentication and transport state without returning credential values.

## Tool workflow

Use read tools freely after authentication:

1. `timetree_login` when an explicit login is useful.
2. `timetree_list_calendars` to identify the target calendar.
3. `timetree_list_events` for date-range or keyword queries.
4. `timetree_list_memos` for category-2 memos.
5. Metadata tools for comments, labels, members, and virtual members.

For writes, follow this sequence:

```text
inspect the target
-> explain the requested mutation
-> call the write tool with confirm=true
-> report the returned result
```

Every write tool rejects calls without `confirm: true`. This includes event,
memo, comment, and label changes. Deletion is irreversible, so require a
separate explicit user confirmation immediately before the call.

## Monitoring

`timetree_start_monitor` polls every 30–3600 seconds. The first poll establishes
a baseline; later additions, updates, and removals are emitted as
`notifications/message`. Monitoring belongs to the MCP process and stops when
the client exits.

## Error handling

- If credentials are missing, show the setup action and stop; do not ask the
  user to paste a password into chat.
- If a calendar returns sync error `-425`, preserve the usable calendars and
  report `skipped_calendars`.
- If a write fails, report the error without retrying destructive operations
  automatically.
- Treat the TimeTree web endpoints as unofficial and expect service changes.
