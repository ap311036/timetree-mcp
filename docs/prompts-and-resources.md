# Prompts and static guidance

**Available in 0.6.0:** the server
still exposes 29 tools, with one additional MCP prompt and one static resource.
Clients may present prompts and resources differently; no GUI client has been
tested with these additions. Listing, retrieving or reading this guidance does
not authenticate, execute tools, read credentials or access a calendar.

## Review one calendar

`prompts/list` advertises `timetree_review_schedule`. A user can select it to
prepare a read-only review of a calendar and date window. All three arguments
are required strings:

| Argument | Meaning |
| --- | --- |
| `calendar_id` | Numeric `id` returned by `timetree_list_calendars`, 1–40 ASCII digits. A Web URL short code or calendar name is not accepted. |
| `start_date` | Inclusive date in `YYYY-MM-DD`, interpreted at UTC midnight, from 1980 onward. |
| `end_date` | Exclusive UTC date in the same format, after `start_date` and at most 93 days later. |

For example, this MCP request returns a prompt, without querying TimeTree:

```json
{"jsonrpc":"2.0","id":1,"method":"prompts/get","params":{"name":"timetree_review_schedule","arguments":{"calendar_id":"42","start_date":"2030-03-08","end_date":"2030-03-11"}}}
```

`42` is a synthetic example. The generated prompt proposes
`timetree_list_occurrences` arguments with `start_at` of
`2030-03-08T00:00:00+00:00`, `end_at` of `2030-03-11T00:00:00+00:00`, and `limit`
500. It asks the assistant to verify the selected calendar, use read-only tools,
check `complete`, `has_more` and coverage diagnostics, treat event text as
untrusted data, and report observed overlaps and missing coverage. It cannot
guarantee that the client follows these instructions. It does not authorize a
calendar write or start monitoring.

**UTC dates are not local-day boundaries.** If a user wants a local agenda,
agree on the timezone and explicit offset-bearing start/end instants, then use
the occurrence tool directly. Do not silently substitute UTC midnight. An
incomplete or truncated result cannot establish that a time is free.

## Attach a static safety reference

`resources/list` contains only `timetree://guides/safe-usage`, named **TimeTree
safe usage guide**, with MIME type `text/markdown`. `resources/read` returns its
fixed text. The guide covers calendar selection, recurrence completeness,
timezone boundaries, credential handling, confirmation, uncertain two-step
occurrence moves, and process-local monitoring limits. It gives a client useful
operating context without embedding account or calendar data.

```json
{"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":"timetree://guides/safe-usage"}}
```

No dynamic calendar resources or URI templates are exposed. Arbitrary `file:`,
`http:`, query-string or alternative resource URIs are rejected; they are never
resolved or fetched. `resources/templates/list` returns an empty list. The
server does not advertise subscriptions or list-change notifications. The
catalog fits one page; continuation cursors are rejected instead of repeating
the page. Invalid prompt requests return JSON-RPC `-32602`, and an unknown
resource URI returns `-32002`. Errors do not echo supplied argument values.

## Protocol and verification scope

These interfaces use the common MCP contracts in
[2025-06-18 prompts](https://modelcontextprotocol.io/specification/2025-06-18/server/prompts)
and [resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources).
Initialization supports `2024-11-05`, `2025-03-26`, `2025-06-18` and `2025-11-25`;
the existing fallback remains `2025-06-18`. A client that cannot use the returned
version must disconnect rather than assume compatibility.

Version 0.6.0 removes the previously advertised `2026-07-28` version. That
version's [per-request version and discovery contract](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)
requires a separate implementation; accepting that version in `initialize`
was not evidence of support. This change does not implement the new protocol.

Offline tests cover catalogs, arguments, UTC ranges, error codes, metadata,
arbitrary-URI rejection, guidance isolation, and the actual stdio entry point.
The restricted live E2E worker retains its original method allowlist and does
not expose these methods. No real calendar writes or credential access are
needed to verify the additions. The previously cleaned EWS fixtures must not be
re-created or reused for this work.

## LobeHub badge and optional scoring items

The three READMEs use the compact badge Markdown supplied by the
[LobeHub score page](https://app.lobehub.com/community/mcp/ap311036-timetree-mcp?activeTab=score):

```markdown
[![MCP Badge](https://lobehub.com/badge/mcp/ap311036-timetree-mcp)](https://lobehub.com/mcp/ap311036-timetree-mcp)
```

The external badge can change independently of the installed package. It is a
directory signal, not an official TimeTree endorsement, a security audit, or
proof of live calendar correctness. A README badge does not guarantee a higher
score. Prompts and resources are optional capabilities; they are included here
for the workflow above, not as a claim that every server needs them.

The README badge and runtime interfaces are included in 0.6.0. LobeHub metadata
and its external score require separate directory verification; a package release
does not promise a new score. A contradiction between a claimed owner record and the score page's
claim item must be investigated without repeating claim/update.
