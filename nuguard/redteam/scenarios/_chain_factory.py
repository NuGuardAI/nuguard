"""Package-level factory for building AttackScenario objects from step lists.

All scenario builder files import ``make_scenario`` from here instead of
duplicating the uuid / ExploitChain / pre_score / AttackScenario boilerplate
inline.

Callers may pass steps in two modes:

* **Bare steps** — ``step_id`` and ``target_node_id`` left as their Pydantic
  defaults (``""``).  The factory auto-assigns ``step_id = "<chain_id>_s<N>"``
  and ``target_node_id = node_id`` so callers never need to know the chain_id.

* **Pre-wired steps** — ``step_id`` and ``target_node_id`` already set by the
  caller (legacy behaviour, e.g. ``sbom_driven.py``).  Existing values are
  preserved unchanged.
"""
from __future__ import annotations

import uuid

from nuguard.models.exploit_chain import ExploitChain, ExploitStep, GoalType, ScenarioType

from .pre_scorer import pre_score
from .scenario_types import AttackScenario


def _wire_step(s: ExploitStep, i: int, chain_id: str, node_id: str) -> ExploitStep:
    """Stamp auto-generated step_id / target_node_id onto a bare step template."""
    upd: dict = {}
    if not s.step_id:
        upd["step_id"] = f"{chain_id}_s{i}"
    if not s.target_node_id:
        upd["target_node_id"] = node_id
    return s.model_copy(update=upd) if upd else s


def make_scenario(
    node_id: str,
    goal_type: GoalType,
    scenario_type: ScenarioType,
    title: str,
    description: str,
    steps: list[ExploitStep],
    *,
    owasp_asi_ref: str = "",
    owasp_llm_ref: str = "",
    policy_clauses: list[str] | None = None,
    precondition_summary: str = "",
    pii_in_path: bool = False,
    pfi_in_path: bool = False,
    has_no_auth_tool: bool = False,
) -> AttackScenario:
    """Build a fully wired AttackScenario from a list of ExploitStep templates."""
    chain_id = str(uuid.uuid4())
    chain = ExploitChain(
        chain_id=chain_id,
        goal_type=goal_type,
        scenario_type=scenario_type,
        sbom_path=[node_id],
        owasp_asi_ref=owasp_asi_ref or None,
        owasp_llm_ref=owasp_llm_ref or None,
        policy_clauses=policy_clauses or [],
    )
    chain.steps = [_wire_step(s, i, chain_id, node_id) for i, s in enumerate(steps, 1)]
    chain.pre_score = pre_score(chain, pii_in_path=pii_in_path, pfi_in_path=pfi_in_path, has_no_auth_tool=has_no_auth_tool)
    return AttackScenario(
        scenario_id=str(uuid.uuid4()),
        goal_type=goal_type,
        scenario_type=scenario_type,
        title=title,
        description=description,
        precondition_summary=precondition_summary,
        target_node_ids=[node_id],
        impact_score=chain.pre_score,
        chain=chain,
    )
