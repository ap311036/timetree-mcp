# Monitoring state and delivery

This guide describes the monitor in **0.5.0**. There are still 29 tools; no
acknowledgement, replay, or queue-reset tool has been added. npm 0.4.0 retains
the earlier partial-snapshot behavior and does not include these queue semantics.

`timetree_start_monitor` polls a selected calendar, or active calendars when
`calendar_id` is omitted, every 30–3600 seconds (default 300). It compares stored
source records, including recurring parents and detached exceptions. It does
not generate a separate change notification for each projected occurrence.
See the [terminology](../CONTEXT.md).

## Complete reads establish the baseline

The first complete, validated snapshot establishes a baseline without a change
notification. Later accepted snapshots produce added, updated, or removed
source-record changes. A snapshot must explicitly report `sync_complete: true`,
contain valid calendar/event identities, and agree with the selected scope.
Partial reads, skipped records/calendars, duplicate identities, malformed data,
or a changed calendar set cannot replace the successful baseline or create
apparent removals. Those failed checks also do not initiate notification retries.

Before monitor validation, the direct reader applies one acquisition budget
across pages and calendars: 20,000 accepted raw events and 8,000,000 serialized
raw-event bytes, with at most 512 calendar-index records. A page/calendar budget
limit stops further acquisition and returns accepted prior events with
`sync_complete: false`, `incomplete_calendars` reason
`request_capacity_exceeded`, and `not_attempted: true` for calendars not yet
requested. The monitor rejects that partial result as a new baseline. These
reader limits are separate from the normalized snapshot and retained-state
limits below.

Changes made and reverted between polls can still be missed. A complete source
snapshot is not a guarantee of recurrence coverage or of a free timeslot.

The new baseline and any new pending batch are accepted together after capacity
checks. The monitor bounds snapshots, retained baselines, and pending batches:
at most 20,000 events and 8,000,000 serialized event bytes per snapshot,
16 baseline scopes with 8,000,000 bytes in total, and 100 pending batches with
8,000,000 bytes in total. Oversized or malformed input fails instead of being
silently truncated. Pending capacity failure does not advance the baseline or
discard retained batches. Previously known-unsent batches may drain after a
valid source check, which can free queue capacity before accepting a new batch.
These are bounds on collected records and retained state, not a guarantee on
raw HTTP response size, download duration, CPU time, or total process memory.

## Pending notifications and transport results

Each pending batch has a stable `batch_id`, scope, and immutable change content.
Batches are processed in arrival order across scopes. Their states are:

| State | Meaning and next action |
| --- | --- |
| `not_sent` | No sink is available, or the transport explicitly confirmed that no write was attempted. The batch stays queued and can be tried after a later valid source check. |
| `in_flight` | A callback is currently attempting the local transport write. This is not a delivery confirmation. |
| `unknown` | The callback failed without proof that nothing was written, including a short write or flush failure. The batch remains pending and blocks subsequent delivery across scopes. It is never automatically retried. |

A callback that returns successfully removes the batch and increments both
`notifications_sent` and `transport_writes_succeeded`. In the stdio server this
means the local JSON notification write and flush completed. It **does not**
prove that the client received, displayed, or acknowledged the notification,
or that a person saw it. The historic `notifications_sent` name is retained for
compatibility; interpret it as a transport-write count.

An `unknown` batch is not cleared by switching sinks, stopping/starting, or
reading status. Inspect the status and client/transport logs before deciding
what happened; there is no automatic resend or reconciliation tool. Do not
restart the process as a recovery procedure: it discards the evidence in memory
without resolving whether the earlier write reached the client.

## Read monitor status

`timetree_monitor_status` does not poll TimeTree. It reports:

| Fields | Interpretation |
| --- | --- |
| `active`, `stopping`, `poll_in_flight` | Whether the worker exists, a stop has been requested, and a poll is still executing. |
| `last_poll`, `last_success`, `last_change` | Last attempt, last accepted complete check, and last accepted change batch. These are separate from delivery. |
| `last_check_complete`, `baseline_advanced` | Whether the latest attempt accepted a complete baseline. A successful unchanged snapshot can advance the baseline too. |
| `baseline_initialized`, `baseline_scope_count` | Whether any scope has a baseline, and how many scopes are retained in this process. |
| `pending_count`, `pending_not_sent_count`, `pending_unknown_count`, `pending_in_flight_count` | Retained batches grouped by state. |
| `pending_batches`, `delivery_blocked` | Batch IDs, scope IDs, states, and whether uncertainty is blocking delivery. No full notification content is returned here. |
| `notifications_sent`, `transport_writes_succeeded`, `delivery_semantics` | Local successful-write count and the explicit `transport_write_only` boundary. |
| `last_error`, `state_storage` | Latest safe error code; storage is `memory_only`. Check pending states even when the latest error is from a calendar read. |

Common errors include `monitor_snapshot_incomplete`,
`monitor_calendar_scope_changed`, `monitor_pending_capacity_exceeded`, and
`monitor_notification_delivery_unknown`. Inspect the failed source or scope;
do not reinterpret an incomplete check as evidence that an event was removed.
If the all-calendar set changes, its old baseline is retained and comparison
stops for that scope. A newly selected scope starts with its own baseline;
existing pending batches and uncertainty remain process-wide.

## Stop, restart, and persistence

`timetree_stop_monitor` requests a stop and waits up to two seconds. A blocked
read or callback may still be in progress, so inspect `active`, `stopping`, and
`poll_in_flight` in the result. The monitor retains the old worker until it
actually exits and rejects another start while a worker or poll is active.
Stopping cannot undo a callback that has already begun.

Stop/start within the same process retains baselines and pending batches.
Exiting or restarting the process loses both; the next process starts fresh
and cannot replay offline changes or reconcile an old uncertain batch. This
memory queue is separate from the durable SQLite journal used for occurrence
moves. It is not a hosted service, webhook, email sender, or phone-alert system.

The monitoring implementation is covered by synthetic source/transport/concurrency
tests. Offline tests and independent review of [shared authentication and the bounded calendar-page bootstrap](clients.md#shared-authentication-and-transport)
are complete, with no remaining blocker in the reviewed scope. After session
invalidation, a later independent poll may authenticate again through the
reader's existing lazy-login path; the failed request itself is not replayed. The owner
reported a completed [read-only stdio run](e2e-runner.md) with 8/8 planned fixture
checks passing. At the pre-release check, code hashes matched the reviewed code
and the approved plan hash was unchanged, but the report contains no version/source/plan
fingerprints to independently bind the files actually loaded to the release artifact.
The runner does not start monitoring or verify notification display/user receipt,
npm installation or its original entry point, GUIs, alternate credential
discovery, or live writes. A separate private integration is not evidence of
these public-package behaviors either. The six fixtures were deleted and checked
absent without changing monitoring state; the completed plan cannot be rerun.
No additional run is requested.
