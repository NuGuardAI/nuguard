"""Generic selector/text heuristics for browser-driven login discovery.

Pure data + pure functions — no Playwright import here, so this module is
importable and unit-testable without the ``browser`` extra installed.

Each candidate list is tried in order by the caller (``session.py``); a
config-supplied override (``BrowserDiscoveryConfig``) is always prepended so
explicit per-app configuration wins over these generic defaults, mirroring
the config-override-then-generic-fallback layering already used by
``nuguard/common/endpoint_probe.py``.
"""
from __future__ import annotations

DEFAULT_LOGIN_TRIGGER_TEXTS: list[str] = [
    "log in",
    "log-in",
    "login",
    "sign in",
    "sign-in",
    "signin",
    "continue",
]

DEFAULT_USERNAME_SELECTORS: list[str] = [
    "input[name='username']",
    "input[name='email']",
    "input[type='email']",
    "input#username",
    "input#email",
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
