"""Playwright-driven browser login, used as a discovery fallback for target apps
that authenticate via an interactive redirect/OAuth flow (e.g. Auth0 Universal
Login) rather than a static header, HTTP Basic Auth, or a simple JSON
login_flow POST — none of which NuGuard's normal pre-scan discovery
(``nuguard/common/discovery.py``) can drive, since it only ever sends plain
HTTP requests.

This module is only imported by ``nuguard/cli/commands/target_browser.py``,
never by any hot path (behavior/redteam runners, `target verify`'s default
flow) — Playwright is a heavy optional dependency (see the ``browser`` extra
in pyproject.toml) and importing ``playwright.async_api`` happens lazily,
inside the functions that need it, so the rest of NuGuard never pays for it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from nuguard.common.browser_login import heuristics
from nuguard.common.browser_login.config import BrowserDiscoveryConfig
from nuguard.common.errors import BrowserLoginError
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page, Request

    from nuguard.common.auth import AuthConfig

_log = get_logger(__name__)

# Static-asset / analytics-noise file extensions and hostname fragments to
# exclude when hunting for the "real" outgoing chat API request. Kept small
# and conservative — a false negative here just means sniffing finds nothing
# (non-fatal), a false positive would report the wrong endpoint.
_ASSET_EXT_RE = re.compile(r"\.(?:js|css|png|jpg|jpeg|svg|gif|woff2?|ico|map)(?:\?|$)", re.I)


@dataclass
class BrowserLoginResult:
    """Everything discovered by a single ``BrowserLoginSession.run()`` call."""

    cookies_written_to: Path
    identity_url: str | None = None
    identity_payload: dict[str, Any] | None = None
    sniffed_endpoint: str | None = None
    sniffed_chat_request: dict[str, Any] | None = None
    candidate_extra_fields: dict[str, str] = field(default_factory=dict)
    ambiguous_fields: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _require_playwright() -> Any:
    """Lazily import playwright.async_api, translating ImportError into a
    BrowserLoginError with an actionable install instruction."""
    try:
        import playwright.async_api as async_api  # noqa: PLC0415
    except ImportError as exc:
        raise BrowserLoginError(
            "Playwright is not installed.",
            step="playwright_not_installed",
            cause=str(exc),
        ) from exc
    return async_api


def _same_origin(url: str, target_url: str) -> bool:
    a, b = urlparse(url), urlparse(target_url)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


class BrowserLoginSession:
    """Drives a headless (by default) browser through a login flow, then
    captures the resulting session cookies and (optionally) sniffs the app's
    own outgoing chat request to discover extra required payload fields.

    Every failure that should abort the command is raised as a
    ``BrowserLoginError`` with a ``step`` attribute callers can map to an
    exit code/user message (see ``nuguard/cli/commands/target_browser.py``).
    Best-effort steps (identity probe, chat sniffing) fail soft — they append
    to ``BrowserLoginResult.warnings`` instead of raising.
    """

    def __init__(
        self,
        target_url: str,
        auth_config: "AuthConfig",
        browser_cfg: BrowserDiscoveryConfig,
        *,
        headless: bool = True,
        timeout_s: int = 30,
    ) -> None:
        self.target_url = target_url.rstrip("/")
        self.auth_config = auth_config
        self.browser_cfg = browser_cfg
        self.headless = headless
        self.timeout_s = timeout_s
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: "Page | None" = None

    async def __aenter__(self) -> "BrowserLoginSession":
        await self._launch()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _launch(self) -> None:
        async_api = _require_playwright()
        _log.info("browser_login: launching chromium (headless=%s)", self.headless)
        try:
            self._playwright = await async_api.async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
        except Exception as exc:  # noqa: BLE001 — translate any launch failure uniformly
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                raise BrowserLoginError(
                    "Chromium browser binary not found.",
                    step="browser_binary_missing",
                    cause=message,
                ) from exc
            raise BrowserLoginError(
                f"Failed to launch browser: {exc}",
                step="launch",
                cause=message,
            ) from exc
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout_s * 1000)

    async def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:  # noqa: BLE001 — best-effort teardown
                    _log.debug("browser_login: error closing browser resource", exc_info=True)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                _log.debug("browser_login: error stopping playwright", exc_info=True)

    @property
    def page(self) -> "Page":
        assert self._page is not None, "BrowserLoginSession not started — use 'async with'"
        return self._page

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    async def run(self, *, chat_message: str = "Hello", sniff_chat: bool = True) -> BrowserLoginResult:
        """Run the full login -> identity-probe -> (optional) chat-sniff -> cookie-export flow."""
        username = self.auth_config.username or ""
        password = self.auth_config.password or ""
        if not username or not password:
            raise BrowserLoginError(
                "No credentials found for the browser login. Set target.auth.username/password "
                "(or their ${ENV_VAR} sources) in nuguard.yaml.",
                step="missing_credentials",
                url=self.target_url,
            )

        await self._navigate()
        await self._trigger_login()
        await self._fill_credentials(username, password)
        await self._submit_and_wait()

        warnings: list[str] = []
        identity_url, identity_payload = await self._probe_identity()
        if identity_payload is None:
            warnings.append(
                "no identity endpoint found — chat_payload_extras confidence will be lower"
            )

        sniffed_endpoint: str | None = None
        sniffed_body: dict[str, Any] | None = None
        candidate_extra_fields: dict[str, str] = {}
        ambiguous_fields: dict[str, list[str]] = {}
        if sniff_chat:
            sniffed_endpoint, sniffed_body = await self._sniff_chat_request(chat_message)
            if sniffed_body is None:
                warnings.append(
                    "could not observe an outgoing chat request — proceeding cookie-only. "
                    "Set target.browser_discovery.chat_input_selector to help."
                )
            else:
                candidate_extra_fields, ambiguous_fields = self._resolve_extra_fields(
                    sniffed_body, identity_payload
                )
                if not candidate_extra_fields and not ambiguous_fields:
                    warnings.append(
                        "no extra payload fields detected — target.chat_payload_extras may not be needed"
                    )

        return BrowserLoginResult(
            cookies_written_to=Path(),  # filled in by the caller after export_cookies()
            identity_url=identity_url,
            identity_payload=identity_payload,
            sniffed_endpoint=sniffed_endpoint,
            sniffed_chat_request=sniffed_body,
            candidate_extra_fields=candidate_extra_fields,
            ambiguous_fields=ambiguous_fields,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    async def _navigate(self) -> None:
        try:
            await self.page.goto(
                self.target_url,
                wait_until="networkidle",
                timeout=self.browser_cfg.navigation_timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrowserLoginError(
                f"Could not reach {self.target_url}: {exc}. Is the app running?",
                step="navigate",
                url=self.target_url,
                cause=str(exc),
            ) from exc

    async def _trigger_login(self) -> None:
        candidates = heuristics.build_text_candidates(
            self.browser_cfg.login_button_text, heuristics.DEFAULT_LOGIN_TRIGGER_TEXTS
        )
        for text in candidates:
            locator = self.page.get_by_text(re.compile(re.escape(text), re.I)).first
            try:
                if await locator.count() > 0:
                    _log.info("browser_login: clicking login trigger %r", text)
                    await locator.click(timeout=2000)
                    await self.page.wait_for_timeout(self.browser_cfg.extra_wait_ms)
                    return
            except Exception:  # noqa: BLE001 — try the next candidate
                _log.debug("browser_login: login trigger candidate %r failed", text, exc_info=True)
                continue

        # Some apps present the login form directly on load (no separate
        # trigger click needed) — treat "credential fields already visible"
        # as success rather than failing here.
        if await self._has_credential_fields():
            _log.info("browser_login: credential fields already visible, no login trigger needed")
            return

        raise BrowserLoginError(
            f"Could not find a login button on {self.target_url}. Try setting "
            "target.browser_discovery.login_button_text, or the app may already be "
            "authenticated / require a different flow.",
            step="find_login_trigger",
            url=self.target_url,
        )

    async def _has_credential_fields(self) -> bool:
        password_candidates = heuristics.build_candidates(
            self.browser_cfg.password_selector, heuristics.DEFAULT_PASSWORD_SELECTORS
        )
        for sel in password_candidates:
            try:
                if await self.page.locator(sel).first.count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def _fill_credentials(self, username: str, password: str) -> None:
        username_candidates = heuristics.build_candidates(
            self.browser_cfg.username_selector, heuristics.DEFAULT_USERNAME_SELECTORS
        )
        password_candidates = heuristics.build_candidates(
            self.browser_cfg.password_selector, heuristics.DEFAULT_PASSWORD_SELECTORS
        )

        username_filled = await self._fill_first_match(username_candidates, username)
        password_filled = await self._fill_first_match(password_candidates, password)

        if not username_filled or not password_filled:
            raise BrowserLoginError(
                "Found a login page but could not locate username/password fields. Set "
                "target.browser_discovery.username_selector / password_selector.",
                step="fill_credentials",
                url=self.target_url,
            )

        submit_candidates = heuristics.build_candidates(
            self.browser_cfg.submit_selector, heuristics.DEFAULT_SUBMIT_SELECTORS
        )
        for sel in submit_candidates:
            try:
                locator = self.page.locator(sel).first
                if await locator.count() > 0:
                    _log.info("browser_login: submitting login form via %r", sel)
                    await locator.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                continue
        # No submit button matched — fall back to pressing Enter in the
        # password field, a common pattern for simple login forms.
        _log.info("browser_login: no submit button matched, pressing Enter")
        await self.page.keyboard.press("Enter")

    async def _fill_first_match(self, selectors: list[str], value: str) -> bool:
        for sel in selectors:
            try:
                locator = self.page.locator(sel).first
                if await locator.count() > 0:
                    await locator.fill(value, timeout=2000)
                    return True
            except Exception as exc:  # noqa: BLE001
                # No exc_info/traceback here: `value` (a credential — username
                # or password) is a local in this frame, and the configured
                # Rich log handler renders tracebacks with local variable
                # values by default, which would print the credential in
                # clear text. Log only the exception type/message.
                _log.debug("browser_login: fill candidate %r failed: %s", sel, exc)
                continue
        return False

    async def _submit_and_wait(self) -> None:
        deadline = time.monotonic() + (self.browser_cfg.navigation_timeout_ms / 1000)
        wait_selector = self.browser_cfg.post_login_wait_selector

        while time.monotonic() < deadline:
            if wait_selector:
                try:
                    if await self.page.locator(wait_selector).first.count() > 0:
                        await self.page.wait_for_timeout(self.browser_cfg.extra_wait_ms)
                        return
                except Exception:  # noqa: BLE001
                    pass
            if _same_origin(self.page.url, self.target_url):
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:  # noqa: BLE001
                    pass
                # Confirm the redirect actually landed back (not just still
                # mid-flow on the target's own error page pre-redirect).
                if _same_origin(self.page.url, self.target_url):
                    await self.page.wait_for_timeout(self.browser_cfg.extra_wait_ms)
                    return
            await self.page.wait_for_timeout(500)

        raise BrowserLoginError(
            f"Login form submitted but the app never redirected back / no post-login signal "
            f"detected within {self.browser_cfg.navigation_timeout_ms // 1000}s. Check "
            "credentials, or set target.browser_discovery.post_login_wait_selector.",
            step="await_redirect",
            url=self.target_url,
        )

    async def _probe_identity(self) -> tuple[str | None, dict[str, Any] | None]:
        """Best-effort: probe common identity endpoints for a JSON body that
        confirms authentication and may carry the app's internal actor/user
        ID (used later to confirm sniffed chat-payload fields)."""
        candidates = (
            [self.browser_cfg.identity_endpoint]
            if self.browser_cfg.identity_endpoint
            else []
        ) + heuristics.DEFAULT_IDENTITY_ENDPOINTS
        seen: set[str] = set()
        for path in candidates:
            if not path or path in seen:
                continue
            seen.add(path)
            url = self.target_url + path
            try:
                resp = await self.page.request.get(url, timeout=5000)
                if resp.ok:
                    payload = await resp.json()
                    if isinstance(payload, dict):
                        _log.info("browser_login: identity probe succeeded at %s", path)
                        return url, payload
            except Exception:  # noqa: BLE001
                _log.debug("browser_login: identity probe %r failed", path, exc_info=True)
                continue
        _log.info("browser_login: no identity endpoint found among %d candidates", len(seen))
        return None, None

    async def _poll_for_locator(self, selectors: list[str], *, budget_s: float) -> Any | None:
        """Poll ``selectors`` in order until one matches or ``budget_s`` elapses.

        Several apps show a transient loading state right after login (a
        "reading your records..." spinner, a skeleton screen) before the real
        chat UI mounts — a single one-shot check would spuriously report "no
        chat input found" during that window.
        """
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            for sel in selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() > 0:
                        return loc
                except Exception:  # noqa: BLE001
                    continue
            await self.page.wait_for_timeout(300)
        return None

    async def _click_first_enabled(self, selectors: list[str], *, budget_s: float) -> bool:
        """Poll ``selectors`` for one that is present AND enabled, then click it.

        Distinct from ``_poll_for_locator``: a send button often exists in the
        DOM immediately but stays disabled until an in-flight request (e.g.
        the same post-login loading state) resolves.
        """
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            for sel in selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_enabled():
                        await loc.click(timeout=2000)
                        return True
                except Exception:  # noqa: BLE001
                    continue
            await self.page.wait_for_timeout(300)
        return False

    async def _sniff_chat_request(self, message: str) -> tuple[str | None, dict[str, Any] | None]:
        """Type a message into the chat UI and capture the resulting outgoing
        POST request body, to discover payload fields NuGuard's static HTTP
        discovery has no way to guess (e.g. an opaque consumer/actor ID)."""
        import json

        captured: dict[str, Any] = {}

        def _on_request(request: "Request") -> None:
            if request.method != "POST":
                return
            url = request.url
            if _ASSET_EXT_RE.search(url):
                return
            if not _same_origin(url, self.target_url):
                return
            if captured:
                return  # first match wins
            body = request.post_data
            if not body:
                return
            try:
                parsed = json.loads(body)
            except (ValueError, TypeError):
                return
            if isinstance(parsed, dict):
                captured["url"] = url
                captured["body"] = parsed

        self.page.on("request", _on_request)
        try:
            input_candidates = heuristics.build_candidates(
                self.browser_cfg.chat_input_selector, heuristics.DEFAULT_CHAT_INPUT_SELECTORS
            )
            # Post-login pages commonly show a brief loading state (e.g. "reading
            # your records...") before the chat UI becomes interactable — poll
            # for the input to appear rather than giving up on a single check.
            input_locator = await self._poll_for_locator(input_candidates, budget_s=10)

            if input_locator is None:
                _log.info("browser_login: no chat input found for sniffing")
                return None, None

            await input_locator.click(timeout=2000)
            await input_locator.fill(message, timeout=2000)

            send_candidates = heuristics.build_candidates(
                self.browser_cfg.send_button_selector, heuristics.DEFAULT_SEND_BUTTON_SELECTORS
            )
            sent = await self._click_first_enabled(send_candidates, budget_s=5)
            if not sent:
                _log.info("browser_login: no enabled send button found, pressing Enter")
                await self.page.keyboard.press("Enter")

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not captured:
                await self.page.wait_for_timeout(250)

            if not captured:
                return None, None
            return captured["url"], captured["body"]
        finally:
            self.page.remove_listener("request", _on_request)

    def _resolve_extra_fields(
        self, sniffed_body: dict[str, Any], identity_payload: dict[str, Any] | None
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        """Diff the sniffed chat body's keys against known message/session
        fields; confirm remaining candidates against the identity payload's
        values (high confidence) or leave them ambiguous."""
        known_lower = {"message", "text", "prompt", "query", "input"}
        candidates: dict[str, str] = {}
        for key, value in sniffed_body.items():
            if key.lower() in known_lower:
                continue
            if key.lower() in heuristics.DYNAMIC_PAYLOAD_FIELD_NAMES:
                continue
            if not isinstance(value, (str, int, float)) or value == "":
                continue
            candidates[key] = str(value)

        confirmed: dict[str, str] = {}
        ambiguous: dict[str, list[str]] = {}
        identity_values = (
            {str(v) for v in identity_payload.values() if isinstance(v, (str, int, float))}
            if identity_payload
            else set()
        )
        for key, value in candidates.items():
            if value in identity_values:
                confirmed[key] = value
            else:
                ambiguous[key] = [value]
        return confirmed, ambiguous

    # ------------------------------------------------------------------
    # Cookie export
    # ------------------------------------------------------------------

    async def export_cookies(self, path: Path) -> Path:
        try:
            cookies = await self._context.cookies()
            write_netscape_cookies(cookies, path)
        except OSError as exc:
            raise BrowserLoginError(
                f"Failed to write cookie file to {path}: {exc}",
                step="cookie_export",
                cause=str(exc),
            ) from exc
        return path


def write_netscape_cookies(cookies: list[dict[str, Any]], path: Path) -> None:
    """Serialize Playwright cookie objects to a Netscape-format cookies.txt,
    matching exactly the format ``_parse_netscape_cookies()`` in
    ``nuguard/common/auth.py`` reads: 7 tab-separated columns per line
    (domain, include_subdomains, path, secure, expiry, name, value), with
    HttpOnly cookies prefixed ``#HttpOnly_<domain>`` (curl's convention,
    already special-cased by that reader). No changes to the reader are
    needed — this is new write-side code matching an existing format.
    """
    lines = ["# Netscape HTTP Cookie File", "# generated by nuguard target discover-browser"]
    for c in cookies:
        domain = c.get("domain", "")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        cookie_path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = c.get("expires")
        expiry = str(int(expires)) if expires and expires > 0 else "0"
        name = c.get("name", "")
        value = c.get("value", "")
        domain_field = f"#HttpOnly_{domain}" if c.get("httpOnly") else domain
        lines.append("\t".join([domain_field, include_sub, cookie_path, secure, expiry, name, value]))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
