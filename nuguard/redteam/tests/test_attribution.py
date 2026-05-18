"""Tests for the attribution probe (§2.8)."""
from __future__ import annotations

from nuguard.redteam.executor.attribution import (
    ATTRIBUTION_FOOTER,
    parse_handled_by,
    strip_meta_footer,
)
from nuguard.sbom.models import AiSbomDocument, Node, NodeType

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sbom(*agent_names: str, tool_names: tuple[str, ...] = ()) -> AiSbomDocument:
    """Build a minimal SBOM with the given agent and tool names."""
    import uuid
    nodes: list[Node] = []
    for name in agent_names:
        nodes.append(Node(
            id=uuid.uuid4(),
            name=name,
            component_type=NodeType.AGENT,
            confidence=1.0,
        ))
    for name in tool_names:
        nodes.append(Node(
            id=uuid.uuid4(),
            name=name,
            component_type=NodeType.TOOL,
            confidence=1.0,
        ))
    return AiSbomDocument(nodes=nodes, edges=[], deps=[], target="test://sbom")


# ── strip_meta_footer ─────────────────────────────────────────────────────────

def test_strip_removes_footer_line() -> None:
    response = (
        "Here is your flight status: AA123 is on time.\n\n"
        "[nuguard-meta] handled_by=Flight Status Agent; tools_used=flight_status_tool; handoff_chain=none"
    )
    clean, raw_footer = strip_meta_footer(response)
    assert "[nuguard-meta]" not in clean
    assert "AA123 is on time" in clean
    assert "[nuguard-meta]" in raw_footer


def test_strip_no_footer_unchanged() -> None:
    response = "I cannot help with that."
    clean, raw_footer = strip_meta_footer(response)
    assert clean == response
    assert raw_footer == ""


def test_strip_trailing_whitespace_cleaned() -> None:
    response = "Hello.\n\n[nuguard-meta] handled_by=Agent; tools_used=none; handoff_chain=none\n"
    clean, _ = strip_meta_footer(response)
    assert not clean.endswith("\n\n")
    assert "Hello." in clean


# ── parse_handled_by — meta footer ───────────────────────────────────────────

def test_parse_meta_footer_resolves_agent() -> None:
    sbom = _sbom("Flight Status Agent", "Cancellation Agent")
    response = (
        "Your flight is on time.\n"
        "[nuguard-meta] handled_by=Flight Status Agent; tools_used=none; handoff_chain=none"
    )
    record = parse_handled_by(response, sbom)
    assert record.source == "meta_footer"
    assert record.handled_by_agent_id is not None
    assert record.confidence == 1.0


def test_parse_meta_footer_resolves_tools() -> None:
    sbom = _sbom("Triage Agent", tool_names=("cancel_flight_tool", "baggage_tool"))
    response = (
        "I have cancelled your flight.\n"
        "[nuguard-meta] handled_by=Triage Agent; tools_used=cancel_flight_tool; handoff_chain=none"
    )
    record = parse_handled_by(response, sbom)
    assert record.source == "meta_footer"
    assert len(record.tools_used_ids) >= 1


def test_parse_meta_footer_handles_reordered_fields() -> None:
    sbom = _sbom("FAQ Agent")
    response = (
        "Here's your answer.\n"
        "[nuguard-meta] tools_used=none; handled_by=FAQ Agent; handoff_chain=none"
    )
    record = parse_handled_by(response, sbom)
    assert record.source == "meta_footer"
    assert record.handled_by_agent_id is not None


def test_parse_meta_footer_unknown_agent_name() -> None:
    sbom = _sbom("Known Agent")
    response = (
        "I can help with that.\n"
        "[nuguard-meta] handled_by=Completely Unknown Bot; tools_used=none; handoff_chain=none"
    )
    record = parse_handled_by(response, sbom)
    # Footer was present and parsed, but the name didn't resolve
    # Source should still be meta_footer since we found the footer
    assert record.source in ("meta_footer", "unknown")


# ── parse_handled_by — natural language fallback ─────────────────────────────

def test_parse_natural_language_agent() -> None:
    sbom = _sbom("FAQ Agent", "Cancellation Agent")
    response = "I'm the FAQ Agent and I can answer questions about your booking."
    record = parse_handled_by(response, sbom)
    assert record.source == "natural_language"
    assert record.handled_by_agent_id is not None
    assert record.confidence > 0


def test_parse_natural_language_tool() -> None:
    sbom = _sbom("Triage Agent", tool_names=("flight_status_tool",))
    response = "I'm using the flight_status_tool to check your flight details."
    record = parse_handled_by(response, sbom)
    assert record.source == "natural_language"
    # tool resolution may succeed
    # At minimum the source is natural_language and confidence > 0
    assert record.confidence > 0


def test_parse_unknown_when_no_signals() -> None:
    sbom = _sbom("Agent A", "Agent B")
    response = "Please provide your confirmation number."
    record = parse_handled_by(response, sbom)
    assert record.source == "unknown"
    assert record.confidence == 0.0
    assert record.handled_by_agent_id is None


# ── ATTRIBUTION_FOOTER constant ───────────────────────────────────────────────

def test_attribution_footer_contains_nuguard_meta() -> None:
    assert "[nuguard-meta]" in ATTRIBUTION_FOOTER


def test_attribution_footer_not_adversarial() -> None:
    # The footer should not contain explicit security/attack words
    footer_lower = ATTRIBUTION_FOOTER.lower()
    assert "attack" not in footer_lower
    assert "exploit" not in footer_lower
    assert "override" not in footer_lower
