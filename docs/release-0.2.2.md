# 0.2.2

TimeTree sync cursors can decrease. Previously the reader stopped after a decreasing
cursor, silently returning only part of a calendar. The reader now follows unseen
cursors in either direction until the upstream terminal page.

- Stop safely on repeated or invalid cursors and at the 25-page limit.
- Report `sync_complete` and `incomplete_calendars` instead of silently treating partial reads as complete.
- Preserve already read events when a later page returns upstream error `-425`; expose the error code without credentials.
- Keep empty unavailable calendars in `skipped_calendars`. This release does not resolve the upstream `-425` failure.

Validation: 5 Node wrapper/metadata tests and 47 Python tests, including decreasing
cursors, cycles, invalid cursors, page limits, and upstream `-425`.
