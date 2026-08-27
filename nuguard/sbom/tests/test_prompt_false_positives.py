"""Regression tests for _is_likely_prompt() over-generation.

Confirmed false positives on a real NestJS/React app (studyield-app):
  - "message" in prompt_ctx_words auto-flagged any variable/function whose
    name merely contains "message" (React MessageBubble component, WebSocket
    handleConnection/handleDisconnect lifecycle methods, NestJS canActivate
    guards), regardless of the string's actual content.
  - Role markers ("system:", "user:", "ai:", ...) matched anywhere as a
    substring, not just at the start of a meaningful line, so incidental
    colons in log lines/labels qualified.

Real prompt templates (name derived from an enclosing "*Prompt"/"*System"
context, or containing genuine role-marker lines) must still be detected.
"""

from __future__ import annotations

from nuguard.sbom.adapters.typescript.prompts import _is_likely_prompt
from nuguard.sbom.core.ts_parser import TSStringLiteral


def _lit(value: str, context: str | None = None) -> TSStringLiteral:
    return TSStringLiteral(value=value, context=context, char_count=len(value))


class TestConfirmedFalsePositivesRejected:
    def test_message_bubble_component_text_not_flagged(self) -> None:
        text = (
            "This component renders a single chat message bubble with the "
            "sender avatar and timestamp shown below the text content."
        )
        assert _is_likely_prompt(_lit(text, context="MessageBubble")) is False

    def test_can_activate_guard_not_flagged(self) -> None:
        text = (
            "Guard clause that checks whether the current request carries a "
            "valid session before allowing the route handler to proceed."
        )
        assert _is_likely_prompt(_lit(text, context="canActivate")) is False

    def test_handle_connection_lifecycle_not_flagged(self) -> None:
        text = (
            "Called whenever a new WebSocket client connects to the gateway "
            "so the connection can be registered in the active client map."
        )
        assert _is_likely_prompt(_lit(text, context="handleConnection")) is False

    def test_incidental_colon_in_log_line_not_flagged(self) -> None:
        text = "Origin: unknown request received without a valid origin header set"
        assert _is_likely_prompt(_lit(text, context="logMessage")) is False


class TestRealPromptsStillDetected(object):
    def test_system_prompt_context_still_flagged(self) -> None:
        text = "You are a helpful assistant that answers questions about the app."
        assert _is_likely_prompt(_lit(text, context="systemPrompt")) is True

    def test_role_marker_at_start_of_line_still_flagged(self) -> None:
        text = (
            "system: You are an assistant for a study platform.\n"
            "user: {question}\n"
        )
        assert _is_likely_prompt(_lit(text, context="template")) is True
