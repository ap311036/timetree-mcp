# Occurrence queries and timezone diagnostics

Version **0.4.0** adds `timetree_list_occurrences` and
`timetree_timezone_status`, bringing the server to 29 tools. Install
`timetree-mcp@0.4.0` or use the matching source checkout. The older npm 0.3.0
package has 27 tools and does not include these additions.

## Read dated occurrences

`timetree_list_occurrences` reads one complete calendar snapshot and expands
supported recurrence rules into a bounded, chronological list. It does not
change a calendar, create a journal, or approve a later edit.

| Argument | Meaning |
| --- | --- |
| `calendar_id` | Required numeric calendar ID as a string, obtained from a calendar read. |
| `start_at` | Required inclusive beginning of the query window. |
| `end_at` | Required exclusive end, later than `start_at` and at most 93 × 24 hours away. |
| `query` | Optional literal title filter, at most 500 characters. |
| `limit` | Optional result limit, 1–500; default 100. |

Both time inputs use `YYYY-MM-DDTHH:MM:SS±HH:MM`, with whole seconds and an
explicit numeric offset. Use `+00:00` instead of `Z`. The inputs identify
absolute instants; their offsets need not match each other or the event's
zone. To query local days, supply the correct offsets at both boundaries.
An event is included when its start is before the window's end and its end is
after the window's start, so an event already in progress can be included.

Illustrative input, using a placeholder calendar ID:

```json
{
  "calendar_id": "12345",
  "start_at": "2030-01-01T00:00:00+09:00",
  "end_at": "2030-02-01T00:00:00+09:00",
  "limit": 100
}
```

The result contains `occurrences`, `count`, `complete`, `coverage_complete`,
`has_more`, and `unsupported` entries with an event UUID and a short reason.
Each returned occurrence identifies its source (`single`, `generated`, or
`detached`), event and series UUIDs, title, timezone, UTC start/end, and local
start/end. An `occurrence_id` identifies a result row; it is not an upstream
event UUID and must not be passed to a general event write.

Inspect completeness flags before summarizing the agenda:

- `coverage_complete` is true only when no record/group produced an unsupported
  diagnostic.
- `has_more` is true when the number of known matching rows exceeds `limit`.
  It can be false even when unsupported records hide additional occurrences.
- `complete` requires both complete coverage and no result truncation.

A successful response or empty list alone is not sufficient evidence that the
calendar is empty or a time is free. A failed or incomplete raw snapshot is
rejected instead of being expanded as though it were complete.

## Supported rule subset

The query handles timed category-1 events in `Asia/Taipei`, `Asia/Tokyo`,
`Asia/Kolkata`, `America/New_York`, and `Europe/London`, with the same zone at
both endpoints, supported dates from 1980 onward, and duration at most 24 hours.
All-day events, unrecognized shapes, complex rules, cross-zone intervals,
missing timezone data, ambiguous/nonexistent local times, and unsafe DST
intervals are reported as unsupported rather than guessed.

| Frequency | Supported selection |
| --- | --- |
| `DAILY` | Anchor wall time, optional `INTERVAL`. |
| `WEEKLY` | Anchor weekday or plain `BYDAY` weekdays, optional `WKST` and `INTERVAL`. |
| `MONTHLY` | Anchor day of month, or one positive `BYMONTHDAY` from 1–31. |
| `YEARLY` | Anchor month/day, or one `BYMONTH` and one positive `BYMONTHDAY`. |

`COUNT`, `UNTIL`, and supported `EXDATE` exclusions apply within this subset.
Invalid month dates are skipped: a monthly event on the 31st has no February
occurrence, and a February 29 yearly event occurs only in leap years. These
nonexistent calendar dates do not consume `COUNT`; `EXDATE` removes an already
counted occurrence without extending the count. Ordinal weekdays, negative or
multiple month-day selections, multiple month selections, and rule combinations
outside this table are not supported. An anchor that contradicts the rule is
not silently repaired.

Starting in 0.4.0, that guard also applies to weekly edit validation:
`BYDAY` must include the series anchor's weekday. New occurrence query/edit validation
uses fresh timezone lookups rather than relying on a cached `ZoneInfo` entry.
Saved operation replay remains offline and does not load timezone data. These
changes do not broaden the supported write frequencies and are not in npm 0.3.0.

Recurrences use the series' local wall time. New York/London validation is
limited to generated candidates within 3660 local days of the anchor; unsafe
DST history can make a series unsupported even when the requested window
itself contains ordinary times. All zones share a 100,000-step budget for
period checks, generated candidates, and single-event checks. The input
snapshot is limited to 20,000 records and 8 MB; titles are limited to 4096 UTF-8
bytes. Exhausted budgets and unsupported shapes do not imply an empty calendar.

A parent and its detached children are projected as one group. Orphaned or
conflicting links, insufficient distinct exclusions, or a child overlapping
an unexcluded parent occurrence at the same instant make the entire group
unsupported. These checks avoid obvious duplicate projections; they do not
establish a one-to-one mapping between each child and its original occurrence.
The result is a conservative local projection, not a verified occurrence list
returned by a TimeTree occurrence endpoint. Known lunar/day-count metadata,
as well as other unsupported scheduling shapes, reduces coverage.

This is local interpretation of a conservative recurrence subset. Synthetic
tests verify the implementation; they do not establish how every TimeTree
backend edge case behaves. See the [isolated live-test prerequisites and
matrix](isolated-calendar-testing.md). Monthly/yearly support is **read-only**.
`timetree_get_occurrence_for_edit` and `timetree_move_occurrence` still accept
only their previously supported DAILY/WEEKLY subset. Query results do not
bypass preflight, confirmation, metadata/link checks, or journal locks.

The existing `timetree_list_events` tool is unchanged: it returns stored event
records and does not expand a recurring series. Use it for general event
search; use `timetree_list_occurrences` when dated occurrences are required.

## Diagnose timezone data without logging in

Call `timetree_timezone_status` with no arguments to check the five supported
zones, or provide one IANA key:

```json
{
  "timezone": "Asia/Kolkata"
}
```

The key is limited to 128 characters and must be a relative IANA identifier,
not an absolute path, traversal expression, or fixed UTC offset. A known zone
outside the supported five, such as `UTC`, may be available while
`supported_for_occurrence_tools` is false.

The diagnostic returns the Python version, per-zone `available` and `status`
(`available`, `missing`, or `error`), supported-zone flags, system-data status,
and `tzdata` package presence/version. It uses fresh zone lookups and reports
these observations separately; it does not infer the active data source,
database freshness, or whether a particular event is editable.

This tool performs no network request, authentication, Keychain access, or
calendar operation. It reports no local paths or environment values and does
not install or repair anything. If data is missing, install `tzdata` into the
same Python environment selected by `TIMETREE_PYTHON`, then restart the server.
A corrupt system timezone file can take precedence over package data, so an
`error` result may require repairing system data or permissions.
