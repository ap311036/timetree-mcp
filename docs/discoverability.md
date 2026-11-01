# Project description and search appearance

The project description emphasizes what users can do:

> TimeTree MCP server for calendar search, events, memos, comments, and change monitoring. Local stdio; confirmation-gated writes.

The English, Traditional Chinese, and Japanese README introductions describe
those capabilities, while retaining a visible notice that this project is not
affiliated with TimeTree and uses private web endpoints. Removing a leading
label must not imply official endorsement or conceal the service dependency.

## Metadata changes

On 2026-11-01, the GitHub About description was updated to the text above and
read back successfully. The existing ten topics were preserved, with three
relevant additions: `calendar-events`, `recurring-events`, and `stdio`.
The resulting topics are:

```text
calendar, calendar-events, claude, codex, cursor, mcp, mcp-server,
model-context-protocol, nodejs, python, recurring-events, stdio, timetree
```

GitHub uses topics to classify repositories and help people discover related
projects. Topics should describe actual project content; they are not evidence
of a particular Google ranking. See [GitHub's topic guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics).

Version 0.4.0 includes matching package descriptions and readable README
introductions, along with occurrence queries and timezone diagnostics. Its
documentation consistently describes all 29 tools and their support limits.
The metadata stored with older npm versions is historical and is not rewritten
by a new release.

## What these changes can and cannot do

Google creates snippets automatically, often from page content and sometimes
from a page's description. The chosen text can differ by search query; editing
GitHub About does not force Google to show that exact sentence or prevent it
from quoting another passage. See [Google's snippet documentation](https://developers.google.com/search/docs/appearance/snippet).

Clear functional descriptions, useful setup instructions, working links, and
accurate terminology help readers understand the project. These follow
[Google Search Essentials](https://developers.google.com/search/docs/essentials).
They do not guarantee indexing, a position, more stars, or a specific amount of
traffic. No purchased stars, fabricated usage claims, repeated keyword blocks,
or unsolicited promotion are part of this change.

Search systems must recrawl and process published changes before they can use
them. Google's [title-link guidance](https://developers.google.com/search/docs/appearance/title-link)
describes a delay that can last days to weeks for those changes. This is not a
deadline for this repository's snippet or ranking. The repository does not
control GitHub's site-wide HTML, crawler configuration, or Search Console
ownership, and no recrawl request or ranking improvement is claimed here.

## Maintenance

- Keep the README, package metadata, tool schemas, and release notes consistent.
- Describe stable capabilities before unreleased experiments, and retain
  installation requirements and verification limits.
- Link useful guides from the README; avoid duplicate pages made only to repeat
  search terms.
- When checking search appearance manually after a release, record the query,
  date, language, and location rather than treating a single result as a fixed
  universal ranking.
