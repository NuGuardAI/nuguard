"""Relevance-based API endpoint selection for direct-HTTP attack probes.

Apps with many API endpoints would otherwise get every probe family
(JWT tampering, reflected XSS, injection, ...) against every endpoint. This
module narrows that fan-out:

* **ci profile** — a small spot-check sample (default 3 endpoints) is used for
  every probe family.
* **other profiles** — when the endpoint count exceeds a threshold (default
  25), the redteam LLM picks, per probe family, only the endpoints where that
  probe is relevant. If the LLM is unavailable or returns unusable output the
  selector falls back to a deterministic heuristic and reports why via the
  returned notes (never silently).

The result is an :class:`EndpointPlan` consumed by
:class:`nuguard.redteam.scenarios.generator.ScenarioGenerator`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nuguard.common.logging import get_logger
from nuguard.sbom.models import Node
from nuguard.sbom.types import ComponentType

if TYPE_CHECKING:
    from nuguard.common.llm_client import LLMClient
    from nuguard.sbom.models import AiSbomDocument

_log = get_logger(__name__)

#: Direct-HTTP probe families the plan can scope per endpoint.
PROBE_FAMILIES: tuple[str, ...] = (
    "auth_bypass",
    "jwt_tampering",
    "mass_assignment",
    "price_tampering",
    "idor",
    "password_reset",
    "injection",
    "path_traversal",
    "open_redirect",
    "xss",
    "auth_scope",
    "rate_limit",
    "open_data_exposure",
)

_FAMILY_HINTS: dict[str, str] = {
    "auth_bypass": "endpoints that should require authentication",
    "jwt_tampering": "endpoints that validate bearer/JWT tokens",
    "mass_assignment": "POST/PUT/PATCH endpoints accepting object bodies",
    "price_tampering": "order/checkout/payment endpoints with price or quantity fields",
    "idor": "endpoints with object/user/tenant id parameters",
    "password_reset": "password reset / account recovery endpoints",
    "injection": "endpoints with parameters that may reach SQL/NoSQL queries",
    "path_traversal": "endpoints with file/path-like parameters",
    "open_redirect": "endpoints with redirect/url/next/return parameters",
    "xss": "endpoints that echo user input back in the response",
    "auth_scope": "endpoints with role/scope restrictions",
    "rate_limit": "expensive, login, or otherwise abusable endpoints",
    "open_data_exposure": "unauthenticated endpoints returning sensitive data",
}

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class EndpointPlan:
    """Allowed endpoint node ids per probe family.

    ``allowed is None`` for a family means "no restriction".
    """

    allowed: dict[str, set[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def permits(self, family: str, endpoint_id: str) -> bool:
        """Return True when *family* may be generated for *endpoint_id*."""
        allowed = self.allowed.get(family)
        return allowed is None or endpoint_id in allowed


def _api_endpoint_nodes(sbom: "AiSbomDocument") -> list[Node]:
    return [n for n in sbom.nodes if n.component_type == ComponentType.API_ENDPOINT]


def _endpoint_score(node: Node) -> int:
    """Heuristic attack-value score: more sensitive/attackable → higher."""
    meta = node.metadata
    score = 0
    if meta.returns_sensitive_data or meta.pii_fields or meta.phi_fields or meta.pfi_fields:
        score += 4
    if meta.auth_required:
        score += 2
    if (meta.method or "GET").upper() in _WRITE_METHODS:
        score += 2
    if meta.path_params or meta.request_body_schema:
        score += 2
    if getattr(meta, "idor_surface", False):
        score += 2
    return score


def heuristic_pick(nodes: list[Node], limit: int) -> list[Node]:
    """Deterministically pick the *limit* highest-value endpoints."""
    ranked = sorted(nodes, key=lambda n: (-_endpoint_score(n), n.name or "", str(n.id)))
    return ranked[:limit]


def _describe(node: Node) -> str:
    meta = node.metadata
    flags = []
    if meta.auth_required:
        flags.append("auth")
    if meta.returns_sensitive_data or meta.pii_fields or meta.phi_fields or meta.pfi_fields:
        flags.append("sensitive")
    if meta.path_params:
        flags.append("path_params=" + ",".join(meta.path_params[:4]))
    if meta.request_body_schema:
        flags.append("body=" + ",".join(list(meta.request_body_schema)[:6]))
    return (
        f"{node.id} | {(meta.method or 'GET').upper()} "
        f"{meta.endpoint or node.name} | {' '.join(flags)}"
    )


def _build_prompt(nodes: list[Node], limit: int | None) -> str:
    families = "\n".join(f'- "{f}": {h}' for f, h in _FAMILY_HINTS.items())
    endpoints = "\n".join(_describe(n) for n in nodes)
    cap = (
        f"Pick at most {limit} endpoint ids per family."
        if limit
        else "Pick only endpoints where the probe is genuinely relevant."
    )
    return (
        "You are planning API security probes for a red-team run. For each probe "
        "family below, choose which of the listed endpoints are worth testing. "
        f"{cap}\n\nProbe families:\n{families}\n\nEndpoints (id | method path | traits):\n"
        f"{endpoints}\n\nRespond with ONLY a JSON object mapping each family name to a "
        'list of endpoint ids (use the exact ids above), e.g. {"xss": ["id1"]}.'
    )


def _parse_plan(raw: str, valid_ids: set[str]) -> dict[str, set[str]] | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    plan: dict[str, set[str]] = {}
    for family in PROBE_FAMILIES:
        ids = data.get(family)
        if isinstance(ids, list):
            plan[family] = {str(i) for i in ids if str(i) in valid_ids}
    return plan or None


async def build_endpoint_plan(
    sbom: "AiSbomDocument",
    *,
    profile: str,
    llm: "LLMClient | None",
    threshold: int = 25,
    ci_spot_checks: int = 3,
) -> EndpointPlan | None:
    """Return an :class:`EndpointPlan`, or ``None`` when no narrowing is needed.

    Parameters
    ----------
    profile:
        Scan profile; ``"ci"`` always narrows to *ci_spot_checks* endpoints.
    llm:
        Redteam LLM client used to reason about per-family relevance.
    threshold:
        Endpoint count above which non-CI profiles use LLM-based narrowing.
    ci_spot_checks:
        Number of endpoints spot-checked in the CI profile.
    """
    nodes = _api_endpoint_nodes(sbom)
    if profile == "ci":
        if len(nodes) <= ci_spot_checks:
            return None
        return await _plan_ci(nodes, llm, ci_spot_checks)
    if len(nodes) <= threshold:
        return None
    return await _plan_llm(nodes, llm, None, f"{len(nodes)} API endpoints exceed threshold {threshold}")


async def _plan_ci(
    nodes: list[Node], llm: "LLMClient | None", limit: int
) -> EndpointPlan:
    plan = await _plan_llm(nodes, llm, limit, f"ci spot-check of {limit} endpoints")
    if plan is not None and any(plan.allowed.values()):
        # Union the LLM's picks so the CI run touches at most a handful of
        # distinct endpoints overall, not `limit` per family.
        union = set().union(*plan.allowed.values())
        keep = {str(n.id) for n in heuristic_pick([n for n in nodes if str(n.id) in union], limit)}
        plan.allowed = {f: ids & keep for f, ids in plan.allowed.items()}
        return plan
    notes = plan.notes if plan is not None else []
    return _heuristic_plan(nodes, limit, notes)


def _heuristic_plan(nodes: list[Node], limit: int, notes: list[str]) -> EndpointPlan:
    keep = {str(n.id) for n in heuristic_pick(nodes, limit)}
    return EndpointPlan(allowed={f: set(keep) for f in PROBE_FAMILIES}, notes=notes)


async def _plan_llm(
    nodes: list[Node], llm: "LLMClient | None", limit: int | None, reason: str
) -> EndpointPlan | None:
    """LLM-based plan; returns a notes-only plan (no ``allowed``) on failure."""
    if llm is None:
        note = f"API endpoint selection ({reason}): no redteam LLM configured — "
        note += "using heuristic selection" if limit else "testing all endpoints"
        _log.warning(note)
        return EndpointPlan(notes=[note])
    valid_ids = {str(n.id) for n in nodes}
    try:
        raw = await llm.complete(
            _build_prompt(nodes, limit),
            system="You are a precise application-security test planner. Output JSON only.",
            label="endpoint_selection",
        )
    except Exception as exc:  # noqa: BLE001 — LLM failures must degrade, not abort the run
        note = f"API endpoint selection ({reason}): LLM call failed ({exc}) — falling back"
        _log.warning(note)
        return EndpointPlan(notes=[note])
    allowed = _parse_plan(raw, valid_ids)
    if allowed is None:
        note = f"API endpoint selection ({reason}): unparseable LLM output — falling back"
        _log.warning(note)
        return EndpointPlan(notes=[note])
    # Families the LLM omitted get no endpoints only if it answered at all;
    # missing families stay unrestricted so a partial answer can't drop coverage.
    note = f"API endpoint selection ({reason}): LLM scoped {len(allowed)} probe families"
    _log.info(note)
    return EndpointPlan(allowed=allowed, notes=[note])
