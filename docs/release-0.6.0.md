# 0.6.0 — Read-only review prompt and static safety resource

TimeTree MCP 0.6.0 adds one MCP prompt and one static resource while retaining
all 29 tools and their existing schemas.

- `timetree_review_schedule` prepares a read-only review for one numeric calendar
  id and an explicit UTC date window of 1–93 days (inclusive start, exclusive end).
  Retrieving it does not authenticate or execute calendar tools.
- `timetree://guides/safe-usage` provides fixed Markdown guidance on complete
  reads, timezones, confirmation, uncertain recurring moves and monitoring.
  It contains no account/calendar data and does not read files or credentials.
- English, Traditional Chinese and Japanese READMEs include the official LobeHub
  badge and feature documentation. The badge is not a security audit or a score
  guarantee.

## Compatibility

The server no longer advertises the unimplemented `2026-07-28` protocol. It
supports `2024-11-05`, `2025-03-26`, `2025-06-18` and `2025-11-25`, retaining
`2025-06-18` as its fallback. Clients unable to use the negotiated version must
disconnect. The new protocol's discovery/versioning contract needs a separate
implementation. Prompts/resources need a compatible client; their availability
does not execute tools or grant calendar-write permission.

## Verification and limits

The release suite passed **417 Python tests and 5 Node tests**.
Offline coverage verifies catalogs, strict arguments, UTC boundaries, protocol
errors, exact-URI access, no credential/calendar dependency, and the formal
stdio entry point. The restricted live E2E worker still rejects guidance methods.
The existing independent review found no mandatory issue in scope. GUI-client
rendering and live calendar behavior of an assistant using the prompt have not
been tested; the previously cleaned calendar fixtures were not reused.

The LobeHub owner/API record and public landing page previously disagreed. A
release or directory update does not prove that every public surface has synced.

## Install

```bash
npx --yes timetree-mcp@0.6.0
```

Requires Node.js 18+ and separately installed Python 3.10+.
