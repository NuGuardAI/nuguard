"""Capability-aware scenario selector.

:func:`select_scenarios` is the entry point for Phase 2 catalog generation.
It:

1. Gates specs against the target's :class:`AppCapabilityProfile`.
2. Calls builder factories to produce concrete :class:`AttackScenario` objects.
3. Prioritises using the doc's six priority rules.
4. Deduplicates by (entry_endpoint, agent, first_turn_intent, delivery_channel,
   sink_type) — same objective through different channels stays distinct.
5. Caps to the requested profile target count with round-robin across categories.
6. Returns the scenario list alongside a :class:`CoverageReport`.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

from nuguard.common.logging import get_logger
from nuguard.sbom.models import AiSbomDocument

from .builders import BUILDER_FACTORIES, AppCapabilityProfile, BuilderContext
from .coverage import CoverageReport
from .spec import ScenarioSpec
from .taxonomy import ScenarioCategory

_log = get_logger(__name__)

# Profile target counts
_PROFILE_CAPS: dict[str, int] = {
    "ci": 20,
    "standard": 40,
    "full": 9999,  # effectively unlimited
    "minimal": 1,
}
_PROFILE_MIN_IMPACT: dict[str, float] = {
    "ci": 5.0,
    "standard": 3.0,
    "full": 0.0,
    "minimal": 0.0,
}


def select_scenarios(
    sbom: AiSbomDocument,
    profile: AppCapabilityProfile,
    scan_profile: str = "full",
    policy: object | None = None,
    with_guided: bool = True,
    catalog: tuple[ScenarioSpec, ...] | None = None,
) -> tuple[list, CoverageReport]:
    """Return ``(scenarios, coverage)`` for the current target.

    Parameters
    ----------
    sbom:
        The target's SBOM document.
    profile:
        App capability profile built by :class:`CapabilityDetector`.
    scan_profile:
        One of ``"ci"``, ``"standard"``, or ``"full"``.
    policy:
        Optional :class:`CognitivePolicy`; passed to factories that need it.
    with_guided:
        When False, guided-conversation specs are skipped (consistent with the
        legacy ``with_guided=False`` path).
    """

    active_catalog: tuple[ScenarioSpec, ...]
    if catalog is None:
        from .registry import SCENARIO_CATALOG
        active_catalog = SCENARIO_CATALOG
    else:
        active_catalog = catalog

    cap = _PROFILE_CAPS.get(scan_profile, 9999)
    min_impact = _PROFILE_MIN_IMPACT.get(scan_profile, 0.0)

    # Primary target agent: use first entry agent or first agent
    entry_ids = profile.entry_agent_ids or profile.all_agent_ids
    if not entry_ids:
        _log.warning("No AGENT nodes found in SBOM — catalog will be empty.")
        return [], CoverageReport(profile=scan_profile)

    node_by_id = {str(n.id): n for n in sbom.nodes}

    # Build one BuilderContext per entry agent (base contexts)
    agent_contexts: list[BuilderContext] = []
    for aid in entry_ids:
        agent = node_by_id.get(aid)
        if agent is None:
            continue
        agent_contexts.append(BuilderContext(
            sbom=sbom,
            spec=None,  # filled per-spec
            profile=profile,
            target_agent=agent,
            target_tool=None,
            policy=policy,
        ))

    # Index concrete SBOM surfaces for context enrichment
    from nuguard.sbom.types import ComponentType
    _tool_nodes = [n for n in sbom.nodes if n.component_type == ComponentType.TOOL]
    _api_ep_nodes = [
        n for n in sbom.nodes if n.component_type == ComponentType.API_ENDPOINT
    ]
    _mcp_tool_nodes = [
        n for n in _tool_nodes
        if n.metadata and (getattr(n.metadata, "mcp_server_url", None) or "mcp" in (n.name or "").lower())
    ]

    # Walk catalog ─────────────────────────────────────────────────────────────
    generated: list = []
    skipped: list[tuple[str, str, str]] = []
    category_counts: dict[str, int] = defaultdict(int)

    for spec in active_catalog:
        # --- capability gate ----
        if not profile.satisfies(spec.required_capabilities):
            missing = spec.required_capabilities - profile.capabilities
            skipped.append((
                spec.id,
                spec.category.value,
                f"capability_missing:{','.join(c.value for c in missing)}",
            ))
            continue

        # --- impact filter ----
        if spec.base_impact < min_impact:
            skipped.append((spec.id, spec.category.value, "profile_capped"))
            continue

        # --- explicitly disabled ----
        if not spec.enabled:
            skipped.append((spec.id, spec.category.value, "spec_disabled"))
            continue

        # --- guided flag ---
        # Memory and multi-session specs need guided conversations
        from .taxonomy import Capability as C
        needs_multi_session = C.MULTI_SESSION in spec.required_capabilities
        if needs_multi_session and not with_guided:
            skipped.append((spec.id, spec.category.value, "needs_guided"))
            continue

        builder_key = spec.resolved_builder_key()
        factory = BUILDER_FACTORIES.get(builder_key)
        if factory is None:
            skipped.append((spec.id, spec.category.value, "factory_missing"))
            continue

        # Determine concrete surface nodes to bind for this spec
        from .taxonomy import Capability as C
        _needs_mcp = (
            C.MCP_SERVER in spec.required_capabilities
            and builder_key in ("mcp_toxic_flow", "mcp_tool_injection", "mcp_output_poisoning")
        )
        _needs_api = builder_key in ("mass_assignment", "auth_bypass", "idor")

        # Build expanded context list with concrete node bindings
        expanded_contexts: list[BuilderContext] = []
        for base_ctx in agent_contexts:
            if _needs_mcp and _mcp_tool_nodes:
                for tool_node in _mcp_tool_nodes[:2]:
                    expanded_contexts.append(BuilderContext(
                        sbom=base_ctx.sbom,
                        spec=spec,
                        profile=base_ctx.profile,
                        target_agent=base_ctx.target_agent,
                        target_tool=tool_node,
                        policy=base_ctx.policy,
                    ))
            elif _needs_api and _api_ep_nodes:
                for ep_node in _api_ep_nodes[:2]:
                    expanded_contexts.append(BuilderContext(
                        sbom=base_ctx.sbom,
                        spec=spec,
                        profile=base_ctx.profile,
                        target_agent=base_ctx.target_agent,
                        target_tool=base_ctx.target_tool,
                        policy=base_ctx.policy,
                        target_endpoint=ep_node,
                    ))
            else:
                expanded_contexts.append(BuilderContext(
                    sbom=base_ctx.sbom,
                    spec=spec,
                    profile=base_ctx.profile,
                    target_agent=base_ctx.target_agent,
                    target_tool=base_ctx.target_tool,
                    policy=base_ctx.policy,
                ))

        # Run factory for each context
        for ctx in expanded_contexts:
            try:
                scenarios = factory(ctx)
            except NotImplementedError:
                skipped.append((spec.id, spec.category.value, "builder_pending"))
                break
            except Exception as exc:
                _log.warning("Catalog builder %r failed for %s: %s", builder_key, spec.id, exc)
                continue
            for s in scenarios:
                generated.append(s)
                category_counts[spec.category.value] += 1

    # Deduplicate ──────────────────────────────────────────────────────────────
    generated = _dedup(generated)

    # Prioritise ───────────────────────────────────────────────────────────────
    generated.sort(key=lambda s: s.impact_score, reverse=True)

    # Cap with round-robin across categories ───────────────────────────────────
    if len(generated) > cap:
        generated = _cap_round_robin(generated, cap)

    # Build coverage report ────────────────────────────────────────────────────
    covered_cats: list[ScenarioCategory] = []
    for cat in ScenarioCategory:
        if category_counts.get(cat.value, 0) > 0:
            covered_cats.append(cat)

    report = CoverageReport(
        profile=scan_profile,
        total_generated=len(generated),
        categories_covered=covered_cats,
        per_category_count=dict(category_counts),
        skipped=skipped,
        capabilities_detected=sorted(profile.capabilities, key=lambda c: c.value),
    )

    _log.info(
        "Catalog selection: %d scenarios generated, %d skipped, %d categories covered",
        len(generated), len(skipped), len(covered_cats),
    )
    return generated, report


def _dedup(scenarios: list) -> list:
    """Remove near-duplicate scenarios, preserving distinct delivery channels."""
    seen: set[str] = set()
    out: list = []
    for s in scenarios:
        # Key = goal_type + scenario_type + catalog_id + delivery_channel + sink_type + agent
        agent_id = s.target_node_ids[0] if s.target_node_ids else ""
        channel = s.delivery_channel.value if s.delivery_channel else ""
        sink = s.sink_type.value if s.sink_type else ""
        # Use first step payload prefix as intent fingerprint
        payload_prefix = ""
        if s.chain and s.chain.steps:
            payload_prefix = s.chain.steps[0].payload[:80]
        elif s.guided_conversation:
            payload_prefix = (s.guided_conversation.goal_description or "")[:80]
        raw = f"{s.goal_type.value}|{s.scenario_type.value}|{s.catalog_id}|{channel}|{sink}|{agent_id}|{payload_prefix}"
        key = hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()  # noqa: S324
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _cap_round_robin(scenarios: list, cap: int) -> list:
    """Keep up to ``cap`` scenarios using round-robin across categories."""
    by_cat: dict[str, list] = defaultdict(list)
    for s in scenarios:
        by_cat[s.category or "unknown"].append(s)
    result: list = []
    while len(result) < cap:
        added = False
        for cat_scenarios in by_cat.values():
            if cat_scenarios and len(result) < cap:
                result.append(cat_scenarios.pop(0))
                added = True
        if not added:
            break
    return result
