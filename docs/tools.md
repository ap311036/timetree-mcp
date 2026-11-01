# MCP tool reference

Version 0.4.0 advertises 29 tools through `tools/list`. It adds occurrence
queries and timezone diagnostics to the 27 tools in 0.3.0. The three occurrence
editing/status tools were introduced in 0.3.0 and are not included in npm 0.2.4.
Check [release and installation guidance](usage.md) before selecting a version.
The advertised tool schemas are the exact machine-readable argument reference.

All calendar writes below require the boolean `confirm: true`. The assistant
must obtain approval for the actual target and scope; the flag alone is not
proof of human approval. Reads authenticate automatically when needed. Use IDs
returned by reads, not guesses based on event titles. Ordinary `calendar_id`
arguments accept strings or integers; occurrence tools require numeric strings.

## Session and credentials

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `timetree_status` | None | Reports authentication, transport, and credential availability; does not log in. May consult local Keychain. |
| `timetree_login` | None | Authenticates over HTTPS using locally configured credentials. |
| `timetree_logout` | None | Clears in-memory cookies and the CSRF token. Does not remove stored credentials or the occurrence journal. |
| `timetree_keychain_status` | None | Reports whether macOS Keychain email/password entries exist, without their values. |

The local `timetree-credentials set/status/clear` helper is a separate CLI, not
an MCP tool. Source installation provides this command; npm does not install
it. See [credential setup](clients.md#configure-credentials-once).

## Read and search

| Tool | Required arguments | Optional arguments and behavior |
| --- | --- | --- |
| `timetree_list_calendars` | None | Returns active calendars. |
| `timetree_list_events` | None | `calendar_id`, `start_date`, `end_date`, `query`, `limit` (1–500, default 100). Returns events overlapping a half-open range, sorted by start time. |
| `timetree_list_memos` | `calendar_id` | `updated_after` (strictly later than the timestamp), `limit` (1–500); category-2 memos. |
| `timetree_list_event_comments` | `calendar_id`, `event_uuid` | Returns active text comments from the event activity response. |
| `timetree_get_calendar_labels` | `calendar_id` | Reads label IDs, names, and colors. |
| `timetree_get_calendar_members` | `calendar_id` | `include_deactivated` (default false). |
| `timetree_get_calendar_virtual_members` | `calendar_id` | `include_deactivated` (default false). |

`start_date` is inclusive and `end_date` exclusive, both `YYYY-MM-DD` interpreted
at **UTC midnight**. If omitted, defaults use the host's current local date and
that date plus seven days. Specify both dates explicitly. For a local-day
agenda, read a sufficiently wide range and compare returned instants in the
intended timezone. There is no timezone argument for range boundaries.

Keyword matching is case-insensitive across title, note, location, calendar
name/ID, and a returned label name. It does not search comments or expand
recurrence rules. Memos and records marked as birthdays are excluded from
event query results; use the memo tool for memos.

`timetree_list_events` reports `sync_complete`, `incomplete_calendars`, and
`skipped_calendars` when applicable. A complete sync can still be truncated by
`limit`. The memo tool currently does not expose those completeness fields,
so a returned memo list is not proof of a complete calendar sync. General event
reads do not expand recurrence rules. An incomplete or truncated agenda cannot
establish that a time is free.

## Occurrence queries and timezone diagnostics (0.4.0)

These tools require version 0.4.0 or the matching source checkout. They are
absent from npm 0.3.0.

| Tool | Required arguments | Optional arguments and behavior |
| --- | --- | --- |
| `timetree_list_occurrences` | `calendar_id` (numeric string), `start_at`, `end_at` | `query` (literal title filter, at most 500 characters), `limit` (1–500, default 100). Reads a strict complete snapshot, then expands supported timed DAILY/WEEKLY/MONTHLY/YEARLY rules for a window of at most 93 × 24 hours. Reports incomplete coverage and truncation explicitly. |
| `timetree_timezone_status` | None | `timezone` (relative IANA key, 1–128 characters). Without it, checks the five supported occurrence zones. Local availability diagnostics only; no authentication or calendar access. |

Query boundaries are whole-second ISO strings with explicit numeric offsets,
`YYYY-MM-DDTHH:MM:SS±HH:MM`; `Z` is not accepted. These are absolute instants and
may use different offsets. Results overlap the half-open window and include
UTC/local times, identifiers, and source type. Inspect `complete`,
`coverage_complete`, `has_more`, and `unsupported`; a returned row is not proof
that an edit is supported. Monthly/yearly expansion is read-only and does not
extend the existing move tool's DAILY/WEEKLY rule support.

Timezone diagnostics distinguish data availability from support for occurrence
tools. System-file availability and `tzdata` package metadata are separate
observations; the active source and freshness are not inferred. See
[supported query rules, exact response semantics, and examples](occurrence-queries.md).

## Events

| Tool | Required arguments | Behavior |
| --- | --- | --- |
| `timetree_create_event` | `calendar_id`, `title`, `start_at`, `end_at`, `confirm` | Creates an event; optional fields below. |
| `timetree_update_event` | `calendar_id`, `event_uuid`, `confirm`, at least one event field | Updates selected fields on the identified event. |
| `timetree_delete_event` | `calendar_id`, `event_uuid`, `confirm` | Deletes the identified event; irreversible. |

Create/update fields include `title`, `all_day`, `start_at`, `end_at`,
`start_timezone`, `end_timezone`, `label_id` (1–10), `category`, `note`,
`location`, `url`, `attendees`, `recurrences`, `alerts`, `file_uuids`,
`checklist`, and `virtual_user_attendees`. A checklist contains objects with
`title` and `checked`. Existing file UUIDs can be referenced; there is no file
upload/download tool. Use metadata reads to obtain label/member identifiers.

General event times accept ISO-8601 strings or Unix **milliseconds**. Prefer
explicit offsets and IANA zone names. Naive times use `start_timezone`, which
defaults to UTC; `end_timezone` defaults to the chosen start timezone. This
also applies to update-time conversion: it does not infer the stored event's
timezone. These general tools do not provide the occurrence adapter's strict
DST validation. Check the intended instants before approving a write.

Creation defaults to a timed category-1 event with empty attendee, recurrence,
alert, and file arrays. `recurrences` is passed through to the unofficial API;
accepting this field is not a guarantee that arbitrary rules are valid or
that the client correctly implements series-edit scope. Updates containing
checklist or virtual attendee fields construct an attachment object; inspect
existing metadata before replacing it. Do not use a general update/delete to
emulate a single-occurrence or "this and following" edit.

## Memos, comments, and labels

| Tool | Required arguments | Optional fields or behavior |
| --- | --- | --- |
| `timetree_create_memo` | `calendar_id`, `title`, `confirm` | `date`, `note`, `label_id`, `location`, `url`, `checklist`, `virtual_user_attendees`. Date defaults to today's UTC date; stored as a category-2 all-day memo. |
| `timetree_update_memo` | `calendar_id`, `memo_uuid`, `confirm`, at least one memo field | Same content fields as creation except `date`. |
| `timetree_delete_memo` | `calendar_id`, `memo_uuid`, `confirm` | Irreversible deletion. |
| `timetree_add_event_comment` | `calendar_id`, `event_uuid`, `content`, `confirm` | Optional `silent` defaults to true. This flag is not a guarantee about every upstream notification. |
| `timetree_update_event_comment` | `calendar_id`, `event_uuid`, `comment_id`, `content`, `confirm` | Updates a comment. |
| `timetree_delete_event_comment` | `calendar_id`, `event_uuid`, `comment_id`, `confirm` | Irreversible deletion. |
| `timetree_update_calendar_labels` | `calendar_id`, `labels`, `confirm` | Non-empty list of `{id, name?, color?}`; ID 1–10, color integer. Merges by ID and preserves omitted labels/fields. A previously absent label ID requires `color`. |

Member and virtual-member tools are read-only. There are no member invitations,
removals, virtual-member creation, calendar creation/deletion, or sharing tools.

## One recurring occurrence

| Tool | Required arguments | Behavior |
| --- | --- | --- |
| `timetree_get_occurrence_for_edit` | `calendar_id`, `event_uuid`, `occurrence_start_at` | Fresh read and validation; returns `editable` and a revision. |
| `timetree_move_occurrence` | Same three fields, `new_start_at`, `expected_revision`, `operation_id`, `confirm` | Creates a linked replacement then excludes the original; preserves duration and supported metadata. |
| `timetree_occurrence_operation_status` | `operation_id` | Reads the local journal only; no live read, resume, retry, or unlock. |

See [supported timezones, DST rejection, exact formats, and recovery
limits](recurring-events.md). These editing/status tools do not expand a range;
use the separate occurrence query tool for that. Single-occurrence deletion
and "this and following" changes remain unsupported. The two writes
are non-atomic and have no upstream compare-and-swap. Never retry uncertainty
with a new operation ID or bypass the durable series lock.

## Monitoring

| Tool | Arguments | Behavior |
| --- | --- | --- |
| `timetree_start_monitor` | Optional `calendar_id`, `interval_seconds` (30–3600, default 300) | Starts one process-local polling monitor for the selected calendar or active calendars. |
| `timetree_monitor_status` | None | Reports running state, polling information, errors, and notification count. |
| `timetree_stop_monitor` | None | Stops polling; does not change calendar data. |

The first successful poll establishes a baseline. Later differences emit MCP
`notifications/message`, whose presentation depends on the client. No email,
phone, webhook, cloud scheduling, or persistent hosted service is provided.
Partial snapshots can look like removals; verify with a fresh calendar read.
See [monitoring limits](usage.md#background-monitoring).

## Environment configuration

| Variable | Purpose |
| --- | --- |
| `TIMETREE_EMAIL` | Login email; takes precedence over macOS Keychain. |
| `TIMETREE_PASSWORD` | Login password; takes precedence over password file and Keychain. Avoid storing it in client JSON. |
| `TIMETREE_PASSWORD_FILE` | Private UTF-8 password file; preferred when not using Keychain. |
| `TIMETREE_PYTHON` | Single Python executable path used by the npm launcher. |
| `TIMETREE_STATE_DIR` | Absolute, stable, private directory for the occurrence journal. |
| `XDG_STATE_HOME`, `LOCALAPPDATA` | Platform fallback roots for journal storage; see [selection order](recurring-events.md#local-journal-storage). |
| `TIMETREE_BASE_URL` | Developer override for the HTTPS service origin; normal users should leave it unset. Credentials and calendar requests go to this origin. |
| `TIMETREE_USER_AGENT` | Optional HTTP user-agent override. |

The default transport is direct HTTPS. The legacy browser reader remains in
the source for compatibility; the normal launcher does not select it and
the new occurrence adapter is part of the direct HTTPS transport.
