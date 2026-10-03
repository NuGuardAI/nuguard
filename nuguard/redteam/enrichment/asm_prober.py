"""Agentic Surface Model prober (redteam-proposal.md W1).

Extends pre-scan discovery beyond the single chat endpoint that
``common/auto_sbom_enricher.py`` deliberately bounds itself to (that module
is shared with the plain ``sbom generate`` path and should stay a narrow,
chat-endpoint-scoped probe). This module lives in ``redteam/enrichment/``
instead — the proposal's own words: "``enrichment/`` already exists —
formalize ASM as its output type" — and is allowed open-ended probing
within its own budget because it only ever runs as part of a redteam scan,
which already has its own circuit breaker, rate limiting, and config
gating.

Every probe here is GET or OPTIONS only — never a method that could mutate
server state on an undiscovered route — and the whole pass is capped by
``max_probe_requests`` (default 25, mirroring ``auto_sbom_enricher``'s
``_MAX_PROBE_REQUESTS`` convention).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from nuguard.common.endpoint_detection.constants import OPENAPI_SCHEMA_PATHS
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.sbom.types import ComponentType

from .asm_models import (
    AgenticSurfaceModel,
    AsmAuthClassification,
    AsmCorsFinding,
    AsmEndpoint,
    AsmObservationChannel,
)

if TYPE_CHECKING:
    from nuguard.common.target_client_builder import TargetClient
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

DEFAULT_MAX_PROBE_REQUESTS = 25

# Schema-exposure path not already in OPENAPI_SCHEMA_PATHS (W02).
_SCHEMA_EXTRA_PATHS: tuple[str, ...] = ("/docs",)

# Tool/agent inventory disclosure candidates (W01) — generic, not app-specific;
# callers extend this via redteam.asm.extra_inventory_paths in nuguard.yaml.
_DEFAULT_INVENTORY_PATHS: tuple[str, ...] = (
    "/api/tools",
    "/api/agents",
    "/.well-known/ai-plugin.json",
    "/.well-known/oauth-protected-resource",
)

# Heuristic for classifying an existing SBOM API_ENDPOINT node as a
# WebSocket/SSE observation channel (W03) — static-structure signal only;
# no new network discovery of *undeclared* WS routes in this phase.
_WS_PATH_HINTS = ("ws", "socket", "stream", "events", "realtime")

_CORS_PROBE_ORIGIN = "https://nuguard-cors-probe.invalid"

# Status codes that mean "this route exists and enforces auth" vs.
# "genuinely not present" — mirrors REACHABLE_AUTH_STATUS_CODES/
# ROTATION_STATUS_CODES in common/endpoint_detection/constants.py, kept
# local since ASM's classification needs are narrower (open/auth/absent).
_AUTH_ENFORCED_STATUSES = frozenset({401, 403})
_ABSENT_STATUSES = frozenset({404, 405, 501})


class _BudgetExhausted(Exception):
    """Internal sentinel — stops probing once max_probe_requests is hit."""


class _Budget:
    def __init__(self, max_requests: int) -> None:
        self.max_requests = max_requests
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.max_requests:
            raise _BudgetExhausted
        self.used += 1


async def _probe_path(
    client: "TargetClient", budget: _Budget, path: str, method: str = "GET",
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, str, dict]:
    budget.spend()
    status, text, body = await client.invoke_endpoint(
        path=path, method=method, extra_headers=extra_headers, strip_auth=True,
    )
    return status, text, body


async def _probe_openapi_and_schema(
    client: "TargetClient", budget: _Budget,
) -> list[AsmEndpoint]:
    endpoints: list[AsmEndpoint] = []
    for path in (*OPENAPI_SCHEMA_PATHS, *_SCHEMA_EXTRA_PATHS):
        try:
            status, _text, _body = await _probe_path(client, budget, path)
        except _BudgetExhausted:
            break
        except Exception as exc:  # noqa: BLE001 — one bad probe must not abort the pass
            _log.debug("ASM openapi probe failed for %s: %s", path, exc)
            continue
        if status in _ABSENT_STATUSES or status == 0:
            continue
        auth_cls: AsmAuthClassification = "auth_enforced" if status in _AUTH_ENFORCED_STATUSES else "open"
        endpoints.append(AsmEndpoint(
            url=path, methods=["GET"], auth_classification=auth_cls,
            source="openapi", status_codes={"GET": status},
        ))
    return endpoints


async def _probe_inventory(
    client: "TargetClient", budget: _Budget, extra_paths: tuple[str, ...],
) -> tuple[list[AsmEndpoint], list[dict]]:
    endpoints: list[AsmEndpoint] = []
    disclosures: list[dict] = []
    for path in (*_DEFAULT_INVENTORY_PATHS, *extra_paths):
        try:
            status, text, body = await _probe_path(client, budget, path)
        except _BudgetExhausted:
            break
        except Exception as exc:  # noqa: BLE001
            _log.debug("ASM inventory probe failed for %s: %s", path, exc)
            continue
        if status in _ABSENT_STATUSES or status == 0:
            continue
        auth_cls: AsmAuthClassification = "auth_enforced" if status in _AUTH_ENFORCED_STATUSES else "open"
        endpoints.append(AsmEndpoint(
            url=path, methods=["GET"], auth_classification=auth_cls,
            source="heuristic_inventory", status_codes={"GET": status},
        ))
        if auth_cls == "open" and 200 <= status < 300:
            disclosure: dict[str, Any] = body if body else {"path": path}
            if not body and text:
                disclosure = {"path": path, "body_excerpt": text[:500]}
            disclosures.append(disclosure)
    return endpoints, disclosures


def _ws_candidate_urls(sbom: "AiSbomDocument", base_url: str) -> list[tuple[str, str]]:
    """Return (node_id, path) pairs for SBOM endpoints that look WS/SSE-shaped."""
    candidates: list[tuple[str, str]] = []
    for node in sbom.nodes:
        if node.component_type != ComponentType.API_ENDPOINT:
            continue
        path = (node.metadata.endpoint if node.metadata else None) or ""
        haystack = f"{node.name or ''} {path}".lower()
        if any(hint in haystack for hint in _WS_PATH_HINTS) and path:
            candidates.append((str(node.id), path))
    return candidates


async def _probe_observation_channels(
    sbom: "AiSbomDocument", base_url: str, budget: _Budget,
) -> list[AsmObservationChannel]:
    from nuguard.redteam.target.ws_client import to_ws_url  # noqa: PLC0415

    channels: list[AsmObservationChannel] = []
    for node_id, path in _ws_candidate_urls(sbom, base_url):
        try:
            budget.spend()
        except _BudgetExhausted:
            break
        url = to_ws_url(base_url) + path
        connect_auth_required = True
        try:
            import websockets  # noqa: PLC0415

            async with await websockets.connect(url, open_timeout=3.0):
                connect_auth_required = False
        except _BudgetExhausted:
            raise
        except Exception as exc:  # noqa: BLE001 — a closed/refused handshake is the "good" outcome
            _log.debug("ASM observation-channel probe: %s rejected connect (%s)", url, exc)
        channels.append(AsmObservationChannel(
            url=url, transport="ws", connect_auth_required=connect_auth_required, node_id=node_id,
        ))
    return channels


async def _probe_cors_reflection(
    client: "TargetClient", budget: _Budget, path: str,
) -> list[AsmCorsFinding]:
    try:
        budget.spend()
    except _BudgetExhausted:
        return []
    headers = await client.probe_cors(path, _CORS_PROBE_ORIGIN)
    if headers is None:
        return []
    allow_origin = headers.get("access-control-allow-origin", "")
    allow_credentials = headers.get("access-control-allow-credentials", "").lower() == "true"
    allow_methods = headers.get("access-control-allow-methods", "")
    origin_reflected = allow_origin in (_CORS_PROBE_ORIGIN, "*")
    return [AsmCorsFinding(
        url=path, origin_reflected=origin_reflected, credentials_allowed=allow_credentials,
        methods_allowed=[m.strip() for m in allow_methods.split(",") if m.strip()],
    )]


async def build_asm(
    client: "TargetClient",
    sbom: "AiSbomDocument",
    max_probe_requests: int = DEFAULT_MAX_PROBE_REQUESTS,
    extra_inventory_paths: tuple[str, ...] = (),
) -> AgenticSurfaceModel:
    """Build the Agentic Surface Model for one scan.

    Best-effort by design: any individual probe's exception is logged and
    swallowed so one unreachable candidate never aborts the rest of the
    pass, and the whole function never raises.
    """
    budget = _Budget(max_probe_requests)
    base_url = client.base_url

    openapi_endpoints: list[AsmEndpoint] = []
    inventory_endpoints: list[AsmEndpoint] = []
    disclosures: list[dict] = []
    channels: list[AsmObservationChannel] = []
    cors_findings: list[AsmCorsFinding] = []

    try:
        openapi_endpoints = await _probe_openapi_and_schema(client, budget)
        inventory_endpoints, disclosures = await _probe_inventory(
            client, budget, extra_inventory_paths,
        )
        channels = await _probe_observation_channels(sbom, base_url, budget)
        cors_findings = await _probe_cors_reflection(client, budget, "/")
    except Exception as exc:  # noqa: BLE001 — ASM must never abort the scan
        _log.warning("ASM prober: unexpected error, returning partial results: %s", exc)

    return AgenticSurfaceModel(
        target_base_urls=[base_url],
        endpoints=[*openapi_endpoints, *inventory_endpoints],
        observation_channels=channels,
        cors_findings=cors_findings,
        inventory_disclosures=disclosures,
        probe_budget_used=budget.used,
    )


def apply_asm_to_sbom(sbom: "AiSbomDocument", asm: AgenticSurfaceModel) -> "AiSbomDocument":
    """Promote a summarized, credential-redacted ASM subset onto AGENT nodes.

    Mirrors how ``auto_sbom_enricher.py`` already writes chat-endpoint facts
    onto the AGENT node — only booleans/counts are promoted, never a header
    value or response body (the full ``AgenticSurfaceModel`` stays a
    runtime-only artifact; see the module docstring).
    """
    from nuguard.sbom.models import AsmSummary  # noqa: PLC0415

    summary = AsmSummary(
        sibling_endpoint_count=len(asm.endpoints),
        unauthenticated_inventory_exposed=asm.has_unauthenticated_inventory_disclosure,
        observation_channel_unauthenticated=asm.has_unauthenticated_observation_channel,
        cors_wildcard_with_credentials_live=asm.has_cors_wildcard_with_credentials,
    )
    for node in sbom.nodes:
        if node.component_type == ComponentType.AGENT and node.metadata is not None:
            node.metadata.asm_summary = summary
    return sbom


def build_asm_findings(asm: AgenticSurfaceModel) -> list[Finding]:
    """W01-W04 — pure recon findings, built directly from the ASM.

    These bypass the scenario/chain/step pipeline entirely: scenario
    generation already runs before the live client exists (see
    orchestrator.py's own comment on endpoint-liveness enrichment for the
    same timing constraint), and there is no attack payload or LLM judgment
    here to warrant that machinery — a GET returning 200 unauthenticated
    *is* the finding. Mirrors the W5 defence-regression pre-pass's shape
    (nuguard.redteam.defence_regressions.evaluator.build_regression_findings).
    """
    findings: list[Finding] = []

    for ep in asm.endpoints:
        if ep.auth_classification != "open":
            continue
        if ep.source == "heuristic_inventory":
            findings.append(_asm_finding(
                "W01", "Unauthenticated inventory disclosure",
                Severity.HIGH,
                f"GET {ep.url} returned tool/agent inventory data without authentication.",
                ep.url,
            ))
        elif ep.source == "openapi":
            findings.append(_asm_finding(
                "W02", "Unauthenticated schema exposure",
                Severity.MEDIUM,
                f"GET {ep.url} returned the API schema without authentication.",
                ep.url,
            ))

    for ch in asm.observation_channels:
        if not ch.connect_auth_required:
            findings.append(_asm_finding(
                "W03", "Unauthenticated observation-channel connect",
                Severity.HIGH,
                f"A WS handshake to {ch.url} succeeded without any credential.",
                ch.url,
            ))

    for cors in asm.cors_findings:
        if cors.origin_reflected and cors.credentials_allowed:
            findings.append(_asm_finding(
                "W04", "CORS reflection misconfiguration",
                Severity.HIGH,
                f"OPTIONS {cors.url} reflected an attacker-controlled Origin "
                "with Access-Control-Allow-Credentials: true.",
                cors.url,
            ))

    return findings


def _asm_finding(catalog_id: str, title: str, severity: Severity, evidence: str, url: str) -> Finding:
    return Finding(
        finding_id=f"asm-{catalog_id.lower()}-{uuid.uuid4().hex[:8]}",
        title=f"{title} ({url})",
        severity=severity,
        description=title,
        evidence=evidence,
        reasoning="Agentic Surface Model pre-pass recon probe (GET/OPTIONS only, no attack payload).",
        affected_component=url,
        authorization_decision="allow",
        guardrail_control="none",
    )
