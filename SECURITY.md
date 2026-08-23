# Security Policy

## Scope

TimeTree MCP handles account credentials and an authenticated calendar session.
Credentials should be stored in macOS Keychain or supplied through environment
variables or a password file with restricted permissions. Session cookies and
CSRF tokens are kept in process memory.

## Reporting a vulnerability

Please do not publish credentials, session data, or an exploitable request in a
public issue. Use a private maintainer contact or the repository host's private
security reporting channel when one is available. Include reproduction steps,
affected versions, and the smallest useful proof of impact.
