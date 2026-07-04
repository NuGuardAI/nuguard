"""Shared factory for building a :class:`TargetAppClient`.

Both :class:`~nuguard.behavior.runner.BehaviorRunner` and
:class:`~nuguard.redteam.executor.orchestrator.RedteamOrchestrator` need to
construct a ``TargetAppClient`` with the same config-resolution logic:

1. Detect a framework adapter (e.g. Google ADK) from the SBOM.
2. Auto-discover chat endpoint / payload shape from SBOM API_ENDPOINT nodes,
   but only for values the caller has *not* explicitly set.
3. Apply a three-tier response-key priority:
   explicit config > SBOM-discovered > ``None``.
4. Build and return a ``TargetAppClient``.

Callers that need auth should resolve their auth headers first and pass them in
via ``auth_headers``.  This module does not perform authentication.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.auth import AuthConfig
    from nuguard.redteam.target.client import TargetAppClient
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Static-site detection helpers
# ---------------------------------------------------------------------------

# Domains that exclusively serve static assets — they have no server-side API surface.
# Azure Static Web Apps requires Azure Functions for server routes; without them the
# site is pure CDN.  Netlify/Vercel/GitHub Pages are similar.
_STATIC_HOSTING_DOMAINS = frozenset(
    [
        "azurestaticapps.net",
        "netlify.app",
        "pages.github.io",
        "github.io",
        "vercel.app",
        "pages.dev",           # Cloudflare Pages
        "web.app",             # Firebase Hosting
        "firebaseapp.com",
        "surge.sh",
        "render.com",
    ]
)


def _is_static_hosting_url(url: str) -> bool:
    """Return True when *url* points at a known static-hosting domain."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in _STATIC_HOSTING_DOMAINS)


