"""Bounded direct HTTPS transport shared by the public reader and E2E guard.

This module owns TLS, response decoding and the in-memory cookie session. It has
no credential discovery, persistence, redirect following, or automatic retry.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import ssl
import subprocess
import sys
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, getproxies


DEFAULT_ORIGIN = "https://timetreeapp.com"
CSRF_PATH = "/signin"
CSRF_CALENDAR_PATH = "/calendars"
AUTH_PATH = "/api/v1/auth/email/signin"
MAX_RESPONSE_BYTES = 8_000_000
MAX_HEADER_BYTES = 65_536
MAX_COOKIES = 128
LOGIN_DIAGNOSTIC_REASONS = frozenset({
    "http_401", "http_403", "http_429", "http_4xx", "http_5xx",
    "http_redirect_rejected", "http_rejected", "tls_failed", "network_timeout",
    "network_failed", "invalid_response", "response_limit", "session_limit",
    "request_denied", "internal_error", "csrf_missing",
})
CSRF_REDIRECT_TARGETS = frozenset({"public_landing", "signin", "calendars", "other_or_missing"})
_TRANSPORT_CODES = LOGIN_DIAGNOSTIC_REASONS | {"proxy_unsupported", "proxy_state_unknown"}


@dataclass
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: Any


@dataclass(frozen=True)
class CsrfCalendarRedirect:
    """Validated /signin transition; carries no response or session payload."""


class TimeTreeHttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TransportError(TimeTreeHttpError):
    """Only fixed codes and bounded public metadata; never provider strings."""

    def __init__(self, code: str, status: int | None = None,
                 redirect_target: str | None = None):
        self.code = code if type(code) is str and code in _TRANSPORT_CODES else "internal_error"
        status = status if type(status) is int and 100 <= status <= 599 else None
        self.redirect_target = (redirect_target if self.code == "http_redirect_rejected"
                                and type(redirect_target) is str
                                and redirect_target in CSRF_REDIRECT_TARGETS else None)
        super().__init__(self.code, status)


@dataclass
class ResponseBudget:
    """Optional aggregate raw-body allowance, independent of process lifetime.

    The bounded reader may consume one extra byte to detect overflow. The
    remaining allowance can therefore become -1 after a rejected response.
    If reading fails before its byte count is known, the allowance is exhausted.
    """
    remaining_bytes: int

    def __post_init__(self):
        if type(self.remaining_bytes) is not int or self.remaining_bytes < 0:
            raise TransportError("request_denied")


def http_failure_reason(status):
    if type(status) is not int:
        return "http_rejected"
    if status in (401, 403, 429):
        return f"http_{status}"
    if 300 <= status < 400:
        return "http_redirect_rejected"
    if 400 <= status < 500:
        return "http_4xx"
    if 500 <= status < 600:
        return "http_5xx"
    return "http_rejected"


def failure_reason(error):
    """Classify trusted codes and exception types, never exception text."""
    if isinstance(error, TransportError):
        return error.code if type(error.code) is str and error.code in _TRANSPORT_CODES else "internal_error"
    if isinstance(error, TimeTreeHttpError) and type(error.status) is int:
        return http_failure_reason(error.status)
    if isinstance(error, ssl.SSLError):
        return "tls_failed"
    if isinstance(error, TimeoutError):
        return "network_timeout"
    if isinstance(error, OSError):
        return "network_failed"
    if isinstance(error, (http.client.IncompleteRead, http.client.BadStatusLine,
                          http.client.LineTooLong, http.client.UnknownProtocol)):
        return "invalid_response"
    return "internal_error"


def csrf_redirect_class(headers, origin=DEFAULT_ORIGIN):
    """Diagnostic only: match one exact raw Location without following it."""
    literals = {
        "/intl/en/": "public_landing", origin + "/intl/en/": "public_landing",
        "/intl/en": "public_landing", origin + "/intl/en": "public_landing",
        "/signin": "signin", origin + "/signin": "signin",
        "/calendars": "calendars", origin + "/calendars": "calendars",
    }
    try:
        locations = headers.get_all("Location")
        if type(locations) is list and len(locations) == 1 and type(locations[0]) is str:
            return literals.get(locations[0], "other_or_missing")
    except Exception:
        pass
    return "other_or_missing"


def _csrf_calendar_location(headers, origin):
    """Authorization is separate from the broader diagnostic classifier."""
    try:
        locations = headers.get_all("Location")
        return (type(locations) is list and len(locations) == 1
                and type(locations[0]) is str
                and locations[0] in {CSRF_CALENDAR_PATH, origin + CSRF_CALENDAR_PATH})
    except Exception:
        return False


def _system_proxy_check():
    if sys.platform != "darwin":
        return
    try:
        result = subprocess.run(
            ["/usr/sbin/scutil", "--proxy"], capture_output=True, text=True,
            timeout=5, check=False, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        output = result.stdout
        if (result.returncode != 0 or type(output) is not str or len(output) > 65_536
                or not output.strip().startswith("<dictionary> {")
                or not output.strip().endswith("}")):
            raise TransportError("proxy_state_unknown")
        for line in output.splitlines():
            pair = line.strip().split(" : ", 1)
            if len(pair) != 2:
                continue
            key, value = pair
            if key.endswith(("Enable", "Enabled")) and value != "0":
                raise TransportError("proxy_unsupported")
            if key in {"ProxyAutoConfigURLString", "ProxyAutoConfigJavaScript"} and value:
                raise TransportError("proxy_unsupported")
    except TransportError:
        raise
    except Exception:
        raise TransportError("proxy_state_unknown") from None


def require_direct_connection(*, environment=None, proxy_lookup=None, system_proxy_check=None):
    """Reject proxy requirements; never change or silently bypass settings."""
    environment = os.environ if environment is None else environment
    if any(str(key).lower().endswith("_proxy") for key in environment):
        raise TransportError("proxy_unsupported")
    try:
        proxies = (proxy_lookup or getproxies)()
        if not isinstance(proxies, dict):
            raise TransportError("proxy_state_unknown")
        if proxies and set(proxies) != {"no"}:
            raise TransportError("proxy_unsupported")
        if "no" in proxies:
            bypass = proxies["no"]
            if (not isinstance(bypass, str) or not bypass.strip() or len(bypass) > 4096
                    or any(ord(char) < 32 or ord(char) == 127 for char in bypass)):
                raise TransportError("proxy_state_unknown")
        (system_proxy_check or _system_proxy_check)()
    except TransportError:
        raise
    except Exception:
        raise TransportError("proxy_state_unknown") from None


def _strict_json(text):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError()

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError()
        return number

    return json.loads(text, object_pairs_hook=unique_pairs, parse_constant=reject_constant,
                      parse_float=finite_float)


def _header_value(value, maximum=4096):
    return (type(value) is str and bool(value) and len(value) <= maximum
            and all(32 <= ord(char) <= 126 for char in value))


# The reader applies this exact wire contract before committing a CSRF token.
valid_header_value = _header_value


def _origin(value):
    try:
        if (type(value) is not str or not value.startswith("https://")
                or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
                or any(char in value for char in "\\?#")):
            raise ValueError()
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.path
                or parsed.query or parsed.fragment or parsed.username is not None
                or parsed.password is not None or parsed.netloc != parsed.netloc.lower()
                or parsed.hostname.endswith(".")):
            raise ValueError()
        host = parsed.hostname
        if ":" in host:
            import ipaddress
            ipaddress.IPv6Address(host)
            canonical = "[" + host + "]"
        else:
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError()
            if any(not label or label.startswith("-") or label.endswith("-") for label in host.split(".")):
                raise ValueError()
            canonical = host
        if parsed.port is not None:
            if not 1 <= parsed.port <= 65535:
                raise ValueError()
            canonical += ":" + str(parsed.port)
        if parsed.netloc != canonical or value != "https://" + canonical:
            raise ValueError()
        return value, parsed.netloc
    except (ValueError, TypeError):
        raise TransportError("request_denied") from None


class DirectHttpsTransport:
    """One direct TLS connection per request, with an in-memory CookieJar."""

    def __init__(self, timeout=60, origin=DEFAULT_ORIGIN, connection_factory=None,
                 proxy_check=None, user_agent=None, max_response_bytes=MAX_RESPONSE_BYTES):
        self.origin, self._host = _origin(origin)
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0
                or type(max_response_bytes) is not int or not 0 < max_response_bytes <= MAX_RESPONSE_BYTES):
            raise TransportError("request_denied")
        self.timeout = min(timeout, 60)
        self.user_agent = (os.environ.get("TIMETREE_USER_AGENT", "timetree-mcp/0.2")
                           if user_agent is None else user_agent)
        if not _header_value(self.user_agent, 512):
            raise TransportError("request_denied")
        self._connection_factory = connection_factory
        self._proxy_check = proxy_check
        self._preflight_complete = False
        self._max_response_bytes = max_response_bytes
        self.cookie_jar = CookieJar()

    def preflight(self):
        if not self._preflight_complete:
            try:
                (self._proxy_check or require_direct_connection)()
            except TransportError:
                raise
            except Exception:
                raise TransportError("proxy_state_unknown") from None
            self._preflight_complete = True

    def clear(self):
        previous = self.cookie_jar
        self.cookie_jar = CookieJar()
        try:
            previous.clear()
        except Exception:
            pass

    def _path(self, method, url, body):
        if (type(method) is not str or method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
                or type(url) is not str or not url.startswith(self.origin + "/")
                or any(ord(char) <= 32 or ord(char) >= 127 for char in url)
                or "\\" in url or "#" in url):
            raise TransportError("request_denied")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != self._host or parsed.fragment:
            raise TransportError("request_denied")
        path = url[len(self.origin):]
        if path.startswith("//") or (method in {"GET", "HEAD"} and body is not None):
            raise TransportError("request_denied")
        return path

    def _headers(self, method, path, supplied):
        expected = {"Content-Type": "application/json", "Accept-Encoding": "identity",
                    "X-TimeTreeA": "web/2.1.0/en", "Referer": self.origin + "/",
                    "User-Agent": self.user_agent}
        if supplied is None:
            return expected
        if not isinstance(supplied, Mapping):
            raise TransportError("request_denied")
        allowed = {key.lower(): value for key, value in expected.items()}
        seen = set()
        for key, value in supplied.items():
            if type(key) is not str or not _header_value(value) or key.lower() in seen:
                raise TransportError("request_denied")
            lowered = key.lower()
            seen.add(lowered)
            if lowered == "x-csrf-token":
                if method in {"GET", "HEAD"} or path == AUTH_PATH:
                    raise TransportError("request_denied")
                expected["X-CSRF-Token"] = value
            elif lowered not in allowed or value != allowed[lowered]:
                raise TransportError("request_denied")
        return expected

    @staticmethod
    def _validate_response_headers(headers, *, framing=True):
        try:
            items = list(headers.items())
            if any(type(key) is not str or type(value) is not str for key, value in items):
                raise TransportError("invalid_response")
            if sum(len(key) + len(value) for key, value in items) > MAX_HEADER_BYTES:
                raise TransportError("response_limit")
            if not framing:
                return
            for name in ("Content-Length", "Transfer-Encoding", "Content-Encoding", "X-CSRF-Token"):
                values = headers.get_all(name, [])
                if len(values) > 1:
                    raise TransportError("invalid_response")
            length = headers.get("Content-Length")
            transfer = headers.get("Transfer-Encoding")
            if length is not None and (not re.fullmatch(r"[0-9]+", length) or transfer is not None):
                raise TransportError("invalid_response")
            if transfer is not None and transfer.lower() != "chunked":
                raise TransportError("invalid_response")
            if headers.get("Content-Encoding", "identity").lower() != "identity":
                raise TransportError("invalid_response")
            token = headers.get("X-CSRF-Token")
            if token is not None and not _header_value(token):
                raise TransportError("invalid_response")
        except TransportError:
            raise
        except Exception:
            raise TransportError("invalid_response") from None

    @staticmethod
    def _decode(raw, status, method, path):
        try:
            text = raw.decode("utf-8")
            if method == "GET" and path in {CSRF_PATH, CSRF_CALENDAR_PATH}:
                return text
            if not raw and (status in (204, 205) or method == "HEAD") and path != AUTH_PATH:
                return ""
            try:
                payload = _strict_json(text)
            except json.JSONDecodeError:
                # Preserve ordinary API text/empty responses. The reader owns
                # operation-specific validation. Duplicate/nonfinite JSON is a
                # ValueError instead and cannot use this compatibility path.
                if path != AUTH_PATH:
                    return text
                raise
            if path == AUTH_PATH and (not isinstance(payload, dict) or payload.get("error") is not None):
                raise ValueError()
            return payload
        except (UnicodeError, ValueError, TypeError, AttributeError, RecursionError):
            raise TransportError("invalid_response") from None

    def request(self, method, url, body=None, headers=None, timeout=None, *, budget=None):
        path = self._path(method, url, body)
        request_headers = self._headers(method, path, headers)
        if timeout is not None and (type(timeout) not in (int, float)
                                    or not math.isfinite(timeout) or timeout <= 0):
            raise TransportError("request_denied")
        if budget is not None:
            if not isinstance(budget, ResponseBudget) or type(budget.remaining_bytes) is not int:
                raise TransportError("request_denied")
            if budget.remaining_bytes <= 0:
                raise TransportError("session_limit")
        self.preflight()
        connection = None
        primary_error = None
        try:
            request = Request(url, method=method, headers=request_headers)
            self.cookie_jar.add_cookie_header(request)
            try:
                data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise TransportError("request_denied") from None
            factory = self._connection_factory or http.client.HTTPSConnection
            connection = factory(self._host, timeout=min(self.timeout, timeout or self.timeout),
                                 context=ssl.create_default_context())
            connection.request(method, path, body=data, headers=dict(request.header_items()))
            response = connection.getresponse()
            status = response.status
            if type(status) is not int or not 100 <= status <= 599:
                raise TransportError("invalid_response")
            self._validate_response_headers(response.headers, framing=False)
            csrf_request = method == "GET" and path in {CSRF_PATH, CSRF_CALENDAR_PATH}
            auth_request = (method == "PUT" and path == AUTH_PATH) or csrf_request
            if 300 <= status < 400 or (auth_request and not 200 <= status < 300):
                target = (csrf_redirect_class(response.headers, self.origin)
                          if csrf_request and 300 <= status < 400 else None)
                if (method == "GET" and path == CSRF_PATH
                        and status in {301, 302, 303, 307, 308}
                        and _csrf_calendar_location(response.headers, self.origin)):
                    # Accept only one exact raw Location: /calendars or this
                    # origin + /calendars. Keep the previous
                    # session, ignore all redirect cookies/token/body, and let
                    # the reader decide whether to make one fixed next GET.
                    return CsrfCalendarRedirect()
                raise TransportError(http_failure_reason(status), status, target)
            if not 200 <= status < 600:
                raise TransportError("http_rejected", status)
            self._validate_response_headers(response.headers)
            bound = self._max_response_bytes
            if budget is not None:
                bound = min(bound, budget.remaining_bytes)
            try:
                raw = response.read(bound + 1)
            except BaseException:
                if budget is not None:
                    budget.remaining_bytes = 0
                raise
            if type(raw) is not bytes:
                raise TransportError("invalid_response")
            if budget is not None:
                budget.remaining_bytes -= len(raw)
            if len(raw) > bound:
                raise TransportError("response_limit")
            remaining_length = getattr(response, "length", None)
            if remaining_length is not None and (type(remaining_length) is not int or remaining_length != 0):
                raise TransportError("invalid_response")
            payload = self._decode(raw, status, method, path)
            if 200 <= status < 300:
                self.cookie_jar.extract_cookies(response, request)
                if len(list(self.cookie_jar)) > MAX_COOKIES:
                    raise TransportError("response_limit")
            return HttpResponse(status, dict(response.headers.items()), payload)
        except BaseException as error:
            primary_error = error
            try:
                self.clear()
            except BaseException:
                pass
            if not isinstance(error, Exception):
                raise
            if isinstance(error, TransportError):
                raise
            raise TransportError(failure_reason(error)) from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except BaseException as error:
                    try:
                        self.clear()
                    except BaseException:
                        pass
                    if primary_error is None:
                        if not isinstance(error, Exception):
                            raise
                        raise TransportError(failure_reason(error)) from None
