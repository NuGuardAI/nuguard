"""Automatic web-session bootstrap (cookie + CSRF token) for endpoint discovery.

Many browser-facing chat apps reject API calls that lack a session cookie and a
matching anti-CSRF token. Both are handed out by an ordinary ``GET`` of the app's
UI page. :class:`WebSessionBootstrapper` performs that handshake so users do not
need a per-app ``prepare-session`` script.

Only ``GET`` requests to HTML pages and one replay of the already-failing probe
are issued. No credentials are guessed. Cookie and token values are held in memory
and never logged.

Example::

    signals = SessionSignals()
    signals.observe(response)          # called for each rejected probe
    if signals.triggered:
        session = await WebSessionBootstrapper(base_url).acquire(["/vendor/"])
        headers = session.headers() if session else {}
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

_log = logging.getLogger(__name__)

# Statuses a server uses to reject a request lacking a session / CSRF token.
_REJECTION_STATUSES = frozenset({400, 401, 403, 419})
_CSRF_HINT_RE = re.compile(r"csrf|xsrf|forgery|authenticity[_ ]token", re.IGNORECASE)
_SESSION_HINT_RE = re.compile(r"session|cookie|not authenticated|login required", re.IGNORECASE)

_CSRF_META_NAMES = frozenset({"csrf-token", "csrf_token", "_csrf", "xsrf-token", "csrf"})
_CSRF_INPUT_NAMES = frozenset({"csrf_token", "_csrf", "csrfmiddlewaretoken", "authenticity_token"})
_XSRF_COOKIE_NAMES = frozenset({"xsrf-token", "csrf-token", "csrftoken"})

DEFAULT_CSRF_HEADER = "X-CSRF-Token"
XSRF_CSRF_HEADER = "X-XSRF-TOKEN"
_ACQUIRE_USER_AGENT = "nuguard-probe/1.0"
_MAX_CANDIDATE_PAGES = 6


@dataclass
class SessionSignals:
    """Collects tell-tale rejections seen while probing for a chat endpoint."""

    csrf_rejections: int = 0
    session_rejections: int = 0
    other_rejections: int = 0
    set_cookie_seen: bool = False
    ui_roots: list[str] = field(default_factory=list)
    rejected_paths: list[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        """True when responses suggest a cookie/CSRF session may be required."""
        return bool(self.csrf_rejections or self.session_rejections or self.other_rejections)

    def observe(self, resp: httpx.Response, path: str | None = None) -> None:
        """Record *resp*; only rejection-class statuses count."""
        if resp.headers.get("set-cookie"):
            self.set_cookie_seen = True
        if resp.status_code not in _REJECTION_STATUSES:
            return
        try:
            text = resp.text[:2000]
        except Exception:  # noqa: BLE001 - body decoding is best effort
            text = ""
        haystack = f"{text} {' '.join(resp.headers.keys())}"
        if _CSRF_HINT_RE.search(haystack):
            self.csrf_rejections += 1
        elif _SESSION_HINT_RE.search(text):
            self.session_rejections += 1
        else:
            self.other_rejections += 1
        if path:
            if path not in self.rejected_paths:
                self.rejected_paths.append(path)
            root = ui_root_for(path)
            if root and root not in self.ui_roots:
                self.ui_roots.append(root)


def ui_root_for(api_path: str) -> str | None:
    """Return the UI mount prefix for an API path, e.g. ``/vendor/api/v1/chat`` -> ``/vendor/``."""
    segments = [s for s in urlsplit(api_path).path.split("/") if s]
    if not segments or segments[0].lower() in {"api", "v1", "v2", "chat", "ws"}:
        return None
    return f"/{segments[0]}/"


@dataclass(frozen=True)
class WebSession:
    """Acquired cookie + anti-CSRF header pair (in-memory only)."""

    cookie_header: str = ""
    csrf_header_name: str = DEFAULT_CSRF_HEADER
    csrf_token: str = ""
    source_page: str = ""

    def headers(self) -> dict[str, str]:
        """Return request headers carrying the session; empty when nothing was acquired."""
        out: dict[str, str] = {}
        if self.cookie_header:
            out["Cookie"] = self.cookie_header
        if self.csrf_token:
            out[self.csrf_header_name] = self.csrf_token
        return out

    def __repr__(self) -> str:  # never expose credential values
        return (
            f"WebSession(cookie={'<set>' if self.cookie_header else '<none>'}, "
            f"csrf={'<set>' if self.csrf_token else '<none>'}, page={self.source_page!r})"
        )


class _CsrfParser(HTMLParser):
    """Extract a CSRF token from ``<meta>`` tags or hidden ``<input>`` fields."""

    def __init__(self) -> None:
        super().__init__()
        self.token = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.token:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name", "").lower() in _CSRF_META_NAMES:
            self.token = a.get("content", "")
        elif tag == "input" and a.get("name", "").lower() in _CSRF_INPUT_NAMES:
            self.token = a.get("value", "")


def extract_csrf_token(resp: httpx.Response, cookies: httpx.Cookies) -> tuple[str, str]:
    """Return ``(header_name, token)`` found in *resp*, or ``("", "")``.

    Sources, in order: response header, HTML meta/hidden input, ``XSRF-TOKEN``-style cookie.
    """
    for name in ("x-csrf-token", "x-xsrf-token"):
        if resp.headers.get(name):
            return DEFAULT_CSRF_HEADER, resp.headers[name]
    if "html" in resp.headers.get("content-type", "").lower() or resp.text.lstrip().startswith("<"):
        parser = _CsrfParser()
        try:
            parser.feed(resp.text)
        except Exception:  # noqa: BLE001 - malformed HTML is not fatal
            pass
        if parser.token:
            return DEFAULT_CSRF_HEADER, parser.token
    for cookie in cookies.jar:
        if cookie.name.lower() in _XSRF_COOKIE_NAMES and cookie.value:
            return XSRF_CSRF_HEADER, cookie.value
    return "", ""


def _cookie_header(cookies: httpx.Cookies) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in cookies.jar)


def candidate_pages(signals: SessionSignals | None, extra: list[str] | None = None) -> list[str]:
    """Ordered, de-duplicated UI pages to GET: probe-derived roots, caller hints, then ``/``."""
    ordered = [*(signals.ui_roots if signals else []), *(extra or []), "/"]
    seen: dict[str, None] = {}
    for page in ordered:
        seen.setdefault(page, None)
    return list(seen)[:_MAX_CANDIDATE_PAGES]


class WebSessionBootstrapper:
    """Acquire a cookie/CSRF session by fetching the app's UI pages."""

    def __init__(self, base_url: str, *, timeout: float = 15.0, headers: dict[str, str] | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Reuse the caller's headers (UA/Accept-*) minus anything session-bearing.
        self._headers = {
            k: v for k, v in (headers or {}).items()
            if k.lower() not in {"cookie", "content-type", "x-csrf-token", "x-xsrf-token"}
        }
        self._headers.setdefault("User-Agent", _ACQUIRE_USER_AGENT)

    async def acquire(self, pages: list[str]) -> WebSession | None:
        """GET each candidate page; return the first session carrying a cookie or CSRF token."""
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            headers=self._headers,
            follow_redirects=True,
        ) as client:
            for page in pages:
                try:
                    resp = await client.get(page)
                except Exception as exc:  # noqa: BLE001 - network errors degrade to "no session"
                    _log.debug("web_session: GET %s failed: %s", page, type(exc).__name__)
                    continue
                if resp.status_code >= 400:
                    continue
                name, token = extract_csrf_token(resp, client.cookies)
                cookie = _cookie_header(client.cookies)
                if cookie or token:
                    session = WebSession(cookie, name or DEFAULT_CSRF_HEADER, token, page)
                    _log.info("web_session: acquired session from %s %r", page, session)
                    return session
        _log.debug("web_session: no session cookie or CSRF token found on %s", pages)
        return None

    async def validate(
        self,
        session: WebSession,
        probe_path: str,
        probe_body: dict[str, Any] | None = None,
    ) -> bool:
        """Return True when the session moves *probe_path* out of the CSRF/session rejection class.

        An empty body should reach schema validation (400/422) rather than a CSRF 403/419.
        """
        headers = {**self._headers, "Content-Type": "application/json", **session.headers()}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(self._timeout), headers=headers,
            ) as client:
                resp = await client.post(probe_path, json=probe_body if probe_body is not None else {})
        except Exception as exc:  # noqa: BLE001
            _log.debug("web_session: validation request failed: %s", type(exc).__name__)
            return False
        if resp.status_code in (401, 403, 419, 404, 405):
            return False
        if resp.status_code == 400 and _CSRF_HINT_RE.search(resp.text[:2000]):
            return False
        return True


def is_session_rejection(status: int, text: str) -> bool:
    """True when a response looks like an expired/missing cookie or CSRF token.

    419 always qualifies; 400/401/403 only when the body names CSRF or a session,
    so genuine authorization denials (which attack scenarios provoke on purpose)
    are never mistaken for an expired session. Such rejections happen before the
    app handler runs, so replaying the request once is safe.
    """
    if status == 419:
        return True
    if status not in (400, 401, 403):
        return False
    return bool(_CSRF_HINT_RE.search(text[:2000]))


SessionRefresher = Callable[[], Awaitable[dict[str, str]]]


def make_session_refresher(
    base_url: str,
    pages: list[str],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> SessionRefresher:
    """Return an async callable that re-acquires the web session and returns its headers.

    The callable returns ``{}`` when no fresh session could be obtained, so callers
    keep their existing behaviour. Candidate *pages* are tried in order, then ``/``.
    """
    bootstrapper = WebSessionBootstrapper(base_url, timeout=timeout, headers=headers)
    ordered = candidate_pages(None, [p for p in pages if p])

    async def _refresh() -> dict[str, str]:
        session = await bootstrapper.acquire(ordered)
        return session.headers() if session else {}

    return _refresh
