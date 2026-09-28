"""Factory for creating framework adapters from AI-SBOM documents.

Usage::

    from nuguard.redteam.target.framework_adapters.factory import make_framework_adapter

    adapter = make_framework_adapter(sbom, adk_config)
    if adapter:
        # target is a Google ADK application
        ...
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.common.ces_client import CESDeploymentConfig
    from nuguard.sbom.models import AiSbomDocument

from .google_adk import ADK_FRAMEWORK_NAMES, GoogleADKAdapter
from .google_ces import CES_FRAMEWORK_NAME, GoogleCESAdapter

_log = get_logger(__name__)


def _extract_agent_source_dirs(sbom: "AiSbomDocument") -> list[str]:
    """Return SBOM-derived top-level source directories, ordered by AGENT node frequency.

    Each AGENT node in the SBOM carries evidence with a ``location.path`` such as
    ``marketing_campaign_agent/agent.py``.  This function counts how many agents
    live in each top-level directory and returns the directories sorted most-to-least
    frequent.  The result is used by :class:`GoogleADKAdapter` to choose the right
    entry from ``/list-apps`` when several ADK sub-projects share the same source tree.

    Args:
        sbom: Parsed AI-SBOM document.

    Returns:
        Ordered list of top-level directory names (most agent-rich first).
        Empty list when no evidence paths are available.
    """
    counts: Counter[str] = Counter()
    for node in getattr(sbom, "nodes", []) or []:
        # Only count AGENT nodes — FRAMEWORK evidence is dominated by import paths
        # that do not represent the primary app directory.
        ctype_raw = getattr(node, "component_type", None)
        if ctype_raw is None:
            continue
        ctype = str(getattr(ctype_raw, "value", ctype_raw))
        if ctype != "AGENT":
            continue

        evidence = getattr(node, "evidence", None) or []
        for ev in evidence:
            if isinstance(ev, dict):
                path: str = ev.get("location", {}).get("path", "") or ""
            else:
                loc = getattr(ev, "location", None)
                path = str(getattr(loc, "path", "") or "") if loc else ""
            if path and "/" in path:
                top_dir = path.split("/")[0]
                if top_dir:
                    counts[top_dir] += 1

    return [d for d, _ in counts.most_common()]


def make_framework_adapter(
    sbom: "AiSbomDocument | None",
    adk_config: Any | None = None,
    target_url: str = "",
) -> GoogleADKAdapter | GoogleCESAdapter | None:
    """Return the appropriate framework adapter for the given AI-SBOM, or ``None``.

    SBOM framework metadata alone is never sufficient to select a direct
    adapter — see issue #552 (Blissful Store proxies CES internally but
    exposes its own ``/api/chat``; selecting CES from SBOM metadata alone
    hijacked traffic to ``ces.googleapis.com`` instead of the supplied
    target). *target_url* is required so each adapter can also confirm the
    supplied target actually is (or the caller explicitly wants) that
    framework's own service, not just that the SBOM mentions it somewhere.

    * **Google CES** — selected only when *target_url* itself is a
      ``ces.googleapis.com`` URL. SBOM ``"google-ces"`` evidence alone
      (e.g. a proxy app that calls CES internally) is informational only
      and never redirects transport away from *target_url*.
    * **Google ADK** — selected when ``summary.frameworks`` contains
      ``"google-adk"``/``"google_adk"`` **and** either (a) ``adk_config``
      explicitly sets ``enabled=true`` (trusted immediately), or (b) no
      explicit opt-in/opt-out was configured, in which case the returned
      adapter defers committing to the ADK protocol until
      :meth:`~.google_adk.GoogleADKAdapter.ensure_session` has live-verified
      the target actually serves an ADK-shaped ``/list-apps`` — see
      :class:`~.google_adk.GoogleADKAdapter`'s ``requires_verification``.
      ``adk_config.enabled=false`` still disables ADK entirely, unchanged.

    Args:
        sbom: Parsed AI-SBOM document.  When ``None`` no adapter is returned.
        adk_config: Optional ADK-specific config object (a
            :class:`~nuguard.config.GoogleADKConfig` instance or any object
            with ``model_fields_set``, ``enabled``, ``app_name``, ``user_id``,
            ``session_per_scenario``, and ``run_path`` attributes). May be
            ``None`` to use defaults.
        target_url: The base URL NuGuard was told to test. Required for both
            CES's URL-match gate and to distinguish "explicit ADK opt-in"
            from "SBOM mentioned ADK" (which now only earns a *deferred,
            live-verified* adapter rather than an immediately-trusted one).

    Returns:
        A configured adapter, or ``None`` to use generic HTTP transport.
    """
    if sbom is None:
        return None

    # Read frameworks from SBOM summary
    summary = getattr(sbom, "summary", None)
    frameworks: list[str] = []
    if summary is not None:
        raw = getattr(summary, "frameworks", None)
        if isinstance(raw, (list, tuple)):
            frameworks = [str(f).lower() for f in raw if f]

    detected_frameworks = {f for f in frameworks if f}

    # CES is gated on target_url alone (issue #552) — SBOM evidence of an
    # internally-proxied CES agent must never redirect NuGuard's own traffic
    # away from the target the caller supplied.
    ces_adapter = _make_ces_adapter(sbom, target_url)
    if ces_adapter is not None:
        return ces_adapter

    if not (detected_frameworks & ADK_FRAMEWORK_NAMES):
        return None

    # Explicit opt-out (unchanged): the target wraps ADK with a custom REST API.
    adk_fields_set: frozenset[str] = getattr(adk_config, "model_fields_set", frozenset())
    if adk_config is not None and not getattr(adk_config, "enabled", True):
        _log.info(
            "make_framework_adapter: ADK adapter disabled via adk.enabled=false — "
            "using generic HTTP POST"
        )
        return None

    # Issue #552: SBOM evidence alone is no longer sufficient to *trust* ADK
    # immediately — only an explicit adk.enabled=true opt-in earns that.
    # "Explicit" is judged via model_fields_set (populated by pydantic at
    # construction from parsed yaml) rather than the boolean value alone,
    # since the default is also True — a config object that never mentions
    # `enabled` must not be mistaken for one that set it to true on purpose.
    # All other cases (adk_config is None, or enabled was never explicitly
    # set) get a deferred adapter that must live-verify an ADK-shaped
    # /list-apps response before its first real session/run call; see
    # GoogleADKAdapter.
    explicit_opt_in = (
        adk_config is not None and "enabled" in adk_fields_set and adk_config.enabled
    )
    requires_verification = not explicit_opt_in

    _log.info(
        "make_framework_adapter: detected Google ADK (frameworks=%s) — creating "
        "GoogleADKAdapter (requires_verification=%s)",
        detected_frameworks & ADK_FRAMEWORK_NAMES,
        requires_verification,
    )

    # Extract config values, falling back to defaults
    app_name: str = ""
    user_id: str = "nuguard"
    session_per_scenario: bool = True
    run_path: str = "/run"

    if adk_config is not None:
        app_name = str(getattr(adk_config, "app_name", "") or "").strip()
        user_id = str(getattr(adk_config, "user_id", "nuguard") or "nuguard")
        _sps = getattr(adk_config, "session_per_scenario", True)
        session_per_scenario = bool(_sps)
        run_path = str(getattr(adk_config, "run_path", "/run") or "/run")

    # Derive SBOM candidate app names so the adapter can pick the correct entry
    # from /list-apps when multiple ADK sub-projects share the same source tree.
    # Only computed when app_name is not already explicitly set.
    sbom_app_candidates: list[str] = []
    if not app_name:
        # Prefer adk_app_name captured from Runner(app_name=...) during SBOM scan.
        # This avoids the /list-apps runtime call entirely when the source was scanned.
        for node in getattr(sbom, "nodes", []) or []:
            _ctype = str(getattr(getattr(node, "component_type", None), "value", "") or "")
            if _ctype != "AGENT":
                continue
            _meta = getattr(node, "metadata", None)
            _sbom_adk_name = (getattr(_meta, "extras", None) or {}).get("adk_app_name", "")
            if _sbom_adk_name and isinstance(_sbom_adk_name, str):
                app_name = _sbom_adk_name.strip()
                _log.info(
                    "make_framework_adapter: using adk_app_name %r from SBOM node %r",
                    app_name,
                    node.name,
                )
                break
    if not app_name:
        sbom_app_candidates = _extract_agent_source_dirs(sbom)
        if sbom_app_candidates:
            _log.debug(
                "make_framework_adapter: SBOM agent candidates for app_name: %s",
                sbom_app_candidates[:5],
            )

    return GoogleADKAdapter(
        app_name=app_name,
        user_id=user_id,
        session_per_scenario=session_per_scenario,
        run_path=run_path,
        sbom_app_candidates=sbom_app_candidates,
        requires_verification=requires_verification,
    )


def _is_ces_target_url(target_url: str) -> bool:
    """True when *target_url* itself points at the CES API host.

    This is the entire gate for selecting CES transport (issue #552) — SBOM
    evidence that an app proxies to CES internally is not, by itself,
    grounds to redirect NuGuard's own traffic there.
    """
    if not target_url:
        return False
    try:
        from urllib.parse import urlsplit  # noqa: PLC0415

        host = urlsplit(target_url).hostname or ""
    except ValueError:
        return False
    return host.lower() == "ces.googleapis.com"


def _make_ces_adapter(sbom: "AiSbomDocument", target_url: str) -> "GoogleCESAdapter | None":
    """Return a :class:`GoogleCESAdapter` only when *target_url* is itself a
    CES API URL, or ``None``.

    SBOM ``"google-ces"`` evidence (``summary.frameworks`` or a matching
    ``API_ENDPOINT`` node) is used only to build the
    :class:`~nuguard.common.ces_client.CESDeploymentConfig` once CES has
    already been selected on *target_url* grounds — it is never, by itself,
    sufficient to select CES (issue #552: a proxy app whose SBOM reports
    CES usage must still be tested at its own supplied target URL).

    Args:
        sbom: Parsed AI-SBOM document.
        target_url: The base URL NuGuard was told to test.

    Returns:
        A configured :class:`GoogleCESAdapter` or ``None``.
    """
    from nuguard.common.ces_client import CESDeploymentConfig  # noqa: PLC0415

    if not _is_ces_target_url(target_url):
        return None

    # Check summary.frameworks
    summary = getattr(sbom, "summary", None)
    frameworks: list[str] = []
    if summary is not None:
        raw = getattr(summary, "frameworks", None)
        if isinstance(raw, (list, tuple)):
            frameworks = [str(f).lower() for f in raw if f]

    has_ces_framework = CES_FRAMEWORK_NAME in frameworks

    # Find a CES API_ENDPOINT node to extract config from
    ces_endpoint_node = None
    for node in getattr(sbom, "nodes", []) or []:
        ctype = str(getattr(getattr(node, "component_type", None), "value", "") or "")
        if ctype != "API_ENDPOINT":
            continue
        node_framework = str(getattr(getattr(node, "metadata", None), "framework", "") or "")
        if node_framework == CES_FRAMEWORK_NAME:
            ces_endpoint_node = node
            break

    _log.info(
        "make_framework_adapter: target_url %r is a CES API URL — creating "
        "GoogleCESAdapter (sbom_ces_evidence=%s)",
        target_url,
        has_ces_framework or ces_endpoint_node is not None,
    )

    # Build config from endpoint node if available
    ces_config: CESDeploymentConfig | None = None
    if ces_endpoint_node is not None:
        meta = getattr(ces_endpoint_node, "metadata", None)
        endpoint = str(getattr(meta, "endpoint", "") or "")
        if endpoint:
            try:
                ces_config = _ces_config_from_endpoint(endpoint)
                # Pull version_id and deployment_id from evidence details if available
                for ev in getattr(ces_endpoint_node, "evidence", None) or []:
                    detail = str(getattr(ev, "detail", "") or "")
                    if detail.startswith("Version: ") and not ces_config.version_id:
                        ces_config = CESDeploymentConfig(
                            project=ces_config.project,
                            location=ces_config.location,
                            app_id=ces_config.app_id,
                            version_id=detail.removeprefix("Version: ").strip(),
                            deployment_id=ces_config.deployment_id,
                        )
                    elif detail.startswith("Deployment: ") and not ces_config.deployment_id:
                        ces_config = CESDeploymentConfig(
                            project=ces_config.project,
                            location=ces_config.location,
                            app_id=ces_config.app_id,
                            version_id=ces_config.version_id,
                            deployment_id=detail.removeprefix("Deployment: ").strip(),
                        )
            except Exception as exc:
                _log.warning(
                    "make_framework_adapter: could not parse CES endpoint URL %r: %s",
                    endpoint,
                    exc,
                )

    if ces_config is None:
        # Minimal placeholder config — caller will need to supply details
        ces_config = CESDeploymentConfig(
            project="unknown",
            location="us",
            app_id="unknown",
            version_id="",
            deployment_id="",
        )

    return GoogleCESAdapter(ces_config)


def _ces_config_from_endpoint(endpoint: str) -> "CESDeploymentConfig":
    """Parse a CES runSession URL or template to extract deployment config.

    Supports the template form::

        https://ces.googleapis.com/v1beta/projects/{p}/locations/{l}/apps/{a}/sessions/...

    Args:
        endpoint: The ``metadata.endpoint`` string from the SBOM node.

    Returns:
        A :class:`~nuguard.common.ces_client.CESDeploymentConfig` instance.

    Raises:
        ValueError: When the URL cannot be parsed.
    """
    import re as _re  # noqa: PLC0415

    from nuguard.common.ces_client import CESDeploymentConfig  # noqa: PLC0415

    pattern = _re.compile(
        r"ces\.googleapis\.com/v1beta/projects/([^/\s]+)/locations/([^/\s]+)/apps/([^/\s]+)"
    )
    match = pattern.search(endpoint)
    if not match:
        raise ValueError(f"Cannot parse CES endpoint: {endpoint!r}")
    project, location, app_id = match.groups()
    return CESDeploymentConfig(
        project=project,
        location=location,
        app_id=app_id,
        version_id="",
        deployment_id="",
    )
