# Local, read-only stdio verification

The owner reported that the controlled read-only stdio run completed all eight
planned fixture checks successfully:

```json
{"status":"passed","stage":"complete","checks_passed":8,"scope":"planned_test_records","transport":"stdio"}
```

This is an owner-reported live pass for the guarded, planned-record workflow.
At the pre-release check, the seven recorded candidate-file hashes matched the previously reviewed
code and the approved private-plan hash was unchanged. However, the run report
itself contains no version, source fingerprint, or plan fingerprint, so the
files actually loaded by that earlier process cannot be independently bound
to the release artifact retroactively. Do not convert this scoped report into
independently reproduced verification of a release artifact.

All six fixture records were subsequently deleted once each through TimeTree's
web interface. A separate read-only calendar integration independently found no
matching records in the approved test windows; monitoring state was not changed.
Cleanup through the web interface does not validate
the public MCP write path. **The completed plan and its launch handoff must not
be rerun:** their expected fixture records no longer exist.

**No additional run is requested.** A later run would need new approved fixtures,
a new reviewed plan, and separate explicit authorization. The shared production
authentication implementation below is included in 0.5.0. Its offline tests and
independent review are complete, with no remaining blocker in the reviewed scope.

The helper exercises a worker process, MCP stdio messages, the public Python
dispatcher, and the same production authentication and HTTPS implementation
used by the ordinary reader, behind a restricted test guard. It
does **not** launch the original npm entry point, test `npx` installation, or
verify a Claude/Cursor/Codex/ChatGPT GUI. Version 0.5.0 includes this helper and
the credential fixes; npm 0.4.0 has the earlier behavior. The reported run is
not a live test of the published npm installation or entry point.
The guard still restricts calendar access and fixture projection and redacts
results. The runner supplies an explicit in-memory constructor credential pair;
it does not test Keychain or password-file discovery, every ordinary status
configuration, or an unrestricted calendar view. Shared implementation is not
a claim that the entire production client workflow has been exercised.

## Earlier redirect evidence

Before the reported pass, an operator attempt returned `stage: "login"`,
`error: "login_failed"`, `phase: "csrf_fetch"`,
`reason: "http_redirect_rejected"`, `redirect_target: "calendars"`, and
`checks_passed: 0`. That failure identified an exact recognized calendar-route
target; it did not establish a usable session. Its report omitted the HTTP
status number, so it must not be described as an observed HTTP 302. Earlier
unclassified and generic failures established less. These are historical
failures, not the latest reported result.

Separate **unauthenticated** checks observed:

