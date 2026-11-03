# Connect your MCP client

TimeTree MCP runs as a **local stdio process**. Choose a client that can launch
a command on the computer holding your credentials. It does not expose an HTTP
server or an OAuth login endpoint.

## Requirements and verification

- Python **3.10+** is required even when installing through npm.
- The npm launcher also requires Node.js **18+** and `npx`.
- Set up a TimeTree email/password locally before reading your calendar.
- Occurrence moves require IANA timezone data. Python reads the system database
  or optional `tzdata`; Windows commonly needs `python -m pip install tzdata`
  in the Python environment selected by `TIMETREE_PYTHON`. The npm wrapper does
  not install this data. See [timezone validation](recurring-events.md#timezones-and-daylight-saving-time).
- Desktop applications may have a different `PATH` from your terminal. Use
  absolute executable paths when necessary. `TIMETREE_PYTHON` accepts one Python
  executable path, not a command such as `py -3.12`.

Recipes below were checked against the official client documentation on
**2026-10-31**. On macOS with Node.js 25.6.1 and Python 3.14.5, the published
`npx --yes timetree-mcp@0.2.4` command completed MCP `initialize`, `tools/list`
(24 tools), and `ping` without credentials or a TimeTree request. JSON examples
were parsed locally. These checks verify the launcher and protocol, **not**
Claude Desktop/Cursor/ChatGPT UI behavior, notification display, or a live
TimeTree login. Windows recipes have not been run on Windows. The configuration recipes below
pin 0.5.0, which provides 29 tools; the historical 0.2.4 check does not verify
the later additions.

The locally packed 0.3.0 release artifact also passed `initialize`, `tools/list`
(27 tools), and `ping` with the same Node/Python versions and no credentials or
calendar requests. This verifies the bundled artifact, not a client GUI or a
live calendar mutation.

On 2026-11-01, the locally packed 0.4.0 artifact passed `initialize`, `tools/list`
(29 tools), `ping`, and `timetree_timezone_status` for `Asia/Tokyo` with the same
Node/Python versions. Its package includes all three READMEs. These checks used
no credentials or calendar requests; they do not verify a client GUI or live
calendar behavior.

Version 0.4.0 introduced `timetree_list_occurrences` and `timetree_timezone_status`
to the 27 tools in 0.3.0. The pinned npm configurations below select the
29-tool 0.5.0 version; a matching [source checkout](usage.md#local-checkout) is also
available. No new client UI validation is implied by these additions.

| Client surface | Connection | Verification boundary |
| --- | --- | --- |
| Codex CLI / local Codex host | stdio command | Installed CLI help checked; published launcher smoke test passed |
| Claude Desktop | local `mcpServers` JSON | Official configuration format reviewed; app UI not tested |
| Cursor desktop | local `mcp.json` | Official configuration format reviewed; app UI not tested |
| ChatGPT desktop with local MCP settings | stdio command | Documented by OpenAI; app/version availability and UI not tested |
| ChatGPT web custom MCP apps/plugins | remote endpoint | No remote deployment or adapter supplied by this project |

## Configure credentials once

### macOS Keychain

The npm package bundles the Python server source but does **not** install the
`timetree-credentials` command or the repository's setup script. To configure
Keychain, use a source checkout:

```bash
git clone https://github.com/ap311036/timetree-mcp.git
cd timetree-mcp
python3 src/timetree_mcp/credentials.py set
python3 src/timetree_mcp/credentials.py status
```

The setup prompts for the password without echoing it. `status` reports
presence only. The npm server can then use those Keychain items; omit the
credential `env` entries in the configurations below. An OS permission prompt
may need your attention.

Alternatively, installing the source package into a virtual environment adds
`timetree-credentials set`, `status`, and `clear` commands; see the
[usage guide](usage.md#local-checkout).

### Password file on any platform

Create a private UTF-8 file containing only your TimeTree password, outside any
repository or synced/shared directory. Restrict its access to your OS account
(for example, `chmod 600 /absolute/path/to/timetree-password` on macOS/Linux).
Use Windows file permissions on Windows. Configure `TIMETREE_EMAIL` and
`TIMETREE_PASSWORD_FILE` as shown below. Do not paste a real password into a
prompt, command history, JSON configuration, or MCP tool argument.

Always configure `TIMETREE_EMAIL` together with `TIMETREE_PASSWORD` or
`TIMETREE_PASSWORD_FILE`. Remove all three variables when switching to Keychain;
do not mix credentials from different accounts. An unreadable configured
password file is an error.

### Credential resolution and status

Since 0.5.0, the server selects one complete credential source:

1. An explicit constructor email/password pair, for Python integrations.
2. Otherwise, `TIMETREE_EMAIL` plus `TIMETREE_PASSWORD` if that variable exists,
   or `TIMETREE_PASSWORD_FILE` when it does not.
3. Otherwise, the macOS Keychain email/password pair.

A selected explicit layer with missing, empty, or whitespace-only values is
rejected. It never borrows a missing field from another layer. An empty
`TIMETREE_PASSWORD` is an error even if a password file is configured. A complete
constructor pair ignores environment settings; a complete environment password
pair does not open the password file or consult Keychain. Keychain is selected
only when no explicit credential settings are present.

`timetree_status` reports `credential_source` as `constructor`, `environment`,
`password_file`, or `keychain`. Explicit sources report `keychain.checked: false`.
For the Keychain source, status checks item presence without reading secret
values. For a password file, status checks that it is a nonempty, readable regular
file without reading its contents. `credentials_configured: true` is only a
configuration/presence check: it does not validate file contents, passwords, or
account access. Login reads the selected secret later; a blank or unreadable
password file fails without falling back to Keychain.

These changes are **not in npm 0.4.0**, whose credential resolver can combine
sources and consult Keychain even with explicit credentials. Use 0.5.0 or
newer for the behavior above. The existing `timetree_keychain_status`
tool remains an explicit Keychain-presence check regardless of login source.

### Shared authentication and transport

Since 0.5.0, the ordinary reader and the guarded E2E runner use the same production
authentication implementation and `DirectHttpsTransport` core. Shared code
handles TLS connections, CookieJar state, headers, UTF-8/JSON decoding, and
transport errors; the worker no longer has a separate HTTP or CSRF path.
`UrllibTransport` remains an import alias for this core, not an alternative
implementation.

Each login attempt sends at most one sign-in PUT followed by one `GET /signin`.
The sign-in body must be a JSON object without an error in both paths; a
successful CSRF response and token must pass validation before authentication
is marked successful. The token must satisfy the shared HTTP-header validation.

One exact bootstrap transition can add a single fixed `GET /calendars`. Only a
`GET /signin` response with status 301/302/303/307/308 and one raw `Location`
equal to `/calendars` or the configured origin plus `/calendars` produces the
empty `CsrfCalendarRedirect` marker. The transport does not follow the redirect
or accept its body, cookies, or token; it preserves the existing cookie jar.
The reader uses its constant calendar-page path, not a response-supplied URL,
and requires a 2xx response with usable CSRF. Any second redirect fails closed.
The E2E guard permits this step only after validated sign-in and the marker from
its first `/signin` GET, and consumes that permission once before dispatch.

Failures and cancellation clear session cookies, CSRF state, and authentication
flags; login does not retry automatically. A rejected redirect or missing CSRF
token is not proof that a password was wrong.

For ordinary API requests, a transport exception, cancellation, or HTTP 401/403
also clears cookies, CSRF state, and authentication flags. The failed request
is not replayed; its original failure remains available to the caller. Other
API errors, including HTTP 400 and sync code `-425`, keep their existing error
handling. A later independent read, including a monitor's next poll, may use
the existing lazy-login path to authenticate again. The E2E runner still allows only one
login attempt per run and does not use this recovery path.

Version 0.5.0 changes compatibility of the default production transport:

- Requests use the configured canonical HTTPS origin, defaulting to TimeTree.
  `TIMETREE_BASE_URL` remains a developer override for a valid HTTPS origin;
  invalid or non-HTTPS configurations are rejected. The origin cannot contain
  user information, a path, a query, or a fragment.
- `TIMETREE_USER_AGENT` remains available as a developer override. Its value
  must be nonempty printable ASCII and no longer than 512 characters.
- The HTTP transport never follows redirects. The reader allows only the
  marker-based, one-step calendar-page bootstrap described above; all other
  redirects are rejected. There is no root-page fallback for CSRF retrieval.
- Active or unknown proxy configurations are rejected explicitly. The previous
  urllib transport could use proxies implicitly; environments depending on
  that behavior may now fail. Any environment key ending in `_proxy`
  (case-insensitive), including `NO_PROXY` or an empty value, causes refusal.
  Valid native macOS `no` bypass metadata alone is accepted; active-proxy and
  PAC checks still apply. The implementation does not change system or proxy
  settings, and bypassing the check is not a remedy.
- Each accepted response body is limited to 8,000,000 bytes, combined parsed
  header names/values to 65,536 characters, and the in-memory cookie jar to
  128 cookies. The header limit excludes HTTP framing overhead.
- Authentication HTTP failures and all redirects do not read response bodies
  or accept their cookies. General API failures still retain bounded decoded
  diagnostics such as sync error `-425`.
- General API responses retain valid UTF-8 text or an empty body where the
  operation permits it, including HTTP 200 delete responses and empty 204/205
  responses. Strict JSON-object validation applies to the sign-in PUT; the
  reader still rejects invalid list/snapshot structures and identities.
  Duplicate JSON keys, non-finite numbers, invalid UTF-8, and invalid response
  framing are rejected. Calendar writes still carry the CSRF header.

The E2E wrapper adds request/total-response/time limits and restricts calendar
reads to its approved plan; it filters fixtures and redacts output. Sharing
authentication does not make its calendar view unrestricted or turn it into a
test of the original npm entry point, all credential sources, client GUIs,
writes, or notification receipt. These changes are **not in npm 0.4.0**.

Offline tests and independent review of the shared authentication and bounded
calendar-page bootstrap are complete, with no remaining blocker in the reviewed
scope. Separately, the owner reported a completed live stdio run with all eight
planned-record checks passing. At the pre-release check, implementation hashes matched the reviewed
code and the approved plan hash was unchanged; the report itself contains no
version/source/plan fingerprint, so the actually loaded files cannot be
independently bound to the release artifact retroactively. All six fixtures were
subsequently deleted and checked absent, so that completed plan cannot be rerun.
Earlier CSRF redirect failures are historical. See [the reported pass and its limits](e2e-runner.md).

### Public-package login remains a separate check

The public stdio package requires credentials configured locally for its own
process. It does not import a browser login, cookies from another client, or a
shared/private cloud MCP session. Successful reads from a separate private
integration, including a timezone example, do not prove the public package's
login, calendar reads, or notification delivery work end to end.

The owner-reported 8/8 pass covers controlled, read-only planned fixtures over
stdio. It does not verify the original npm entry point or installation, a GUI,
alternate credential discovery, live writes, or notification delivery. No
additional run is requested; do not import another integration's session. The
[isolated calendar write plan](isolated-calendar-testing.md) remains unexecuted
and separate from this read-only success.

The [local read-only stdio runner](e2e-runner.md) included in 0.5.0
offers a separate operator flow: hidden prompts in the owner's Terminal,
credentials held only for the run, and a private hash-verified plan for known
marked fixtures. It shares the production authentication/HTTPS implementation
while restricting calendar reads and results; it does not verify the original
npm entry point or a client GUI. The reported live pass and its source-binding
limitation apply only to that controlled flow. The old fixture plan and handoff
must not be reused. A later run would require new approved fixtures and a
reviewed plan, as well as separate explicit authorization.

## Codex

The installed `codex mcp add --help` supports a stdio command after `--` and
`--env KEY=VALUE` entries. With a password file:

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp@0.6.0
```

With macOS Keychain already configured:

```bash
codex mcp add timetree -- npx --yes timetree-mcp@0.6.0
```

If needed, add `--env TIMETREE_PYTHON=/absolute/path/to/python3` before `--`.
Inspect an existing `timetree` entry before replacing it. Restart the client
after configuration changes and use `/mcp` to check the connection. OpenAI's
[MCP configuration guide](https://developers.openai.com/codex/mcp) explains
shared local client configuration and additional options.

## Claude Desktop

Merge this entry into `mcpServers` in your existing configuration; preserve
other servers. The official
[local MCP guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
documents **Settings → Developer → Edit Config**. Paths include
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows, and
`~/.config/Claude/claude_desktop_config.json` on Linux.

```json
{
  "mcpServers": {
    "timetree": {
      "command": "npx",
      "args": ["--yes", "timetree-mcp@0.6.0"],
      "env": {
        "TIMETREE_EMAIL": "your-email@example.com",
        "TIMETREE_PASSWORD_FILE": "/absolute/path/to/timetree-password"
      }
    }
  }
}
```

Replace placeholders, save, and fully quit/reopen Claude Desktop. Inspect the
local server's status and logs before using its tools. This repository does
not currently provide an installable Claude desktop extension (`.mcpb`).

## Cursor

Merge the following into your user-level `~/.cursor/mcp.json`. A project can
instead use `.cursor/mcp.json`; avoid committing private paths or account
information. The [`mcp.json` documentation](https://cursor.com/docs/mcp)
describes stdio settings and configuration locations.

```json
{
  "mcpServers": {
    "timetree": {
      "type": "stdio",
      "command": "npx",
      "args": ["--yes", "timetree-mcp@0.6.0"],
      "env": {
        "TIMETREE_EMAIL": "your-email@example.com",
        "TIMETREE_PASSWORD_FILE": "/absolute/path/to/timetree-password"
      }
    }
  }
}
```

Enable the server in Cursor's MCP settings and inspect its tool list. Keep
tool approvals enabled while trying the integration.

## ChatGPT: desktop and web are different

OpenAI's current [local MCP guide](https://developers.openai.com/codex/mcp)
documents a ChatGPT desktop **Settings → MCP servers → Add server** flow with
STDIO support. If your installed desktop app exposes that flow, choose STDIO,
use command `npx` with arguments `--yes`, `timetree-mcp@0.6.0`, and configure
credentials as above. Local clients on the same Codex host share MCP
configuration. This recipe has not been tested in the desktop UI.

ChatGPT **web custom MCP apps/plugins** use remote MCP endpoints, according to
the [OpenAI help guide](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt).
An `npx` command or Claude-style JSON cannot be entered as a remote endpoint.
This package has no Streamable HTTP/SSE listener, remote authentication layer,
OAuth server, or hosted service. Remote web integration therefore needs
additional implementation and validation; it is not offered by these recipes.
A tunnel alone does not convert this stdio process into a remote MCP server.

## Windows and executable paths

Some Windows clients need the `npx.cmd` launcher run through `cmd.exe`. Where
direct `npx` startup fails, the command fields can be:

```json
{
  "command": "cmd",
  "args": ["/c", "npx", "--yes", "timetree-mcp@0.6.0"],
  "env": {
    "TIMETREE_PYTHON": "C:\\Path\\To\\Python\\python.exe",
    "TIMETREE_EMAIL": "your-email@example.com",
    "TIMETREE_PASSWORD_FILE": "C:\\Users\\you\\private\\timetree-password"
  }
}
```

This is a replacement **server entry**, not a complete client configuration.
Use real absolute paths and double backslashes in JSON. The wrapper tries
`python.exe` and `python` on Windows; it does not invoke the `py` launcher.
You can also avoid Node/npm by using an absolute Python executable as `command`
and the checkout's absolute `src/timetree_mcp/server.py` path as its sole `args`
entry. Neither Windows path has been exercised in this validation.

## First connection and troubleshooting

1. Check that the client lists the TimeTree tools. Discovery requires no login.
2. Ask for `timetree_status` to check credential availability. It does not
   authenticate to TimeTree. Since 0.5.0, it uses only presence
   checks and skips Keychain for explicit credentials; see the version-specific
   [credential behavior](#credential-resolution-and-status).
3. Ask to list calendars; this authenticates as needed. Confirm the intended
   account and calendar before trying any writes.
4. For a write, review the exact calendar, event, date/time, and change first.
   `confirm: true` is required by the server, but cannot prove that the client
   actually asked you. See the [read/write workflow](usage.md#read-and-write-workflow).

If the server exits immediately, check Python's version, executable paths, and
client logs. Running the command alone waits for MCP input; no terminal UI is
expected. If `npx` reports `timetree-mcp: command not found` inside this
repository, use `node bin/timetree-mcp.js` for the checkout, or run the published
command from another directory. npm can otherwise resolve the current package
instead of downloading its published launcher.

Do not assume monitoring produces a desktop alert. It emits MCP log
notifications only while the process is running; client presentation varies.
See [monitoring limits](usage.md#background-monitoring), the 0.5.0
[pending-batch and transport semantics](monitoring.md), and the
[offline demo](demo.md) before relying on it.
