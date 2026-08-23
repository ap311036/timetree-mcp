# Usage Guide

## NPM client configuration

For an MCP client that can run NPM commands, use the published wrapper:

```json
{
  "mcpServers": {
    "timetree-mcp": {
      "command": "npx",
      "args": ["--yes", "timetree-mcp@0.2.1"],
      "env": {
        "TIMETREE_EMAIL": "your-email@example.com",
        "TIMETREE_PASSWORD_FILE": "/absolute/path/to/timetree-password"
      }
    }
  }
}
```

Do not put a real password in the JSON. On macOS, omit the credential
environment variables after configuring the Keychain helper:

```bash
./scripts/setup_timetree_keychain.sh
timetree-credentials status
```

## Local checkout

Run the Python package directly during development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
env PYTHONPATH=src python -m timetree_mcp.server
```

Run the NPM wrapper from the checkout:

```bash
TIMETREE_PYTHON="$PWD/.venv/bin/python" node bin/timetree-mcp.js
```

## Read and write workflow

Start with `timetree_status`, then list calendars before querying or mutating a
target. Use `timetree_list_events` for a bounded date range and inspect the
returned event identifiers before an update or delete. Every mutation must
include `confirm: true`; deletes should always receive a separate explicit
confirmation immediately before the call.

## Background monitoring

Start `timetree_start_monitor` with an interval from 30 to 3600 seconds. The
first poll establishes a baseline. Later changes are sent as MCP
`notifications/message` events and can be inspected with
`timetree_monitor_status`.
