# Contributing

Thanks for helping improve TimeTree MCP.

## Development setup

The Python server uses only the standard library at runtime. Python 3.10 or
newer is required. The NPM wrapper additionally requires Node.js 18 or newer.
Install the Python package before running its tests so the test suite exercises
the same `src` layout used by packaging and CI.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
python -m unittest discover -s tests -v
npm test
```

## Changes

- Keep changes focused and add or update tests for behavior changes.
- Preserve the explicit `confirm=true` guard on every write operation.
- Do not add credentials, session cookies, password files, or private account
  data to commits, issues, or test fixtures.
- Treat TimeTree web endpoints as unofficial and verify endpoint changes with
  mocked tests before relying on them.

## Pull requests

Target the default branch, `master`. Existing clones that still track `main`
can update with:

```bash
git branch -m main master
git fetch origin
git branch --set-upstream-to=origin/master master
git remote set-head origin -a
```

Run the rename command only if the clone has a local `main` branch and no local
`master`; keep any local work before switching branches. GitHub redirects old
file-view URLs, but raw-content URLs and Git branch tracking need updating.

Describe the user-visible behavior, test command and result, and any known
limitations. Keep unrelated formatting or refactoring out of the same change.
