"""Factory for creating framework adapters from AI-SBOM documents.

Usage::

    from nuguard.redteam.target.framework_adapters.factory import make_framework_adapter

    adapter = make_framework_adapter(sbom, adk_config)
    if adapter:
        # target is a Google ADK application
        ...
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nuguard.common.ces_client import CESDeploymentConfig
    from nuguard.sbom.models import AiSbomDocument

from .google_adk import ADK_FRAMEWORK_NAMES, GoogleADKAdapter
from .google_ces import CES_FRAMEWORK_NAME, GoogleCESAdapter

_log = logging.getLogger(__name__)


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
) -> GoogleADKAdapter | GoogleCESAdapter | None:
    """Return the appropriate framework adapter for the given AI-SBOM, or ``None``.

    Currently the only supported framework is **Google ADK**.  Detection is
    based on the ``summary.frameworks`` field of the SBOM — when it contains
    ``"google-adk"`` or ``"google_adk"`` a :class:`GoogleADKAdapter` is
    returned, configured from ``adk_config`` (a
    :class:`~nuguard.config.GoogleADKConfig` instance or any object with
    ``app_name``, ``user_id``, ``session_per_scenario``, and ``run_path``
    attributes).

    Args:
        sbom: Parsed AI-SBOM document.  When ``None`` no adapter is returned.
        adk_config: Optional ADK-specific config object.  May be ``None`` to
            use defaults (e.g. ``user_id="nuguard"``).

    Returns:
        A configured :class:`GoogleADKAdapter` when Google ADK is detected,
        otherwise ``None``.
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

    # CES takes priority over ADK: a serve.py that proxies a CES agent will expose
    # ADK-compatible endpoints locally, but the real target is ces.googleapis.com.
    # Check for CES first; only fall back to ADK when CES is absent.
    ces_adapter = _make_ces_adapter(sbom)
    if ces_adapter is not None:
        return ces_adapter

    if not (detected_frameworks & ADK_FRAMEWORK_NAMES):
        return None

    _log.info(
        "make_framework_adapter: detected Google ADK (frameworks=%s) — creating GoogleADKAdapter",
        detected_frameworks & ADK_FRAMEWORK_NAMES,
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
    )


def _make_ces_adapter(sbom: "AiSbomDocument") -> "GoogleCESAdapter | None":
    """Return a :class:`GoogleCESAdapter` when the SBOM reports a CES app, or ``None``.

    Searches ``summary.frameworks`` and then scans individual nodes for the
    ``"google-ces"`` framework marker.  When found, builds a
    :class:`~nuguard.common.ces_client.CESDeploymentConfig` from the first
    matching ``API_ENDPOINT`` node's endpoint URL.

    Args:
        sbom: Parsed AI-SBOM document.

    Returns:
        A configured :class:`GoogleCESAdapter` or ``None``.
    """
    from nuguard.common.ces_client import CESDeploymentConfig  # noqa: PLC0415

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

    if not has_ces_framework and ces_endpoint_node is None:
        return None

    _log.info(
        "make_framework_adapter: detected Google CES — creating GoogleCESAdapter"
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
