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
pin 0.4.0, which provides 29 tools; the historical 0.2.4 check does not verify
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

Version 0.4.0 adds `timetree_list_occurrences` and `timetree_timezone_status`
to the 27 tools in 0.3.0. The pinned npm configurations below select the
29-tool release; a matching [source checkout](usage.md#local-checkout) is also
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

For the normal launcher, email resolves from `TIMETREE_EMAIL`, then Keychain.
Password resolves from `TIMETREE_PASSWORD`, then `TIMETREE_PASSWORD_FILE`, then
Keychain. Remove old credential environment variables when switching accounts
or moving to Keychain. An unreadable configured password file is an error.

## Codex

The installed `codex mcp add --help` supports a stdio command after `--` and
`--env KEY=VALUE` entries. With a password file:

```bash
codex mcp add timetree \
  --env TIMETREE_EMAIL=your-email@example.com \
  --env TIMETREE_PASSWORD_FILE=/absolute/path/to/timetree-password \
  -- npx --yes timetree-mcp@0.4.0
```

With macOS Keychain already configured:

```bash
codex mcp add timetree -- npx --yes timetree-mcp@0.4.0
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
      "args": ["--yes", "timetree-mcp@0.4.0"],
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
      "args": ["--yes", "timetree-mcp@0.4.0"],
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
use command `npx` with arguments `--yes`, `timetree-mcp@0.4.0`, and configure
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
  "args": ["/c", "npx", "--yes", "timetree-mcp@0.4.0"],
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
2. Ask for `timetree_status` to check credential availability. This can consult
   local Keychain but does not authenticate to TimeTree.
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
See [monitoring limits](usage.md#background-monitoring) and the
[offline demo](demo.md) before relying on it.
