# Reproducible offline demo

This is output from the real MCP dispatcher and monitor using a synthetic
in-memory reader in place of TimeTree HTTPS. It reads no credentials, opens no
network connection, and changes no calendar. It is not a recording of Claude,
Cursor, Codex, ChatGPT, or a successful TimeTree login.

From a source checkout with Python 3.10+:

```bash
python3 scripts/demo.py
```

The script demonstrates calendar/event results, rejection of a write without
`confirm: true`, an accepted **simulated** write, and monitor change detection.
It checks that the rejected call never reaches the reader, that the first poll
is silent, and that a later change reaches the notification callback. Two
manual polls replace the background timer; notification timestamps and the
random change-batch ID are omitted from the output for reproducibility. The
baseline has no batch, so its `batch_id` is `null`. The script prints the tool result payloads,
not the full JSON-RPC envelopes or a client's rendered conversation.

The recording below is from the **0.5.0 implementation** with complete-snapshot
validation and pending batches. It is not the output of npm 0.4.0 and does not
verify client display or a real calendar connection.

## Recorded output

```text
OFFLINE DEMO: synthetic calendar; no credentials, network, or real writes.
timetree_list_calendars
{"isError":false,"calendars":[{"id":"demo-family","name":"Demo family"}]}
timetree_list_events
{"isError":false,"events":[{"id":"demo-lunch","calendar_id":"demo-family","calendar_name":"Demo family","title":"Family lunch","start":"2026-11-07T12:00:00+08:00","end":"2026-11-07T13:00:00+08:00"}],"sync_complete":true,"calendars":[{"id":"demo-family","name":"Demo family"}]}
timetree_create_event
{"isError":true,"error":"confirm must be true before a TimeTree write operation"}
timetree_create_event
{"isError":false,"demo_only":true,"success":true,"event":{"id":"demo-created","title":"Saturday picnic"}}
Monitor baseline
{"primed":true,"event_count":1,"added":0,"updated":0,"removed":0,"changes":[],"check_complete":true,"baseline_advanced":true,"batch_id":null,"pending_count":0}
Monitor change (batch ID omitted)
{"primed":false,"event_count":1,"added":0,"updated":1,"removed":0,"changes":[{"kind":"updated","event_id":"demo-lunch","calendar_id":"demo-family","calendar_name":"Demo family","title":"Family lunch at the park","start":"2026-11-07T12:00:00+08:00","end":"2026-11-07T13:00:00+08:00"}],"check_complete":true,"baseline_advanced":true,"pending_count":0}
Notification changes (timestamp omitted)
[{"kind":"updated","event_id":"demo-lunch","calendar_id":"demo-family","calendar_name":"Demo family","title":"Family lunch at the park","start":"2026-11-07T12:00:00+08:00","end":"2026-11-07T13:00:00+08:00"}]
```

## Try it with your assistant

After [configuring a client](clients.md), start with a bounded read:

> List my calendars, then show the events for November 7, 2026 in the calendar
> I choose. Tell me if any calendar could not be fully synced.

For a write:

> Draft a picnic on November 7 from 14:00 to 15:00 in Asia/Taipei. Show me the
> target calendar and details, and wait for my approval before creating it.

These are suggested prompts, not captured assistant responses. The server checks
`confirm: true`; the client or agent must obtain your approval. Check the actual
TimeTree result after any write. For recurring events, see the
[recurrence limitations](recurring-events.md).

For monitoring, ask the assistant to start `timetree_start_monitor`, keep the
client and server running, then inspect `timetree_monitor_status`. A new process
starts with a fresh baseline. Notifications use MCP log messages; their display
and whether they trigger a visible alert depend on the client. This is not a
cloud push service or a guaranteed phone/desktop notification.
For queue states, transport-write counts, and uncertain batches in 0.5.0,
see [monitoring state and delivery](monitoring.md).
