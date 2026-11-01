# TimeTree MCP

**Read, update, and monitor your TimeTree calendars from an MCP assistant.**

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

[![npm](https://img.shields.io/npm/v/timetree-mcp?color=blue)](https://www.npmjs.com/package/timetree-mcp)
[![CI](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ap311036/timetree-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Node.js >=18](https://img.shields.io/badge/node-%3E%3D18-339933)](package.json)
[![Python >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-3776AB)](pyproject.toml)

TimeTree MCP is a [Model Context Protocol](https://modelcontextprotocol.io/)
server for calendar search, events, memos, comments, and change monitoring.
It runs locally over stdio, connects directly to TimeTree over HTTPS, and requires `confirm: true` for
calendar writes. Keep credentials in macOS Keychain or a local password file.

```bash
npx --yes timetree-mcp@0.4.0
```

**Python 3.10+ is required even when using npm.** The wrapper also requires
Node.js 18+. Configure credentials and an MCP client before using calendar tools;
the command above starts a stdio server, not an interactive chat interface.

Version **0.4.0** provides **29 tools**, adding read-only recurrence occurrence
queries and timezone diagnostics to the 27 tools in 0.3.0. See
[occurrence queries and timezone diagnostics](#occurrence-queries-and-timezone-diagnostics)
for the supported rules and validation limits.

[Quick start](#quick-start) · [Client setup and validation status](docs/clients.md) ·
[Reproducible demo](docs/demo.md) · [Tool reference](#mcp-tools)

Try asking your assistant:

- “Summarize this week's family events and identify overlapping appointments.”
- “Find next month's events mentioning the dentist.”
- “Prepare a family dinner for Saturday at 2 pm; show me the details before creating it.”
- “While this session stays open, watch the family calendar for changes.”

These are example prompts, not captured TimeTree responses. See the
[demo](docs/demo.md) for reproducible steps and the scope of its verification.

## What it does

| Capability | Details |
|---|---|
| Read and search | Calendars, date-range and keyword event queries, memos, comments, labels, members, and virtual members. |
| Write with a confirmation flag | Create, update, and delete events, memos, and comments; merge-update label names and colors. |
| Event details | Timed/all-day events, notes, locations, URLs, labels, attendees, alerts, checklists, virtual attendees, and existing file UUID references. |
| Move one recurring occurrence | Preflight and move a supported DAILY/WEEKLY occurrence in Taipei, Tokyo, Kolkata, New York, or London, with strict timezone checks and a durable operation journal. |
| Keep secrets out of tool arguments | Credentials come from the local process environment, a password file, or macOS Keychain. Session cookies and CSRF tokens remain in process memory. |
| Monitor event changes | Poll every 30–3600 seconds and emit MCP notifications for added, updated, or removed events. |
| Run with few dependencies | Python standard-library runtime; npm is a launcher for the included Python server. |

`confirm: true` is an enforced API gate. It does **not** prove that the assistant
asked a human for approval. Configure your client to review writes, and verify
the target calendar, event, time zone, and scope before approving a change.

Credentials are sent to TimeTree for authentication over HTTPS. Calendar content
is returned to the MCP client and may be processed by its AI provider; local
credential storage does not make calendar queries local-only.

> [!IMPORTANT]
> This project is not affiliated with TimeTree. The official Connect App/API
> [was discontinued on December 22, 2023](https://timetreeapp.com/intl/en/newsroom/2023-07-23/connectapp-api).
> This server uses private TimeTree Web endpoints that may change without notice.
> Follow TimeTree's terms and policies.

## Quick start

### 1. Check requirements

- Python 3.10 or newer and a TimeTree account.
- Node.js 18 or newer for the npm launcher.
- A client that can launch a local **stdio** MCP server.
- macOS for Keychain support; password files or environment variables are available on other platforms.
- An IANA timezone database for occurrence moves. Python uses the system database
  or the optional `tzdata` package; missing data causes rejection, not a fixed-offset fallback.

Codex, Claude Desktop, and Cursor setup examples are in the
[client guide](docs/clients.md), with verification status stated separately.
ChatGPT desktop builds with local MCP settings can use this stdio command,
according to OpenAI's documentation; the desktop UI has not been tested here.
ChatGPT web custom apps/plugins need a remote MCP endpoint, which this package
does not provide. See the guide for the requirements of each client.

### 2. Configure credentials

**Password file (cross-platform):** create a local file containing only your
TimeTree password using a local editor. On macOS/Linux, restrict access:

```bash
chmod 600 /absolute/path/to/timetree-password
```

Give the MCP process these environment variables through its client configuration:

```text
TIMETREE_EMAIL=your-email@example.com
TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password
```

Use the appropriate file permissions on Windows. Do not put passwords in chat,
tool arguments, committed files, or shell command history. `TIMETREE_PASSWORD`
is also supported, but a password file avoids storing the password in client JSON.

**macOS Keychain:** the setup helper is available in a source checkout:

```bash
git clone https://github.com/ap311036/timetree-mcp.git
cd timetree-mcp
python3 src/timetree_mcp/credentials.py set
python3 src/timetree_mcp/credentials.py status
```

The password prompt hides input. Status reports presence only, not values.
After setup, the npm launcher can read these Keychain items automatically; omit
the credential environment variables in client configuration. To remove the items:

```bash
python3 src/timetree_mcp/credentials.py clear
```

An environment password takes precedence over a password file, then Keychain.
The email environment variable takes precedence over Keychain.

### 3. Connect your client

For Codex with a password file:

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp@0.4.0
```

For macOS Keychain, omit both `--env` options. If a `timetree` entry already exists,
review it before replacing it. See [client setup](docs/clients.md) for Claude
Desktop, Cursor, executable paths, and troubleshooting.

Ask your assistant to run `timetree_status`, then `timetree_list_calendars`.
Authentication happens through the configured credentials; never paste a password
into the conversation.

### Install from source

As an alternative to npm, from the checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell.
This installs `timetree-mcp` and `timetree-credentials`. You can also run the server
without installation using `python3 src/timetree_mcp/server.py`.

## MCP tools

| Group | Tools |
|---|---|
| Session | `timetree_login`, `timetree_logout`, `timetree_status`, `timetree_keychain_status` |
| Read | `timetree_list_calendars`, `timetree_list_events`, `timetree_list_memos`, `timetree_list_event_comments` |
| Calendar metadata | `timetree_get_calendar_labels`, `timetree_get_calendar_members`, `timetree_get_calendar_virtual_members` |
| Events | `timetree_create_event`, `timetree_update_event`, `timetree_delete_event` |
| Memos | `timetree_create_memo`, `timetree_update_memo`, `timetree_delete_memo` |
| Comments | `timetree_add_event_comment`, `timetree_update_event_comment`, `timetree_delete_event_comment` |
| Labels | `timetree_update_calendar_labels` |
| Occurrence moves | `timetree_get_occurrence_for_edit`, `timetree_move_occurrence`, `timetree_occurrence_operation_status` |
| Monitoring | `timetree_start_monitor`, `timetree_monitor_status`, `timetree_stop_monitor` |
| Occurrence queries and diagnostics (0.4.0) | `timetree_list_occurrences`, `timetree_timezone_status` |

All event, memo, comment, and label writes require `confirm: true`. General event
time inputs accept ISO-8601 strings or Unix milliseconds; occurrence moves require
whole seconds and an explicit numeric UTC offset matching the series timezone. List calendars and inspect returned
event identifiers before an update or deletion. Omitted labels are preserved by
label merge updates.

**New in 0.3.0 (not included in 0.2.4):** move one timed DAILY/WEEKLY occurrence in
`Asia/Taipei`, `Asia/Tokyo`, `Asia/Kolkata`, `America/New_York`, or `Europe/London`, preserving its duration and supported
metadata. Preflight checks the exact original instant and observed parent/child
identity. The move requires `confirm: true`, the returned revision, and a stable
operation ID. A durable local journal blocks duplicate dispatch after interruption;
the child creation and parent exclusion are still two non-atomic writes.
See the [supported scope and recovery limits](docs/recurring-events.md) before use.

Start and end must use the same supported IANA zone, dates must be from 1980
onward, and duration must be at most 24 hours. Missing timezone data, mismatched
offsets, ambiguous/nonexistent local times, and intervals crossing an offset
change are rejected. Safe local wall-clock recurrences can span DST changes;
New York/London validation checks at most 3660 local days from the series anchor
and rejects unsafe intervening occurrences. Preflight and success return the
series `timezone`. Synthetic tests cover these rules; no live multi-timezone
calendar writes or client UI round trips were performed for this feature.

Occurrence deletion and "this and following" edits are not provided. Do not use
a general event update/delete as a substitute for single-occurrence scope.
`timetree_list_events` still does not expand recurrence rules, so a date-range
query can miss later occurrences. General event tools accept raw recurrence
arrays without promising support for arbitrary recurrence rules or edit scopes.
There are no file-upload, calendar-management, or member invitation/removal tools.
See the [complete tool reference](docs/tools.md) for all 29 tools, every argument group,
optional field, environment setting, and unsupported operation.

### Occurrence queries and timezone diagnostics

`timetree_list_occurrences` expands a supported timed series into dated
occurrences for a window of at most 93 days. It includes single and detached
events, with explicit reporting when unsupported rules or unsafe timezone
cases make the result incomplete. The query accepts a conservative subset of
DAILY, WEEKLY, MONTHLY, and YEARLY rules in the same five supported zones.
Monthly and yearly support is **read-only**; single-occurrence moves remain
limited to DAILY/WEEKLY. An incomplete or truncated result cannot establish
that a time is free. See [query scope and examples](docs/occurrence-queries.md).

`timetree_timezone_status` diagnoses timezone-data availability in the Python
environment running the server, without authentication or calendar access.
It distinguishes supported occurrence zones from other requested zones and
reports missing data with setup guidance. This check does not validate a live
calendar or make unsupported edit scopes safe.

Version 0.4.0 also uses fresh timezone-data lookups for new
occurrence query/edit validation and rejects weekly `BYDAY` rules that omit the series'
anchor weekday. Replaying an already recorded operation remains offline and
does not require timezone data. These additional guards are not in npm 0.3.0.

### Monitoring limitations

The first poll establishes a baseline. Later changes are delivered as MCP
`notifications/message`; whether they are visible depends on the client. This
is polling, not instant push, and does not send email or phone notifications.
Monitoring stops when the MCP process exits and does not run as a persistent
cloud service. Restarting the process requires starting monitoring again.
Use `timetree_monitor_status` to inspect the last poll, errors, and notification count.
Partial snapshots can appear as removals; verify apparent deletions with a fresh read.

### Read completeness

`sync_complete` reports whether synchronization of the selected calendars finished;
it does not mean a result filtered by `limit` contains every event. Repeated or
invalid cursors and the 25-page cap are reported in `incomplete_calendars`, with
already-read events preserved. Upstream `-425` errors preserve `error_code`;
failed calendars with no retrieved events appear in `skipped_calendars` and make
`sync_complete` false.

Event date-range boundaries are UTC midnight, with an inclusive `start_date`
and exclusive `end_date`; they are not local-day boundaries in the event's zone.
Keyword search covers titles, notes, locations, calendar names/IDs, and returned
label names. Memo reads have a separate `updated_after` filter but currently
do not expose synchronization completeness fields.

Calendar listing and default event sync include active calendars only:
`deactivated_at` missing, `null`, or numeric `0` means active; nonzero numbers mean
inactive. Invalid types produce an error. Explicitly selecting an inactive
calendar is rejected before sync. Both the direct HTTPS and legacy browser readers
follow these rules. Treat incomplete reads as partial evidence when summarizing a
schedule or interpreting apparent removals.

## Documentation and development

- [Client setup](docs/clients.md): configuration examples and validation status.
- [Demo](docs/demo.md): reproducible behavior and verification boundaries.
- [Usage guide](docs/usage.md): local development and monitoring.
- [Complete tool reference](docs/tools.md): all 29 tools, supported fields, defaults, and configuration.
- [Occurrence queries](docs/occurrence-queries.md): recurrence expansion and timezone diagnostics added in 0.4.0.
- [Isolated calendar tests](docs/isolated-calendar-testing.md): live-test prerequisites, current blocker, and synthetic cases.
- [Agent guide](docs/agent-guide.md): tool workflow and error handling.
- [Companion skill](skills/timetree-mcp/SKILL.md): operating guidance for agents that support skills.
- [Publishing](docs/publishing.md): npm, version checks, MCP Registry metadata, and GitHub Actions.
- [Discoverability](docs/discoverability.md): accurate project metadata and search-appearance limits.
- [`server.json`](server.json): manifest for `io.github.ap311036/timetree-mcp`.
- [Security policy](SECURITY.md) · [Contributing](CONTRIBUTING.md).

```bash
npm test
npm run pack:check
```

`npm test` runs the Node wrapper/metadata tests and the Python unittest suite.
Python tests can also run independently:

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The Python server lives in `src/timetree_mcp/`, the npm launcher in `bin/`, and
tests in `tests/` and `test/`. The legacy ego-browser transport remains in
`src/timetree_mcp/browser.py` for compatibility; the server defaults to direct HTTPS.

## License

[MIT](LICENSE).
