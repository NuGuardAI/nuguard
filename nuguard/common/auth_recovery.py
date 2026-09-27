"""Automatic browser-login recovery when static auth fails — or silently
produces unusable responses — against a live target.

Wraps the same machinery ``nuguard target discover-browser`` uses (see
``nuguard/cli/commands/target_browser.py``) so that ``target verify``,
``behavior``, and ``redteam`` can self-heal two distinct bootstrap problems
without requiring the user to remember to run that command by hand first:

1. **auth_failed** (401/403) — apps that authenticate via an interactive
   redirect/OAuth flow (e.g. Auth0 Universal Login) a plain HTTP client can't
   drive.
2. **ok, but the response body is empty/unparseable** — apps whose chat
   endpoint accepts the request (2xx) but silently no-ops when a required
   body field (e.g. an opaque per-user consumer/actor ID) is missing, instead
   of returning a proper 400/422 a schema-heal could learn from. There is no
   error text to reason about here, and the SBOM's static extraction often
   has nothing for the endpoint either (it commonly only sees the frontend's
   SPA route, not the real backend API) — a live browser session driving the
   app's own UI is the only thing that reliably observes what the real chat
   request looks like. Deliberately does NOT try to *guess* a value for a
   field that looks like a per-user identity (that would mean silently
   testing as the wrong user) — it only harvests whatever the browser's own
   authenticated session actually sent.

This is a best-effort fallback: it requires username+password credentials on
a ``basic``/``login_flow``/``cookie_file`` config (the credentials a browser
login form needs — even when that config is already authenticating fine via
a previously-recovered cookie_file, its credentials are what let a browser
session re-sniff the real request shape), and silently no-ops (returning
``None``) whenever the ``browser`` extra isn't installed or the login flow
itself fails — callers fall back to their pre-existing handling exactly as
before this module existed.

``cookie_file`` configs only carry credentials here when a
``target.browser_login`` (or ``redteam.browser_login``) sibling block is
set — see ``nuguard/cli/commands/target_browser.py`` and the matching
comments in ``nuguard/config.py``'s auth-block flattening. Without it, a
config that's already on cookie_file (and has no separate credentials block)
has nothing to recover with, same as before.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from nuguard.common.auth import AuthConfig
from nuguard.common.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class BrowserAuthRecovery:
    """Result of a successful browser-login recovery attempt."""

    auth_config: AuthConfig
    # Extra chat-payload fields (e.g. consumerID) the browser's chat-sniff
    # step confirmed the app's own UI sends. Empty when sniffing found none.
    chat_payload_extras: dict[str, str]


async def attempt_browser_auth_recovery(
    *,
    target_url: str,
    auth_config: AuthConfig,
    config_path: Path | None,
    reason: str = "auth_failed",
) -> BrowserAuthRecovery | None:
    """Try a real-browser login/sniff and return the recovered auth + extras.

    Only attempted for ``basic``/``login_flow`` configs that carry a
    username+password. Returns ``None`` on any failure — missing ``browser``
    extra, login/navigation failure, or anything else — so callers can fall
    back to their existing handling without special-casing this path.

    ``reason`` is used only for logging (``"auth_failed"`` or
    ``"empty_body"``) — the recovery flow itself is identical either way.

    When ``config_path`` is given, the discovered cookie_file/auth and any
    sniffed ``chat_payload_extras`` are persisted back into that nuguard.yaml
    (same write ``discover-browser --write`` performs) so subsequent runs
    skip the browser entirely. This is best-effort: a persistence failure
    (e.g. concurrent edit) is logged and does not prevent the recovered
    values from being used for the current run.
    """
    if auth_config.type not in ("basic", "login_flow", "cookie_file") or not (
        auth_config.username and auth_config.password
    ):
        return None

    try:
        from nuguard.common.browser_login.public_api import (  # noqa: PLC0415
            BrowserDiscoveryRequest,
            discover_browser,
        )
        from nuguard.common.errors import BrowserLoginError  # noqa: PLC0415
    except ImportError:
        _log.debug(
            "auth_recovery: 'browser' extra not installed — skipping browser-login "
            "recovery. Install with: pip install \"nuguard[browser]\" && "
            "playwright install chromium"
        )
        return None

    cookie_file = (
        (config_path.parent / "cookies.txt")
        if config_path is not None
        else Path(tempfile.gettempdir()) / "nuguard-auth-recovery-cookies.txt"
    )

    _log.warning(
        "auth_recovery: %s for %s auth — attempting browser-login recovery "
        "(target: %s)",
        "static auth was rejected" if reason == "auth_failed" else
        "response body was empty/unparseable despite a 2xx",
        auth_config.type,
        target_url,
    )

    try:
        result = await discover_browser(
            BrowserDiscoveryRequest(
                target_url=target_url,
                auth_type="basic",
                auth_username=auth_config.username,
                auth_password=auth_config.password,
                headless=True,
            ),
            cookie_file=cookie_file,
        )
    except BrowserLoginError as exc:
        _log.warning("auth_recovery: browser-login recovery failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 — recovery must never crash the caller
        _log.warning("auth_recovery: unexpected error during browser-login recovery: %s", exc)
        return None

    # SPAs that authenticate API calls with an Authorization header (JWT in
    # localStorage) ignore cookies — prefer the captured bearer token.
    bearer = result.bearer_token.get_secret_value() if result.bearer_token else ""
    recovered_auth = (
        AuthConfig(type="bearer", header=f"Authorization: Bearer {bearer}")
        if bearer
        else AuthConfig(type="cookie_file", cookie_file=result.cookies_written_to)
    )
    recovered = BrowserAuthRecovery(
        auth_config=recovered_auth,
        chat_payload_extras=dict(result.candidate_extra_fields),
    )
    _log.warning(
        "auth_recovery: browser-login recovery succeeded — %s%s",
        "captured the app's bearer token (used for this run only)"
        if bearer else f"session captured to {result.cookies_written_to}",
        f", sniffed extra field(s) {list(result.candidate_extra_fields)}"
        if result.candidate_extra_fields else "",
    )

    # A bearer token is a short-lived secret — never write it into
    # nuguard.yaml. Cookie sessions keep the existing persistence behaviour.
    if config_path is not None and not bearer:
        _persist_recovered_auth(
            config_path,
            cookie_file=result.cookies_written_to,
            chat_payload_extras=result.candidate_extra_fields,
        )

    return recovered


def _persist_recovered_auth(
    config_path: Path,
    *,
    cookie_file: str,
    chat_payload_extras: dict[str, str],
) -> None:
    """Best-effort write of the recovered cookie_file auth back into nuguard.yaml.

    Also persists any ``chat_payload_extras`` the browser login's chat-sniff
    step confirmed (e.g. an opaque consumer/actor ID the app requires
    alongside the message body) — without this, a recovered session can still
    fail every chat request with a 400 for a missing body field even though
    auth itself now works.
    """
    try:
        from nuguard.common.browser_login.yaml_writer import (  # noqa: PLC0415
            apply_target_updates,
            load_editable_yaml,
            write_yaml,
        )

        relative_cookie_path = "./" + str(
            Path(cookie_file).resolve().relative_to(config_path.resolve().parent)
        )
    except ValueError:
        relative_cookie_path = cookie_file
    except Exception as exc:
        _log.warning("auth_recovery: could not prepare nuguard.yaml update: %s", exc)
        return

    try:
        editable = load_editable_yaml(config_path)
        apply_target_updates(
            editable,
            cookie_file=relative_cookie_path,
            chat_payload_extras=chat_payload_extras,
        )
        write_yaml(editable)
        _log.warning(
            "auth_recovery: nuguard.yaml updated — future runs will use %s directly "
            "without re-running browser login.",
            config_path,
        )
    except Exception as exc:  # noqa: BLE001 — persistence is a bonus, not required
        _log.warning(
            "auth_recovery: recovered session works for this run, but could not persist "
            "it to %s (%s). Consider running 'nuguard target discover-browser --write' "
            "manually.",
            config_path,
            exc,
        )


__all__ = ["BrowserAuthRecovery", "attempt_browser_auth_recovery"]
