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
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
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
# Servers (e.g. FinBot) may bind a session to the client fingerprint seen when it was
# issued. These headers are sent during acquisition and re-sent with the session on
# every later request, so every NuGuard component presents the same fingerprint.
PINNED_SESSION_HEADERS: dict[str, str] = {
    "User-Agent": "nuguard-session/1.0",
    "Accept-Language": "en-US",
    "Accept-Encoding": "identity",
}
_MAX_CANDIDATE_PAGES = 6
_MAX_CRAWL_FETCHES = 12
_MAX_CRAWL_DEPTH = 2
_MAX_VALIDATION_PATHS = 40
_LINK_RE = re.compile(r"""(?:href|action)\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_ASSET_RE = re.compile(r"\.(?:css|js|png|jpe?g|gif|svg|ico|woff2?|ttf|map|json|xml|pdf)(?:\?|$)", re.IGNORECASE)


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

    @property
    def root(self) -> str | None:
        """UI mount prefix of the page that issued the session (e.g. ``/vendor/``)."""
        return ui_root_for(self.source_page) if self.source_page else None

    def headers(self) -> dict[str, str]:
        """Return request headers carrying the session; empty when nothing was acquired."""
        out: dict[str, str] = {}
        if self.cookie_header or self.csrf_token:
            out.update(PINNED_SESSION_HEADERS)
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


def extract_csrf_from_parts(
    headers: Mapping[str, str], text: str, cookies: Mapping[str, str],
) -> tuple[str, str]:
    """Return ``(header_name, token)`` from lower-cased response headers, body text and cookies.

    Sources, in order: response header, HTML meta/hidden input, ``XSRF-TOKEN``-style cookie.
    Transport-agnostic so the pinned pentest transport can reuse it.
    """
    for name in ("x-csrf-token", "x-xsrf-token"):
        if headers.get(name):
            return DEFAULT_CSRF_HEADER, headers[name]
    if "html" in headers.get("content-type", "").lower() or text.lstrip().startswith("<"):
        parser = _CsrfParser()
        try:
            parser.feed(text)
        except Exception:  # noqa: BLE001 - malformed HTML is not fatal
            pass
        if parser.token:
            return DEFAULT_CSRF_HEADER, parser.token
    for name, value in cookies.items():
        if name.lower() in _XSRF_COOKIE_NAMES and value:
            return XSRF_CSRF_HEADER, value
    return "", ""


def extract_csrf_token(resp: httpx.Response, cookies: httpx.Cookies) -> tuple[str, str]:
    """Return ``(header_name, token)`` found in *resp*, or ``("", "")``."""
    return extract_csrf_from_parts(
        {k.lower(): v for k, v in resp.headers.items()},
        resp.text,
        {c.name: c.value for c in cookies.jar if c.value},
    )


def cookies_from_set_cookie(lines: Iterable[str]) -> dict[str, str]:
    """Parse ``Set-Cookie`` header lines into ``{name: value}`` (attributes dropped)."""
    out: dict[str, str] = {}
    for line in lines:
        pair = line.split(";", 1)[0]
        name, sep, value = pair.partition("=")
        if sep and name.strip():
            out[name.strip()] = value.strip()
    return out


def extract_page_links(html: str, base_netloc: str = "") -> list[str]:
    """Same-origin page paths linked from *html* (no assets, fragments or queries), in order."""
    out: dict[str, None] = {}
    for raw in _LINK_RE.findall(html):
        parts = urlsplit(raw.strip())
        if parts.scheme in ("http", "https"):
            if not base_netloc or parts.netloc != base_netloc:
                continue
        elif parts.scheme or parts.netloc or not parts.path.startswith("/"):
            continue
        path = parts.path or "/"
        if _ASSET_RE.search(path) or path.startswith("/static/"):
            continue
        out.setdefault(path, None)
    return list(out)


def _cookie_header(cookies: httpx.Cookies) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in cookies.jar)


def candidate_pages(signals: SessionSignals | None, extra: list[str] | None = None) -> list[str]:
    """Ordered, de-duplicated UI pages to start the session crawl from."""
    # Caller/SBOM-derived roots first, then "/" (the crawl root), then roots guessed from
    # rejected probe paths — those include many fallback routes and are the least reliable.
    ordered = [*(extra or []), "/", *(signals.ui_roots if signals else [])]
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
        self._headers.update(PINNED_SESSION_HEADERS)

    async def acquire(
        self,
        pages: list[str],
        *,
        accept: "Callable[[WebSession], Awaitable[bool]] | None" = None,
    ) -> WebSession | None:
        """Crawl from *pages* for a session; return the first one that passes *accept*.

        Pages are fetched breadth-first (bounded by ``_MAX_CRAWL_FETCHES`` and
        ``_MAX_CRAWL_DEPTH``). A page carrying a CSRF token is preferred; when a page
        yields none, its same-origin links are queued so a token living on a portal
        page (e.g. ``/vendor/``) linked from ``/`` is still found. A cookie-only
        session is the fallback when no page offers a token.
        """
        queue: deque[tuple[str, int]] = deque((p, 0) for p in pages)
        seen: set[str] = set(pages)
        fetched = 0
        fallback: WebSession | None = None
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
            headers=self._headers,
            follow_redirects=True,
        ) as client:
            netloc = urlsplit(self._base_url).netloc
            while queue and fetched < _MAX_CRAWL_FETCHES:
                page, depth = queue.popleft()
                try:
                    resp = await client.get(page)
                except Exception as exc:  # noqa: BLE001 - network errors degrade to "no session"
                    _log.debug("web_session: GET %s failed: %s", page, type(exc).__name__)
                    continue
                fetched += 1
                if resp.status_code >= 400:
                    continue
                name, token = extract_csrf_token(resp, client.cookies)
                cookie = _cookie_header(client.cookies)
                landed = resp.url.path or page
                if token:
                    session = WebSession(cookie, name or DEFAULT_CSRF_HEADER, token, landed)
                    if accept is None or await accept(session):
                        _log.info("web_session: acquired session from %s %r", landed, session)
                        return session
                    # A token that did not clear the rejected routes (e.g. a site-wide
                    # token on a public page) — keep crawling for the portal's own.
                elif cookie and fallback is None:
                    fallback = WebSession(cookie, DEFAULT_CSRF_HEADER, "", landed)
                if depth < _MAX_CRAWL_DEPTH and "html" in resp.headers.get("content-type", "").lower():
                    for link in extract_page_links(resp.text, netloc):
                        if link not in seen:
                            seen.add(link)
                            queue.append((link, depth + 1))
        if fallback is not None and (accept is None or await accept(fallback)):
            _log.info("web_session: acquired cookie-only session %r", fallback)
            return fallback
        _log.debug("web_session: no usable session found (fetched %d pages)", fetched)
        return None

    async def validate(
        self,
        session: WebSession,
        probe_path: str,
        probe_body: dict[str, Any] | None = None,
    ) -> bool:
        """Return True when the session moves *probe_path* out of the CSRF/session rejection class."""
        return await self.validate_any(session, [probe_path], probe_body) is not None

    async def validate_any(
        self,
        session: WebSession,
        probe_paths: list[str],
        probe_body: dict[str, Any] | None = None,
    ) -> str | None:
        """Return the first of *probe_paths* the session clears (400/422/2xx), else ``None``.

        An empty body should reach schema validation rather than a CSRF 403/419.
        404/405 mean the route does not exist and are skipped.
        """
        headers = {**self._headers, "Content-Type": "application/json", **session.headers()}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(self._timeout), headers=headers,
            ) as client:
                for path in list(dict.fromkeys(probe_paths))[:_MAX_VALIDATION_PATHS]:
                    resp = await client.post(path, json=probe_body if probe_body is not None else {})
                    if resp.status_code in (401, 403, 419, 404, 405):
                        continue
                    if resp.status_code == 400 and _CSRF_HINT_RE.search(resp.text[:2000]):
                        continue
                    if resp.status_code >= 500:
                        continue
                    return path
        except Exception as exc:  # noqa: BLE001
            _log.debug("web_session: validation request failed: %s", type(exc).__name__)
        return None


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
