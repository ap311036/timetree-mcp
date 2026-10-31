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
`server.json`, documentation, and the companion skill. It must not include
credentials, `.env` files, tests, or Python cache files.

## Manual release

1. Update the four version locations listed above.
2. Run `npm test` and `npm run pack:check`.
3. Commit the release and create a matching tag, for example:

   ```bash
   git tag v0.2.1
   git push origin master v0.2.1
   ```

4. Publish the public package:

   ```bash
   npm publish --access public
   ```

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

If Trusted Publishing is unavailable for the account, configure an NPM publish
token as a repository secret named `NPM_TOKEN` and add the corresponding token
configuration to the publish step.

## MCP Registry metadata

`server.json` is the canonical MCP Registry manifest. It declares the stable
`io.github.ap311036/timetree-mcp` identity, the NPM package, stdio transport,
and the environment variables used for credentials and the direct HTTPS
transport. Keep its version and package identifier aligned with `package.json`.
