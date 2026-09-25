"""Generic selector/text heuristics for browser-driven login discovery.

Pure data + pure functions — no Playwright import here, so this module is
importable and unit-testable without the ``browser`` extra installed.

Each candidate list is tried in order by the caller (``session.py``); a
config-supplied override (``BrowserDiscoveryConfig``) is always prepended so
explicit per-app configuration wins over these generic defaults, mirroring
the config-override-then-generic-fallback layering already used by
``nuguard/common/endpoint_detection``.
"""
from __future__ import annotations

import json
import re

DEFAULT_LOGIN_TRIGGER_TEXTS: list[str] = [
    "log in",
    "log-in",
    "login",
    "sign in",
    "sign-in",
    "signin",
    "continue",
]

# Modal/banner buttons that block clicks on the page underneath (welcome
# dialogs, cookie-consent banners). Matched as exact, case-insensitive button
# names — never substrings, so "Close account" is not clicked.
DEFAULT_OVERLAY_DISMISS_TEXTS: list[str] = [
    "dismiss",
    "close",
    "close dialog",
    "accept",
    "accept all",
    "accept cookies",
    "allow all",
    "got it",
    "ok",
    "i agree",
    "agree",
    "no thanks",
    "not now",
    "skip",
    "me want it!",
]

# Menus that hide the login entry on SPAs (e.g. an "Account" dropdown whose
# first item is "Login"). Opened when no login trigger is directly visible.
DEFAULT_ACCOUNT_MENU_TEXTS: list[str] = [
    "account",
    "my account",
    "show/hide account menu",
    "user menu",
    "profile",
    "sign in / register",
]

# Login routes tried directly (relative to the target URL) when neither a
# visible trigger nor an account menu leads to a login form. Includes the
# hash-router form used by Angular/Vue SPAs.
DEFAULT_LOGIN_ROUTES: list[str] = [
    "/login",
    "/#/login",
    "/signin",
    "/#/signin",
    "/auth/login",
    "/users/sign_in",
]

DEFAULT_USERNAME_SELECTORS: list[str] = [
    "input[name='username']",
    "input[name='email']",
    "input#email",
    "input[aria-label*='email' i]",
    "input[type='email']",
    "input#username",
    "input[autocomplete='username']",
]

DEFAULT_PASSWORD_SELECTORS: list[str] = [
    "input[name='password']",
    "input[type='password']",
    "input#password",
    "input[autocomplete='current-password']",
]

DEFAULT_SUBMIT_SELECTORS: list[str] = [
    "button[type='submit']",
    "input[type='submit']",
]

DEFAULT_CHAT_INPUT_SELECTORS: list[str] = [
    "textarea",
    "[contenteditable='true']",
    "input[placeholder*='message' i]",
    "input[placeholder*='ask' i]",
    "input[placeholder*='chat' i]",
    "textarea[placeholder*='ask' i]",
]

DEFAULT_SEND_BUTTON_SELECTORS: list[str] = [
    # Most-specific first. Playwright's `:text-is()` is an EXACT (whole
    # normalized text) match — unlike `:has-text()`/`has-text()`, which does
    # substring matching against a button's full text content, including any
    # descendant text. A generic `button:has-text('Send')` is dangerously
    # promiscuous: on a page with a chat-history sidebar, it can match a
    # conversation-preview row whose text happens to contain the word "send"
    # anywhere (e.g. "Yes, please send to the email on file...") well before
    # it reaches the actual Send button. `button[type='submit']` is
    # similarly too broad on pages with unrelated submit buttons.
    "button:text-is('Send')",
    "button[aria-label*='send' i]",
    "button:has-text('Send')",
    "button[type='submit']",
]

DEFAULT_IDENTITY_ENDPOINTS: list[str] = [
    "/auth/me",
    "/api/auth/me",
    "/api/me",
    "/me",
    "/api/user",
    "/whoami",
]

# Payload field names treated as session-scoped/dynamic rather than a
# candidate "extra identity field" during chat-request sniffing — these are
# generated per-conversation by chat runners themselves, not a stable
# per-account identifier NuGuard needs to configure.
DYNAMIC_PAYLOAD_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "sessionid",
        "session_id",
        "conversationid",
        "conversation_id",
        "requestid",
        "request_id",
        "traceid",
        "trace_id",
        "messageid",
        "message_id",
        "timestamp",
        "nonce",
    }
)


def build_candidates(configured: str, defaults: list[str]) -> list[str]:
    """Merge a single configured selector (tried first) with a default list.

    An empty/blank ``configured`` value is dropped — callers rely on this to
    mean "use heuristics only" without special-casing the empty string.
    """
    if configured and configured.strip():
        return [configured.strip(), *defaults]
    return list(defaults)


def build_text_candidates(configured: list[str], defaults: list[str]) -> list[str]:
    """Merge configured login-trigger texts (tried first) with defaults, de-duplicated."""
    seen: set[str] = set()
    merged: list[str] = []
    for text in [*configured, *defaults]:
        key = text.strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(text.strip())
    return merged


# A compact JWS/JWT: three base64url segments, header starting "eyJ" ('{"').
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*$")
# Storage keys whose value is a bearer token even when it isn't a JWT.
_TOKEN_STORAGE_KEY_RE = re.compile(r"(?:^|[_.-])(?:access[_-]?)?token$|^jwt$|^id[_-]?token$", re.IGNORECASE)


def _token_from_value(key: str, value: object, depth: int = 0) -> str | None:
    if isinstance(value, str):
        candidate = value.strip().strip('"')
        if candidate.lower().startswith("bearer "):
            candidate = candidate[7:].strip()
        if _JWT_RE.match(candidate):
            return candidate
        if depth == 0 and candidate[:1] in ("{", "["):
            try:
                return _token_from_value(key, json.loads(candidate), depth + 1)
            except ValueError:
                return None
        if _TOKEN_STORAGE_KEY_RE.search(key) and 16 <= len(candidate) <= 4096 and " " not in candidate:
            return candidate
        return None
    if isinstance(value, dict) and depth < 3:
        for sub_key, sub_value in value.items():
            found = _token_from_value(str(sub_key), sub_value, depth + 1)
            if found:
                return found
    return None


def extract_storage_token(storage: dict[str, object]) -> str | None:
    """Find a bearer token in a browser ``localStorage``/``sessionStorage`` dump.

    SPAs commonly keep the post-login JWT in web storage and send it as an
    ``Authorization: Bearer`` header rather than a cookie, so a cookie-only
    session capture misses it. Prefers JWT-shaped values, including ones
    nested in a JSON-encoded storage entry (``{"accessToken": "eyJ..."}``);
    otherwise accepts an opaque value stored under a token-named key.
    """
    fallback: str | None = None
    for key, value in storage.items():
        found = _token_from_value(str(key), value)
        if not found:
            continue
        if _JWT_RE.match(found):
            return found
        fallback = fallback or found
    return fallback


def bearer_from_authorization_header(value: str) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header value."""
    parts = (value or "").strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None
