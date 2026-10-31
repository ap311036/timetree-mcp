# Recurring events: current limits and implementation assessment

Reviewed on 2026-10-31. **Editing or deleting one occurrence, or this and later
occurrences, is not implemented.** Use TimeTree's own app for those actions.
Do not substitute `timetree_update_event` or `timetree_delete_event` when the user
asks to change only one date: those tools address an event UUID, with no
occurrence selector or scope.

## What this server does today

- Event creation and updates accept a `recurrences` array and pass it through to
  the web API. This is not a validated recurrence editor.
- Event reads preserve `recurrences` and `recurrence` fields returned by TimeTree,
  but **do not expand rules into individual occurrences**. Date filtering compares
  each returned event's stored start and end. A recurring series that starts
  outside the requested date range can therefore be absent even when it repeats
  inside that range. Do not treat the result as a complete recurring agenda or
  use it alone to assert that a time is free.
- `sync_complete` reports synchronization completeness, not recurrence expansion
  or whether every occurrence is included in the displayed results.
- Updates and deletes require `confirm: true`, but this flag neither chooses a
  recurrence scope nor proves that the user approved the intended scope.

These behaviors can be inspected in [payload construction](../src/timetree_mcp/operations.py),
[date filtering](../src/timetree_mcp/parser.py), and the
[event HTTP methods](../src/timetree_mcp/http_reader.py).

## Evidence from another implementation

The public `ehs208/TimeTree-MCP` implementation was reviewed at immutable commit
[`bf6703acd718a132e4eba4df2fb0a49ebf42cc96`](https://github.com/ehs208/TimeTree-MCP/tree/bf6703acd718a132e4eba4df2fb0a49ebf42cc96).
Its [occurrence tools](https://github.com/ehs208/TimeTree-MCP/blob/bf6703acd718a132e4eba4df2fb0a49ebf42cc96/src/tools/recurring-event-tools.ts)
compose ordinary event operations: excluding an occurrence with `EXDATE`,
creating a replacement linked through `parent_id`, or splitting a series with
an end bound and a new event. Edits can require two writes. That code also
handles some ambiguous failures by reading the series again before deciding
whether to remove a newly created replacement.

Its [recurrence tests](https://github.com/ehs208/TimeTree-MCP/blob/bf6703acd718a132e4eba4df2fb0a49ebf42cc96/tests/recurrence.test.mjs)
describe observations from the TimeTree web app, including UTC exception dates
and date-only end bounds. Its
[tool tests](https://github.com/ehs208/TimeTree-MCP/blob/bf6703acd718a132e4eba4df2fb0a49ebf42cc96/tests/recurring-tools.test.mjs)
exercise simulated API responses and partial failures. Those are useful
implementation evidence, but running those mocks would not verify the current
TimeTree service or this Python client's behavior.

TimeTree's [official Connect App API ended on December 22, 2023](https://timetreeapp.com/intl/en/newsroom/2023-07-23/connectapp-api).
The web endpoints used by these projects have no compatibility guarantee from
that former API. This assessment did not authenticate to TimeTree, inspect
account credentials, or modify a real calendar.

## Why occurrence writes are deferred

No sanitized request/response fixtures or independently reproduced round trip
for this client were available during this assessment. Adding a guessed
`scope` parameter would not establish how the service selects an occurrence.
Reimplementing the multi-request approach also needs more than a payload
change: the current reader has no recurrence expansion, event-detail retrieval,
or complete preservation of series linkage and attached-file metadata.

Incorrect exception dates can hide the wrong occurrence. An update after a
timeout can have an uncertain outcome; blindly retrying or rolling back can
duplicate or lose events. Concurrent edits can make a previously read series
stale. These cases need explicit handling before offering occurrence writes.

## Requirements before implementation

1. In an explicitly designated disposable test calendar, capture sanitized
   requests and responses for single-occurrence edits/deletes and series splits.
   Confirm identifiers, `parent_id`, `EXDATE`, inclusive end bounds, timezone and
   all-day behavior, preserved fields, and visibility in TimeTree's own UI.
   Keep credentials and personal calendar content out of fixtures and logs.
2. Implement bounded recurrence reads first, with explicit unsupported-rule and
   truncation results. Test timezone transitions, date-only bounds, exclusions,
   first/last occurrences, and a series starting outside the query window.
3. Add write tests for missing/changed targets, unrelated concurrent edits,
   create success followed by update failure, lost responses after either
   write, failed verification reads, and failed compensating actions. Report
   uncertain outcomes with event identifiers instead of retrying blindly.
4. Present calendar, series, exact occurrence date/timezone, requested scope,
   and before/after values for confirmation. Never silently widen a request
   from one occurrence to the whole series. Re-read the target before writing,
   then verify the intended result and the unchanged neighboring occurrences.

Until those checks are available, the safe workflow is to inspect the series
in TimeTree and make occurrence-specific changes there. Do not manufacture
exceptions through the raw `recurrences` input as a workaround.
