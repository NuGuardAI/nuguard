"""Coverage tracking and coverage-turn generation for BehaviorRunner.

Tracks which SBOM-declared agents and tools have been seen in agent responses
across all turns.  Updated after each TurnVerdict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nuguard.behavior._utils import normalise_name, parse_json_array

if TYPE_CHECKING:
    from nuguard.behavior.judge import TurnVerdict
    from nuguard.behavior.models import IntentProfile
    from nuguard.common.llm_client import LLMClient

_log = logging.getLogger(__name__)

MAX_COVERAGE_TURNS = 5

_SYSTEM_PROMPT_BASE = (
    "You are writing follow-up messages for an AI agent integration test. "
    "Each message should be a natural-language user request that exercises a specific "
    "agent or tool that has not yet been mentioned in earlier responses. "
    "Keep each message concise (1-2 sentences). "
    "Requests must be contextually relevant to the application being tested. "
    "End every message with: "
    "'Please keep the response under 500 words and list all agents and tools "
    "involved in handling this request.'"
)


@dataclass
class CoverageState:
    """Mutable coverage snapshot for a single scenario run.

    Args:
        expected_agents: All agent node names from the SBOM (global denominator for
            adaptive-turn generation).
        expected_tools: All tool node names from the SBOM.
        scoped_agents: Optional subset of agents relevant to *this* scenario.
            When provided, ``coverage_pct`` is computed against this set rather
            than all expected_agents.  Adaptive-turn generation still uses the
            full expected set so out-of-scope components are still probed if
            budget allows.
        scoped_tools: Optional subset of tools relevant to *this* scenario.
    """

    expected_agents: set[str] = field(default_factory=set)
    expected_tools: set[str] = field(default_factory=set)
    seen_agents: set[str] = field(default_factory=set)
    seen_tools: set[str] = field(default_factory=set)
    scoped_agents: set[str] | None = None
    scoped_tools: set[str] | None = None

    def update(self, verdict: "TurnVerdict") -> None:
        """Record which agents and tools appeared in *verdict*."""
        for agent in verdict.agents_mentioned:
            cleaned = agent.strip()
            if cleaned:
                self.seen_agents.add(cleaned)
                self.seen_agents.add(normalise_name(cleaned))

        for tool in verdict.tools_mentioned:
            cleaned = tool.strip()
            if cleaned:
                self.seen_tools.add(cleaned)
                self.seen_tools.add(normalise_name(cleaned))

        _log.debug(
            "CoverageState.update: seen_agents=%s seen_tools=%s",
            self.seen_agents,
            self.seen_tools,
        )

    @property
    def uncovered_agents(self) -> set[str]:
        """Agent nodes that have not been observed yet."""
        return {
            a for a in self.expected_agents
            if a not in self.seen_agents and normalise_name(a) not in self.seen_agents
        }

    @property
    def uncovered_tools(self) -> set[str]:
        """Tool nodes that have not been observed yet."""
        return {
            t for t in self.expected_tools
            if t not in self.seen_tools and normalise_name(t) not in self.seen_tools
        }

    @property
    def coverage_pct(self) -> float:
        """Fraction of expected components that have been seen.

        If ``scoped_agents`` / ``scoped_tools`` were provided at construction,
        coverage is measured against those subsets (scenario-level coverage).
        Otherwise the full expected set is used (global denominator).

        Returns 1.0 when there are no expected components in scope.
        """
        scope_agents = self.scoped_agents if self.scoped_agents is not None else self.expected_agents
        scope_tools = self.scoped_tools if self.scoped_tools is not None else self.expected_tools
        total = len(scope_agents) + len(scope_tools)
        if total == 0:
            return 1.0
        covered_agents = sum(
            1 for a in scope_agents
            if a in self.seen_agents or normalise_name(a) in self.seen_agents
        )
        covered_tools = sum(
            1 for t in scope_tools
            if t in self.seen_tools or normalise_name(t) in self.seen_tools
        )
        return round((covered_agents + covered_tools) / total, 4)


def _template_message(
    component: str,
    description: str,
    domain_context: str = "",
    intent: "IntentProfile | None" = None,
) -> str:
    """Return a deterministic template follow-up for *component*."""
    action = description.strip() if description.strip() else "perform its primary function"
    if intent and intent.app_purpose:
        prefix = f"Within the context of {intent.app_purpose[:60]}, "
    elif domain_context:
        prefix = f"Within the current {domain_context} session, "
    else:
        prefix = ""
    return (
        f"{prefix}can you use {component} to {action}? "
        "Please keep the response under 500 words and list all agents and tools "
        "involved in handling this request."
    )


async def generate_coverage_turns(
    uncovered: set[str],
    session_context: str,
    component_descriptions: dict[str, str],
    llm_client: "LLMClient | None",
    domain_context: str = "",
    intent: "IntentProfile | None" = None,
) -> list[str]:
    """Return follow-up messages targeting uncovered SBOM components.

    Args:
        uncovered: Component names (agents or tools) not yet exercised.
        session_context: Brief summary of the last agent response.
        component_descriptions: Mapping of component name → description.
        llm_client: Optional LLM client for richer message generation.
        domain_context: One-line description of the application under test.
        intent: Optional IntentProfile for intent-grounded messages.

    Returns:
        A list of at most MAX_COVERAGE_TURNS message strings.
    """
    if not uncovered:
        return []

    targets = sorted(uncovered)[:MAX_COVERAGE_TURNS]

    if llm_client is None or getattr(llm_client, "api_key", None) is None:
        return [
            _template_message(name, component_descriptions.get(name, ""), domain_context, intent)
            for name in targets
        ]

    comp_list = "\n".join(
        f"- {name}: {component_descriptions.get(name, 'no description available')}"
        for name in targets
    )

    # Build context prefix from intent when available
    app_context = ""
    if intent and intent.app_purpose:
        app_context = f"Application context: {intent.app_purpose}\n\n"
    elif domain_context:
        app_context = f"Application context: {domain_context}\n\n"

    prompt = (
        f"{app_context}"
        f"Previous agent response summary: {session_context[:500]}\n\n"
        f"The following AI agent components have not yet been exercised in this test session:\n"
        f"{comp_list}\n\n"
        f"Write exactly {len(targets)} follow-up messages, one per component, as a JSON array "
        f"of strings. Each message must:\n"
        f"  - Be grounded in the application context above (not a generic probe).\n"
        f"  - Directly request the use of the named component with a concrete example.\n"
        f"  - End with: 'Please keep the response under 500 words and list all agents "
        f"and tools involved in handling this request.'\n\n"
        f"Output: JSON array only, no extra text."
    )

    system = (f"Context: {intent.app_purpose}\n" if intent and intent.app_purpose else "") + _SYSTEM_PROMPT_BASE

    try:
        raw = await llm_client.complete(prompt, system=system, label="behavior:coverage_gen")
    except Exception as exc:
        _log.warning("generate_coverage_turns: LLM call failed (%s), using templates", exc)
        return [
            _template_message(name, component_descriptions.get(name, ""), domain_context, intent)
            for name in targets
        ]

    items = parse_json_array(raw)
    if items is not None:
        messages = [str(m).strip() for m in items if m][:MAX_COVERAGE_TURNS]
        if messages:
            _log.debug("generate_coverage_turns: generated %d coverage turns", len(messages))
            return messages

    _log.debug("generate_coverage_turns: LLM output not parseable, using templates")
    return [
        _template_message(name, component_descriptions.get(name, ""), domain_context, intent)
        for name in targets
    ]
