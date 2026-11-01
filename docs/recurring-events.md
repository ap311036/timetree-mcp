# Move one occurrence of a recurring event

Version 0.3.0 adds a conservative **single-occurrence time move**.
It preserves the occurrence's duration and supported event metadata, creates a
linked replacement, and excludes the original date from its series. It does
not provide occurrence deletion, general recurrence editing, or "this and
following" changes. These tools are not included in npm 0.2.4;
use `timetree-mcp@0.4.0` or the matching [source checkout](usage.md#local-checkout).

This implementation was checked with synthetic fixtures. Current public
TimeTree frontend code was inspected without authentication on 2026-10-31.
No live calendar mutation or client UI round trip was performed during this
implementation. The endpoints remain unofficial.

## Supported scope

The preflight accepts only a narrow, validated shape:

- A timed, category-1 series with dates from 1980 onward. Start and end must
  use the same supported IANA zone: `Asia/Taipei`, `Asia/Tokyo`, `Asia/Kolkata`,
  `America/New_York`, or `Europe/London`.
- Start/end timestamps and input timestamps at whole-second precision; the
  duration must be greater than zero and no longer than 24 hours.
- One `RRULE` with `FREQ=DAILY` or `FREQ=WEEKLY`. Supported bounds include
  `INTERVAL`, `COUNT`, and `UNTIL` expressed as UTC or a date-only bound;
  weekly rules may use `BYDAY` and `WKST`. Supported `EXDATE` exclusions are
  honored. Unsupported combinations and recurrence fields are rejected.
  Starting in 0.4.0, a weekly anchor must also match its explicit
  `BYDAY` selection; an inconsistent rule is rejected instead of guessed.
- The requested original instant must be a generated, non-excluded occurrence
  of that series. Inputs use `YYYY-MM-DDTHH:MM:SS±HH:MM`, with an explicit
  numeric offset matching the series zone at that local time. Use `+00:00`
  instead of `Z`; fractional seconds and naive times are rejected.
- The raw parent event's `id` must equal its `uuid`. The current calendar
  snapshot must contain at least one existing child corroborating both
  `parent_id` and `recurring_uuid` links to that parent, with no conflicting
  linkage among the relevant records.

All-day events, other timezones, cross-start/end-zone events, complex rules, and
unsupported meaningful metadata are rejected. The implementation does not
silently discard fields to make an event editable. A valid series can still
be uneditable when its observed identity/link contract is insufficient.
**Do not manufacture a child event just to pass the linkage check.**

### Timezones and daylight saving time

Rules are evaluated using the series' local calendar and wall-clock time.
For example, a safe 09:00 New York weekly series remains at 09:00 across the
spring offset change; the corresponding UTC instant changes. The selected
offset must match that date, so New York `-05:00` in winter is not a blanket
offset for summer. Kolkata's `+05:30` offset is supported. The zone name is
preserved on the replacement event and returned as `timezone` by preflight
and successful moves.

The adapter rejects repeated local times during a fall-back transition, even
if an input specifies one possible offset. It also rejects nonexistent local
times during spring-forward, missing timezone names, offset mismatches, and
an original or replacement event interval crossing an offset transition.
It never guesses which side of a transition the user intended.

For New York and London, recurrence validation checks the intervening generated
occurrences from the series anchor to the requested original date, up to
**3660 local days**. An ambiguous/nonexistent intervening occurrence or unsafe
interval causes rejection, including when it is excluded; this avoids guessing
how the unofficial service counts DST exceptions. A more distant original
date is rejected. Safe daytime series can cross seasons within that bound.
Taipei, Tokyo, and Kolkata retain the fixed-offset behavior for supported dates.

Python's `zoneinfo` needs an IANA database, from the operating system or the
optional [`tzdata` package](https://docs.python.org/3/library/zoneinfo.html#data-sources).
Windows commonly needs `tzdata`; install it into the **same Python environment**
used by `TIMETREE_PYTHON`, for example `python -m pip install tzdata` in your
virtual environment. The npm wrapper does not install it. Missing data produces
a clear failure before writes; it never substitutes a fixed offset.
Version 0.4.0 uses fresh lookups for new occurrence queries and edit
validation, so a cached zone cannot mask missing installed data. Saved journal
replay remains independent of timezone data.

## Read behavior is unchanged

Version 0.4.0 adds a separate `timetree_list_occurrences` tool
for bounded recurrence expansion, plus `timetree_timezone_status` for local
data diagnostics. These are not in npm 0.3.0. See [query scope and
examples](occurrence-queries.md). Its MONTHLY/YEARLY interpretation is read-only
and does not broaden this move adapter's DAILY/WEEKLY support. Live backend
edge-case behavior has not been established; see the [isolated test
prerequisites](isolated-calendar-testing.md).

`timetree_list_events` still does **not** expand recurrence rules into dated
occurrences. Its filtering compares the stored event's start/end, so a series
starting outside the requested range may be absent even if it repeats within
that range. `sync_complete` describes synchronization, not recurrence expansion
or a complete agenda. Do not use these results alone to claim that a time is
free.

For an occurrence move, start with an already identified series UUID and the
exact original occurrence date/time. Preflight reads a fresh calendar
snapshot and validates that specific occurrence; it is not an agenda-expansion
tool. Never substitute `timetree_update_event` or `timetree_delete_event` for a
request to change only one date.

## 1. Preflight the exact occurrence

Call `timetree_get_occurrence_for_edit` with:

| Argument | Meaning |
| --- | --- |
| `calendar_id` | Numeric calendar ID as a **string**, such as `"12345"` |
| `event_uuid` | Known parent series UUID |
| `occurrence_start_at` | Exact original local start, including seconds and the zone's numeric offset |

Illustrative input only; replace the identifiers and date with the inspected
target:

```json
{
  "calendar_id": "12345",
  "event_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "occurrence_start_at": "2030-01-07T09:00:00+08:00"
}
```

The result includes `editable`, the original time and duration, the series
identity, `timezone`, and a `revision` fingerprint of the calendar snapshot. Proceed only
when `editable` is true. For example,
`occurrence_child_link_contract_unverified` means the live snapshot did not
establish the required linkage; use TimeTree's app instead.

Preflight does not create the local journal or write to TimeTree. It can still
require authentication and calendar reads. Present the calendar, series,
original occurrence, proposed new start/end, and **single-occurrence** scope
to the user before writing.

## 2. Move with confirmation and an operation ID

`timetree_move_occurrence` takes the same three identifying fields plus:

| Argument | Meaning |
| --- | --- |
| `new_start_at` | New start in `YYYY-MM-DDTHH:MM:SS±HH:MM`, matching the series zone; duration is preserved |
| `expected_revision` | Exact `revision` returned by the preflight |
| `operation_id` | A unique, stable identifier for this logical move |
| `confirm` | Must be the boolean `true`, after the user approves the move |

The template below deliberately contains a revision placeholder. Replace it
with the preflight's 64-character revision; do not generate your own hash:

```json
{
  "calendar_id": "12345",
  "event_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "occurrence_start_at": "2030-01-07T09:00:00+08:00",
  "new_start_at": "2030-01-07T10:00:00+08:00",
  "expected_revision": "PASTE_REVISION_FROM_PREFLIGHT",
  "operation_id": "move-20300107-0900-01",
  "confirm": true
}
```

Keep the same `operation_id` associated with this request. IDs accept letters,
digits, underscores, and hyphens, up to 160 characters. The `confirm` flag is a
server-side gate; it does not prove a human approved the action. The assistant
and client must still obtain approval for the actual target and scope.

A changed snapshot produces a stale-revision rejection before the first
calendar write. Read again and review the current proposed move. The editor
also takes fresh snapshots around the write sequence and verifies the child,
parent exclusions, preserved metadata, and other calendar records. These
checks detect changes but cannot provide a server-side transaction or prevent
another client from editing between requests.

A request whose new time equals the original time is rejected with
`occurrence_time_unchanged`. No move is accepted and no journal entry is created
for such a request.

In particular, another client can add or change a recurrence rule after the
last read and before the parent PUT. That rule can be overwritten by this
request, and the final read cannot detect a change that was already overwritten.
Avoid editing the series from another client during the move. Preflight and
successful results include `compare_and_swap: false` and this warning; success
confirms the observed final state, not protection from an unobservable race.

On verified success, the returned `event_uuid` identifies the **new child**;
`series_uuid` identifies the original parent. Start/end values in results may
be rendered as UTC ISO-8601 timestamps. Inspect them as instants in the intended
timezone.

## Two writes are not atomic

The operation first records its intent locally, then:

1. Creates a linked child event at the new time, preserving duration and the
   supported metadata. The child POST omits `recurrences`.
2. After reading back and verifying the child, updates the original parent's
   recurrence set with the original occurrence's `EXDATE`, preserving its
   RRULE. This parent request contains only `recurrences` and `silent`.
3. Reads back and verifies the resulting calendar state before reporting
   success.

Both writes use `silent: true`, following the observed first-party flow. This
is not a guarantee about every upstream activity feed or client notification.
A failure between the requests can leave both the original occurrence and a
replacement visible. A lost response can leave the actual outcome unknown.
The implementation does not retry either write or perform an automatic
rollback.

## Inspect an uncertain result without writing again

`timetree_occurrence_operation_status` reads the **local journal**, not the live
calendar:

```json
{
  "operation_id": "move-20300107-0900-01"
}
```

It can report the recorded phase, completion state, stored result, and a
created child UUID when one was received. `found: false` means this local
journal has no matching record; it does not prove no calendar write occurred.
Even a stored successful result describes the earlier verification, not the
calendar's present state.

If a move reports `occurrence_outcome_uncertain_do_not_retry` or
`occurrence_series_locked_do_not_retry`, stop. A recorded partial/uncertain
operation keeps a durable lock on that series without expiry, including after
a process restart. Repeating the same request does not dispatch new writes;
changing its contents while reusing an operation ID is rejected.

**Do not switch operation IDs, delete the journal, change its directory, or
use another host to get around the lock.** Do not call raw event tools to
simulate a rollback. Inspect the actual series and any child in TimeTree's
own app, retain the operation ID and status, and ask a maintainer to reconcile
the state. This version provides no automatic recovery, resume, or unlock
command.

## Local journal storage

Writes require a usable, private journal before any calendar mutation. The
SQLite file is `occurrence-edits.sqlite3`, with directory selection in this
order:

1. `TIMETREE_STATE_DIR`, when set: an **absolute** directory override.
2. `$XDG_STATE_HOME/timetree-mcp`, when `XDG_STATE_HOME` is set.
3. `%LOCALAPPDATA%\timetree-mcp` on Windows, when available.
4. `~/.local/state/timetree-mcp` otherwise.

The editor creates a missing directory/file only when a write needs the
journal. POSIX uses owner-only directory/file permissions (700/600); unsafe
paths, symlinks, incorrect ownership/permissions, or unwritable state cause a
failure rather than an unjournaled write. Windows relies on the current
user's private directory ACLs. Do not place the journal in a shared or synced
folder.

The journal holds hashes, identifiers, phases, and operation results such as
returned event IDs and times. It does not store event titles, notes, calendar
snapshots, or passwords. Treat its IDs and timestamps as private data.

All local MCP processes operating on the same account must use the same stable
state location. The journal coordinates processes sharing that file on one
machine; it does not synchronize locks across computers or protect against
edits from the TimeTree app. Preflight does not need to create a journal.

## First-party contract evidence

On 2026-10-31, unauthenticated `GET https://timetreeapp.com/signin` referenced
frontend release `d10bcfc7dab06f3fefd21d1b9aadc2c9309c23be`. The following public
files returned HTTP 200; SHA-256 hashes identify the exact inspected bytes.
No authenticated request or calendar API request was needed for this review.

| Public source | SHA-256 |
| --- | --- |
| [main-B50tCVUi.js](https://frontend.assets.timetreeapp.com/assets/main-B50tCVUi.js) | `0ada2f98ea41bf2389a595c45cb6c576f1a1a4bab14e05268636a0672016f240` |
| [getLocation-BbpPhD_v.js](https://frontend.assets.timetreeapp.com/assets/getLocation-BbpPhD_v.js) | `6cfdc79b347f7b69a11683d19f3749c8115b862b244450796f62b934fbf0d773` |
| [syncCalendarEvents-D_93mIcb.js](https://frontend.assets.timetreeapp.com/assets/syncCalendarEvents-D_93mIcb.js) | `d3935a1fecbbec9892f6564a70462a2a58dbe07fdfbe205ccc551eb117828830` |
| [useCalendarLabelsByCalendarIds-EZYLyNk9.js](https://frontend.assets.timetreeapp.com/assets/useCalendarLabelsByCalendarIds-EZYLyNk9.js) | `50b1e7bee99097a6105c34d7d8a326c55072a257143127e8f271db9ec1fede2a` |
| [getEventFilterByAttendees-DHD_E4Br.js](https://frontend.assets.timetreeapp.com/assets/getEventFilterByAttendees-DHD_E4Br.js) | `5dd46bb00761a252f7f07340919e1b662951245a10edfc20a937e4e72b3ad0da` |

The network helper returns response JSON directly. Sync and model converters
preserve raw `id`, `uuid`, and `parent_id`; the child clone uses the parent's
raw `id` for `parent_id`, rather than substituting `primary_id`. The child
serializer omits recurrence rules when a parent is present. The occurrence
flow POSTs the child, adds an EXDATE to the parent's recurrence set while
restoring the original RRULE string, then PUTs only the recurrence set and
`silent`. It returns the mapped child object with the UUID from the POST
response.

These sources establish first-party client intent, not a supported public API
contract or proof that `id` and `uuid` match in every account. Runtime identity
checks and fresh read-back verification remain necessary. The stricter
supported subset above intentionally rejects cases that this server cannot
validate safely.
