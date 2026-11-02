# Isolated calendar verification plan

This is the separate fresh-calendar write-test plan; it has not been executed.
The owner-reported read-only runner pass below does not execute this plan.
On 2026-11-01, no
authenticated calendar request, test-calendar creation, calendar write, or
deletion was performed for this assessment. Offline tests use synthetic data.

## Evidence and current boundary

An unauthenticated request to the public
[TimeTree sign-in page](https://timetreeapp.com/signin) on 2026-11-01 referenced
[main-B50tCVUi.js](https://frontend.assets.timetreeapp.com/assets/main-B50tCVUi.js).
Its SHA-256 remained
`0ada2f98ea41bf2389a595c45cb6c576f1a1a4bab14e05268636a0672016f240`, matching the
frontend inspected for the [single-occurrence implementation](recurring-events.md#first-party-contract-evidence).
The bundle contains monthly and yearly UI choices, month-day/month selectors,
and the existing child-plus-exclusion editing flow. This is evidence of
first-party client behavior, not a supported public API contract or a live
verification of month-end, leap-year, DST, or cross-zone mutations.

The read-only query follows a deliberately restricted recurrence model.
[RFC 5545 recurrence rules](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.3.10)
exclude invalid calendar dates from both results and COUNT; a monthly 31st does
not become the last day of shorter months. Missing date components inherit
from DTSTART. The
[recurrence-set definition](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.5.3)
leaves an anchor that does not match its rule undefined, so the query rejects
that shape. Unsupported or ambiguous cases must be reported as incomplete,
not presented as evidence that the user is free.

MONTHLY/YEARLY expansion is a local, read-only capability. It does not enable
MONTHLY/YEARLY occurrence moves. Existing write restrictions, identity checks,
confirmation requirements, journal locks, and uncertainty handling remain in
force until the relevant live contract can be verified safely.

## Current blocker

The separate [read-only stdio runner](e2e-runner.md) included in 0.5.0 uses a reviewed,
hash-verified plan for known marked records in its one preauthorized calendar.
It briefly reads that calendar's full raw snapshot in worker memory before
fixture filtering. This bounded read-only flow is distinct from the fresh
isolated-calendar write plan below; it does not authorize other targets,
mutations, or reuse of browser/private cloud sessions. The owner reported a
completed live stdio run with 8/8 planned-record checks passing. At the pre-release check, code
hashes matched the reviewed code and the approved plan hash was unchanged. The
report has no version/source/plan fingerprints, so the files actually loaded
cannot be independently bound to the release artifact retroactively. Earlier
CSRF failures are historical, as recorded in the runner guide.

Version 0.5.0 shares its production authentication and HTTPS implementation
between the ordinary reader and E2E guard; the guard still limits calendar
reads, fixture projection, and output. Offline tests and independent review of
shared authentication and the validated `/signin` to fixed `/calendars`
bootstrap are complete, with no remaining blocker in the reviewed scope.
It permits only one additional GET,
without accepting redirect bodies, cookies, or tokens, and rejects a second
redirect. All six read-only test fixtures were subsequently deleted through
TimeTree's web interface and independently checked absent in the approved
windows through a separate read-only calendar integration; monitoring state
was unchanged. The completed plan and handoff cannot be rerun. No
additional run is requested. The read-only pass and this cleanup do not verify
npm installation or its original entry point, GUIs, alternate credential
discovery, live writes, or notifications. Its credentials and in-memory HTTPS
session remain separate from browser sessions and private cloud integrations;
do not import their cookies or session state. Reads through another private
integration are separate evidence. Local synthetic fixtures verify code
behavior only.

| Item | Status |
| --- | --- |
| Action | Provision and verify a new calendar isolated from all existing personal and work calendars, then run bounded synthetic DST/cross-zone tests. |
| Target | A newly created test calendar with a newly verified ID; no existing calendar is an acceptable substitute. |
| Missing capability | The public MCP tool set has no calendar-creation tool. Existing integration allowlists do not authorize new test targets. No authorized authenticated test-calendar creation session was established. |
| Execution state | This fresh-calendar write plan was not attempted. No authenticated calendar access or live mutation occurred for this plan; there was no permission denial or uncertain write. The separate read-only result above does not change this status. |
| Required next step | The owner configures local public-package credentials and creates a fresh isolated calendar through TimeTree's supported UI, or provides a previously prepared fresh test calendar with authorized access. Confirm the exact test calendar before resuming. The UI is used for provisioning, not for importing its session into the public MCP server. |

This table describes the separate fresh-calendar write plan and does not
authorize another runner invocation. Its pending credential/setup requirements
do not invalidate or expand the reported read-only pass.

Changing credentials or bypassing an integration's calendar allowlist is not a
way to establish isolation. Do not inspect existing calendars to find a
convenient test target. If creating or granting access to a test calendar needs
another login or permission, stop that step and report the specific requirement.
The browser feasibility check did not inspect calendar content or cookies,
enter credentials, or attempt to create a calendar. It did not establish whether
a saved login could be reused; an authorized isolated creation flow must still
be verified before testing.

## Preconditions for a future live run

1. Prefer a dedicated test account with no personal/work calendars. Otherwise,
   have the owner create a fresh, private calendar in TimeTree's own UI and
   record its exact identifier through an authorized selection flow. A name
   alone is insufficient. No existing calendar may be renamed or repurposed.
2. Confirm the test calendar has no invitees, external integrations, imported
   events, monitoring delivery, or shared notifications. Keep test titles,
   notes, attendees, and reminders synthetic; do not copy real calendar data.
3. Bind every test request to the verified test calendar ID. A transport guard
   must reject every other calendar path before sending any request. Use a
   private, stable test journal and never share it with a production MCP process.
4. Verify an empty baseline for this calendar only. If the transport cannot
   avoid accessing other calendars, stop before testing and use a truly isolated
   account. Store private IDs and snapshots outside the repository; commit only
   synthetic, redacted fixtures and behavior summaries.
5. Prepare the exact synthetic inputs and expected state changes before each
   write. Do not disable existing guards or manufacture linkage merely to make
   the current editor accept a series. Any first-party UI edit used to study
   detached-child behavior needs its own reviewed test case and read-back.

## Test matrix

The examples below are synthetic expectations for investigation, not claims
about results already observed in TimeTree.

| Case | Offline assertion and required live evidence |
| --- | --- |
| Fixed-offset baseline | Taipei, Tokyo, and Kolkata preserve local time, exact numeric offset, duration, and both zone names. Compare UI rendering with selected-calendar raw reads. |
| Safe DST daytime recurrence | New York around 2030-03-10 and 2030-11-03, and London around 2030-03-31 and 2030-10-27, retain 09:00 local time while UTC changes. Verify original and moved intervals separately. |
| DST gap/fold | A nonexistent or repeated local time, either possible fold offset, or an interval crossing an offset change is rejected before dispatch. Verify zero calendar write requests, not just an error message. |
| Missing timezone data | Removing data from an isolated test runtime blocks new dispatch; saved journal replay remains local. Do not modify the machine's shared timezone installation. |
| Cross-zone start/end | Current queries/moves reject the shape. A future supported behavior requires separately observed UI and backend semantics; a successful raw POST alone is insufficient. |
| Monthly day 31 | With a matching January 31 anchor and COUNT=3, the restricted model yields January, March, and May 31. Compare the UI's generated dates, stored rule, and selected-calendar read-back. |
| Yearly February 29 | A matching 2028-02-29 anchor with COUNT=3 yields 2028, 2032, and 2036 in the restricted model. Include century boundaries in offline tests. |
| EXDATE plus detached child | Preserve the original rule, exclude the original instant once, retain the child's two verified parent links, and show the replacement exactly once. Verify no collateral record changes. |
| Lost response or interruption | Simulate offline. Once a live dispatch has an unknown outcome, stop; inspect the same operation ID and selected-calendar state without retrying the mutation. |

## Acceptance and cleanup

A live case passes only when the before/after state, stored rule and links,
expected UTC/local times, and first-party UI rendering agree. Record the
runtime/timezone-data version and public frontend revision. A write response
alone does not establish recurrence semantics or protection from concurrent
changes. The API still lacks a verified conditional update; avoid all concurrent
edits during the test and retain that limitation in the report.

Do not automatically retry, compensate, remove a child, clear a journal lock,
or delete a calendar after an uncertain operation. Keep the isolated artifacts
for inspection. Event or calendar deletion may be irreversible; obtain separate
approval for the exact synthetic targets before destructive cleanup when a
reliable restoration path has not been verified. Do not delete anything from
an existing personal or work calendar.

Report each case as passed, failed, blocked, or not attempted, with the exact
target class and reason. Publish only evidence actually collected; synthetic
tests and public source inspection must remain distinguishable from live tests.

## Notification evidence is a separate test

The no-invitee recurrence plan above cannot establish whether other members
receive TimeTree notifications. `silent` payload checks and a successful local
MCP transport write cannot establish that either. Any future shared-notification
test needs separate authorization for synthetic sender/recipient accounts, the
exact test calendar and actions, client notification settings, and observed
notification channels. No such test is recorded here. Keep notification
suppression and user receipt marked as unverified.