| Public request | Observation |
| --- | --- |
| [TimeTree homepage](https://timetreeapp.com/) | HTTP 302 to the [public English landing page](https://timetreeapp.com/intl/en/). |
| [TimeTree sign-in page](https://timetreeapp.com/signin) | HTTP 200; one nonempty CSRF meta value matched the existing public reader's regex. No CSRF response header was present. Only counts/results were recorded, not the token value. |
| [TimeTree calendar route](https://timetreeapp.com/calendars) | HTTP 302; its target was not exposed or recognized in the recorded diagnostic. |

Repeated anonymous checks still found `/signin` returning HTTP 200 with one
CSRF meta value, and `/calendars` returning HTTP 302 with an unclassified target
that was not printed. These anonymous responses alone do not establish an authenticated
calendar session or usable authenticated CSRF material. Do not assign the
anonymous HTTP 302 status to the operator's response. Diagnostic classification
does not itself follow a target or retry login.

## Shared production authentication (0.5.0)

The ordinary reader and the E2E guard now delegate to the same
`http_transport.DirectHttpsTransport` implementation for TLS requests,
CookieJar handling, headers, UTF-8/JSON decoding, and transport errors. Each
instance keeps its own session; the runner does not import another session.
The old `UrllibTransport` import name is a compatibility alias for this same
implementation, not a second HTTP path. The worker no longer implements its
own HTTP stack or overrides CSRF retrieval.

Both use the production login sequence: at most one sign-in PUT followed by
one `GET /signin` per attempt. The initial sign-in response must pass the shared
HTTP and JSON-object checks. A successful CSRF response must pass the shared
status/body/token checks before the reader sets `authenticated: true`.

There is one narrowly bounded additional step. For `GET /signin` only, the
shared transport can return an empty `CsrfCalendarRedirect` marker when the
status is 301, 302, 303, 307, or 308 and exactly one raw `Location` equals
`/calendars` or the configured origin plus `/calendars`. It does not fetch that
target, read the redirect body, accept redirect cookies or tokens, or copy its
headers into the marker. The existing cookie jar is preserved for this marker.
The reader then makes at most one GET to its constant same-origin `/calendars`
path. That response must be 2xx with usable CSRF material; a second redirect,
unusable response, or missing token fails and clears the session. There is no
root-page fallback, arbitrary target traversal, or automatic login retry.

In the E2E worker, an accepted `/calendars` HTML response is held within the
existing response limits in process memory and inspected only for CSRF. It may
contain other page-initialization content. The pass report does not identify
whether this optional step ran or disclose the authenticated page contents.
That HTML is never executed, logged, saved, or
included in output. Calendar-event access and fixture filtering remain guarded
as described below. This disclosure does not request another run.

The E2E guard permits this additional GET only after a validated sign-in PUT
and the marker from its first `/signin` GET. The permission is consumed once,
before dispatch, and cannot be reused after a failure. Failure or cancellation
clears cookies, CSRF state, and authentication flags. The runner still permits
only one login attempt in a run.

The shared reader also clears that session state after an ordinary request's
transport exception, cancellation, or HTTP 401/403. It preserves the original
failure and does not replay the failed request. A later independent read,
including a monitor's next poll, may authenticate again through the ordinary lazy-login path; the
E2E guard instead retains its one-attempt limit. Other API errors, including
HTTP 400 and sync code `-425`, keep their existing error handling.

Anonymous `/signin` HTML matching the existing parser supports this route
choice, but does not prove it succeeds in the operator's authenticated session.
A successful monitor read is not evidence for that authenticated CSRF GET.
The optional redirect diagnostic describes a rejected response; its value alone
does not authorize a fetch. Only the validated marker and state described above
permit the single fixed bootstrap GET. This is not general redirect support.

Version 0.5.0 changes the **default production transport policy**,
not just the test URL. Requests stay on the configured canonical HTTPS origin
(TimeTree by default); developer HTTPS-origin overrides remain available but
are validated. Redirects are refused except for the marker-based bootstrap
transition described above; the HTTP transport itself never follows them.
Active or unknown proxy configurations
are explicitly rejected instead of being implicitly used through urllib. No
OS, network, or proxy settings are changed. The E2E guard additionally pins the
TimeTree origin and the reviewed request/fixture scope. See the full
[compatibility notes](clients.md#shared-authentication-and-transport).

The shared core bounds each accepted response body to 8,000,000 bytes, combined
parsed header names/values to 65,536 characters, and the cookie jar to 128
cookies. The header limit excludes HTTP framing overhead. Authentication HTTP
failures and all redirects do not consume response bodies or accept cookies
from those responses. Ordinary API errors still retain bounded decoded
diagnostics, including sync code `-425`. General API responses may retain valid
UTF-8 text or an empty body, including HTTP 200 delete responses and empty
204/205 responses; strict JSON-object validation applies to the sign-in PUT.
Operation-specific reader checks still reject invalid lists, snapshots, and
identities. Writes still send their CSRF header. The read-only E2E runner does
not verify live writes.

The E2E guard retains its 32,000,000-byte aggregate response limit and
600-second deadline. Its request budget is `3 + 27 × number_of_plan_checks`,
allowing the one additional bootstrap GET; fixture filtering and output
redaction remain in place. These
restrictions do not replace production authentication with a test-specific
success path. Offline parity tests exercise the shared authentication and
failure handling, but cannot establish a successful provider login. The final
offline suite passed 405 Python tests and 5 Node tests. Independent review
passed 137 core/reader/worker tests, 7 tests using real HTTP response parsing
over synthetic wire data, and a 64-case shared-core matrix: 20 allowed
successes, 34 rejected variants/statuses, and 10 destination failures. These
groups overlap and must not be added into a total. No remaining blocker was
found in the reviewed scope, including stale bootstrap permission after failure
or cancellation. No additional run is requested. npm 0.4.0 is unchanged
and does not include these source changes.

## Scope and private plan

Use only the reviewed private plan and its independently supplied SHA-256 hash.
The plan identifies one preauthorized calendar and known, marked source
records. It contains private calendar/event identifiers and expected fixture
values; keep it outside the repository with owner-only `0600` permissions.
Do not paste it into chat or include it in an issue, commit, screenshot, or
public report. No private identifiers or fixture titles are embedded in this
guide.

The plan schema is:

| Field | Meaning |
| --- | --- |
| `version` | Plan format version, currently `1`. |
| `calendar_id`, `calendar_name` | The one approved target and its exact name. Allowed names are `EWS` or `EWS 行事曆`; the worker requires the observed name to equal the plan's selected value. Changing these fields does not authorize another calendar. |
| `records` | Known records, each with `source_uuid` and the exact marked `title`. |
| `checks` | Named checks containing tool `arguments` and exact expected occurrence results. |
| Check `arguments` | `calendar_id`, explicit `start_at`/`end_at`, `limit`, and optional `query`. |
| Check `expected` | Expected `event_uuid`, `start_at`, `end_at`, `timezone`, `start_local`, `end_local`, `source`, and `series_uuid` fields. |

The plan is capped at 65,536 bytes, 1–6 known records, and 1–12 unique checks.
Each check has an explicit limit of at most 100 and at most 50 expected rows.
Unknown fields, duplicate identities, unmarked titles, and unexpected query
shapes are rejected. The approved hash is 64 lowercase hexadecimal characters.

A hash mismatch is a stop condition. Do not simply compute and accept a new
hash for an unreviewed plan. Changes to a target, record, window, or expectation
require another review of the plan before the run.

This is read-only verification of the named fixtures. It does not create,
update, delete, invite, move an occurrence, start monitoring, or test TimeTree
notification suppression. It cannot establish completeness of the whole
calendar or that an unlisted time is free.

The provider reads the account's calendar-index metadata to verify the target,
and briefly receives the **whole authorized calendar's raw event snapshot in
worker memory before filtering to the marked records**. Other calendars' event
snapshots are not requested. The adapter is not an upstream server-side fixture
filter. If index access or the target's full snapshot exceeds the approved
scope, do not run it. Other calendar metadata and non-fixture content are not
included in the final report; keep that report separate from the private plan.
The worker limits each accepted HTTP response body to 8,000,000 bytes and the
total accepted response bodies to 32,000,000 bytes. Exceeding these limits fails
the run rather than yielding a partial success.

## Operator steps after authorization

These are reference instructions, not a request to repeat the completed run.
Its fixtures have been removed, so its old plan and handoff are no longer
runnable. A later run would need new approved fixtures and a new reviewed plan,
as well as separate explicit authorization.

1. Use your own Terminal window on the Mac. Do not enter credentials through
   chat, an agent-controlled terminal, shell arguments, an environment variable,
   a password file, or Keychain for this test. Do not import browser cookies or
   a private cloud MCP session.
2. Obtain the reviewed checkout, new private plan, and approved hash from
   that run's operator handoff. Its private launch instructions supply the actual
   paths and hash. Confirm that Python 3.10+ and IANA timezone data are available
   in that Python environment; the helper does not install them.
3. From that reviewed checkout, run the following shape, replacing the
   placeholders with the approved local values. On Apple Silicon Macs with
   Homebrew Python:

   ```bash
   /opt/homebrew/bin/python3 -I -B scripts/e2e_stdio.py \
     --plan /absolute/path/to/private-e2e-plan.json \
     --sha256 APPROVED_64_CHARACTER_SHA256
   ```

   The command contains no credential values. Use the exact Python executable
   supplied by the handoff if it differs. `-I` isolates Python from user-site
   configuration and Python environment overrides; `-B` prevents bytecode files.
   Do not add `env -i`: the parent checks for proxy environment keys before
   launching the worker with a fixed clean environment. A proxy-related refusal
   needs an approved direct environment, not a bypass of the check.
   The worker also checks macOS proxy/PAC settings with the read-only
   `scutil --proxy` command before prompting; it does not launch further
   subprocesses after credential input. Its HTTPS origin is fixed; only the
   narrowly bounded bootstrap described above can add one calendar-page GET.
   Use the normal command shown above; the worker mode is an
   internal implementation detail.
4. Enter both email and password only at the worker's hidden `getpass` prompts
   on `/dev/tty`. The worker refuses to fall back to echoed input. Neither
   secret is saved to a credential file, Keychain, environment setting, or
   command history by the helper.
5. Wait for the bounded report. A successful run establishes only the listed
   fixture checks through this adapter. Any error, timeout, or interrupted run
   is not a pass. Retain only the sanitized result needed for review; never
   attach the plan or credential/session data.

Once the worker run starts, the final JSON report contains `status`, `stage`,
`checks_passed`, `scope`, and `transport`, plus a fixed error code on failure.
A startup or plan-validation failure can return only `status` and `error`.
A successful result has
`status: "passed"`, `stage: "complete"`, and exit code zero. Some checks passing
before a later failure does not make the overall run pass. The report does not
include credentials, cookies, fixture identifiers, titles, or occurrence rows.
The helper does not automatically retry failed login or calendar requests.

## Startup troubleshooting on macOS

An earlier candidate could exit before showing a prompt because its Terminal
stream could not be opened in update mode. The fix uses an output-only prompt
stream and has macOS pseudo-terminal (PTY) tests with synthetic credentials.
This verifies prompt behavior, not a live TimeTree login. If a later run is
explicitly authorized with new fixtures and a new reviewed plan, use the
**command and plan hash approved for that run**, not the completed handoff. Enter nothing
until the hidden email/password prompt appears; never type a secret at a shell
prompt or into chat.

When the worker fails during initial startup, the parent maps its exit status
to a fixed error code:

| Error | Next step |
| --- | --- |
| `proxy_not_supported` | Stop. The runner cannot use the detected proxy configuration; do not disable proxies or bypass checks for this test. |
| `proxy_state_unknown` | Stop and report the final JSON. Do not assume the connection is direct. |
| `terminal_unavailable` | Use your own interactive Terminal for an authorized run; do not redirect or pipe credential input. |
| `credential_input_unavailable` | Hidden input was unavailable or invalid. Check the operator Terminal setup before another run; do not supply credentials by another channel. |
| `operator_cancelled` | The operator cancelled startup; no successful run is claimed. |
| `worker_startup_failed` | Stop and report the final JSON for review. |

These codes apply to initial worker startup, not arbitrary failures later in
the run. Valid native macOS `no` proxy-bypass metadata alone is not treated as
an active proxy; active-proxy and PAC checks still apply. Share **only the final
JSON report** when diagnosing a failure—no secrets, private plan, debug logs,
screenshots, or copied Terminal input.

## Safe login diagnostics

Version 0.5.0 can report `error: "login_failed"` with a
`login_diagnostic` object containing two required fixed fields:

- `phase`: `sign_in` for the shared production sign-in PUT, or `csrf_fetch` for
  obtaining CSRF material through `/signin` and, only after the validated marker,
  the single fixed `/calendars` GET needed to complete login.
- `reason`: one of the fixed codes below. It is a failure category, not a full
  explanation of the underlying cause.

An optional `redirect_target` is accepted **only** when `phase` is `csrf_fetch`
and `reason` is `http_redirect_rejected`. Its value is one of four fixed classes:

| `redirect_target` | Meaning |
| --- | --- |
| `public_landing` | An exact recognized public-landing target. |
| `signin` | An exact recognized sign-in target. |
| `calendars` | An exact recognized calendar-route target. |
| `other_or_missing` | No recognized exact target, or no target provided. |

Classification compares only exact allowlisted raw target forms. Unknown forms,
including forms with queries, are classified as `other_or_missing`; no raw
`Location` value, query, header, body, cookie, or arbitrary URL is returned.
These classes do not establish authentication or CSRF validity. No target is
followed and no new request is made to classify it.

| Reason | Interpretation and next step |
| --- | --- |
| `http_401`, `http_403` | HTTP refusal. Neither code alone proves the password is wrong; review the phase and result before another attempt. |
| `http_429` | HTTP rate-limit response. Stop repeated attempts and review before another run. |
| `http_4xx`, `http_5xx` | An HTTP client/server error class; the response body is not exposed. Review the result before retrying. |
| `http_redirect_rejected` | A redirect outside the single allowed bootstrap transition, including any second redirect, was rejected. Neither the production reader nor the E2E guard follows the rejected target or broadens its allowed origin. |
| `http_rejected` | The response was rejected; the code alone does not identify the cause. |
| `tls_failed`, `network_timeout`, `network_failed` | A TLS, timeout, or network failure category. No arbitrary exception details are exported. |
| `invalid_response` | The response did not match an accepted format. Stop for review before any further attempt; do not relax parsing to make the run pass. |
| `response_limit`, `session_limit` | A response or bounded-session limit was reached. Review rather than raising limits or looping retries. |
| `request_denied` | The restricted request boundary refused the operation; do not bypass it. |
| `internal_error` | An internal failure without a more specific safe category. Stop for review. |
| `csrf_missing` | The CSRF material needed to complete login was missing. This does not prove the credentials were bad or that login completed successfully. |

Only allowlisted phase/reason strings and the conditional target class may
accompany this diagnostic. Raw response bodies, headers, URLs, cookies,
credentials, and arbitrary exception text are not exported. A missing or
unrecognized diagnostic remains an unknown cause; do not reinterpret the
earlier `mcp_tool_failed` result as one of these codes.

There is no retry loop, and **no rerun is authorized by this diagnostic change**.
Report and review the current findings before seeking explicit authorization
for any later operator run. If such a run is authorized, use only the owner's
Terminal and hidden prompts, and share only the final JSON. Do not provide a
password, screenshot, browser/session data, or debug logs in chat. Unknown or
response-format failures require further review. Diagnostic categories alone
are not success evidence; the separate owner-reported pass and its fingerprint
limitations are recorded at the top of this guide.

## Deadline and cleanup

The run has a 600-second deadline, including time spent at the credential
prompts. Normal completion clears the worker's local session and closes the
process. On interruption or timeout the parent attempts bounded worker cleanup
and terminal restoration; a cleanup failure is reported as a failed run.
The CLI requires a saved Terminal-settings snapshot before starting the worker,
so it can attempt to restore input echo even if the worker must be killed.
Credentials are held in process memory for the run; this is not a promise of
secure memory zeroization or server-side session revocation. The helper does
not change or remove credentials you may have configured elsewhere.

The ordinary monitoring queue in 0.5.0 is also memory-only, but this helper
does not start it or establish notification display/user receipt. See
[monitoring semantics](monitoring.md), [client validation boundaries](clients.md),
and the separate [isolated calendar test plan](isolated-calendar-testing.md).
Broader live recurrence, write, notification, and GUI evidence remains separate.
