# Publishing

This project publishes the NPM wrapper and bundled Python MCP server as the
`timetree-mcp` package. The same release version is recorded in
`package.json`, `pyproject.toml`, `src/timetree_mcp/server.py`, and
`server.json`.

## Local validation

Run the full release checks before publishing:

```bash
npm test
npm run pack:check
```

The package contents should include the Node launcher, Python source,
`server.json`, all three READMEs (English, Traditional Chinese, Japanese),
documentation, and the companion skill. It must not include credentials,
`.env` files, local journals/databases, tests, or Python cache files.

## Release through GitHub Actions

1. Inspect remote tags, GitHub Releases, and npm versions before choosing an
   unused version. Update all four version locations listed above, including
   both top-level and package versions in `server.json`. Update documentation
   and release notes without claiming unperformed verification.
2. Run `npm test` and `npm run pack:check`. Inspect the packed files and smoke
   test the packed launcher with MCP `initialize`, `tools/list`, and `ping`.
   No credentials or real calendar writes are needed for these checks.
3. Commit and push to `master`, verify branch CI, then create and push the
   matching version tag. For a release whose chosen version is 0.4.0:

   ```bash
   git tag v0.4.0
   git push origin v0.4.0
   ```

4. The tag automatically starts `.github/workflows/publish.yml`, which
   publishes through npm Trusted Publishing. Inspect its conclusion before
   taking further action. Do not also run `npm publish` for the same release.
5. Verify the package version and integrity on npm, inspect the downloaded
   package, and smoke test the published launcher. Then create the matching
   GitHub Release with the release notes and verify its tag target.

If a command or workflow has an uncertain result, inspect npm and GitHub first.
Never rerun a publication solely because its response was lost. If the workflow
fails, fix the identified cause and follow its existing-version guard; do not
overwrite or move an already published release tag.

The package requires Node.js 18+ and Python 3.10+ at runtime.

## GitHub Actions

`.github/workflows/publish.yml` validates Python tests, Node wrapper tests,
`server.json`, and `npm pack --dry-run` for version tags. It skips a version
that already exists on NPM.

For passwordless releases, configure NPM Trusted Publishing for the package
with:

- GitHub owner: `ap311036`
- Repository: `timetree-mcp`
- Workflow: `.github/workflows/publish.yml`

The checked-in workflow is the primary release path. Changes to publishing
credentials or repository permissions require separate review; they are not
part of an ordinary version release.

## MCP Registry metadata

`server.json` is the canonical MCP Registry manifest. It declares the stable
`io.github.ap311036/timetree-mcp` identity, the NPM package, stdio transport,
and the environment variables used for credentials and the direct HTTPS
transport. Keep its version and package identifier aligned with `package.json`.
The manifest alone does not publish to the MCP Registry. This repository does
not have an automated MCP Registry or PyPI publication step; do not describe
those registries as published without separate verification.
