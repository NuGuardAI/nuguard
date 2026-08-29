"""Regression tests for the capability-discovery text heuristics in
nuguard.common.discovery.

Covers a real failure observed against a live target (getmosaiccare.com,
2026-08-28): a healthcare agent answered the sub-agents probe with "No, I
don't use agents/sub-agents... Here is exactly what I am: - **A single AI
assistant** - **I read your question** and reason about which tools are
needed..." — a negative answer that nonetheless contains a bulleted
self-description. _is_refusal() didn't recognise "I don't use" as a refusal,
and _extract_list_items() harvested the bullets verbatim (markdown bold and
all) as fake sub-agent names, which then flowed into ungrounded, hallucinated
behavior scenarios.
"""
from __future__ import annotations

from nuguard.common.discovery import _extract_list_items, _is_refusal


class TestIsRefusal:
    def test_i_dont_use_is_a_refusal(self) -> None:
        assert _is_refusal("No, I don't use agents, sub-agents, orchestrators, or planners.")

    def test_i_dont_have_is_still_a_refusal(self) -> None:
        assert _is_refusal("I don't have access to that information.")

    def test_positive_answer_is_not_a_refusal(self) -> None:
        assert not _is_refusal("Yes, I use a planner agent and a reviewer agent.")


class TestExtractListItems:
    def test_rejects_narrative_self_description_bullets(self) -> None:
        # The bulleted part alone, as _extract_list_items would see it if
        # _is_refusal ever failed to catch the leading "I don't use..."
        # sentence (defense in depth — the primary fix is the widened
        # _REFUSAL_RE, which discards this whole response before it ever
        # reaches _extract_list_items; see TestIsRefusal).
        text = (
            "- **A single AI assistant**\n"
            "- **I read your question** and reason about which tools are needed to answer it well\n"
            "- **I call those tools**\n"
            "- **I synthesize the results** and write a clear, personalized answer\n"
        )
        items = _extract_list_items(text)
        assert "I read your question" not in items
        assert "I call those tools" not in items
        assert "I synthesize the results" not in items
        assert not any(item.lower().startswith(("i ", "i'm", "i've")) for item in items)

    def test_strips_markdown_bold_from_plausible_names(self) -> None:
        text = "- **get_vitals**\n- **lookup_drug**\n- **get_lab_results**\n"
        assert _extract_list_items(text) == ["get_vitals", "lookup_drug", "get_lab_results"]

    def test_still_extracts_plain_bullet_tool_names(self) -> None:
        text = "Here are my tools:\n- Check balance\n- Transfer funds\n- Apply for a loan\n"
        assert _extract_list_items(text) == ["Check balance", "Transfer funds", "Apply for a loan"]

    def test_prose_fallback_still_works(self) -> None:
        text = "I can book flights, cancel reservations, and check in."
        items = _extract_list_items(text)
        assert "book flights" in items
        assert "cancel reservations" in items
        assert "check in" in items
