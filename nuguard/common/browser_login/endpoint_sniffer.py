"""Bounded, operator-declared browser crawl for REST-endpoint discovery.

The static AI-SBOM extractor can miss endpoints entirely — a route only ever
called from client-side JavaScript in response to a button click, not a
statically-analyzable string literal. :func:`crawl_and_sniff` extends
:meth:`~nuguard.common.browser_login.session.BrowserLoginSession._sniff_chat_request`'s
existing "capture one outgoing chat POST" machinery into "capture every
same-origin XHR/fetch request observed during a bounded, caller-declared
sequence of page visits" — never autonomous link-following (too open-ended
for a security tool; could trigger a destructive action like a delete-account
button), only relative paths the operator explicitly listed in
``browser_discovery_nav_targets``.

Like :mod:`nuguard.common.browser_login.session`, this module is only meant
to be imported when the caller has actually enabled browser-based discovery
(``browser_discover_endpoints`` config, off by default) — Playwright is a
heavy optional dependency (the ``browser`` extra) and must never be imported
from a hot path unconditionally.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from nuguard.common.browser_login.session import _ASSET_EXT_RE, _same_origin
from nuguard.common.errors import BrowserLoginError
from nuguard.common.logging import get_logger

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument


class _SniffableSession(Protocol):
    """Structural subset of :class:`~nuguard.common.browser_login.session.BrowserLoginSession`
    that :func:`crawl_and_sniff` needs — lets tests supply a lightweight fake
    Playwright session instead of a real (or heavily mocked) one."""

    target_url: str

    @property
    def page(self) -> Any: ...

    async def _sniff_chat_request(
        self, message: str
    ) -> tuple[str | None, dict[str, Any] | None]: ...


_log = get_logger(__name__)

_RESPONSE_SNIPPET_MAX_CHARS = 200
_DEFAULT_BUDGET_S = 30.0
_DEFAULT_MAX_REQUESTS = 50

# Evidence kind/confidence for nodes created from this crawl, distinct from
# nuguard.common.discovery's "dynamic_probe" (confidence 0.5) since this is
# an observed real network call, not a text-parsed capability name.
BROWSER_SNIFF_EVIDENCE_KIND = "browser_sniff"
BROWSER_SNIFF_CONFIDENCE = 0.6


class SniffedRequest(BaseModel):
    """One captured same-origin XHR/fetch request.

    Deliberately carries no full request/response bodies — only top-level
    JSON key names and a short response snippet — so a crawl never hoovers
    secrets/PII into the SBOM.
    """

    method: str
    url: str
    path: str
    status_code: int | None = None
    request_body_keys: list[str] = Field(default_factory=list)
    response_snippet: str = ""
    same_origin: bool = True


def _extract_body_keys(body: str | None) -> list[str]:
    if not body:
        return []
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        _log.debug("endpoint_sniffer: request body not JSON, skipping key extraction")
        return []
    if isinstance(parsed, dict):
        return list(parsed.keys())
    return []


async def crawl_and_sniff(
    session: "_SniffableSession",
    *,
    nav_targets: list[str],
    interact_chat: bool = False,
    budget_s: float = _DEFAULT_BUDGET_S,
    max_requests: int = _DEFAULT_MAX_REQUESTS,
) -> list[SniffedRequest]:
    """Visit each of *nav_targets* (relative paths) and passively record every
    same-origin, non-asset XHR/fetch request observed along the way.

    Never follows links or fills forms beyond the caller-declared targets.
    Stops early once *budget_s* elapses or *max_requests* is reached, always
    returning whatever was captured so far rather than raising. A missing
    Chromium/Playwright installation (:class:`BrowserLoginError`) is treated
    as "nothing to discover" — logged and swallowed, not propagated — since
    this is an optional enrichment step, never a required one.
    """
    captured: dict[tuple[str, str], SniffedRequest] = {}
    deadline = time.monotonic() + budget_s

    def _on_request(request: Any) -> None:
        if len(captured) >= max_requests:
            return
        url = request.url
        if _ASSET_EXT_RE.search(url):
            return
        is_same_origin = _same_origin(url, session.target_url)
        if not is_same_origin:
            return
        key = (request.method, urlparse(url).path)
        if key in captured:
            return
        captured[key] = SniffedRequest(
            method=request.method,
            url=url,
            path=urlparse(url).path,
            request_body_keys=_extract_body_keys(request.post_data),
            same_origin=is_same_origin,
        )

    def _on_response(response: Any) -> None:
        key = (response.request.method, urlparse(response.url).path)
        entry = captured.get(key)
        if entry is None or entry.status_code is not None:
            return
        entry.status_code = response.status
        # Response body capture is best-effort and synchronous-unsafe (it's a
        # coroutine on Playwright's Response) — snippet capture is handled
        # via a fire-and-forget task so a slow/failed body read never blocks
        # the crawl's navigation.
        asyncio.create_task(_capture_snippet(entry, response))

    async def _capture_snippet(entry: SniffedRequest, response: Any) -> None:
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 — best-effort only
            return
        entry.response_snippet = text[:_RESPONSE_SNIPPET_MAX_CHARS]

    try:
        page = session.page
    except AssertionError:
        _log.warning("endpoint_sniffer: browser session not started, skipping crawl")
        return []

    page.on("request", _on_request)
    page.on("response", _on_response)
    try:
        for target in nav_targets:
            if time.monotonic() >= deadline or len(captured) >= max_requests:
                break
            url = session.target_url + (target if target.startswith("/") else f"/{target}")
            try:
                await page.goto(url, timeout=10000)
                await page.wait_for_timeout(500)
            except Exception as exc:  # noqa: BLE001 — one bad target must not abort the crawl
                _log.info("endpoint_sniffer: nav target %r unreachable: %s", target, exc)
                continue

        if interact_chat and time.monotonic() < deadline and len(captured) < max_requests:
            try:
                chat_url, chat_body = await session._sniff_chat_request("Hello")  # noqa: SLF001
            except BrowserLoginError as exc:
                _log.warning("endpoint_sniffer: chat interaction skipped: %s", exc)
            except Exception as exc:  # noqa: BLE001
                _log.info("endpoint_sniffer: chat interaction failed: %s", exc)
            else:
                if chat_url is not None:
                    key = ("POST", urlparse(chat_url).path)
                    captured.setdefault(
                        key,
                        SniffedRequest(
                            method="POST",
                            url=chat_url,
                            path=urlparse(chat_url).path,
                            request_body_keys=list(chat_body.keys()) if chat_body else [],
                        ),
                    )
    except BrowserLoginError as exc:
        _log.warning("endpoint_sniffer: crawl aborted, browser unavailable: %s", exc)
        return []
    finally:
        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)

    return list(captured.values())


def merge_sniffed_endpoints_into_sbom(sbom: "AiSbomDocument", sniffed: list[SniffedRequest]) -> int:
    """Merge newly-discovered endpoints from *sniffed* into *sbom* as new
    ``API_ENDPOINT`` nodes, deduplicated against existing nodes.

    Reuses :class:`~nuguard.sbom.core.gap_fill.dedup.DedupContext` /
    ``_normalize_endpoint`` — the same primitive
    ``nuguard/sbom/core/gap_fill/rounds.py`` uses to avoid duplicating
    LLM-discovered nodes — rather than routing through the full LLM gap-fill
    round machinery, since this is a simpler exact-network-evidence case (no
    LLM judgment call needed: we observed the request actually happen).
    Returns the count of newly-added nodes.
    """
    from nuguard.sbom.core.gap_fill.dedup import DedupContext, _normalize_endpoint
    from nuguard.sbom.models import Evidence, Node, SourceLocation
    from nuguard.sbom.types import ComponentType

    if not sniffed:
        return 0

    dedup_ctx = DedupContext(sbom)
    added = 0
    location = SourceLocation(path="<browser_sniff>", line=None)

    for req in sniffed:
        display_name = f"{req.method} {req.path}"
        # Match the "endpoint:{METHOD}:{path}" canonical_name convention every
        # static adapter uses (fastapi_adapter.py, flask_adapter.py,
        # nestjs_adapter.py, gorilla_mux.py, http_router.py, ...) so a
        # sniffed endpoint dedups correctly against a statically-extracted
        # one for the same route, not just against other sniffed ones.
        canonical = f"endpoint:{req.method.upper()}:{req.path}"
        normalized = _normalize_endpoint(canonical)
        if not dedup_ctx.check_and_register(normalized, ComponentType.API_ENDPOINT, fuzzy=True):
            continue

        node = Node(
            name=display_name,
            component_type=ComponentType.API_ENDPOINT,
            confidence=BROWSER_SNIFF_CONFIDENCE,
            evidence=[
                Evidence(
                    kind=BROWSER_SNIFF_EVIDENCE_KIND,
                    confidence=BROWSER_SNIFF_CONFIDENCE,
                    detail=f"browser_sniff: observed {req.method} {req.path} (status={req.status_code})",
                    location=location,
                )
            ],
        )
        node.metadata.endpoint = req.path
        node.metadata.method = req.method
        node.metadata.extras["canonical_name"] = canonical
        sbom.nodes.append(node)
        added += 1
        _log.info(
            "endpoint_sniffer: added new API_ENDPOINT node %r from browser crawl", display_name
        )

    return added


__all__ = [
    "BROWSER_SNIFF_CONFIDENCE",
    "BROWSER_SNIFF_EVIDENCE_KIND",
    "SniffedRequest",
    "crawl_and_sniff",
    "merge_sniffed_endpoints_into_sbom",
]
