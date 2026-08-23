# Contributing

Thanks for helping improve TimeTree MCP.

## Development setup

The project uses only the Python standard library at runtime. Python 3.10 or
newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
python -m unittest discover -s tests -v
```

## Changes

- Keep changes focused and add or update tests for behavior changes.
- Preserve the explicit `confirm=true` guard on every write operation.
- Do not add credentials, session cookies, password files, or private account
  data to commits, issues, or test fixtures.
- Treat TimeTree web endpoints as unofficial and verify endpoint changes with
  mocked tests before relying on them.

## Pull requests

Describe the user-visible behavior, test command and result, and any known
limitations. Keep unrelated formatting or refactoring out of the same change.
