# 0.5.0 — Shared authentication, sync completeness, and guarded monitoring

TimeTree MCP 0.5.0 keeps the existing **29 tools** and improves authentication,
partial-read handling, and monitoring state.

## Changes

- **One complete credential source.** Explicit credentials no longer borrow
  missing values from another source or unnecessarily consult Keychain.
  `timetree_status` checks presence without reading password-file contents or
  Keychain secrets; configured credentials are not proof of a successful login.
- **Shared, bounded HTTPS authentication.** The ordinary reader and controlled
  stdio runner use the same transport and login implementation. CSRF retrieval
  permits only an exactly validated `/signin` transition to one fixed
  `/calendars` GET. Redirect bodies, cookies, and tokens are ignored; a second
  redirect fails. Authentication becomes ready only after a valid CSRF response.
  Transport failures, cancellation, and HTTP 401/403 invalidate local session
  state without replaying the failed request.
- **Explicit synchronization completeness.** Malformed pages, duplicate or
  cross-calendar identities, and acquisition limits produce incomplete results
  with diagnostics. Memo reads now expose completeness metadata too. Strict
  occurrence reads reject incomplete sources.
- **Guarded monitoring state.** Only complete, validated snapshots advance the
  baseline. Bounded, process-local batches preserve known-unsent and uncertain
  delivery states. Uncertain writes block automatic replay; a timed-out stop
  cannot start a second poller. Transport-write success is not user receipt,
  and process restart loses the queue without resolving uncertain outcomes.
- **Controlled read-only verification and documentation.** The scoped stdio
  runner uses temporary hidden credential input, a reviewed private plan, fixed
  request limits, and redacted output. English, Traditional Chinese, and
  Japanese guides document the supported behavior and its limits.

## Compatibility notes

- The default transport explicitly rejects active or unknown proxies, including
  the presence of any environment key ending in `_proxy` (case-insensitive,
  including `NO_PROXY` and empty values). It does not modify proxy settings.
  Environments relying on urllib's implicit proxy behavior may no longer work.
- Developer HTTPS-origin and user-agent overrides remain available but are
  validated. Response, header, cookie, and synchronization limits now apply.
  Ordinary valid UTF-8 text/empty API responses remain supported; strict
  JSON-object validation applies to sign-in.
- Event create/update reject the formerly ignored `silent` and `content`
  arguments. Use `note` for event notes. Comment `silent` must be a JSON boolean;
  omitting it still defaults to true. This does not establish notification
  suppression.

## Verification and limits

The offline suite passed **405 Python tests and 5 Node tests**. Independent
review covered the shared authentication/transport and guarded runner, including
failure, cancellation, redirect, and stale-state cases, with no remaining blocker
in the reviewed scope. These checks do not prove all upstream behavior.

The owner reported a completed live stdio run with **8/8 planned-record checks
passing**. At the pre-release check, implementation hashes matched the reviewed
code and the approved plan hash was unchanged. The report itself contains no
version/source/plan fingerprint, so the files loaded by that earlier process
cannot be independently tied to this release artifact retroactively.

All six fixture records were then deleted once each through TimeTree's web
interface and independently checked absent in the approved windows through a
separate read-only calendar integration. Monitoring state was unchanged. The
completed plan and handoff must not be rerun. Cleanup through the web interface
does not verify the public MCP write path.

The reported run covered controlled, read-only fixtures. It did not verify npm
installation or the original npm entry point, client GUIs, alternate credential
discovery, live public-MCP writes, notification suppression, or user receipt.

## Install

```bash
npx --yes timetree-mcp@0.5.0
```

Node.js 18+ and a separately installed Python 3.10+ are required. Configure
credentials locally before calendar access; never put a password in chat or
MCP tool arguments.
