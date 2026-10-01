# 0.2.3

Calendar listing and default event synchronization now exclude calendars marked
inactive by the TimeTree API's `deactivated_at` field. Previously inactive
calendars could be listed and queried, resulting in upstream `-425` errors and
an incomplete synchronization result.

- Apply the same filtering at the shared calendar entry point in both the direct HTTPS reader and the legacy browser reader.
- Preserve calendars when the field is absent, null, or numeric zero; exclude nonzero numeric values.
- Reject invalid field types explicitly rather than silently hiding calendars.
- Reject explicitly selected inactive calendars before event synchronization.
- Continue reporting genuine synchronization failures for active calendars.

Validation: 5 Node tests and 52 Python tests, covering flat/nested records,
missing/null/zero values, inactive calendars, invalid values, browser filtering,
and active-calendar upstream errors.

The API field interpretation was independently implemented after reviewing
[ehs208/TimeTree-MCP's API client](https://github.com/ehs208/TimeTree-MCP/blob/64c8188eac103c0d8e8726b5fc5b2c31f961f3a5/src/client/api.ts#L113-L116)
and numeric/null schema (MIT licensed); no source code was copied.