def _fallback_url_from_sbom(sbom: "AiSbomDocument | None") -> str | None:
    """Return the best deployment URL from the SBOM summary, or None.

    Prefers non-localhost HTTPS URLs over HTTP, and skips localhost/127.0.0.1
    entries entirely when a non-local URL is available (localhost addresses are
    only reachable from the machine running the application, not from nuguard).
    """
    if sbom is None:
        return None
    summary = getattr(sbom, "summary", None)
    if summary is None:
        return None
    candidates: list[str] = []
    for url in (getattr(summary, "deployment_urls", None) or []):
        if not url or not isinstance(url, str):
            continue
        url = url.rstrip("/")
        try:
            from urllib.parse import urlparse as _urlparse
            host = (_urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if host in ("localhost", "127.0.0.1", "::1") or host.startswith("192.168.") or host.startswith("10."):
            continue  # skip local-only addresses
        candidates.append(url)
    if not candidates:
        # Fall back to localhost entries only if there's nothing else
        for url in (getattr(summary, "deployment_urls", None) or []):
            if url and isinstance(url, str):
                return url.rstrip("/")
        return None
    # Prefer https over http
    https_first = sorted(candidates, key=lambda u: (0 if u.startswith("https://") else 1))
    return https_first[0]


# ---------------------------------------------------------------------------
# Auth upgrade helpers — basic → login_flow
# ---------------------------------------------------------------------------

_LOGIN_PATH_RE = re.compile(
    r"/(login|auth|signin|sign-in|authenticate|token)(/|$)",
    re.IGNORECASE,
)
_USERNAME_KEYS = frozenset(["username", "user", "email", "user_id", "login", "identifier"])
_PASSWORD_KEYS = frozenset(["password", "passwd", "pass", "secret", "pin"])
# Ordered preference list for the token key in the login response.
_TOKEN_KEY_CANDIDATES = ["token", "access_token", "jwt", "id_token", "auth_token"]


def _discover_login_endpoint(sbom: "AiSbomDocument") -> tuple[str, str, str] | None:
    """Scan SBOM API_ENDPOINT nodes for a login endpoint.

    Returns ``(endpoint_path, username_field, password_field)`` for the
    best candidate, or ``None`` when no login-like endpoint is found.
    """
    try:
        from nuguard.sbom.models import NodeType  # noqa: PLC0415
    except Exception:
        return None

    best: tuple[float, str, str, str] | None = None  # (score, path, user_field, pass_field)
    for node in sbom.nodes:
        if node.component_type != NodeType.API_ENDPOINT:
            continue
        meta = node.metadata
        if not meta:
            continue
        path = (meta.endpoint or "").strip()
        if not path or not path.startswith("/"):
            continue
        method = (meta.method or "POST").upper()
        if method != "POST":
            continue
        if not _LOGIN_PATH_RE.search(path):
            continue

        # Check that the request body has both a username-like and password-like field.
        schema: dict = {}
        try:
            raw = meta.request_body_schema
            if isinstance(raw, dict):
                schema = {k.lower(): v for k, v in raw.items()}
        except Exception:
            pass

        user_field = next((k for k in schema if k in _USERNAME_KEYS), None)
        pass_field = next((k for k in schema if k in _PASSWORD_KEYS), None)
        if not user_field or not pass_field:
            continue

        score = node.confidence * 10
        if any(tok in path.lower() for tok in ("/login", "/signin")):
            score += 3
        if best is None or score > best[0]:
            # Recover the original casing from the schema for the payload key names.
            orig_schema: dict = {}
            try:
                raw2 = meta.request_body_schema
                if isinstance(raw2, dict):
                    orig_schema = raw2
            except Exception:
                pass
            orig_user = next(
                (k for k in orig_schema if k.lower() == user_field), user_field
            )
            orig_pass = next(
                (k for k in orig_schema if k.lower() == pass_field), pass_field
            )
            best = (score, path, orig_user, orig_pass)

    return (best[1], best[2], best[3]) if best else None


def resolve_auth_config_with_sbom_fallback(
    auth_config: "AuthConfig | None",
    sbom: "AiSbomDocument | None",
) -> tuple["AuthConfig", str | None]:
    """Upgrade a basic-auth config to login_flow when the SBOM has a login endpoint.

    When ``auth_config.type == "basic"`` (username + password) but no login flow
    is configured, this function scans the SBOM for a POST login endpoint whose
    request body accepts username/password fields.  If one is found, it builds a
    ``LoginFlowConfig`` from the SBOM metadata and the supplied credentials and
    returns an upgraded ``AuthConfig(type="login_flow", ...)``.

    The token response key defaults to "token" (most common for simple REST APIs).
    If the app uses a different key the user should set ``auth.login_flow`` explicitly
    in ``nuguard.yaml``; the note returned by this function documents that.

    Returns:
        ``(auth_config, note)`` — ``note`` is a human-readable string when an
        upgrade was applied, ``None`` otherwise.
    """
    if auth_config is None or auth_config.type != "basic":
        return auth_config or _none_auth(), None  # type: ignore[return-value]
    if not (auth_config.username and auth_config.password):
        return auth_config, None
    if sbom is None:
        return auth_config, None

    result = _discover_login_endpoint(sbom)
    if result is None:
        return auth_config, None

    endpoint_path, user_field, pass_field = result

    try:
        from nuguard.common.auth import AuthConfig, LoginFlowConfig  # noqa: PLC0415
    except Exception:
        return auth_config, None

    login_flow = LoginFlowConfig(
        endpoint=endpoint_path,
        method="POST",
        payload={user_field: auth_config.username, pass_field: auth_config.password},
        token_response_key=_TOKEN_KEY_CANDIDATES[0],  # "token" — most common default
        token_header="Authorization: Bearer",
        refresh_on_401=True,
    )
    # Keep the original username/password on the upgraded config (even though
    # type="login_flow" doesn't use them directly) so bootstrap can fall back to
    # sending them as HTTP Basic auth straight to the chat endpoint if the login
    # endpoint turns out not to work.
    upgraded = AuthConfig(
        type="login_flow",
        login_flow=login_flow,
        username=auth_config.username,
        password=auth_config.password,
    )

    note = (
        f"auth.type='basic' with credentials for '{auth_config.username}' was upgraded to "
        f"'login_flow' using SBOM-discovered login endpoint '{endpoint_path}' "
        f"(payload fields: {user_field!r}/{pass_field!r}, "
        f"token_response_key: '{_TOKEN_KEY_CANDIDATES[0]}'). "
        f"If the token key differs, set auth.login_flow explicitly in nuguard.yaml."
    )
    _log.warning("resolve_auth_config_with_sbom_fallback: %s", note)
    return upgraded, note


def _none_auth() -> "AuthConfig":
    from nuguard.common.auth import AuthConfig  # noqa: PLC0415
    return AuthConfig(type="none")


# ---------------------------------------------------------------------------
# URL resolution (exposed separately so callers can resolve before bootstrap)
# ---------------------------------------------------------------------------

def resolve_target_url(
    target_url: str,
    sbom: "AiSbomDocument | None",
) -> tuple[str, list[str]]:
    """Resolve *target_url*, applying static-hosting fallback from the SBOM if needed.

    Returns ``(resolved_url, notes)`` where *notes* is a list of human-readable
    strings describing any automatic adjustments.  When no adjustment is needed the
    returned URL equals *target_url* (stripped of a trailing slash) and *notes* is
    empty.

    Callers should use this before running auth bootstrap so that bootstrap targets
    the same backend URL that ``build_target_app_client`` would resolve to.
    """
    notes: list[str] = []
    url = target_url.rstrip("/") if target_url else target_url
    if url and _is_static_hosting_url(url):
        fallback = _fallback_url_from_sbom(sbom)
        if fallback and fallback != url:
            note = (
                f"Target URL '{url}' is a static hosting site with no server-side "
                f"API endpoints. Falling back to SBOM deployment URL '{fallback}' for "
                f"behavior and redteam testing. Update target.url in nuguard.yaml to suppress "
                f"this warning."
            )
            _log.warning("resolve_target_url: %s", note)
            notes.append(note)
            url = fallback
        else:
            note = (
                f"Target URL '{url}' is a static hosting site with no server-side "
                f"API endpoints, and no fallback URL was found in the SBOM. "
                f"Tests will likely fail. Set target.url in nuguard.yaml to the backend URL."
            )
            _log.warning("resolve_target_url: %s", note)
            notes.append(note)
    return url, notes


# ---------------------------------------------------------------------------
# Main factory
# ---------------------------------------------------------------------------

def build_target_app_client(
    target_url: str,
    *,
    endpoint: str = "",
    payload_key: str = "message",
    payload_list: bool = False,
    payload_format: str = "json",
    response_key: str | None = None,
    timeout: float = 60.0,
    auth_headers: dict[str, str] | None = None,
    sbom: "AiSbomDocument | None" = None,
    adk_cfg: Any = None,
    explicitly_set: frozenset[str] | set[str] = frozenset(),
    payload_extras: dict[str, Any] | None = None,
) -> "TargetAppClient":
    """Build a :class:`TargetAppClient` with SBOM-assisted config resolution.

    Args:
        target_url: Base URL of the target application.
        endpoint: Chat endpoint path (e.g. ``"/chat"``).  Empty string means
            not yet determined — SBOM discovery and framework adapters may fill
            it in.
        payload_key: JSON body key for the chat message (default ``"message"``).
        payload_list: Whether to wrap the payload value in a list.
        payload_format: ``"json"`` or ``"form"`` encoding for the POST body.
        response_key: Explicit top-level key to extract from the JSON response.
        timeout: HTTP request timeout in seconds.
        auth_headers: Headers to include on every request (e.g. auth token).
        sbom: Optional AI-SBOM for framework detection and endpoint discovery.
        adk_cfg: Optional ADK deployment config forwarded to the framework adapter
            factory.
        explicitly_set: Set of config field names that were *explicitly* provided
            by the user (e.g. Pydantic ``model_fields_set``).  Discovery only
            overrides values that are *not* in this set.

    Returns:
        A fully configured :class:`TargetAppClient` instance.  Check
        ``client.resolution_notes`` for any automatic config adjustments.
    """
    from nuguard.redteam.target.client import TargetAppClient

    resolution_notes: list[str] = []

    # ── 0. Static-site detection — fall back to SBOM deployment_url ────────
    _resolved_url, _url_notes = resolve_target_url(target_url, sbom)
    if _url_notes:
        resolution_notes.extend(_url_notes)
        target_url = _resolved_url
    elif _resolved_url:
        target_url = _resolved_url

    # ── 1. Framework adapter ────────────────────────────────────────────────
    framework_adapter = None
    if sbom is not None:
        try:
            from nuguard.redteam.target.framework_adapters.factory import make_framework_adapter
            framework_adapter = make_framework_adapter(sbom, adk_cfg)
            if framework_adapter is not None and not endpoint:
                endpoint = framework_adapter.run_path
                _log.info(
                    "build_target_app_client: framework adapter detected — using endpoint %s",
                    endpoint,
                )
        except Exception as exc:
            _log.debug("build_target_app_client: framework adapter detection failed: %s", exc)

    # ── 2. SBOM-based endpoint / payload discovery ──────────────────────────
    config_has_explicit_endpoint = bool(endpoint) and ("target_endpoint" in explicitly_set)
    config_has_explicit_payload = "chat_payload_key" in explicitly_set
    config_has_explicit_response_key = "chat_response_key" in explicitly_set

    discovered_response_key: str | None = None
    if sbom is not None and (not config_has_explicit_endpoint or not config_has_explicit_payload):
        try:
            from nuguard.redteam.executor.orchestrator import _discover_chat_config
            discovered_path, discovered_key, discovered_list, discovered_response_key = (
                _discover_chat_config(
                    sbom,
                    chat_path=endpoint or "/chat",
                    chat_payload_key=payload_key,
                    chat_payload_list=payload_list,
                )
            )
            if not config_has_explicit_endpoint and discovered_path not in ("/chat", endpoint):
                _log.info(
                    "build_target_app_client: SBOM-discovered endpoint %s (was %s)",
                    discovered_path,
                    endpoint or "/chat",
                )
                endpoint = discovered_path
            if not config_has_explicit_payload and discovered_key != payload_key:
                _log.info(
                    "build_target_app_client: SBOM-discovered payload key %s (was %s)",
                    discovered_key,
                    payload_key,
                )
                payload_key = discovered_key
                payload_list = discovered_list
        except Exception as exc:
            _log.debug("build_target_app_client: SBOM chat config discovery failed: %s", exc)

    # ── 3. Three-tier response key: explicit > SBOM-discovered > None ───────
    if not config_has_explicit_response_key and discovered_response_key:
        _log.info(
            "build_target_app_client: using SBOM-discovered response key %s",
            discovered_response_key,
        )
        response_key = discovered_response_key

    # ── 4. Construct the client ─────────────────────────────────────────────
    client = TargetAppClient(
        base_url=target_url,
        chat_path=endpoint or "/chat",
        timeout=timeout,
        default_headers=auth_headers or None,
        chat_payload_key=payload_key,
        chat_payload_list=payload_list,
        chat_payload_format=payload_format,
        chat_response_key=response_key,
        framework_adapter=framework_adapter,
        chat_payload_extras=payload_extras or None,
    )
    client.resolution_notes = resolution_notes
    return client
