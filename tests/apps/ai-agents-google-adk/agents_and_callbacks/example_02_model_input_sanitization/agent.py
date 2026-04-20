# agent.py in 02_model_input_sanitization
import re
from typing import Optional

from google.adk.agents import Agent as LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

# Simplified PII patterns for demo
PII_PATTERNS = {
    "CREDIT_CARD": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
    "SSN": re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
}
REDACTION_PLACEHOLDER = "[REDACTED PII]"


def before_model_callback_sanitize(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    modified = False
    if llm_request.contents:
        # Process the last user message for sanitization
        last_user_content_index = -1
        for i in range(len(llm_request.contents) - 1, -1, -1):
            if llm_request.contents[i].role == "user":
                last_user_content_index = i
                break

        if (
            last_user_content_index != -1
            and llm_request.contents[last_user_content_index].parts
        ):
            original_text = llm_request.contents[last_user_content_index].parts[0].text
            sanitized_text = original_text

            print(f"\n[BEFORE MODEL] Original user input: '{original_text}'")
            for pii_type, pattern in PII_PATTERNS.items():
                if pattern.search(sanitized_text):
                    sanitized_text = pattern.sub(REDACTION_PLACEHOLDER, sanitized_text)
                    modified = True
                    print(f"[BEFORE MODEL] Redacted {pii_type}.")

            if modified:
                llm_request.contents[last_user_content_index].parts[
                    0
                ].text = sanitized_text
                print(f"[BEFORE MODEL] Sanitized input to LLM: '{sanitized_text}'")
            print("\n")
    return None


input_sanitizer_agent = LlmAgent(
    name="input_sanitizer_agent",
    description="An agent that sanitizes user input for PII before LLM processing.",
    model="gemini-2.0-flash",
    instruction="You are a helpful general knowledge assistant.",
    before_model_callback=before_model_callback_sanitize,
)

root_agent = input_sanitizer_agent
